"""反応記録（SPEC.md 機能10の最小版）: 公開した記事のスキ数・コメント数・購入数を手入力で記録する。

保存先: 作業フォルダ store.vault() の reactions.json（{"records": [...]}）。
1件 = ある日に数えた、ある記事の反応。同じ記事を何度も記録できる（記事ごとに一覧にする）。
note の CSV 取り込みは今回は作らない。

ネタ出し（ideas.py）には top_reactions() の要約（タイトルと数値）だけを渡す。メモは渡さない。
「反応がよかった順」は、記事ごとの最新の記録を 購入数 → スキ数 → コメント数 の順で比べて決める。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

from . import store
from .kakera import _alloc_id, _locked, _num

FIELDS = ("title", "published", "recorded", "likes", "comments", "purchases", "memo")
COUNT_FIELDS = (("likes", "スキ数"), ("comments", "コメント数"), ("purchases", "購入数"))
MAX_TITLE = 200
MAX_MEMO = 2000


def _path() -> Path:
    return store.vault() / "reactions.json"


def _load() -> List[dict]:
    data = store.read_json(_path(), {}) or {}
    recs = data.get("records", []) if isinstance(data, dict) else []
    return [r for r in recs if isinstance(r, dict)]


def _save(records: List[dict]) -> None:
    store.write_json(_path(), {"records": records})


def _one_line(v, limit: int) -> str:
    return re.sub(r"\s+", " ", "" if v is None else str(v)).strip()[:limit]


def _date(v, label: str, default_today: bool = False) -> str:
    s = "" if v is None else str(v).strip()
    if not s:
        return date.today().isoformat() if default_today else ""
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValueError(f"{label}は YYYY-MM-DD の形で入れてください（{s}）。") from None


def _count(v, label: str) -> int:
    s = "" if v is None else str(v).strip()
    if s == "":
        return 0
    try:
        n = int(s)
    except ValueError:
        raise ValueError(f"{label}は 0 以上の数字で入れてください（{s}）。") from None
    if n < 0:
        raise ValueError(f"{label}は 0 以上の数字で入れてください（{s}）。")
    return n


def _clean(fields: dict, base: Optional[dict] = None) -> dict:
    rec = dict(base or {})
    for f in FIELDS:
        if f not in fields:
            continue
        v = fields[f]
        if f == "title":
            rec[f] = _one_line(v, MAX_TITLE)
        elif f == "published":
            rec[f] = _date(v, "公開日")
        elif f == "recorded":
            rec[f] = _date(v, "記録日", default_today=True)
        elif f == "memo":
            rec[f] = ("" if v is None else str(v)).replace("\r\n", "\n").strip()[:MAX_MEMO]
        else:
            rec[f] = _count(v, dict(COUNT_FIELDS)[f])
    if not rec.get("title"):
        raise ValueError("記事名（公開タイトル）を入れてください。")
    rec.setdefault("published", "")
    rec.setdefault("recorded", date.today().isoformat())
    for f, _ in COUNT_FIELDS:
        rec.setdefault(f, 0)
    rec.setdefault("memo", "")
    return rec


def _check_id(rid) -> str:
    rid = str(rid or "").strip()
    if not re.fullmatch(r"R\d+", rid):
        raise KeyError(rid)
    return rid


def list_reactions() -> List[dict]:
    """すべての記録（記録日の新しい順）。"""
    return sorted(_load(), key=lambda r: (str(r.get("recorded", "")), _num(str(r.get("id", "")))), reverse=True)


def get_reaction(rid) -> dict:
    rid = _check_id(rid)
    for r in _load():
        if r.get("id") == rid:
            return r
    raise KeyError(rid)


def add_reaction(title, published="", recorded="", likes=0, comments=0, purchases=0, memo="") -> dict:
    rec = _clean({"title": title, "published": published, "recorded": recorded, "likes": likes,
                  "comments": comments, "purchases": purchases, "memo": memo})
    with _locked():
        records = _load()
        rid = _alloc_id("R", max([_num(str(r.get("id", ""))) for r in records] + [0]))
        ts = store.now()
        full = {"id": rid}
        full.update(rec)
        full.update({"created": ts, "updated": ts})
        records.append(full)
        _save(records)
    return full


def update_reaction(rid, **fields) -> dict:
    unknown = [f for f in fields if f not in FIELDS]
    if unknown:
        raise ValueError(f"反応記録に更新できない項目があります: {', '.join(unknown)}")
    rid = _check_id(rid)
    with _locked():
        records = _load()
        for i, r in enumerate(records):
            if r.get("id") == rid:
                new = _clean(fields, r)
                new["updated"] = store.now()
                records[i] = new
                _save(records)
                return new
    raise KeyError(rid)


def delete_reaction(rid) -> None:
    rid = _check_id(rid)
    with _locked():
        records = _load()
        rest = [r for r in records if r.get("id") != rid]
        if len(rest) == len(records):
            raise KeyError(rid)
        _save(rest)


def _score(r: dict):
    return (int(r.get("purchases") or 0), int(r.get("likes") or 0), int(r.get("comments") or 0))


def by_article() -> List[dict]:
    """記事（公開タイトル）ごとにまとめる。latest は記録日が一番新しい記録。反応がよかった順。"""
    groups: Dict[str, List[dict]] = {}
    for r in list_reactions():
        groups.setdefault(str(r.get("title", "")), []).append(r)
    out = [{"title": t, "records": recs, "latest": recs[0], "published": next((x.get("published") for x in recs
                                                                              if x.get("published")), "")}
           for t, recs in groups.items()]
    out.sort(key=lambda g: _score(g["latest"]), reverse=True)
    return out


def top_reactions(n: int = 10) -> List[dict]:
    """反応がよかった順の要約（記事ごとに最新の記録）。タイトルと数値だけ（メモは含めない）。"""
    out = []
    for g in by_article()[:max(0, int(n))]:
        r = g["latest"]
        out.append({"title": g["title"], "published": g["published"], "recorded": r.get("recorded", ""),
                    "likes": int(r.get("likes") or 0), "comments": int(r.get("comments") or 0),
                    "purchases": int(r.get("purchases") or 0)})
    return out
