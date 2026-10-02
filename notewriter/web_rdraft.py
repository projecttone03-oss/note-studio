"""リサーチ型記事の下書きの画面（/rdraft）。資料を選ぶ → 渡す全文の確認 → 実行 → 下書きの画面へ。"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote

from . import research as R
from . import research_writing as RW
from .web import e
from .web_ext import confirm_page, job_page

PREFIXES = ("/rdraft",)


def rurl(name: str, rest: str = "") -> str:
    return "/rdraft/" + quote(name, safe="") + rest


def durl(name: str) -> str:
    return "/drafts/" + quote(name, safe="")


def page_select(name: str) -> str:
    ms = R.list_materials(name)
    fresh = int(R.load_config().get("freshness_days") or 365)
    rows = []
    for m in ms:
        st = (f'<span class="badge b-strong">鮮度切れ（{e(m["age_days"])}日前）</span>' if m["stale"]
              else f'<span class="badge b-ok">{e(m["age_days"])}日前</span>' if m["age_days"] is not None else "")
        rows.append(f'<label class="opt"><input type="checkbox" name="ids" value="{e(m["id"])}" checked>'
                    f'<span><b>{e(m["id"])}</b> {e(m["title"])}<br><span class="small muted">調べた日 {e(m["researched_at"])}・'
                    f'出典 {len(m["sources"])} 件</span> {st}</span></label>')
    body = ("".join(rows) if rows else
            f'<p class="muted">この記事のリサーチ資料がありません。<a href="{e("/research/materials?article=" + quote(name, safe=""))}">リサーチ資料</a>'
            f'で記事を選んで取り込んでください。</p>')
    btn = '<div class="btnrow sticky"><button class="btn primary">確認画面へ</button></div>' if rows else ""
    return f"""<p class="small"><a href="{e(durl(name))}">{e(name)} の下書き</a></p>
<h1>資料から下書きを作る</h1>
<div class="note strong"><b>本文は、選んだリサーチ資料だけから作ります。</b><br>
<span class="small">資料に見当たらない数字・カギかっこ・カタカナ語には「要確認」の印を付けます。
調べてから {fresh} 日を超えた資料（鮮度切れ）を根拠にした段落にも印を付けます。本文は書き換えません。</span></div>
<form method="post" action="{e(rurl(name, "/confirm"))}"><div class="card"><h2>使う資料を選ぶ</h2>{body}</div>{btn}</form>"""


def page_job(job: dict):
    name = job.get("article", "")
    if job.get("status") == "conflict":
        acts = "" if job.get("discarded") else (
            f'<div class="btnrow"><form method="post" action="/rdraft/jobs/{e(job["id"])}/accept"><button class="btn warm">この結果で置き換える</button></form>'
            f'<form method="post" action="/rdraft/jobs/{e(job["id"])}/discard"><button class="btn">見送る</button></form></div>')
        done = f'<div class="card warm"><h2>保留にしました</h2><p>{e(job.get("error"))}</p>{acts}</div>'
    else:
        done = (f'<div class="card"><h2>できました</h2><p>v{e(job.get("version"))} として保存しました。'
                f'要確認・鮮度切れの印を見ながら、資料と照らして確かめてください。</p>'
                f'<div class="btnrow"><a class="btn primary" href="{e(durl(name))}">下書きを見る</a></div></div>')
    return job_page(job, "資料からの下書き", durl(name), done)


def route_get(path: str, qs: dict):
    m = re.fullmatch(r"/rdraft/jobs/(RJ\d+)", path)
    if m:
        page, head = page_job(RW.JOB.get(m[1]))
        return page, "資料からの下書き", "drafts", head
    m = re.fullmatch(r"/rdraft/([^/]+)", path)
    if m:
        name = unquote(m[1])
        RW.W._article(name)
        return page_select(name), "資料から下書きを作る", "drafts", ""
    return None


def back(path: str) -> str:
    m = re.fullmatch(r"/rdraft/([^/]+)(/.*)?", path)
    return f"/rdraft/{m[1]}" if m and m[1] != "jobs" else "/drafts"


def post(h, path: str, form) -> bool:
    m = re.fullmatch(r"/rdraft/jobs/(RJ\d+)/(accept|discard)", path)
    if m:
        job = RW.JOB.get(m[1])
        if m[2] == "accept":
            meta = RW.apply_conflicted(m[1])
            h._redirect_to(durl(job["article"]), f"v{meta['v']} として保存しました")
        else:
            RW.discard(m[1])
            h._redirect_to(durl(job["article"]), "見送りました（本文は変わっていません）")
        return True
    m = re.fullmatch(r"/rdraft/([^/]+)/(confirm|run)", path)
    if not m:
        return False
    name, ids = unquote(m[1]), form.list("ids")
    if not ids:
        raise ValueError("資料を1つ以上選んでください。")
    if m[2] == "confirm":
        p = RW.prepare(name, ids)
        extra = ""
        if p["stale"]:
            extra += ('<div class="note strong">鮮度切れの資料があります: '
                      + "、".join(f'{e(s["id"])}（{e(s["researched_at"])}）' for s in p["stale"])
                      + '。最新の情報で調べ直すことをおすすめします。</div>')
        if p["replaces"]:
            extra += '<div class="note strong">いまの下書きの本文は置き換わります（前の版は残るので戻せます）。</div>'
        hidden = "".join(f'<input type="hidden" name="ids" value="{e(i)}">' for i in p["material_ids"])
        h._page("渡す前の確認", confirm_page("Claude に渡す前の確認",
                                         f"記事「{e(name)}」の比較表と下書きを、資料 {e('、'.join(p['material_ids']))} だけから作らせます。",
                                         p, rurl(name, "/run"), rurl(name), hidden, extra), "drafts")
    else:
        jid = RW.start(name, ids, form.get("confirm_token"))
        h._redirect_to(f"/rdraft/jobs/{jid}", "Claude に渡しました。終わるまでお待ちください")
    return True
