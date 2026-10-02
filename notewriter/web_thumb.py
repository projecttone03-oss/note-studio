"""サムネイルの画面（/thumbnails）。ひな形を選んでタイトルとキーワードを差し込み、SVG・HTML（あれば PNG）を作る。"""
from __future__ import annotations

import base64
import re
from urllib.parse import quote, unquote

from . import published as PB
from . import thumbnail as T
from . import writing as W
from .web import e

PREFIXES = ("/thumbnails",)


def turl(name: str, rest: str = "") -> str:
    return "/thumbnails/a/" + quote(name, safe="") + rest


def _title(name: str) -> str:
    p = W.draft_path(name)
    return PB.title_of(p.read_text(encoding="utf-8"), name) if p.is_file() else name


def page_article(name: str, qs: dict) -> str:
    W._article(name)
    title = (qs.get("title") or [_title(name)])[0]
    kw = (qs.get("kw") or [T.keyword(title)])[0]
    eng = T.png_engine()
    eng_txt = {"playwright": "PNG も作ります（playwright）", "chrome": "PNG も作ります（Chromium）"}.get(
        eng, "PNG を作る道具（playwright か Chromium）が見つからないため、SVG と HTML だけ作ります。")
    tpls = "".join(f'<label class="chk"><input type="checkbox" name="tpl" value="{e(t["name"])}" checked>{e(t.get("label", t["name"]))}</label>'
                   for t in T.config()["templates"])
    cards = []
    for f in T.list_files(name):
        data = T.read_file(name, f["svg"])[0]
        src = "data:image/svg+xml;base64," + base64.b64encode(data).decode("ascii")
        links = " ".join(f'<a class="btn sm" href="{e(turl(name, "/file/" + f[ext]))}">{ext.upper()} を保存</a>'
                         for ext in ("png", "svg", "html") if f[ext])
        cards.append(f'<div class="thumb"><img src="{src}" alt="{e(f["label"])}"><div class="small"><b>{e(f["label"])}</b></div>'
                     f'<div class="btnrow">{links}</div></div>')
    return f"""<p class="small"><a href="/publish/a/{e(quote(name, safe=""))}">{e(name)} の公開準備</a></p>
<h1>サムネイル</h1>
<div class="grid"><div><div class="card"><h2>できたもの</h2>{"".join(cards) or '<p class="muted">まだありません。右で作ってください。</p>'}
<p class="small muted">1280×670px（1.91:1）。文字の形は、開く機械に入っているフォントで変わります。</p></div></div>
<div><div class="card warm"><h2>作る</h2><form method="post" action="{e(turl(name, "/generate"))}">
<label class="f">タイトル（小さめに入る文）</label><textarea name="title" rows="3">{e(title)}</textarea>
<label class="f">キーワード（大きく入る語。タイトルから自動で選んだもの）</label><input type="text" name="kw" value="{e(kw)}">
<label class="f">ひな形</label><div class="chks">{tpls}</div>
<p class="small">{e(eng_txt)}</p><div class="btnrow"><button class="btn primary">作る</button></div></form></div></div></div>"""


def publish_card(name: str, text: str) -> str:
    n = len(T.list_files(name))
    return (f'<div class="card"><h2>サムネイル</h2><p class="small">1280×670px のひな形にタイトルとキーワードを差し込みます。'
            f'{"（" + str(n) + " 種類 作成済み）" if n else ""}</p><a class="btn" href="{e(turl(name))}">サムネイルを作る</a></div>')


def publish_post(h, path: str, form) -> bool:
    return False


def route_get(path: str, qs: dict):
    m = re.fullmatch(r"/thumbnails/a/([^/]+)/file/([^/]+)", path)
    if m:
        name = unquote(m[1])
        data, ctype = T.read_file(name, m[2])
        return {"body": data, "ctype": ctype, "filename": f"{W.slug(name)}-{m[2]}"}
    m = re.fullmatch(r"/thumbnails/a/([^/]+)", path)
    if m:
        name = unquote(m[1])
        return page_article(name, qs), "サムネイル", "publish", ""
    return None


def back(path: str) -> str:
    m = re.fullmatch(r"/thumbnails/a/([^/]+)(/.*)?", path)
    return f"/thumbnails/a/{m[1]}" if m else "/publish"


def post(h, path: str, form) -> bool:
    m = re.fullmatch(r"/thumbnails/a/([^/]+)/generate", path)
    if not m:
        return False
    name = unquote(m[1])
    out = T.generate(name, form.get("title"), form.list("tpl"), form.get("kw") or None)
    n_png = sum(1 for o in out if o["png"])
    h._redirect_to(turl(name), f"{len(out)} 種類作りました" + (f"（PNG {n_png} 枚）" if n_png else "（SVG・HTML）"))
    return True
