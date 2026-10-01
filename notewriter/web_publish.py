"""公開準備の画面（/publish）。無料/有料の境界チェック・note スマホプレビューほか。

ここで作るのは「確認用の表示」と「投稿用に整形した文章」まで。note への投稿・公開ボタンは人がブラウザで押す。
"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote

from . import publish as P
from . import writing as W
from .web import e, lines_html

PREFIXES = ("/publish",)
SEV_CLASS = {"strong": "b-strong", "warn": "b-warn", "info": "b-info"}

# 公開準備の画面にカードと POST を足す機能ごとのファイル。各モジュールは publish_card(name, text) -> HTML と
# publish_post(h, path, form) -> bool を持つ。
CARD_MODULES = ("web_growth", "web_thumb", "web_sns")


def _card_modules():
    import importlib
    return [importlib.import_module("notewriter." + n) for n in CARD_MODULES]


def purl(name: str, rest: str = "") -> str:
    return "/publish/a/" + quote(name, safe="") + rest


def draft_text(name: str) -> str:
    W._article(name)
    p = W.draft_path(name)
    if not p.is_file():
        raise ValueError(f"「{name}」の下書きがまだありません（下書きの画面で作るか、drafts/ に置いてください）。")
    return p.read_text(encoding="utf-8")


def findings_html(fs) -> str:
    if not fs:
        return '<p><span class="badge b-ok">警告はありません</span></p>'
    rows = "".join(f'<tr><td><span class="badge {SEV_CLASS[f["severity"]]}">{e(P.SEVERITY[f["severity"]])}</span></td>'
                   f'<td class="num">{e(f["line"] or "—")}</td><td>{e(f["message"])}</td></tr>' for f in fs)
    return f'<div class="tablewrap"><table><tr><th>重さ</th><th class="num">行</th><th>内容</th></tr>{rows}</table></div>'


def boundary_card(text: str) -> str:
    cfg = P.config()
    fs = P.check_boundary(text)
    sp = P.split(text, cfg)
    free_h = [h for lv, h, _ in P.headings(sp["free"]) if lv >= 2]
    paid_h = [h for lv, h, _ in P.headings(sp["paid"]) if lv >= 2]
    cols = ""
    if sp["marker_line"]:
        cols = (f'<div class="cols"><div><h3>無料エリアの見出し</h3><ul>{"".join(f"<li>{e(h)}</li>" for h in free_h) or "<li class=muted>なし</li>"}</ul></div>'
                f'<div><h3>有料エリアの見出し</h3><ul>{"".join(f"<li>{e(h)}</li>" for h in paid_h) or "<li class=muted>なし</li>"}</ul></div></div>')
    marks = " ／ ".join(f"<code>{e(m)}</code>" for m in cfg["paywall_markers"])
    return (f'<div class="card" id="boundary"><h2>無料/有料の境界チェック</h2>'
            f'<p class="small">区切り行（{marks}）より前が無料、後ろが有料です。有料の見出しが無料エリアで予告されているかを機械的に突き合わせます（警告だけ。本文は変えません）。</p>'
            f'{findings_html(fs)}{cols}</div>')


def checklist_card(name: str, text: str) -> str:
    """公開前に確かめることの一覧（上から順に。押すとその場所へ）。投稿・公開は人がブラウザで行う。"""
    fs = P.check_boundary(text)
    n = P.summarize(fs)
    if n["strong"]:
        b = f'<span class="badge b-strong">強い警告 {n["strong"]}</span>'
    elif n["warn"]:
        b = f'<span class="badge b-warn">警告 {n["warn"]}</span>'
    else:
        b = '<span class="badge b-ok">OK</span>'
    rows = [
        (b, '<a href="#boundary">無料/有料の境界と [要追加] の残り</a>'),
        ('<span class="badge b-st">人が確認</span>', '<a href="#compliance">コンプライアンスチェック（編集後の全文）</a>'),
        ('<span class="badge b-st">見る</span>', '<a href="#preview">スマホでの見え方（プレビュー）</a>'),
        ('<span class="badge b-st">決める</span>', '<a href="#price">価格（目安を見て自分で決める）</a>'),
        ('<span class="badge">任意</span>', '<a href="#crosssell">記事末尾の案内・サムネイル・SNS</a>'),
    ]
    items = "".join(f"<li>{badge}{link}</li>" for badge, link in rows)
    return (f'<div class="card warm"><h2>公開前のチェックリスト</h2><ul class="checklist">{items}</ul>'
            f'<p class="small muted">note への投稿・公開ボタンは、確かめたあとにブラウザで自分で押します。</p></div>')


def page_index() -> str:
    rows = []
    for r in W.list_drafts():
        if not r["latest"]:
            continue
        rows.append(f'<tr><td><a href="{e(purl(r["article"]))}"><b>{e(r["article"])}</b></a></td>'
                    f'<td>v{r["latest"]["v"]}</td></tr>')
    table = (f'<div class="tablewrap"><table><tr><th>記事</th><th>最新の版</th></tr>{"".join(rows)}</table></div>'
             if rows else '<p class="muted">下書きのある記事がありません。</p>')
    return f"""<h1>公開準備</h1>
<div class="note"><b>note への投稿・公開は、ブラウザで人が行います。</b>ここでは公開前の確認と、投稿用の文章づくりだけを行います。</div>
<div class="card"><h2>下書きから</h2>{table}</div>
<div class="card"><h2>貼り付けて確かめる（保存しません）</h2>
<form method="post" action="/publish/paste"><textarea name="text" class="tall" rows="12" placeholder="# タイトル&#10;&#10;無料エリア…&#10;&#10;&lt;!-- ここから有料 --&gt;&#10;&#10;## 有料の見出し"></textarea>
<label class="f">価格（プレビュー用・任意）</label><input type="number" name="price" min="0" step="10">
<div class="btnrow"><button class="btn primary" name="act" value="check">境界チェック</button>
<button class="btn" name="act" value="preview">プレビュー</button></div></form></div>"""


def page_article(name: str, qs: dict) -> str:
    text = draft_text(name)
    price = (qs.get("price") or [""])[0]
    extra = "".join(m.publish_card(name, text) for m in _card_modules())
    return f"""<p class="small"><a href="/publish">公開準備</a> ／ <a href="/drafts/{e(quote(name, safe=""))}">下書き</a></p>
<h1>{e(name)} の公開準備</h1>
{checklist_card(name, text)}
<div class="card" id="preview"><h2>note スマホプレビュー</h2>
<p class="small">本文幅 620px 相当・見出し・目次・有料の区切り線を再現した表示です（見た目の目安）。</p>
<form method="get" action="{e(purl(name, "/preview"))}"><label class="f">価格（任意。プレビューの有料ラインに表示）</label>
<input type="number" name="price" min="0" step="10" placeholder="例: 300" value="{e(price)}">
<div class="btnrow"><button class="btn primary">プレビューを開く</button></div></form></div>
{boundary_card(text)}
<div class="card" id="compliance"><h2>コンプライアンスチェック</h2><p class="small">公開前に、編集後を含む本文全体を必ず確かめてください。</p>
<form method="post" action="/check"><input type="hidden" name="mode" value="drafts"><input type="hidden" name="drafts" value="{e(W.draft_rel(name))}">
<button class="btn">コンプライアンスチェック</button></form></div>
{extra}"""


def route_get(path: str, qs: dict):
    if path == "/publish":
        return page_index(), "公開準備", "publish", ""
    m = re.fullmatch(r"/publish/a/([^/]+)/preview", path)
    if m:
        name = unquote(m[1])
        text = draft_text(name)
        price = re.sub(r"\D", "", (qs.get("price") or [""])[0])[:7]
        return {"body": P.preview_page(text, name, price, P.check_boundary(text), back=purl(name))}
    m = re.fullmatch(r"/publish/a/([^/]+)", path)
    if m:
        name = unquote(m[1])
        return page_article(name, qs), f"{name} の公開準備", "publish", ""
    return None


def back(path: str) -> str:
    m = re.fullmatch(r"/publish/a/([^/]+)(/.*)?", path)
    return f"/publish/a/{m[1]}" if m else "/publish"


def post(h, path: str, form) -> bool:
    if path == "/publish/paste":
        text = form.get("text", strip=False)
        if not text.strip():
            raise ValueError("本文を貼り付けてください。")
        if form.get("act") == "preview":
            price = re.sub(r"\D", "", form.get("price"))[:7]
            h._send(P.preview_page(text, "貼り付けた本文", price, P.check_boundary(text), back="/publish"))
        else:
            h._page("境界チェック", f'<p class="small"><a href="/publish">公開準備</a></p><h1>境界チェックの結果</h1>'
                                   f'{boundary_card(text)}<div class="card"><h2>チェックした本文</h2>'
                                   f'<div class="small">{lines_html(text[:3000])}</div></div>', "publish")
        return True
    for m in _card_modules():
        if m.publish_post(h, path, form):
            return True
    return False
