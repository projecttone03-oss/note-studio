"""かけらの観点タグの候補（SPEC.md 第4段階「タグ付けの自動サジェスト」）。

本文にキーワード（config/kakera_suggest.json）が出てきた観点を「候補」として示すだけ。AI は使わず、自動では付けない。
"""
from __future__ import annotations

from typing import Dict, List

from . import kakera as K
from . import store

DEFAULTS = {"keywords": {}, "max_suggestions": 4}


def config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(store.read_json(store.CONFIG_DIR / "kakera_suggest.json", {}) or {})
    try:
        over = store.read_json(store.vault() / "config" / "kakera_suggest.json", {}) or {}
        kw = dict(cfg["keywords"])
        kw.update(over.get("keywords") or {})
        cfg.update(over)
        cfg["keywords"] = kw
    except store.VaultError:
        pass
    return cfg


def keywords() -> Dict[str, List[str]]:
    """設定の観点（kakera.json）にある観点だけの語の一覧（画面の JavaScript にも渡す）。"""
    vps = K.load_config()["viewpoints"]
    return {v: list(ws) for v, ws in (config().get("keywords") or {}).items() if v in vps}


def suggest(body: str, chosen=()) -> List[dict]:
    """[{"viewpoint", "words"}]（当てはまった語が多い順。すでに付いている観点は除く）。"""
    text = body or ""
    out = []
    for vp, words in keywords().items():
        if vp in chosen:
            continue
        hit = [w for w in words if w and w in text]
        if hit:
            out.append({"viewpoint": vp, "words": hit[:5], "n": len(hit)})
    out.sort(key=lambda x: -x["n"])
    return out[: int(config().get("max_suggestions") or 4)]


def add_viewpoint(kid: str, vp: str) -> dict:
    k = K.get_kakera(kid)
    if vp not in K.load_config()["viewpoints"]:
        raise ValueError(f"観点「{vp}」は設定にありません。")
    if vp in k["viewpoints"]:
        return k
    return K.update_kakera(kid, viewpoints=k["viewpoints"] + [vp])
