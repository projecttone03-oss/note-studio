"""かけらの観点タグの候補（/kakera-suggest）。候補を押したときだけ、その観点をかけらに足す。"""
from __future__ import annotations

import json
import re

from . import kakera_suggest as KS
from .web import e

PREFIXES = ("/kakera-suggest",)


def card(k: dict) -> str:
    sug = KS.suggest(k["body"], k["viewpoints"])
    if not sug:
        return ""
    items = "".join(
        f'<form method="post" action="/kakera-suggest/{e(k["id"])}" class="inline"><input type="hidden" name="viewpoint" value="{e(s["viewpoint"])}">'
        f'<button class="btn sm">＋ {e(s["viewpoint"])}</button></form><span class="small muted">（{e("・".join(s["words"]))}）</span> '
        for s in sug)
    return (f'<div class="card"><h2>観点の候補</h2><p class="small">本文の言葉から見つけた候補です。合っていれば押してください（自動では付けません）。</p>'
            f'<div class="btnrow">{items}</div></div>')


def data_attr() -> str:
    """新しく書く画面で、入力中に候補を光らせるための語の一覧（JSON）。"""
    return e(json.dumps(KS.keywords(), ensure_ascii=False))


def route_get(path: str, qs: dict):
    return None


def back(path: str) -> str:
    m = re.fullmatch(r"/kakera-suggest/(K\d+)", path)
    return f"/kakera/{m[1]}" if m else "/kakera"


def post(h, path: str, form) -> bool:
    m = re.fullmatch(r"/kakera-suggest/(K\d+)", path)
    if not m:
        return False
    vp = form.get("viewpoint")
    KS.add_viewpoint(m[1], vp)
    h._redirect_to(f"/kakera/{m[1]}", f"観点「{vp}」を足しました")
    return True
