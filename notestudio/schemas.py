"""Claude Codeが作るJSONの検証。エラーは日本語で返し、そのまま修正指示に使えるようにする。"""
from __future__ import annotations

from typing import Any

SOURCE_TYPES = {"official", "primary", "news", "qa", "sns", "note", "blog", "book", "other"}
CONFIDENCE = {"high", "medium", "low"}
SCORE_KEYS = {
    "demand": "需要の強さ",
    "gap": "良質な有料記事の少なさ",
    "willingness_to_pay": "お金を払う動機の強さ",
    "feasibility": "個人が公開情報＋自分の経験で書けるか",
    "longevity": "売れ続ける期間",
}
SCORE_WEIGHTS = {"demand": 0.25, "gap": 0.25, "willingness_to_pay": 0.2, "feasibility": 0.2, "longevity": 0.1}
DIFF_TYPES = {
    "primary_info": "一次情報",
    "angle": "独自の切り口",
    "diagram": "図解",
    "template": "テンプレート",
    "checklist": "チェックリスト",
    "calculation": "試算・シミュレーション",
    "curation": "比較・整理表",
}


class Checker:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def err(self, path: str, msg: str) -> None:
        self.errors.append(f"{path}: {msg}")

    def text(self, obj: dict, key: str, path: str, required: bool = True) -> None:
        v = obj.get(key)
        if v is None or (isinstance(v, str) and not v.strip()):
            if required:
                self.err(f"{path}.{key}", "必須です（空文字不可）")
            return
        if not isinstance(v, str):
            self.err(f"{path}.{key}", "文字列にしてください")

    def str_list(self, obj: dict, key: str, path: str, min_len: int = 0) -> None:
        v = obj.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            self.err(f"{path}.{key}", "文字列の配列にしてください")
        elif len(v) < min_len:
            self.err(f"{path}.{key}", f"{min_len}個以上必要です（現在{len(v)}個）")

    def obj_list(self, obj: dict, key: str, path: str, min_len: int = 0) -> list[dict]:
        v = obj.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
            self.err(f"{path}.{key}", "オブジェクトの配列にしてください")
            return []
        if len(v) < min_len:
            self.err(f"{path}.{key}", f"{min_len}個以上必要です（現在{len(v)}個）")
        return v

    def claim(self, c: dict, path: str) -> None:
        self.text(c, "claim", path)
        src = c.get("source")
        if not isinstance(src, dict):
            self.err(f"{path}.source", "出典オブジェクト {url, title, type} が必須です")
        else:
            # 書籍（references/books/）はURLの代わりに書名・著者・該当ページで特定する
            self.text(src, "url", f"{path}.source", required=src.get("type") != "book")
            self.text(src, "title", f"{path}.source")
            if src.get("type") == "book":
                self.text(src, "publisher", f"{path}.source")
                self.text(src, "location", f"{path}.source")
            if src.get("type") not in SOURCE_TYPES:
                self.err(f"{path}.source.type", f"{sorted(SOURCE_TYPES)} のいずれか")
        if c.get("confidence") not in CONFIDENCE:
            self.err(f"{path}.confidence", "high / medium / low のいずれか")


def validate_research(data: Any) -> list[str]:
    ck = Checker()
    if not isinstance(data, dict):
        return ["ルート: オブジェクトにしてください"]
    ck.text(data, "brief", "$")
    ck.str_list(data, "queries", "$", min_len=1)
    for i, c in enumerate(ck.obj_list(data, "candidates", "$", min_len=1)):
        p = f"candidates[{i}]"
        for key in ("title", "summary", "target_reader", "reader_pain", "gap", "primary_info_needed"):
            ck.text(c, key, p)
        ck.text(c, "differentiation_hint", p, required=False)
        ck.text(c, "score_rationale", p)
        ck.str_list(c, "keywords", p, min_len=2)
        ck.str_list(c, "channels", p, min_len=1)
        ck.str_list(c, "risks", p)
        for j, ev in enumerate(ck.obj_list(c, "demand_evidence", p, min_len=1)):
            ck.claim(ev, f"{p}.demand_evidence[{j}]")
        for j, comp in enumerate(ck.obj_list(c, "competitors", p)):
            ck.text(comp, "title", f"{p}.competitors[{j}]")
            price = comp.get("price")
            if price is not None and not isinstance(price, int):
                ck.err(f"{p}.competitors[{j}].price", "整数（円）かnullにしてください")
        pr = c.get("price_range")
        if not isinstance(pr, dict):
            ck.err(f"{p}.price_range", "{min, max, note} が必須です")
        else:
            for k in ("min", "max"):
                if pr.get(k) is not None and not isinstance(pr.get(k), int):
                    ck.err(f"{p}.price_range.{k}", "整数（円）かnullにしてください")
        scores = c.get("scores")
        if not isinstance(scores, dict):
            ck.err(f"{p}.scores", f"{list(SCORE_KEYS)} を1〜5で")
        else:
            for k in SCORE_KEYS:
                v = scores.get(k)
                if not isinstance(v, int) or not 1 <= v <= 5:
                    ck.err(f"{p}.scores.{k}", "1〜5の整数")
    return ck.errors


def validate_plan(data: Any) -> list[str]:
    ck = Checker()
    if not isinstance(data, dict):
        return ["ルート: オブジェクトにしてください"]
    if not isinstance(data.get("theme_id"), int):
        ck.err("$.theme_id", "対象テーマのID（整数）が必須です")
    ck.text(data, "value_proposition", "$")
    ba = data.get("reader_before_after")
    if not isinstance(ba, dict):
        ck.err("$.reader_before_after", "{before, after} が必須です")
    else:
        ck.text(ba, "before", "$.reader_before_after")
        ck.text(ba, "after", "$.reader_before_after")
    ck.str_list(data, "why_paid", "$", min_len=2)
    for i, d in enumerate(ck.obj_list(data, "differentiators", "$", min_len=1)):
        p = f"differentiators[{i}]"
        if d.get("type") not in DIFF_TYPES:
            ck.err(f"{p}.type", f"{sorted(DIFF_TYPES)} のいずれか")
        for key in ("title", "description", "how_to_make"):
            ck.text(d, key, p)
        if d.get("effort") not in {"low", "medium", "high"}:
            ck.err(f"{p}.effort", "low / medium / high のいずれか")
    pw = data.get("paywall")
    if not isinstance(pw, dict):
        ck.err("$.paywall", "{free_part, paid_part, boundary_hook, rationale} が必須です")
    else:
        ck.str_list(pw, "free_part", "$.paywall", min_len=1)
        ck.str_list(pw, "paid_part", "$.paywall", min_len=1)
        ck.text(pw, "boundary_hook", "$.paywall")
        ck.text(pw, "rationale", "$.paywall")
    for i, t in enumerate(ck.obj_list(data, "titles", "$", min_len=3)):
        ck.text(t, "text", f"titles[{i}]")
        ck.text(t, "angle", f"titles[{i}]")
    for i, o in enumerate(ck.obj_list(data, "outlines", "$", min_len=2)):
        p = f"outlines[{i}]"
        ck.text(o, "name", p)
        ck.text(o, "concept", p)
        sections = ck.obj_list(o, "sections", p, min_len=3)
        for j, s in enumerate(sections):
            ck.text(s, "heading", f"{p}.sections[{j}]")
            if not isinstance(s.get("paid"), bool):
                ck.err(f"{p}.sections[{j}].paid", "true / false")
        flags = {s.get("paid") for s in sections}
        if sections and not ({True, False} <= flags):
            ck.err(p, "無料セクションと有料セクションの両方が必要です")
    price = data.get("price")
    if not isinstance(price, dict) or not isinstance(price.get("suggested"), int):
        ck.err("$.price", "{suggested: 整数, rationale} が必須です")
    else:
        ck.text(price, "rationale", "$.price")
    for i, c in enumerate(ck.obj_list(data, "claims", "$")):
        ck.claim(c, f"claims[{i}]")
    ck.str_list(data, "open_questions", "$")
    return ck.errors


def score_total(scores: dict) -> float:
    """1〜5の各スコアを重み付けして100点満点に換算する。"""
    return round(sum(scores[k] * w for k, w in SCORE_WEIGHTS.items()) * 20, 1)
