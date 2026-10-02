"""反応記録の CSV 取り込み（SPEC.md 機能10）。列名は config/reactions_csv.json で合わせる（note の CSV の形は未確認のため）。

流れ: ファイルを読む → 取り込む前の確認（読み取れた行・読めなかった行を表示）→ 人が押したときだけ記録に足す。
同じ記事・同じ記録日・同じ数の行は二重に入れない。
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date
from typing import Dict, List, Optional, Tuple

from . import reactions, store

DEFAULTS = {"columns": {}, "memo_columns": {}, "max_rows": 2000}


def config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(store.read_json(store.CONFIG_DIR / "reactions_csv.json", {}) or {})
    try:
        cfg.update(store.read_json(store.vault() / "config" / "reactions_csv.json", {}) or {})
    except store.VaultError:
        pass
    return cfg


def _key(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "")).casefold()


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("文字コードを読み取れませんでした（UTF-8 か Shift_JIS の CSV にしてください）。")


def _date(v: str) -> str:
    v = unicodedata.normalize("NFKC", v or "").strip()
    m = re.match(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", v)
    return f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}" if m else ""


def _num(v: str) -> int:
    v = unicodedata.normalize("NFKC", v or "").replace(",", "").strip()
    if not v:
        return 0
    if not re.fullmatch(r"\d+", v):
        raise ValueError(f"数ではありません（{v}）")
    return int(v)


def parse(text: str, recorded: str = "") -> dict:
    """{"rows": [...], "errors": [(行番号, 理由)], "mapping": {項目: 列名}, "unknown": [使わない列]}"""
    cfg = config()
    recorded = recorded or date.today().isoformat()
    reader = csv.reader(io.StringIO(text.replace("\r\n", "\n").replace("\r", "\n")))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("CSV が空です。")
    keys = [_key(h) for h in header]
    mapping: Dict[str, int] = {}
    for field, names in (cfg.get("columns") or {}).items():
        for n in names:
            if _key(n) in keys:
                mapping[field] = keys.index(_key(n))
                break
    memo_map: Dict[str, int] = {}
    for label, names in (cfg.get("memo_columns") or {}).items():
        for n in names:
            if _key(n) in keys:
                memo_map[label] = keys.index(_key(n))
                break
    if "title" not in mapping:
        raise ValueError("記事のタイトルの列が見つかりません（1行目の見出し: " + "、".join(header[:12])
                         + "）。config/reactions_csv.json の columns.title に、この CSV の列名を足してください。")
    used = set(mapping.values()) | set(memo_map.values())
    rows, errors = [], []
    for i, row in enumerate(reader, start=2):
        if not any(c.strip() for c in row):
            continue
        if len(rows) >= int(cfg.get("max_rows") or 2000):
            errors.append((i, "行が多すぎるため、ここから先は読みませんでした"))
            break

        def col(f: str) -> str:
            j = mapping.get(f)
            return row[j].strip() if j is not None and j < len(row) else ""

        try:
            rec = {"title": col("title"), "published": _date(col("published")), "recorded": recorded,
                   "likes": _num(col("likes")), "comments": _num(col("comments")), "purchases": _num(col("purchases"))}
            if not rec["title"]:
                raise ValueError("タイトルが空です")
            memo = [f"{label}: {row[j].strip()}" for label, j in memo_map.items() if j < len(row) and row[j].strip()]
            rec["memo"] = "（CSV から取り込み）" + (" " + "・".join(memo) if memo else "")
            rows.append(rec)
        except ValueError as ex:
            errors.append((i, str(ex)))
    return {"rows": rows, "errors": errors, "mapping": {f: header[j] for f, j in mapping.items()},
            "memo": {k: header[j] for k, j in memo_map.items()},
            "unknown": [h for j, h in enumerate(header) if j not in used]}


def _sig(r: dict) -> Tuple:
    return (r.get("title"), r.get("recorded"), int(r.get("likes") or 0), int(r.get("comments") or 0), int(r.get("purchases") or 0))


def import_rows(rows: List[dict]) -> Tuple[int, int]:
    """(足した件数, 同じ記録があって飛ばした件数)。"""
    have = {_sig(r) for r in reactions.list_reactions()}
    added = skipped = 0
    for r in rows:
        if _sig(r) in have:
            skipped += 1
            continue
        reactions.add_reaction(**r)
        have.add(_sig(r))
        added += 1
    return added, skipped
