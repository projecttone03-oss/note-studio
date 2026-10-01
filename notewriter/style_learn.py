"""文体ルールの候補（SPEC.md 機能6）。

- 材料は style/edits.jsonl（AI の段落と人の手直しの組）。ツールなしの Claude に渡し、ルールの「候補」を出させる。
- 候補は style/candidates.json に「未検討」で置くだけ。人が採用したものだけ style/rules.md に追記する（自動反映しない）。
- 候補の文に、材料の文章がそのまま長く入っていたら（内容の混入や書籍の書き写しの疑い）印を付け、
  直さないと採用できないようにする（判断は人。書き換えはしない）。
- 参考書籍（books.py）からの候補も同じ置き場・同じ採用の流れを使う（source が book:B0001）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import jobs, store
from . import writing as W
from .kakera import _alloc_id, _locked, _num

STATUSES = ("未検討", "採用", "却下")
DEFAULTS = {"edits_max": 40, "edits_max_chars": 20000, "max_candidates": 8, "copy_max_chars": 20}
JOB = jobs.JobKind("style", "SJ", "実行中の文体ルール候補づくりがあります。終わってから実行してください。")


def config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update((W.load_config().get("style_candidates") or {}))
    return cfg


def _path():
    return store.vault() / "style" / "candidates.json"


def _load() -> List[dict]:
    return list((store.read_json(_path(), {}) or {}).get("items", []))


def _save(items: List[dict]) -> None:
    store.write_json(_path(), {"items": items})


def list_candidates(status: str = "") -> List[dict]:
    items = _load()
    if status:
        items = [c for c in items if c.get("status") == status]
    return sorted(items, key=lambda c: _num(c["id"]), reverse=True)


def get_candidate(cid: str) -> dict:
    for c in _load():
        if c.get("id") == cid:
            return c
    raise KeyError(cid)


def add_candidates(rows: List[dict], source: str, job_id: str, source_text: str) -> List[dict]:
    """候補を「未検討」で追加する。すでにルール集にある文と同じものは入れない。"""
    limit = int(config()["copy_max_chars"])
    rules = jobs._squash(W.get_style())
    out = []
    with _locked():
        items = _load()
        known = {jobs._squash(c.get("rule", "")) for c in items} | {""}
        for r in rows:
            rule = " ".join(str(r.get("rule") or "").split())[:300]
            key = jobs._squash(rule)
            if key in known or key in rules:
                continue
            known.add(key)
            flags = []
            hit = jobs.longest_common(rule, source_text)
            if len(hit) >= limit:
                flags.append(f"材料の文がそのまま {len(hit)} 字続いています（「{hit[:40]}」）。"
                             "内容や書籍の文を写していないか確かめ、自分の言葉に直してから採用してください。")
            cid = _alloc_id("SC", max([_num(c["id"]) for c in items] + [0]))
            c = {"id": cid, "rule": rule, "reason": " ".join(str(r.get("reason") or "").split())[:400],
                 "refs": [str(x) for x in (r.get("pairs") or r.get("refs") or [])][:10],
                 "source": source, "status": "未検討", "flags": flags, "created": store.now(),
                 "decided_at": "", "job": job_id}
            items.append(c)
            out.append(c)
        _save(items)
    return out


def source_text_of(c: dict) -> str:
    src = c.get("source", "")
    if src.startswith("book:"):
        from . import books
        try:
            return books.full_text(src[5:])
        except KeyError:
            return ""
    return "\n".join(x.get("ai", "") + "\n" + x.get("human", "") for x in W.list_edits())


def adopt(cid: str, text: Optional[str] = None) -> dict:
    """人が採用した候補だけを rules.md に1行で追記する。text で直してから採用もできる。"""
    with _locked():
        items = _load()
        c = next((x for x in items if x["id"] == cid), None)
        if c is None:
            raise KeyError(cid)
        if c["status"] != "未検討":
            raise ValueError(f"{cid} はもう「{c['status']}」です。")
        rule = " ".join((text if text is not None else c["rule"]).split())
        if not rule:
            raise ValueError("ルールの文が空です。")
        hit = jobs.longest_common(rule, source_text_of(c))
        if len(hit) >= int(config()["copy_max_chars"]):
            raise ValueError(f"材料の文がそのまま {len(hit)} 字続いています（「{hit[:40]}」）。自分の言葉に直してから採用してください。")
        body = W.get_style().rstrip("\n")
        W.save_style(body + "\n- " + rule + "\n")
        c.update({"status": "採用", "rule": rule, "decided_at": store.now(), "flags": []})
        _save(items)
        return c


def reject(cid: str) -> dict:
    with _locked():
        items = _load()
        c = next((x for x in items if x["id"] == cid), None)
        if c is None:
            raise KeyError(cid)
        if c["status"] != "未検討":
            raise ValueError(f"{cid} はもう「{c['status']}」です。")
        c.update({"status": "却下", "decided_at": store.now()})
        _save(items)
        return c


# ---------------------------------------------------------------- 手直しの組から候補を出す

def _pairs_text(edits: List[dict]) -> str:
    parts = []
    for i, x in enumerate(edits, 1):
        parts.append(f"[{i}] AI: {x.get('ai', '').strip()}\n    人: {x.get('human', '').strip()}")
    return "\n\n".join(parts)


def prepare_from_edits() -> dict:
    cfg = config()
    edits = W.list_edits()[-int(cfg["edits_max"]):]
    if not edits:
        raise ValueError("まだ手直しの記録がありません（AI が書いた段落を人が直すと記録されます）。")
    while edits and len(_pairs_text(edits)) > int(cfg["edits_max_chars"]):
        edits = edits[1:]  # 古いものから外す
    if not edits:
        raise ValueError("手直しの記録が長すぎます（config/writing.json の style_candidates.edits_max_chars を見直してください）。")
    pairs = _pairs_text(edits)
    prompt = jobs.fill(jobs.template("style_candidates.md"), {
        "max": str(cfg["max_candidates"]), "style": W._style_for_prompt(W.load_config()), "pairs": pairs})
    return {"kind": "edits", "prompt": prompt, "chars": len(prompt), "count": len(edits),
            "confirm_token": jobs.token("style-edits", prompt), "job": {"source": "edits", "count": len(edits)}}


def parse_candidates(text: str) -> List[dict]:
    data = jobs.json_obj(text)
    rows = data.get("candidates")
    if not isinstance(rows, list):
        raise ValueError("Claude の返答に候補（candidates）がありませんでした。")
    return [r for r in rows if isinstance(r, dict) and str(r.get("rule") or "").strip()]


def _handle_edits(job: dict, text: str) -> None:
    rows = parse_candidates(text)
    edits = W.list_edits()
    src = "\n".join(x.get("ai", "") + "\n" + x.get("human", "") for x in edits)
    added = add_candidates(rows, "edits", job["id"], src)
    job["added"] = [c["id"] for c in added]


def start_from_edits(confirm_token: str) -> str:
    return JOB.start(prepare_from_edits(), confirm_token, _handle_edits)


def run_from_edits(confirm_token: str) -> dict:
    return JOB.run(prepare_from_edits(), confirm_token, _handle_edits)


def counts() -> Dict[str, int]:
    out = {s: 0 for s in STATUSES}
    for c in _load():
        out[c.get("status", "")] = out.get(c.get("status", ""), 0) + 1
    return out
