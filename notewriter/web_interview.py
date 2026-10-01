"""インタビューモードの画面（/interview）。スマホで1問ずつ答える。音声入力はキーボード（OS）のマイクを使う。"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote

from . import interview as I
from . import kakera as K
from .web import e, lines_html, short

PREFIXES = ("/interview",)


def iurl(name: str, rest: str = "") -> str:
    return "/interview/" + quote(name, safe="") + rest


def page_index() -> str:
    rows = []
    for a in K.list_articles():
        n = len(I.pending(a["name"]))
        rows.append(f'<a class="pick-row" href="{e(iurl(a["name"]))}"><b>{e(a["name"])}</b>'
                    f'<span class="badge {"b-st" if n else "b-ok"}">{"質問 " + str(n) + " 問" if n else "今は質問なし"}</span></a>')
    body = "".join(rows) or '<p class="muted">まだ記事がありません。<a href="/articles">記事と区間</a>を登録してください。</p>'
    return f"""<h1>インタビュー</h1>
<div class="note">質問に答えるだけで、答えが<b>かけら</b>として保存されます。答えにくい質問はスキップできます。</div>
<div class="card"><h2>どの記事について話しますか？</h2><div class="stack">{body}</div></div>"""


def page_article(name: str) -> str:
    q = I.next_question(name)
    rest = len(I.pending(name))
    cfg = K.load_config()
    recent = "".join(f'<div class="kcard"><div class="head"><a class="kid" href="/kakera/{e(k["id"])}">{e(k["id"])}</a>'
                     f'<span>{e(k["section"])}</span><span>{e("、".join(k["viewpoints"]))}</span></div><p>{e(short(k["body"], 120))}</p></div>'
                     for k in I.answered(name)[:5])
    if q is None:
        main = (f'<div class="card"><h2>今は質問がありません</h2><p>空いている観点と [要追加] がなくなりました。おつかれさまでした。</p>'
                f'<form method="post" action="{e(iurl(name, "/reset"))}"><div class="btnrow">'
                f'<button class="btn">スキップした質問をもう一度出す</button><a class="btn" href="/kakera/new">自由にかけらを書く</a></div></form></div>')
    else:
        vps = "".join(f'<label class="chk"><input type="checkbox" name="viewpoints" value="{e(v)}"{" checked" if v == q["viewpoint"] else ""}>{e(v)}</label>'
                      for v in cfg["viewpoints"])
        main = f"""<div class="card qcard"><div class="small muted">残り {rest} 問 ／ 区間「{e(q["section"])}」 ／ {e(q["kind"])}</div>
<p class="question">{e(q["text"])}</p>
<form method="post" action="{e(iurl(name, "/answer"))}"><input type="hidden" name="key" value="{e(q["key"])}">
<textarea name="answer" rows="7" autofocus placeholder="ここに答えを書く（キーボードのマイクで話しても入力できます）"></textarea>
<details><summary>観点を選び直す（いまは「{e(q["viewpoint"])}」）</summary><div class="chks">{vps}</div></details>
<div class="btnrow"><button class="btn primary big">保存して次へ</button>
<button class="btn" formaction="{e(iurl(name, "/skip"))}" formnovalidate>スキップ</button></div></form></div>
<p class="small muted">🎤 音声で答えるとき: スマホのキーボードのマイクボタンを押して話します（音声はスマホの機能で文字になり、このアプリには文字だけが届きます）。</p>"""
    return f"""<p class="small"><a href="/interview">インタビュー</a></p>
<h1>{e(name)}</h1>{main}
<div class="card"><h2>さっき答えたこと</h2>{recent or '<p class="muted">まだありません。</p>'}</div>"""


def route_get(path: str, qs: dict):
    if path == "/interview":
        return page_index(), "インタビュー", "interview", ""
    m = re.fullmatch(r"/interview/([^/]+)", path)
    if m:
        name = unquote(m[1])
        return page_article(name), f"インタビュー {name}", "interview", ""
    return None


def back(path: str) -> str:
    m = re.fullmatch(r"/interview/([^/]+)(/.*)?", path)
    return f"/interview/{m[1]}" if m else "/interview"


def post(h, path: str, form) -> bool:
    m = re.fullmatch(r"/interview/([^/]+)/(answer|skip|reset)", path)
    if not m:
        return False
    name = unquote(m[1])
    if m[2] == "answer":
        k = I.answer(name, form.get("key"), form.get("answer", strip=False), form.list("viewpoints") or None)
        h._redirect_to(iurl(name), f"かけら {k['id']} として保存しました")
    elif m[2] == "skip":
        I.skip(name, form.get("key"))
        h._redirect_to(iurl(name), "スキップしました")
    else:
        n = I.reset_skipped(name)
        h._redirect_to(iurl(name), f"スキップした {n} 問をもう一度出します")
    return True
