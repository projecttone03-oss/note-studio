"""値付け支援（SPEC.md 機能12）: 文字数と情報密度から価格帯の「目安」を出す。最終決定は人。

AI は使わない（機械的な数え上げ）。理由もいっしょに表示し、過去に公開した記事の価格（published.py）も参考に並べる。
"""
from __future__ import annotations

import re
from typing import List, Optional

from . import publish as P
from . import store

DEFAULTS = {"bands": [{"max_chars": None, "range": [100, 500]}],
            "density": {"numbers_per_1000": 8, "lists_per_1000": 4, "tables": 1, "sources": 3, "headings": 4},
            "shift_up_score": 4, "shift_down_score": 1}
_NUM = re.compile(r"[0-9０-９]+(?:[.,．][0-9０-９]+)?")
_LIST = re.compile(r"^\s*(?:[-*・]|\d+[.．)])\s+\S", re.M)
_TABLE = re.compile(r"^\s*\|[\s:|-]+\|\s*$", re.M)
_URL = re.compile(r"https?://[^\s)）\]」>]+")


def config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(store.read_json(store.CONFIG_DIR / "pricing.json", {}) or {})
    try:
        cfg.update(store.read_json(store.vault() / "config" / "pricing.json", {}) or {})
    except store.VaultError:
        pass
    return cfg


def _chars(text: str) -> int:
    body = "\n".join(l for l in text.splitlines() if not re.match(r"^\s*(#|<!--|\|[\s:|-]+\|)", l))
    return len(re.sub(r"\s+", "", body))


def metrics(text: str) -> dict:
    sp = P.split(text)
    paid = sp["paid"] if sp["marker_line"] else sp["free"]
    n = max(_chars(paid), 1)
    return {"has_paywall": sp["marker_line"] is not None, "total_chars": _chars(text), "paid_chars": _chars(paid),
            "numbers": len(_NUM.findall(paid)), "lists": len(_LIST.findall(paid)), "tables": len(_TABLE.findall(paid)),
            "sources": len(set(_URL.findall(paid))), "headings": len([h for h in P.headings(paid) if h[0] >= 2]),
            "per1000": 1000.0 / n}


def suggest(text: str) -> dict:
    cfg = config()
    m = metrics(text)
    d = cfg["density"]
    checks = [
        ("数字", m["numbers"] * m["per1000"], float(d["numbers_per_1000"]), "1000字あたり"),
        ("箇条書き", m["lists"] * m["per1000"], float(d["lists_per_1000"]), "1000字あたり"),
        ("表", m["tables"], float(d["tables"]), "個"),
        ("出典URL", m["sources"], float(d["sources"]), "件"),
        ("見出し", m["headings"], float(d["headings"]), "個"),
    ]
    score = 0
    reasons: List[str] = []
    for label, v, th, unit in checks:
        hit = v >= th
        score += int(hit)
        reasons.append(f"{label}: {v:.1f}（{unit}。目安 {th:g} 以上で加点）{' ✓' if hit else ''}")
    bands = cfg["bands"]
    idx = len(bands) - 1
    for i, b in enumerate(bands):
        if b.get("max_chars") is None or m["paid_chars"] <= int(b["max_chars"]):
            idx = i
            break
    base = idx
    if score >= int(cfg["shift_up_score"]):
        idx = min(idx + 1, len(bands) - 1)
    elif score <= int(cfg["shift_down_score"]):
        idx = max(idx - 1, 0)
    lo, hi = bands[idx]["range"]
    head = (f"有料エリア {m['paid_chars']}字" if m["has_paywall"] else f"全文 {m['paid_chars']}字（区切り行がないため全文で計算）")
    shift = "" if idx == base else ("（情報密度が高いので1つ上の帯）" if idx > base else "（情報密度が低めなので1つ下の帯）")
    return {"low": int(lo), "high": int(hi), "score": score, "max_score": len(checks), "metrics": m,
            "summary": f"{head} → {int(lo)}〜{int(hi)}円 が目安{shift}", "reasons": reasons}


def past_prices() -> List[dict]:
    from . import published
    return [p for p in published.list_published() if p.get("price") is not None]
