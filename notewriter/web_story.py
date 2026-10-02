"""つぶやく画面（/tsubuyaki）と、ボックスごとの下書き（/story）。

- つぶやく: ボックスを選ぶ欄と文字を入れる欄だけ。投稿すると入力欄が空になり、下に新しい順で並ぶ。かけらは別のボックスへ移せる。
- 下書き: note で読むように記事1本を続けて表示。Claude が足した文は色と下線で区別。段落を押したときだけ直す欄が出る。
  横（スマホでは下）に「足りないかけら」の質問リスト。答えはそのボックスのかけらとして保存。
"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote

from . import boxes as BX
from . import kakera as K
from . import story as S
from . import writing as W
from .web import e, lines_html, qurl
from .web_ext import confirm_page, job_page

PREFIXES = ("/tsubuyaki", "/story")


def surl(box: str, rest: str = "") -> str:
    return "/story/b/" + quote(box, safe="") + rest


def _box_options(cur: str, include_unboxed: bool = False) -> str:
    names = BX.box_names() + ([BX.UNBOXED] if include_unboxed else [])
    return "".join(f'<option value="{e(n)}"{" selected" if n == cur else ""}>{e(n)}</option>' for n in names)


def _time(ts: str) -> str:
    return (ts or "").replace("T", " ")[5:16]


# ---------------------------------------------------------------- つぶやく

def page_tsubuyaki(qs: dict) -> str:
    BX.ensure_registered()
    names = BX.box_names()
    box = (qs.get("box") or [""])[0] or BX.last_box() or (names[0] if names else "")
    if box and box != BX.UNBOXED and box in names:
        BX.remember_box(box)
    has_unboxed = any(not k["article"] for k in K.list_kakera())
    cfg = K.load_config()
    if not names:
        return """<h1>つぶやく</h1>
<div class="card warm"><h2>まずボックスを作る</h2><p>ボックスは「何についての思い出か」を分ける入れ物です（例: 在宅事件・前編、パチンコ、借金）。名前はあとから増やせます。</p>
<form method="post" action="/tsubuyaki/box"><input type="text" name="name" placeholder="ボックスの名前" required>
<div class="btnrow"><button class="btn primary big">ボックスを作る</button></div></form></div>"""
    items = BX.kakera_in(box) if box else []
    vps = "".join(f'<label class="chk"><input type="checkbox" name="viewpoints" value="{e(v)}">{e(v)}</label>' for v in cfg["viewpoints"])
    feed = "".join(
        f'<div class="tweet" id="{e(k["id"])}"><label class="tsel"><input type="checkbox" name="ids" value="{e(k["id"])}" form="moveform"></label>'
        f'<div class="tbody"><div class="ttext">{lines_html(k["body"])}</div>'
        f'<div class="small muted">{e(_time(k["created"]))}{" ・" + e(k["section"]) if k["section"] else ""}'
        f' ・<a href="/kakera/{e(k["id"])}">詳しく</a></div></div></div>' for k in items)
    move_bar = ""
    if items and (len(names) > 1 or box == BX.UNBOXED):
        others = "".join(f'<option value="{e(n)}">{e(n)}</option>' for n in names if n != box)
        move_bar = (f'<form method="post" action="/tsubuyaki/move" id="moveform" class="movebar">'
                    f'<input type="hidden" name="from" value="{e(box)}"><span class="small">選んだ <b data-count>0</b> 件を</span>'
                    f'<select name="to">{others}</select><button class="btn sm">へ移す</button></form>')
    draft_btn = (f'<a class="btn" href="{e(surl(box))}">このボックスの下書きへ</a>' if box and box != BX.UNBOXED else "")
    return f"""<h1>つぶやく</h1>
<form method="get" action="/tsubuyaki" class="boxpick"><label class="f" for="t-box">ボックス</label>
<select id="t-box" name="box" data-autosubmit>{_box_options(box, has_unboxed)}</select><noscript><button class="btn sm">切り替え</button></noscript></form>
<form method="post" action="/tsubuyaki" class="card post"><input type="hidden" name="box" value="{e(box)}">
<textarea name="body" class="tsubuyaki" rows="5" autofocus required placeholder="ふと浮かんだことを、ひとことでも（マイクで話しても入力できます）" data-ctrl-enter></textarea>
<div class="btnrow"><button class="btn primary big wide">投稿</button></div>
<details><summary>詳しく（区間・観点。入れなくてOK）</summary>
<label class="f">区間（空なら下書きのときに自動で付きます）</label><input type="text" name="section">
<label class="f">観点（空なら言葉から自動で付きます）</label><div class="chks">{vps}</div></details></form>
<div class="card"><h2>{e(box)} のかけら（{len(items)}）</h2>
{feed or '<p class="muted">まだありません。上でつぶやいてください。</p>'}
{'<p class="small muted">左の□を選ぶと、別のボックスへ移せます（まとめて選べます）。</p>' if move_bar else ""}{move_bar}
<div class="btnrow">{draft_btn}<a class="btn sm" href="/story">ボックスの一覧</a></div></div>
<details class="card"><summary>新しいボックスを作る</summary><form method="post" action="/tsubuyaki/box">
<input type="text" name="name" placeholder="例: パチンコ" required><div class="btnrow"><button class="btn">作る</button></div></form></details>"""


# ---------------------------------------------------------------- ボックスの一覧

def page_index() -> str:
    BX.ensure_registered()
    rows = []
    for b in BX.list_boxes():
        if b.get("unboxed"):
            rows.append(f'<a class="pick-row" href="{e(qurl("/tsubuyaki", box=BX.UNBOXED))}"><span><b>{e(b["name"])}</b>'
                        f'<br><span class="small muted">かけら {b["count"]}（ボックスへ移してください）</span></span><span class="badge b-warm">移す</span></a>')
            continue
        vs = W.versions(b["name"])
        st = S.state(b["name"])
        open_gaps = sum(1 for g in st["gaps"] if not g.get("answered")) if vs else 0
        state = (f'下書き v{vs[-1]["v"]}' + (f'・足りない {open_gaps}' if open_gaps else "")) if vs else "下書きはまだ"
        rows.append(f'<a class="pick-row" href="{e(surl(b["name"]))}"><span><b>{e(b["name"])}</b>'
                    f'<br><span class="small muted">かけら {b["count"]}・{e(state)}</span></span>'
                    f'<span class="badge b-vp">{"読む" if vs else "作る"}</span></a>')
    return f"""<h1>下書き</h1>
<div class="note">ボックスごとに、かけらから記事を1本作ります。<a href="/tsubuyaki">つぶやく</a>画面でかけらを増やしてから作り直せます。</div>
<div class="card"><h2>ボックス</h2><div class="stack">{"".join(rows) or '<p class="muted">まだボックスがありません。</p>'}</div>
<div class="btnrow"><a class="btn primary" href="/tsubuyaki">つぶやく</a></div></div>
<p class="small"><a href="/drafts">区間ごとの下書き・リサーチ型の記事（以前の画面）</a></p>"""


# ---------------------------------------------------------------- 記事を読む・直す

def sentence_html(s: dict) -> str:
    if s.get("gap"):
        return f'<span class="gapmark" id="m-{e(s["gap"])}">{e(s["text"])}</span>'
    cls = []
    if s.get("bridge"):
        cls.append("bridge")
    if s.get("flags"):
        cls.append("chkw")
    title = "Claude が足した文（かけらに直接もとづかない）" if s.get("bridge") else ""
    if s.get("flags"):
        title = (title + " / " if title else "") + " / ".join(s["flags"])
    t = e(s["text"])
    return f'<span class="{" ".join(cls)}" title="{e(title)}">{t}</span>' if cls else t


def render_article(box: str, blocks) -> str:
    out = []
    for i, b in enumerate(blocks):
        if b["kind"] == "heading":
            level = len(b["text"]) - len(b["text"].lstrip("#"))
            text = b["text"].lstrip("#").strip()
            out.append(f'<h2 class="atitle">{e(text)}</h2>' if level == 1 else f'<h3 class="ahead">{e(text)}</h3>')
            continue
        body = ("".join(sentence_html(s) for s in b["sentences"]) if b.get("sentences")
                else lines_html(b["text"]))
        out.append(f'<details class="para" id="p-{i}"><summary>{body}</summary>'
                   f'<form method="post" action="{e(surl(box, "/para"))}"><input type="hidden" name="index" value="{i}">'
                   f'<textarea name="text" rows="5">{e(b["text"])}</textarea>'
                   f'<div class="btnrow"><button class="btn sm primary">この段落を保存</button>'
                   f'<span class="small muted">空にして保存すると段落を消します。前の版は残ります。</span></div></form></details>')
    return "".join(out)


def questions_html(box: str, st: dict) -> str:
    items = []
    for g in st["gaps"]:
        done = bool(g.get("answered"))
        badge = f'<span class="badge b-ok">済み</span>' if done else ""
        form = (f'<p class="small">答えた内容はかけら <a href="/kakera/{e(g["answered"])}">{e(g["answered"])}</a> になりました。</p>' if done else
                f'<form method="post" action="{e(surl(box, "/gap/" + str(g["no"])))}">'
                f'<textarea name="answer" rows="4" placeholder="思い出せることを、ひとことでも"></textarea>'
                f'<div class="btnrow"><button class="btn primary sm">答える（かけらとして保存）</button></div></form>')
        why = f'<p class="small muted">{e(g["why"])}</p>' if g.get("why") else ""
        items.append(f'<details class="q{" done" if done else ""}" id="q-{e(g["no"])}"><summary><b>{e(S.gap_label(int(g["no"])))}</b> '
                     f'{e(g["question"])} {badge}</summary>{why}{form}</details>')
    answered = sum(1 for g in st["gaps"] if g.get("answered"))
    return (f'<div class="card warm"><h2>足りないかけら</h2>'
            f'{"".join(items) or "<p class=muted>足りない所はありません。</p>"}'
            f'<p class="small">答えたもの {answered} / {len(st["gaps"])}</p>'
            f'<form method="post" action="{e(surl(box, "/generate"))}"><div class="btnrow">'
            f'<button class="btn {"primary" if answered else ""} wide">答えた分を入れて下書きを書き直す</button></div></form>'
            f'<p class="small muted">前の版は版の履歴に残ります。</p></div>')


def page_story(box: str, qs: dict) -> str:
    a = W._article(box)
    name = a["name"]
    blocks = S.current_blocks(name)
    n_k = len(S._box_kakera(name))
    running = S.JOB.running()
    run = (f'<div class="note">実行中: <a href="/story/jobs/{e(running["id"])}">{e(running["id"])}</a></div>' if running else "")
    settings = (f'<details class="card"><summary>設定</summary><form method="post" action="{e(surl(name, "/settings"))}">'
                f'<label class="chk"><input type="checkbox" name="confirm" value="1"{" checked" if S.confirm_before_send() else ""}>'
                f'Claude に渡す前に、渡す全文を確認する</label><div class="btnrow"><button class="btn sm">保存</button></div></form></details>')
    if not blocks:
        return f"""<p class="small"><a href="/story">下書き</a></p><h1>{e(name)}</h1>{run}
<div class="card warm"><h2>まだ下書きがありません</h2><p>このボックスのかけら（{n_k}）だけで、記事を1本書きます。見出しの構成も Claude が考えます（あとで直せます）。</p>
<form method="post" action="{e(surl(name, "/generate"))}"><div class="btnrow"><button class="btn primary big">このボックスのかけらで下書きを作る</button>
<a class="btn" href="{e(qurl("/tsubuyaki", box=name))}">先につぶやく</a></div></form></div>{settings}"""
    cur = W.current(name)["meta"]
    n_bridge = len(S.bridge_sentences(name))
    changed = ('<div class="card alert"><h2>下書きファイルが直接編集されています</h2><p>先に「全体を手で直す」の欄から保存し直すか、'
               f'<a href="/drafts/{e(quote(name, safe=""))}">以前の画面</a>で版として保存してください。</p></div>' if W.file_changed(name) else "")
    return f"""<p class="small"><a href="/story">下書き</a> ／ <a href="{e(qurl("/tsubuyaki", box=name))}">つぶやく</a> ／
<a href="/drafts/{e(quote(name, safe=""))}/versions">版の履歴</a> ／ <a href="/publish/a/{e(quote(name, safe=""))}">公開準備</a></p>
<h1>{e(name)}</h1>{run}{changed}
<p class="small muted">v{cur["v"]}（{e(W.SOURCES.get(cur["source"], cur["source"]))}・{e(_time(cur["created"]))}）</p>
<div class="legend small"><span class="bridge">色つきの下線</span> = Claude が足した文（{n_bridge}文。かけらにない内容になっていないか確かめてください）
<span class="chkw">点線</span> = かけらに見当たらない語がある文 ／ 段落を押すと直せます</div>
<div class="grid"><div><article class="card story">{render_article(name, blocks)}</article>
<details class="card"><summary>全体を手で直す</summary><p class="small">直した前と後は、文体学習の材料として記録されます。</p>
<form method="post" action="{e(surl(name, "/edit"))}"><textarea name="body" class="tall" rows="18">{e(W.render(blocks))}</textarea>
<div class="btnrow"><button class="btn primary">保存（新しい版）</button></div></form></details></div>
<div>{questions_html(name, S.state(name))}{settings}</div></div>"""


def page_bridge(box: str) -> str:
    name = W._article(box)["name"]
    items = "".join(f'<li><a href="{e(surl(name, "#p-" + str(i)))}">{e(s["text"])}</a>'
                    + "".join(f'<div class="flag">⚠ {e(f)}</div>' for f in s.get("flags") or []) + "</li>"
                    for i, s in S.bridge_sentences(name))
    return f"""<p class="small"><a href="{e(surl(name))}">{e(name)}</a></p><h1>Claude が足した文</h1>
<div class="note">かけらに直接もとづかない文（つなぎの文・説明）です。「自分が言っていないこと」になっていないか、1つずつ確かめてください。
直すときは、記事の画面で段落を押します。</div><div class="card"><ol class="stack">{items or "<li class=muted>ありません。</li>"}</ol></div>"""


def page_job(job: dict):
    box = job.get("box", "")
    if job.get("status") == "conflict":
        acts = "" if job.get("discarded") else (
            f'<div class="btnrow"><form method="post" action="/story/jobs/{e(job["id"])}/accept"><button class="btn warm">この結果で置き換える</button></form>'
            f'<form method="post" action="/story/jobs/{e(job["id"])}/discard"><button class="btn">見送る</button></form></div>')
        done = f'<div class="card warm"><h2>保留にしました</h2><p>{e(job.get("error"))}</p>{acts}</div>'
    else:
        done = (f'<div class="card"><h2>できました</h2><p>v{e(job.get("version"))} として保存しました。'
                f'足した文 {e(job.get("bridge_count", 0))}・足りない所 {e(job.get("gap_count", 0))}。</p>'
                f'<div class="btnrow"><a class="btn primary" href="{e(surl(box))}">記事を読む</a></div></div>')
    return job_page(job, "下書きづくり", surl(box) if box else "/story", done)


# ---------------------------------------------------------------- 振り分け

def route_get(path: str, qs: dict):
    if path == "/tsubuyaki":
        return page_tsubuyaki(qs), "つぶやく", "tsubuyaki", ""
    if path == "/story":
        return page_index(), "下書き", "story", ""
    m = re.fullmatch(r"/story/jobs/(TJ\d+)", path)
    if m:
        page, head = page_job(S.JOB.get(m[1]))
        return page, "下書きづくり", "story", head
    m = re.fullmatch(r"/story/b/([^/]+)/bridge", path)
    if m:
        return page_bridge(unquote(m[1])), "Claude が足した文", "story", ""
    m = re.fullmatch(r"/story/b/([^/]+)", path)
    if m:
        name = unquote(m[1])
        return page_story(name, qs), name, "story", ""
    return None


def back(path: str) -> str:
    if path.startswith("/tsubuyaki"):
        return "/tsubuyaki"
    m = re.fullmatch(r"/story/b/([^/]+)(/.*)?", path)
    return f"/story/b/{m[1]}" if m else "/story"


def _start(h, name: str, token: str = "") -> None:
    p = S.prepare(name)
    jid = S.start(name, token or p["confirm_token"])
    h._redirect_to(f"/story/jobs/{jid}", "Claude に渡しました。終わるまでお待ちください（1〜数分）")


def post(h, path: str, form) -> bool:
    if path == "/tsubuyaki":
        box = form.get("box")
        k = BX.post(form.get("body", strip=False), box, form.get("section"), form.list("viewpoints") or None)
        h._redirect_to(qurl("/tsubuyaki", box=box), f"かけら {k['id']} を保存しました")
        return True
    if path == "/tsubuyaki/box":
        a = BX.create_box(form.get("name"))
        h._redirect_to(qurl("/tsubuyaki", box=a["name"]), f"ボックス「{a['name']}」を作りました")
        return True
    if path == "/tsubuyaki/move":
        to = form.get("to")
        moved = BX.move(form.list("ids"), to)
        h._redirect_to(qurl("/tsubuyaki", box=form.get("from")), f"{len(moved)} 件を「{to}」へ移しました")
        return True
    m = re.fullmatch(r"/story/jobs/(TJ\d+)/(accept|discard)", path)
    if m:
        job = S.JOB.get(m[1])
        if m[2] == "accept":
            meta = S.apply_conflicted(m[1])
            h._redirect_to(surl(job["box"]), f"v{meta['v']} として保存しました")
        else:
            S.discard(m[1])
            h._redirect_to(surl(job["box"]), "見送りました（下書きは変わっていません）")
        return True
    m = re.fullmatch(r"/story/b/([^/]+)/(generate|run|para|edit|settings|gap/(\d+))", path)
    if not m:
        return False
    name, act = unquote(m[1]), m[2]
    if act == "generate":
        if S.confirm_before_send():
            p = S.prepare(name)
            extra = ""
            if p["replaces"]:
                extra = ('<div class="note strong">いまの下書きは新しい版に置き換わります（前の版は版の履歴に残ります）。'
                         '前の版で自分が書いた・直した文は「本人が書いた文」として渡すので、新しい下書きにも使われます。</div>')
            h._page("渡す前の確認", confirm_page(
                "Claude に渡す前の確認",
                f"ボックス「{e(name)}」のかけら {len(p['kakera_ids'])} 個"
                + (f"と、本人が書いた文 {len(p['human'])} 個" if p["human"] else "") + "だけで、記事を1本書かせます。",
                p, surl(name, "/run"), surl(name), "", extra), "story")
        else:
            _start(h, name)
    elif act == "run":
        _start(h, name, form.get("confirm_token") or "-")
    elif act == "para":
        meta = S.replace_paragraph(name, int(form.get("index") or -1), form.get("text", strip=False))
        h._redirect_to(surl(name), f"v{meta['v']} として保存しました")
    elif act == "edit":
        meta = W.save_human_edit(name, form.get("body", strip=False), "全体を手で直した")
        h._redirect_to(surl(name), f"v{meta['v']} として保存しました")
    elif act == "settings":
        S.set_confirm_before_send(form.get("confirm") == "1")
        h._redirect_to(surl(name), "設定を保存しました")
    else:
        k = S.answer(name, int(m[3]), form.get("answer", strip=False))
        h._redirect_to(surl(name) + f"#q-{m[3]}", f"かけら {k['id']} として保存しました")
    return True
