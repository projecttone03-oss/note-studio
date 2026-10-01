"""SNS 導線（SPEC.md 機能13）: X・Threads 向けの投稿文、Web Intent、型タグ、直接宣伝の比率、月1の健康診断。

- 投稿の実行は、公式の Web Intent（本文入力済みの投稿画面）を開くだけ。最後の送信ボタンは人が押す。投稿 API は使わない。
- Web Intent の URL はハードコードせず config/sns.json から読む。
- 投稿文の下書きは、ツールなしの Claude（jobs.py）に、記事のタイトルと無料エリアだけを渡して作らせる（有料部分・かけらは渡さない）。
- 「投稿した」は人が押して記録する。直近30日の直接宣伝の割合が1割を超えたら警告する。

保存先: 作業フォルダの sns/posts.json・sns/health.json
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional
from urllib.parse import quote

from . import jobs
from . import kakera as K
from . import publish as P
from . import store
from . import writing as W

STATUSES = ("下書き", "投稿済み")
JOB = jobs.JobKind("sns", "XJ", "実行中の投稿文づくりがあります。終わってから実行してください。")
DEFAULTS = {"platforms": {}, "intents_verified": False, "types": ["好きになってもらう", "知ってもらう", "リサーチ", "直接宣伝"],
            "promo_type": "直接宣伝", "promo_ratio_max": 0.1, "window_days": 30, "health_fields": [], "type_hints": {}}


def config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(store.read_json(store.CONFIG_DIR / "sns.json", {}) or {})
    try:
        cfg.update(store.read_json(store.vault() / "config" / "sns.json", {}) or {})
    except store.VaultError:
        pass
    return cfg


def _posts_path():
    return store.vault() / "sns" / "posts.json"


def _load() -> List[dict]:
    return list((store.read_json(_posts_path(), {}) or {}).get("items", []))


def _save(items: List[dict]) -> None:
    store.write_json(_posts_path(), {"items": items})


def _check(platform: str, ptype: str) -> None:
    cfg = config()
    if platform not in cfg["platforms"]:
        raise ValueError(f"SNS「{platform}」は設定にありません（{'／'.join(cfg['platforms'])}）。")
    if ptype not in cfg["types"]:
        raise ValueError(f"型「{ptype}」は設定にありません（{'／'.join(cfg['types'])}）。")


# ---------------------------------------------------------------- 文字数・Intent

def x_weight(text: str) -> int:
    """X の文字数の目安（半角英数・記号は1、それ以外は2、URL は23）。"""
    t = unicodedata.normalize("NFC", text or "")
    n = 0
    for m in re.finditer(r"https?://\S+|.", t, re.S):
        s = m.group(0)
        if len(s) > 1:
            n += 23
        elif ord(s) <= 0x10FF or 0x2000 <= ord(s) <= 0x200D or 0x2010 <= ord(s) <= 0x201F or 0x2032 <= ord(s) <= 0x2037:
            n += 1
        else:
            n += 2
    return n


def length_flags(platform: str, text: str) -> List[str]:
    p = config()["platforms"].get(platform) or {}
    out = []
    if p.get("max_weight") and x_weight(text) > int(p["max_weight"]):
        out.append(f"長すぎます（{x_weight(text)} / {p['max_weight']}。日本語は1文字2と数えます）")
    if p.get("max_chars") and len(text) > int(p["max_chars"]):
        out.append(f"長すぎます（{len(text)} / {p['max_chars']}字）")
    return out


def intent_url(platform: str, text: str) -> str:
    tpl = str((config()["platforms"].get(platform) or {}).get("intent_url") or "")
    if not tpl.startswith("https://") or "{text}" not in tpl:
        raise ValueError(f"{platform} の intent_url の設定がおかしいです（https:// で始まり {{text}} を含む形にしてください）。")
    return tpl.replace("{text}", quote(text, safe=""))


# ---------------------------------------------------------------- 投稿

def list_posts(status: str = "") -> List[dict]:
    items = sorted(_load(), key=lambda x: K._num(x["id"]), reverse=True)
    return [x for x in items if not status or x.get("status") == status]


def get_post(pid: str) -> dict:
    for x in _load():
        if x["id"] == pid:
            return x
    raise KeyError(pid)


def add_post(platform: str, ptype: str, text: str, article: str = "", flags=(), job: str = "") -> dict:
    _check(platform, ptype)
    text = (text or "").strip()
    if not text:
        raise ValueError("投稿文が空です。")
    with K._locked():
        items = _load()
        x = {"id": K._alloc_id("S", max([K._num(i["id"]) for i in items] + [0])), "platform": platform, "type": ptype,
             "text": text, "article": K._text(article), "status": "下書き", "created": store.now(), "posted_at": "",
             "flags": list(flags), "job": job}
        items.append(x)
        _save(items)
    return x


def update_post(pid: str, text: Optional[str] = None, ptype: Optional[str] = None) -> dict:
    with K._locked():
        items = _load()
        for x in items:
            if x["id"] == pid:
                if ptype is not None:
                    _check(x["platform"], ptype)
                    x["type"] = ptype
                if text is not None:
                    if not text.strip():
                        raise ValueError("投稿文が空です。")
                    x["text"] = text.strip()
                    x["flags"] = [f for f in x.get("flags", []) if not f.startswith("長すぎ")]
                _save(items)
                return x
    raise KeyError(pid)


def mark_posted(pid: str, when: str = "") -> dict:
    """人が送信したあとに押す（記録だけ。アプリは送信しない）。"""
    when = (when or date.today().isoformat())[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
        raise ValueError("投稿日は YYYY-MM-DD の形で入れてください。")
    with K._locked():
        items = _load()
        for x in items:
            if x["id"] == pid:
                x.update({"status": "投稿済み", "posted_at": when})
                _save(items)
                return x
    raise KeyError(pid)


def delete_post(pid: str) -> None:
    with K._locked():
        items = _load()
        rest = [x for x in items if x["id"] != pid]
        if len(rest) == len(items):
            raise KeyError(pid)
        _save(rest)


def promo_ratio(today: Optional[date] = None) -> dict:
    cfg = config()
    today = today or date.today()
    since = today - timedelta(days=int(cfg["window_days"]))
    posted = []
    for x in _load():
        try:
            d = datetime.strptime(x.get("posted_at") or "", "%Y-%m-%d").date()
        except ValueError:
            continue
        if since < d <= today:
            posted.append(x)
    promo = sum(1 for x in posted if x["type"] == cfg["promo_type"])
    ratio = promo / len(posted) if posted else 0.0
    by_type: Dict[str, int] = {t: 0 for t in cfg["types"]}
    for x in posted:
        by_type[x["type"]] = by_type.get(x["type"], 0) + 1
    return {"total": len(posted), "promo": promo, "ratio": ratio, "max": float(cfg["promo_ratio_max"]),
            "over": ratio > float(cfg["promo_ratio_max"]), "by_type": by_type, "days": int(cfg["window_days"])}


# ---------------------------------------------------------------- 健康診断（月1の手入力）

def _health_path():
    return store.vault() / "sns" / "health.json"


def list_health() -> List[dict]:
    items = list((store.read_json(_health_path(), {}) or {}).get("items", []))
    return sorted(items, key=lambda x: (x["month"], x["platform"]), reverse=True)


def save_health(month: str, platform: str, values: Dict[str, object]) -> dict:
    cfg = config()
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        raise ValueError("月は YYYY-MM の形で入れてください。")
    if platform not in cfg["platforms"]:
        raise ValueError(f"SNS「{platform}」は設定にありません。")
    rec = {"month": month, "platform": platform, "recorded": store.now()}
    for key, label in cfg["health_fields"]:
        raw = str(values.get(key) if values.get(key) is not None else "").strip().replace(",", "")
        if raw == "":
            rec[key] = None
            continue
        if not raw.isdigit():
            raise ValueError(f"{label} は 0 以上の数字で入れてください。")
        rec[key] = int(raw)
    with K._locked():
        items = [x for x in list_health() if not (x["month"] == month and x["platform"] == platform)]
        items.append(rec)
        store.write_json(_health_path(), {"items": items})
    return rec


def health_due(today: Optional[date] = None) -> List[str]:
    """今月まだ記録していない SNS（表示名）。"""
    month = (today or date.today()).strftime("%Y-%m")
    done = {x["platform"] for x in list_health() if x["month"] == month}
    return [p.get("label", k) for k, p in config()["platforms"].items() if k not in done]


# ---------------------------------------------------------------- 投稿文の下書き（ツールなしの Claude）

def material(article: str) -> dict:
    name = W._article(article)["name"]
    p = W.draft_path(name)
    if not p.is_file():
        raise ValueError(f"「{name}」の下書きがありません。")
    text = p.read_text(encoding="utf-8")
    sp = P.split(text)
    from . import published
    title = published.title_of(text, name)
    free = sp["free"]
    free = "\n".join(l for l in free.splitlines() if not l.startswith("# ")).strip()
    if "[要追加" in free:
        free = re.sub(r"\[要追加[：:][^\]]*\]", "", free)
    return {"title": title, "free": free[:4000]}


def prepare(article: str, ptype: str) -> dict:
    cfg = config()
    if ptype not in cfg["types"]:
        raise ValueError(f"型「{ptype}」は設定にありません。")
    m = material(article)
    if not m["free"].strip():
        raise ValueError("無料エリアに本文がありません（投稿文の材料は無料エリアだけです）。")
    prompt = jobs.fill(jobs.template("sns.md"), {
        "type": ptype, "hint": str((cfg.get("type_hints") or {}).get(ptype) or ""), "title": m["title"], "free": m["free"],
        "url_note": "記事URLは画面で人が足す"})
    name = W._article(article)["name"]
    return {"prompt": prompt, "chars": len(prompt), "confirm_token": jobs.token("sns", name, ptype, prompt),
            "job": {"article": name, "type": ptype}}


def _handle(job: dict, text: str) -> None:
    data = jobs.json_obj(text)
    m = material(job["article"])
    added = []
    for platform in config()["platforms"]:
        body = str(data.get(platform) or "").strip()
        if not body:
            continue
        flags = length_flags(platform, body)
        terms = W.unsupported_terms(body, [m["title"], m["free"]])
        if terms:
            flags.append("材料（タイトル・無料エリア）に見当たらない語（要確認）: " + "、".join(terms))
        added.append(add_post(platform, job["type"], body, job["article"], flags, job["id"])["id"])
    if not added:
        raise ValueError("Claude の返答に投稿文がありませんでした。")
    job["added"] = added


def start(article: str, ptype: str, confirm_token: str) -> str:
    return JOB.start(prepare(article, ptype), confirm_token, _handle)


def run(article: str, ptype: str, confirm_token: str) -> dict:
    return JOB.run(prepare(article, ptype), confirm_token, _handle)
