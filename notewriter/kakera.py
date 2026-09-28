"""かけら管理（SPEC.md 機能1）: かけら・ネタ帳・記事と区間・充足度・生成来歴。

保存先（すべて作業フォルダ store.vault() 配下）:
- kakera/K0001.md   かけら（front matter + Markdown本文）
- neta/N0001.md     ネタ帳（記事に紐付かないメモ。かけらの手前の入り口）
- articles.json     記事と区間の並び
- provenance.json   生成来歴（どのかけらを根拠に本文を確定したか）
- counters.json     採番済みの最大番号（削除後も番号を再利用しないため）

書き込みはすべて store.atomic_write 経由。読み書きの組み合わせ（採番・JSONへの追記）は
作業フォルダ内の .nw-lock でプロセス間排他する（Web画面とCLIの同時操作対策）。
"""
from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import store

try:
    import fcntl
except ImportError:  # pragma: no cover  Windows では排他なし（対象外の環境）
    fcntl = None

DEFAULT_CONFIG = {
    "viewpoints": ["出来事", "五感", "体の反応", "感情", "セリフ", "分岐点", "時系列・背景"],
    "coverage_viewpoints": ["五感", "体の反応", "セリフ", "分岐点", "感情"],
    "statuses": ["未使用", "使用済み", "保留"],
}
NETA_STATUSES = ("未整理", "かけら化済み")
NO_ARTICLE = "（記事未設定）"

KAKERA_LIST_KEYS = ("viewpoints", "tags")
KAKERA_FIELDS = ("article", "section", "viewpoints", "status", "tags", "source", "body")
NETA_LIST_KEYS = ("tags",)
NETA_FIELDS = ("tags", "status", "kakera_id", "body")
READONLY_FIELDS = ("id", "created", "updated", "path")


class ReferencedError(Exception):
    """生成来歴から参照されているかけらを force なしで削除しようとした。records に参照一覧。"""

    def __init__(self, kid: str, records: List[dict]):
        self.kid = kid
        self.records = records
        targets = "、".join(f"{r.get('article', '')}/{r.get('target', '')}" for r in records)
        super().__init__(f"{kid} は生成来歴 {len(records)} 件から参照されています（{targets}）。"
                         "削除するなら force=True を指定してください。")


# ---- 設定 ----

def load_config() -> dict:
    """リポジトリの config/kakera.json を読み、作業フォルダの config/kakera.json があれば項目ごとに上書き。"""
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(store.read_json(store.CONFIG_DIR / "kakera.json", {}) or {})
    try:
        local = store.read_json(store.vault() / "config" / "kakera.json", {}) or {}
    except store.VaultError:
        local = {}
    cfg.update(local)
    return cfg


# ---- 内部の道具 ----

_rlock = threading.RLock()
_depth = 0
_lock_fd: Optional[int] = None


@contextmanager
def _locked():
    """作業フォルダ単位の排他（同一プロセス内では入れ子可）。"""
    global _depth, _lock_fd
    with _rlock:
        if _depth == 0:
            d = store.vault()
            fd = os.open(str(d / ".nw-lock"), os.O_RDWR | os.O_CREAT, 0o600)
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            _lock_fd = fd
        _depth += 1
        try:
            yield
        finally:
            _depth -= 1
            if _depth == 0 and _lock_fd is not None:
                if fcntl is not None:
                    fcntl.flock(_lock_fd, fcntl.LOCK_UN)
                os.close(_lock_fd)
                _lock_fd = None


def _text(v) -> str:
    """front matter の1行値として保存・再読込しても同じになる形に揃える。"""
    return re.sub(r"[\r\n]+", " ", "" if v is None else str(v)).strip()


def _as_list(v) -> List[str]:
    """文字列（カンマ・読点区切り）でもリストでも受け、重複を除いたリストにする。"""
    if v is None:
        return []
    items = [v] if isinstance(v, str) else list(v)
    out: List[str] = []
    for x in items:
        for y in store.split_list(_text(x)):
            if y not in out:
                out.append(y)
    return out


def _check_id(xid: str, prefix: str) -> str:
    xid = str(xid or "").strip()
    if not re.fullmatch(rf"{prefix}\d+", xid):
        raise KeyError(xid)
    return xid


def _num(xid: str) -> int:
    return int(re.sub(r"\D", "", xid) or 0)


def _alloc_id(prefix: str, existing_max: int) -> str:
    """counters.json と既存の最大番号の大きい方 +1 を採番する（_locked の中で呼ぶ）。"""
    path = store.vault() / "counters.json"
    counters = store.read_json(path, {}) or {}
    n = max(int(counters.get(prefix, 0)), existing_max) + 1
    counters[prefix] = n
    store.write_json(path, counters)
    return f"{prefix}{n:04d}"


def _folder_max(folder: Path, prefix: str) -> int:
    return _num(store.next_id(folder, prefix)) - 1


def _read_item(path: Path, list_keys) -> dict:
    meta, body = store.parse_md(path.read_text(encoding="utf-8"), list_keys)
    item = {k: v for k, v in meta.items()}
    item["id"] = str(item.get("id") or path.stem)
    item["body"] = body.rstrip("\n")
    item["path"] = str(path)
    return item


def _list_items(folder: Path, prefix: str, list_keys) -> List[dict]:
    if not folder.is_dir():
        return []
    paths = [p for p in folder.glob(f"{prefix}*.md") if re.fullmatch(rf"{prefix}\d+", p.stem)]
    return [_read_item(p, list_keys) for p in sorted(paths, key=lambda p: _num(p.stem))]


# ---- かけら ----

def _kakera_dir() -> Path:
    return store.vault() / "kakera"


def _kakera_item(item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "article": item.get("article", ""),
        "section": item.get("section", ""),
        "viewpoints": item.get("viewpoints", []),
        "status": item.get("status", ""),
        "tags": item.get("tags", []),
        "source": item.get("source", ""),
        "created": item.get("created", ""),
        "updated": item.get("updated", ""),
        "body": item.get("body", ""),
        "path": item.get("path", ""),
    }


def _write_kakera(k: dict) -> dict:
    path = _kakera_dir() / f"{k['id']}.md"
    meta = {key: k[key] for key in ("id", "article", "section", "viewpoints", "status",
                                     "tags", "source", "created", "updated")}
    store.atomic_write(path, store.dump_md(meta, k["body"]))
    k["path"] = str(path)
    return k


def _check_status(status: str) -> str:
    statuses = load_config()["statuses"]
    if status not in statuses:
        raise ValueError(f"使用状況「{status}」は設定にありません（{'／'.join(statuses)}）。")
    return status


def list_kakera() -> List[dict]:
    """すべてのかけら（ID昇順）。"""
    return [_kakera_item(x) for x in _list_items(_kakera_dir(), "K", KAKERA_LIST_KEYS)]


def get_kakera(kid: str) -> dict:
    kid = _check_id(kid, "K")
    path = _kakera_dir() / f"{kid}.md"
    if not path.is_file():
        raise KeyError(kid)
    return _kakera_item(_read_item(path, KAKERA_LIST_KEYS))


def create_kakera(body: str, article: str = "", section: str = "", viewpoints: Iterable[str] = (),
                  status: str = "未使用", tags: Iterable[str] = (), source: str = "") -> dict:
    status = _check_status(_text(status) or "未使用")
    with _locked():
        kid = _alloc_id("K", _folder_max(_kakera_dir(), "K"))
        ts = store.now()
        k = {"id": kid, "article": _text(article), "section": _text(section),
             "viewpoints": _as_list(viewpoints), "status": status, "tags": _as_list(tags),
             "source": _text(source), "created": ts, "updated": ts,
             "body": (body or "").rstrip("\n")}
        return _write_kakera(k)


def update_kakera(kid: str, **fields) -> dict:
    """body を含む任意の項目を更新する。id・created 等は無視、未知の項目は ValueError。"""
    unknown = [f for f in fields if f not in KAKERA_FIELDS and f not in READONLY_FIELDS]
    if unknown:
        raise ValueError(f"かけらに更新できない項目があります: {', '.join(unknown)}")
    with _locked():
        k = get_kakera(kid)
        for f, v in fields.items():
            if f in READONLY_FIELDS:
                continue
            if f in KAKERA_LIST_KEYS:
                k[f] = _as_list(v)
            elif f == "body":
                k[f] = (v or "").rstrip("\n")
            elif f == "status":
                k[f] = _check_status(_text(v))
            else:
                k[f] = _text(v)
        k["updated"] = store.now()
        return _write_kakera(k)


def search_kakera(q: str = "", article: str = "", section: str = "", viewpoint: str = "",
                  status: str = "", tag: str = "") -> List[dict]:
    """全条件AND。q は本文・タグ・区間・記事名の部分一致（空白区切りの語はAND、大文字小文字無視）。"""
    words = [w.casefold() for w in (q or "").split()]
    article, section, viewpoint, status, tag = (_text(x) for x in (article, section, viewpoint, status, tag))
    out = []
    for k in list_kakera():
        if article and k["article"] != article:
            continue
        if section and k["section"] != section:
            continue
        if viewpoint and viewpoint not in k["viewpoints"]:
            continue
        if status and k["status"] != status:
            continue
        if tag and tag not in k["tags"]:
            continue
        if words:
            hay = "\n".join([k["body"], " ".join(k["tags"]), k["section"], k["article"]]).casefold()
            if not all(w in hay for w in words):
                continue
        out.append(k)
    return out


def delete_warnings(kid: str) -> List[dict]:
    """このかけらを根拠にした生成来歴（段落・記事）の一覧。空なら参照なし。"""
    kid = str(kid or "").strip()
    return [r for r in list_provenance() if kid in r.get("kakera_ids", [])]


def delete_kakera(kid: str, force: bool = False) -> None:
    """来歴に参照があり force=False なら ReferencedError。来歴レコード自体は消さずに残す。"""
    with _locked():
        k = get_kakera(kid)
        refs = delete_warnings(k["id"])
        if refs and not force:
            raise ReferencedError(k["id"], refs)
        Path(k["path"]).unlink()


# ---- ネタ帳 ----

def _neta_dir() -> Path:
    return store.vault() / "neta"


def _neta_item(item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "tags": item.get("tags", []),
        "status": item.get("status", "") or NETA_STATUSES[0],
        "kakera_id": item.get("kakera_id", ""),
        "created": item.get("created", ""),
        "updated": item.get("updated", ""),
        "body": item.get("body", ""),
        "path": item.get("path", ""),
    }


def _write_neta(n: dict) -> dict:
    path = _neta_dir() / f"{n['id']}.md"
    meta = {key: n[key] for key in ("id", "tags", "status", "kakera_id", "created", "updated")}
    store.atomic_write(path, store.dump_md(meta, n["body"]))
    n["path"] = str(path)
    return n


def list_neta() -> List[dict]:
    return [_neta_item(x) for x in _list_items(_neta_dir(), "N", NETA_LIST_KEYS)]


def get_neta(nid: str) -> dict:
    nid = _check_id(nid, "N")
    path = _neta_dir() / f"{nid}.md"
    if not path.is_file():
        raise KeyError(nid)
    return _neta_item(_read_item(path, NETA_LIST_KEYS))


def create_neta(body: str, tags: Iterable[str] = ()) -> dict:
    with _locked():
        nid = _alloc_id("N", _folder_max(_neta_dir(), "N"))
        ts = store.now()
        n = {"id": nid, "tags": _as_list(tags), "status": NETA_STATUSES[0], "kakera_id": "",
             "created": ts, "updated": ts, "body": (body or "").rstrip("\n")}
        return _write_neta(n)


def update_neta(nid: str, **fields) -> dict:
    unknown = [f for f in fields if f not in NETA_FIELDS and f not in READONLY_FIELDS]
    if unknown:
        raise ValueError(f"ネタに更新できない項目があります: {', '.join(unknown)}")
    with _locked():
        n = get_neta(nid)
        for f, v in fields.items():
            if f in READONLY_FIELDS:
                continue
            if f == "tags":
                n[f] = _as_list(v)
            elif f == "body":
                n[f] = (v or "").rstrip("\n")
            elif f == "status":
                if _text(v) not in NETA_STATUSES:
                    raise ValueError(f"ネタの状態「{v}」は使えません（{'／'.join(NETA_STATUSES)}）。")
                n[f] = _text(v)
            else:
                n[f] = _text(v)
        n["updated"] = store.now()
        return _write_neta(n)


def delete_neta(nid: str) -> None:
    with _locked():
        Path(get_neta(nid)["path"]).unlink()


def promote_neta(nid: str, article: str = "", section: str = "", viewpoints: Iterable[str] = (),
                 tags: Iterable[str] = ()) -> dict:
    """ネタからかけらを作る（source=ネタID、タグはネタのタグ＋指定分）。ネタは「かけら化済み」にする。"""
    with _locked():
        n = get_neta(nid)
        if n["status"] == "かけら化済み":
            raise ValueError(f"{n['id']} はかけら化済みです（{n['kakera_id']}）。")
        k = create_kakera(n["body"], article=article, section=section, viewpoints=viewpoints,
                          tags=_as_list(list(n["tags"]) + _as_list(tags)), source=n["id"])
        update_neta(n["id"], status="かけら化済み", kakera_id=k["id"])
        return k


# ---- 記事と区間 ----

def _articles_path() -> Path:
    return store.vault() / "articles.json"


def _load_articles() -> List[dict]:
    data = store.read_json(_articles_path(), {}) or {}
    return list(data.get("articles", []))


def list_articles() -> List[dict]:
    """記事一覧（series, order 順）。"""
    arts = [{"name": a.get("name", ""), "series": a.get("series", ""), "order": int(a.get("order", 0) or 0),
             "sections": list(a.get("sections", []))} for a in _load_articles()]
    return sorted(arts, key=lambda a: (a["series"], a["order"], a["name"]))


def save_article(name: str, series: str = "", order: int = 0, sections: Iterable[str] = ()) -> dict:
    """記事を保存する（同名は上書き）。sections はリスト推奨（文字列ならカンマ・読点・改行区切り）。"""
    name = _text(name)
    if not name:
        raise ValueError("記事名が空です。")
    if isinstance(sections, str):
        secs = [s for line in sections.splitlines() for s in store.split_list(line)]
    else:
        secs = [_text(s) for s in (sections or ())]
    uniq: List[str] = []
    for s in secs:
        if s and s not in uniq:
            uniq.append(s)
    art = {"name": name, "series": _text(series), "order": int(order or 0), "sections": uniq}
    with _locked():
        arts = [a for a in _load_articles() if a.get("name") != name]
        arts.append(art)
        store.write_json(_articles_path(), {"articles": arts})
    return art


def delete_article(name: str) -> None:
    """記事を一覧から外す（なければ KeyError）。その記事のかけらは消さない。"""
    name = _text(name)
    with _locked():
        arts = _load_articles()
        rest = [a for a in arts if a.get("name") != name]
        if len(rest) == len(arts):
            raise KeyError(name)
        store.write_json(_articles_path(), {"articles": rest})


def coverage(article: str) -> dict:
    """区間ごとの充足度（coverage_viewpoints の観点ごとのかけら件数）。"""
    name = _text(article)
    vps = list(load_config()["coverage_viewpoints"])
    order: List[str] = []
    for a in _load_articles():
        if a.get("name") == name:
            order = [s for s in a.get("sections", []) if s]
            break
    ks = [k for k in list_kakera() if k["article"] == name]
    for k in ks:
        if k["section"] and k["section"] not in order:
            order.append(k["section"])
    sections = []
    for sec in order:
        in_sec = [k for k in ks if k["section"] == sec]
        counts = {v: sum(1 for k in in_sec if v in k["viewpoints"]) for v in vps}
        sections.append({"section": sec, "counts": counts, "total": len(in_sec),
                         "missing": [v for v in vps if counts[v] == 0],
                         "filled": sum(1 for v in vps if counts[v] > 0)})
    return {"article": name, "viewpoints": vps, "sections": sections,
            "unsectioned": sum(1 for k in ks if not k["section"])}


# ---- 生成来歴 ----

def _provenance_path() -> Path:
    return store.vault() / "provenance.json"


def _load_provenance() -> List[dict]:
    data = store.read_json(_provenance_path(), {}) or {}
    return list(data.get("records", []))


def list_provenance() -> List[dict]:
    """生成来歴（古い順）。missing_kakera_ids は削除済みのかけら（保存はせず読み出し時に計算）。"""
    kdir = _kakera_dir()
    out = []
    for r in _load_provenance():
        r = dict(r)
        r["missing_kakera_ids"] = [k for k in r.get("kakera_ids", []) if not (kdir / f"{k}.md").is_file()]
        out.append(r)
    return out


def record_provenance(article: str, target: str, kakera_ids: Iterable[str]) -> dict:
    """本文確定時の軽量な記録。存在しないかけらIDがあれば ValueError（何も記録しない）。"""
    ids = _as_list(kakera_ids)
    with _locked():
        missing = []
        for kid in ids:
            try:
                get_kakera(kid)
            except KeyError:
                missing.append(kid)
        if missing:
            raise ValueError(f"存在しないかけらIDがあります: {', '.join(missing)}")
        records = _load_provenance()
        gid = _alloc_id("G", max([_num(str(r.get("id", ""))) for r in records] + [0]))
        rec = {"id": gid, "article": _text(article), "target": _text(target),
               "kakera_ids": ids, "generated_at": store.now()}
        records.append(rec)
        store.write_json(_provenance_path(), {"records": records})
        return rec


# ---- ダッシュボード ----

def stats() -> dict:
    """かけら総数・状態別件数・記事別件数（記事一覧の順、未登録の記事名は後ろ）・ネタ未整理件数。"""
    ks = list_kakera()
    by_status: Dict[str, int] = {s: 0 for s in load_config()["statuses"]}
    for k in ks:
        by_status[k["status"]] = by_status.get(k["status"], 0) + 1
    by_article: Dict[str, int] = {a["name"]: 0 for a in list_articles()}
    for k in ks:
        key = k["article"] or NO_ARTICLE
        by_article[key] = by_article.get(key, 0) + 1
    neta = list_neta()
    return {"kakera_total": len(ks), "by_status": by_status, "by_article": by_article,
            "neta_total": len(neta),
            "neta_unsorted": sum(1 for n in neta if n["status"] == NETA_STATUSES[0])}
