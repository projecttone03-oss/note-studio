"""文体ルールの候補の画面（/style/candidates）。候補は人が採用したときだけ文体ルール集に入る。"""
from __future__ import annotations

import re

from . import style_learn as S
from .web import e, lines_html
from .web_ext import confirm_page, flags_html, job_page

PREFIXES = ("/style/candidates", "/style/jobs")
STATUS_CLASS = {"未検討": "b-st", "採用": "b-ok", "却下": ""}


def source_label(src: str) -> str:
    if src.startswith("book:"):
        return f"参考書籍 {src[5:]}"
    return "手直しの記録"


def candidate_card(c: dict) -> str:
    actions = ""
    if c["status"] == "未検討":
        actions = (f'<form method="post" action="/style/candidates/{e(c["id"])}/adopt">'
                   f'<label class="f">ルールの文（直してから採用できます）</label>'
                   f'<textarea name="rule" rows="2">{e(c["rule"])}</textarea>'
                   f'<div class="btnrow"><button class="btn primary sm">採用して文体ルール集に追記</button>'
                   f'<button class="btn sm" formaction="/style/candidates/{e(c["id"])}/reject">却下</button></div></form>')
    refs = f'<span class="small muted">根拠: {e("、".join(c.get("refs") or []))}</span>' if c.get("refs") else ""
    return (f'<div class="kcard" id="{e(c["id"])}"><div class="head"><span class="kid">{e(c["id"])}</span>'
            f'<span class="badge {STATUS_CLASS.get(c["status"], "")}">{e(c["status"])}</span>'
            f'<span class="badge b-tag">{e(source_label(c.get("source", "")))}</span>{refs}</div>'
            f'<p><b>{e(c["rule"])}</b></p><p class="small muted">{lines_html(c.get("reason", ""))}</p>'
            f'{flags_html(c.get("flags"))}{actions}</div>')


def page_candidates(qs: dict) -> str:
    show = (qs.get("show") or ["未検討"])[0]
    items = S.list_candidates("" if show == "all" else show)
    counts = S.counts()
    tabs = " ".join(f'<a class="badge {"b-vp" if show == s else ""}" href="/style/candidates?show={e(s)}">{e(s)} {counts.get(s, 0)}</a>'
                    for s in S.STATUSES) + f' <a class="badge {"b-vp" if show == "all" else ""}" href="/style/candidates?show=all">すべて</a>'
    running = S.JOB.running()
    run = (f'<div class="note">実行中: <a href="/style/jobs/{e(running["id"])}">{e(running["id"])}</a></div>' if running else "")
    cards = "".join(candidate_card(c) for c in items) or '<p class="muted">ここに出す候補はありません。</p>'
    return f"""<p class="small"><a href="/style">文体ルール集</a></p>
<h1>文体ルールの候補</h1>
<div class="note"><b>候補は自動では反映しません。</b>採用したものだけが文体ルール集に1行で追記されます（あとから自由に直せます）。<br>
<span class="small">材料の文章がそのまま長く入った候補には印を付けます。自分の言葉に直してから採用してください。</span></div>
{run}
<div class="card"><h2>手直しの記録から候補を出す</h2>
<p class="small">AI の段落を人が直した記録（新しいものから最大 {e(S.config()["edits_max"])} 組）を、ツールなしの Claude に渡します。</p>
<form method="post" action="/style/candidates/confirm"><button class="btn primary">確認画面へ</button></form>
<p class="small"><a href="/books">参考書籍から候補を出す</a></p></div>
<div class="card"><h2>候補</h2><p>{tabs}</p>{cards}</div>"""


def page_job(job: dict):
    added = job.get("added") or []
    done = (f'<div class="card"><h2>できました</h2><p>候補を {len(added)} 件追加しました（同じ内容・既存のルールと同じものは除きました）。</p>'
            f'<div class="btnrow"><a class="btn primary" href="/style/candidates">候補を見る</a></div></div>')
    return job_page(job, "文体ルール候補の実行", "/style/candidates", done)


def route_get(path: str, qs: dict):
    if path == "/style/candidates":
        return page_candidates(qs), "文体ルールの候補", "drafts", ""
    m = re.fullmatch(r"/style/jobs/(SJ\d+)", path)
    if m:
        page, head = page_job(S.JOB.get(m[1]))
        return page, "文体ルール候補の実行", "drafts", head
    return None


def back(path: str) -> str:
    return "/style/candidates"


def post(h, path: str, form) -> bool:
    if path == "/style/candidates/confirm":
        p = S.prepare_from_edits()
        h._page("渡す前の確認", confirm_page("Claude に渡す前の確認", f"手直しの記録 {p['count']} 組から、文体ルールの候補を出させます。",
                                          p, "/style/candidates/run", "/style/candidates"), "drafts")
        return True
    if path == "/style/candidates/run":
        jid = S.start_from_edits(form.get("confirm_token"))
        h._redirect_to(f"/style/jobs/{jid}", "Claude に渡しました。終わるまでお待ちください")
        return True
    m = re.fullmatch(r"/style/candidates/(SC\d+)/(adopt|reject)", path)
    if m:
        if m[2] == "adopt":
            S.adopt(m[1], form.get("rule"))
            h._redirect_to("/style/candidates", f"{m[1]} を採用して文体ルール集に追記しました")
        else:
            S.reject(m[1])
            h._redirect_to("/style/candidates", f"{m[1]} を却下しました")
        return True
    return False
