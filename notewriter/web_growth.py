"""値付けの目安・クロスセル導線・公開済み記事の登録・執筆ペースの画面。"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote

from . import kakera as K
from . import pace as PC
from . import pricing as PR
from . import published as PB
from . import writing as W
from .web import e, lines_html

PREFIXES = ("/published", "/pace")


def purl(name: str, rest: str = "") -> str:
    return "/publish/a/" + quote(name, safe="") + rest


# ---------------------------------------------------------------- 公開準備の画面に足すカード

def price_card(name: str, text: str) -> str:
    s = PR.suggest(text)
    past = PR.past_prices()[:5]
    past_html = ""
    if past:
        past_html = ("<p class='small'>これまでに公開した記事: " + "、".join(
            f"{e(p['title'][:20])}（{'無料' if not p['price'] else str(p['price']) + '円'}）" for p in past) + "</p>")
    return (f'<div class="card"><h2>値付けの目安</h2><p class="money">{s["low"]}〜{s["high"]}円</p>'
            f'<p>{e(s["summary"])}</p><details><summary>理由（情報密度 {s["score"]}/{s["max_score"]} 点）</summary>'
            f'<ul class="small">{"".join(f"<li>{e(r)}</li>" for r in s["reasons"])}</ul></details>{past_html}'
            f'<p class="small muted">文字数と情報密度から機械的に出した目安です。価格は自分で決めてください（帯は config/pricing.json）。</p></div>')


def crosssell_card(name: str, text: str) -> str:
    title = PB.title_of(text, name)
    items = PB.related(title, exclude_article=name)
    if not PB.list_published():
        body = '<p class="muted">公開済みの記事がまだ登録されていません。<a href="/published">公開した記事を登録</a>すると、ここに案内文が出ます。</p>'
    elif not items:
        body = ('<p class="muted">タグやタイトルの語が近い公開済みの記事が見つかりませんでした。'
                '<a href="/published">登録した記事のタグ</a>を見直すか、手で書いてください。</p>')
    else:
        cs = PB.crosssell_text(title, exclude_article=name, items=items)
        body = (f'<p class="small">近い順: {"、".join(e(x["title"]) for x in items)}</p>'
                f'<textarea rows="6" readonly onclick="this.select()">{e(cs)}</textarea>'
                f'<form method="post" action="{e(purl(name, "/crosssell"))}" data-confirm="下書きの末尾にこの案内文を足して、新しい版として保存します。よいですか？">'
                f'<div class="btnrow"><button class="btn">下書きの末尾に足す（新しい版）</button></div></form>')
    return f'<div class="card"><h2>クロスセル導線（記事末尾の案内）</h2>{body}</div>'


def publish_card(name: str, text: str) -> str:
    return price_card(name, text) + crosssell_card(name, text)


def publish_post(h, path: str, form) -> bool:
    m = re.fullmatch(r"/publish/a/([^/]+)/crosssell", path)
    if not m:
        return False
    name = unquote(m[1])
    text = W.draft_path(name).read_text(encoding="utf-8")
    cs = PB.crosssell_text(PB.title_of(text, name), exclude_article=name)
    if not cs:
        raise ValueError("足せる案内文がありません。")
    if cs.strip() in text:
        raise ValueError("同じ案内文がもう入っています。")
    meta = W.save_human_edit(name, text.rstrip("\n") + "\n\n" + cs, "クロスセルの案内文を末尾に追加")
    h._redirect_to(purl(name), f"案内文を足して v{meta['v']} として保存しました")
    return True


# ---------------------------------------------------------------- 公開済みの記事

def item_form(x: dict, action: str, submit: str) -> str:
    arts = "".join(f'<option value="{e(a["name"])}"{" selected" if a["name"] == x.get("article") else ""}>{e(a["name"])}</option>'
                   for a in K.list_articles())
    return f"""<form method="post" action="{e(action)}">
<label class="f">タイトル</label><input type="text" name="title" value="{e(x.get("title", ""))}" required>
<label class="f">URL（https://note.com/…）</label><input type="text" name="url" value="{e(x.get("url", ""))}" inputmode="url">
<div class="cols"><div><label class="f">価格（円・無料は0）</label><input type="number" name="price" min="0" step="10" value="{e(x.get("price", ""))}"></div>
<div><label class="f">公開日</label><input type="date" name="published" value="{e(x.get("published", ""))}"></div></div>
<label class="f">タグ（カンマ区切り。クロスセルの近さに使う）</label><input type="text" name="tags" value="{e("、".join(x.get("tags") or []))}">
<label class="f">ひとこと紹介（案内文に入る）</label><input type="text" name="summary" value="{e(x.get("summary", ""))}">
<label class="f">このアプリの記事（任意）</label><select name="article"><option value="">（なし）</option>{arts}</select>
<div class="btnrow"><button class="btn primary">{e(submit)}</button></div></form>"""


def page_published() -> str:
    items = PB.list_published()
    rows = "".join(
        f'<div class="kcard" id="{e(x["id"])}"><div class="head"><span class="kid">{e(x["id"])}</span><span>{e(x["published"])}</span>'
        f'<span class="badge b-ok">{"無料" if not x["price"] else str(x["price"]) + "円"}</span>'
        f'{"".join(f"<span class=badge>{e(t)}</span>" for t in x.get("tags") or [])}</div>'
        f'<p><b>{e(x["title"])}</b>{"<br><span class=small>" + e(x["url"]) + "</span>" if x.get("url") else ""}</p>'
        f'<details><summary>直す・削除</summary>{item_form(x, "/published/" + x["id"], "保存")}'
        f'<form method="post" action="/published/{e(x["id"])}/delete" data-confirm="登録を削除します（note の記事は消えません）。よいですか？">'
        f'<div class="btnrow"><button class="btn ng sm">登録を削除</button></div></form></details></div>' for x in items)
    return f"""<h1>公開した記事</h1>
<div class="note">note で公開した記事を、ここに手で登録します（note から自動では取ってきません）。
クロスセルの案内文・値付けの参考・執筆ペースに使います。</div>
<div class="grid"><div><div class="card"><h2>登録した記事</h2>{rows or '<p class="muted">まだありません。</p>'}</div></div>
<div><div class="card warm"><h2>記事を登録する</h2>{item_form({}, "/published/new", "登録する")}</div></div></div>"""


def _fields(form) -> dict:
    return {k: form.get(k) for k in ("title", "url", "price", "tags", "published", "summary", "article")}


# ---------------------------------------------------------------- 執筆ペース

def pace_html(compact: bool = False) -> str:
    s = PC.summary()
    mx = max([max(w["kakera"], w["neta"], w["published"]) for w in s["weeks"]] + [1])

    def bar(v: int, cls: str) -> str:
        return f'<span class="pbar {cls}" style="width:{max(4, int(100 * v / mx)) if v else 0}%"></span>'

    rows = "".join(f'<tr><td class="small">{w["start"].strftime("%m/%d")}〜</td>'
                   f'<td>{bar(w["kakera"], "k")}<span class="small">{w["kakera"]}</span></td>'
                   f'<td>{bar(w["neta"], "n")}<span class="small">{w["neta"]}</span></td>'
                   f'<td>{bar(w["published"], "p")}<span class="small">{w["published"]}</span></td></tr>'
                   for w in (s["weeks"][:4] if compact else s["weeks"]))
    return (f'<p><b>{e(s["headline"])}</b></p><p class="small">{e(s["total"])}</p>'
            f'<div class="tablewrap"><table class="pace"><tr><th>週</th><th>かけら</th><th>ネタ</th><th>公開</th></tr>{rows}</table></div>')


def home_card() -> str:
    return f'<div class="card"><h2>ペース</h2>{pace_html(True)}<p class="small"><a href="/pace">くわしく</a></p></div>'


def page_pace() -> str:
    return f"""<h1>ペース</h1>
<div class="card"><h2>この8週間</h2>{pace_html()}
<p class="small muted">続けてきた量を見るための表示です。決まったノルマや連続記録はありません。休んだ週があっても大丈夫です。</p></div>"""


# ---------------------------------------------------------------- 振り分け

def route_get(path: str, qs: dict):
    if path == "/published":
        return page_published(), "公開した記事", "publish", ""
    if path == "/pace":
        return page_pace(), "ペース", "home", ""
    return None


def back(path: str) -> str:
    return "/published" if path.startswith("/published") else "/pace"


def post(h, path: str, form) -> bool:
    if path == "/published/new":
        x = PB.add(**_fields(form))
        h._redirect_to(f"/published#{x['id']}", f"「{x['title']}」を登録しました")
        return True
    m = re.fullmatch(r"/published/(PB\d+)(/delete)?", path)
    if m:
        if m[2]:
            PB.delete(m[1])
            h._redirect_to("/published", f"{m[1]} の登録を削除しました")
        else:
            PB.update(m[1], **_fields(form))
            h._redirect_to(f"/published#{m[1]}", "保存しました")
        return True
    return False
