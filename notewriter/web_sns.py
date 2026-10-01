"""SNS 導線の画面（/sns）。投稿文の下書き・Web Intent で投稿画面を開く・投稿の記録・健康診断。

アプリは投稿しない。「X で開く」は本文入力済みの投稿画面を開くだけで、送信ボタンは人が押す。
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote

from . import kakera as K
from . import sns as S
from . import writing as W
from .web import e, lines_html, qurl
from .web_ext import confirm_page, flags_html, job_page

PREFIXES = ("/sns",)


def _type_options(cur: str = "") -> str:
    return "".join(f'<option value="{e(t)}"{" selected" if t == cur else ""}>{e(t)}</option>' for t in S.config()["types"])


def _article_options(cur: str = "") -> str:
    names = [d["article"] for d in W.list_drafts() if d["latest"]]
    return "".join(f'<option value="{e(n)}"{" selected" if n == cur else ""}>{e(n)}</option>' for n in names)


def post_card(x: dict) -> str:
    cfg = S.config()
    plat = cfg["platforms"].get(x["platform"]) or {}
    label = plat.get("label", x["platform"])
    count = (f'{S.x_weight(x["text"])} / {plat["max_weight"]}' if plat.get("max_weight")
             else f'{len(x["text"])} / {plat.get("max_chars", "—")}字')
    stored = [f for f in x.get("flags", []) if not f.startswith("長すぎ")]
    flags = flags_html(stored + S.length_flags(x["platform"], x["text"]))
    posted = x["status"] == "投稿済み"
    try:
        url = S.intent_url(x["platform"], x["text"])
        open_btn = (f'<a class="btn primary" href="{e(url)}" target="_blank" rel="noopener noreferrer" '
                    f'title="本文入力済みの投稿画面を開きます。送信は自分で押します">{e(label)} で開く</a>')
    except ValueError as ex:
        open_btn = f'<span class="flag">⚠ {e(ex)}</span>'
    mark = ("" if posted else
            f'<form method="post" action="/sns/{e(x["id"])}/posted" class="inline"><input type="hidden" name="date" value="{date.today().isoformat()}">'
            f'<button class="btn">投稿した</button></form>')
    return f"""<div class="kcard sns-{e(x["platform"])}" id="{e(x["id"])}"><div class="head"><span class="kid">{e(x["id"])}</span>
<span class="badge b-tag">{e(label)}</span><span class="badge {"b-warm" if x["type"] == cfg["promo_type"] else "b-vp"}">{e(x["type"])}</span>
<span class="badge {"b-ok" if posted else "b-st"}">{e(x["status"])}{" " + e(x["posted_at"]) if posted else ""}</span>
{"<span class=small>" + e(x["article"]) + "</span>" if x.get("article") else ""}<span class="small muted">{e(count)}</span></div>
<p class="snstext">{lines_html(x["text"])}</p>{flags}
<p class="small muted">「開く」は投稿画面を出すだけです。送信ボタンは自分で押し、そのあと「投稿した」を押します。</p>
<div class="btnrow">{open_btn}{mark}</div>
<details><summary>直す・削除</summary><form method="post" action="/sns/{e(x["id"])}">
<textarea name="text" rows="5">{e(x["text"])}</textarea><label class="f">型</label><select name="type">{_type_options(x["type"])}</select>
<div class="btnrow"><button class="btn sm">保存</button>
<button class="btn sm ng" formaction="/sns/{e(x["id"])}/delete" formnovalidate>削除</button></div></form></details></div>"""


def page_sns(qs: dict) -> str:
    cfg = S.config()
    show = (qs.get("show") or ["下書き"])[0]
    art = (qs.get("article") or [""])[0]
    pr = S.promo_ratio()
    warn = ""
    if pr["over"]:
        warn = (f'<div class="card alert"><h2>直接宣伝が多めです</h2><p>直近{pr["days"]}日の投稿 {pr["total"]} 件のうち、直接宣伝が {pr["promo"]} 件'
                f'（{pr["ratio"] * 100:.0f}%）です。目安は {pr["max"] * 100:.0f}% 以下。「好きになってもらう」「知ってもらう」投稿を増やすと、宣伝が届きやすくなります。</p></div>')
    unverified = ("" if cfg.get("intents_verified") else
                  '<div class="note strong">Web Intent（X・Threads の投稿画面を開くリンク）は、まだ実機で確かめていません。'
                  'スマホと PC で一度開いてみて、問題なければ <code>config/sns.json</code> の intents_verified を true にしてください。</div>')
    due = S.health_due()
    due_html = (f'<div class="note">今月の健康診断がまだです（{e("、".join(due))}）。<a href="/sns/health">記録する</a></div>' if due else "")
    running = S.JOB.running()
    run = f'<div class="note">実行中: <a href="/sns/jobs/{e(running["id"])}">{e(running["id"])}</a></div>' if running else ""
    tabs = " ".join(f'<a class="badge {"b-vp" if show == s else ""}" href="{e(qurl("/sns", show=s))}">{e(s)}</a>' for s in S.STATUSES + ("all",))
    posts = "".join(post_card(x) for x in S.list_posts("" if show == "all" else show)) or '<p class="muted">ありません。</p>'
    bt = "".join(f'<span class="badge">{e(t)} {n}</span>' for t, n in pr["by_type"].items())
    plats = "".join(f'<option value="{e(k)}">{e(v.get("label", k))}</option>' for k, v in cfg["platforms"].items())
    arts = _article_options(art)
    gen = (f'<form method="post" action="/sns/confirm"><label class="f">記事</label><select name="article">{arts}</select>'
           f'<label class="f">型</label><select name="type">{_type_options()}</select>'
           f'<div class="btnrow"><button class="btn primary">確認画面へ</button></div></form>'
           if arts else '<p class="muted">下書きのある記事がありません。</p>')
    return f"""<h1>SNS</h1>
<div class="note"><b>アプリは投稿しません。</b>「X で開く」「Threads で開く」は本文入力済みの投稿画面を開くだけです。送信ボタンは自分で押し、そのあと「投稿した」を押して記録します。</div>
{unverified}{warn}{due_html}{run}
<div class="grid"><div>
<div class="card"><h2>投稿</h2><p>{tabs}</p>{posts}</div></div>
<div><div class="card warm"><h2>投稿文を作る</h2><p class="small">記事のタイトルと<b>無料エリアだけ</b>を、ツールなしの Claude に渡して X・Threads 向けの下書きを作ります（有料部分・かけらは渡しません）。</p>{gen}</div>
<div class="card"><h2>自分で書く</h2><form method="post" action="/sns/new"><select name="platform">{plats}</select>
<label class="f">型</label><select name="type">{_type_options()}</select><textarea name="text" rows="4" required></textarea>
<div class="btnrow"><button class="btn">下書きとして保存</button></div></form></div>
<div class="card"><h2>直近{pr["days"]}日の型</h2><p>{bt}</p><p class="small">投稿した {pr["total"]} 件のうち直接宣伝 {pr["promo"]} 件（目安 {pr["max"] * 100:.0f}% 以下）</p>
<a class="btn" href="/sns/health">健康診断（月1）</a></div></div></div>"""


def page_health() -> str:
    cfg = S.config()
    month = date.today().strftime("%Y-%m")
    fields = "".join(f'<label class="f">{e(label)}</label><input type="number" name="{e(key)}" min="0" inputmode="numeric">'
                     for key, label in cfg["health_fields"])
    plats = "".join(f'<option value="{e(k)}">{e(v.get("label", k))}</option>' for k, v in cfg["platforms"].items())
    heads = "".join(f"<th class=num>{e(label)}</th>" for _, label in cfg["health_fields"])
    rows = "".join(f'<tr><td>{e(x["month"])}</td><td>{e((cfg["platforms"].get(x["platform"]) or {}).get("label", x["platform"]))}</td>'
                   + "".join(f'<td class="num">{"—" if x.get(k) is None else e(x[k])}</td>' for k, _ in cfg["health_fields"]) + "</tr>"
                   for x in S.list_health())
    return f"""<p class="small"><a href="/sns">SNS</a></p><h1>健康診断（月1回）</h1>
<div class="note">月に1回、各 SNS の管理画面で見た数を手で入れます（アプリは SNS に接続しません）。同じ月・同じ SNS は上書きします。</div>
<div class="grid"><div><div class="card"><h2>これまでの記録</h2><div class="tablewrap"><table><tr><th>月</th><th>SNS</th>{heads}</tr>{rows}</table></div>
{"" if rows else '<p class="muted">まだありません。</p>'}</div></div>
<div><div class="card warm"><h2>記録する</h2><form method="post" action="/sns/health">
<label class="f">月</label><input type="text" name="month" value="{month}" pattern="\\d{{4}}-\\d{{2}}">
<label class="f">SNS</label><select name="platform">{plats}</select>{fields}
<div class="btnrow"><button class="btn primary">保存</button></div></form></div></div></div>"""


def page_job(job: dict):
    added = job.get("added") or []
    done = (f'<div class="card"><h2>できました</h2><p>下書きを {len(added)} 件作りました。中身を確かめ、直してから投稿画面を開いてください。</p>'
            f'<div class="btnrow"><a class="btn primary" href="/sns">投稿の一覧へ</a></div></div>')
    return job_page(job, "投稿文づくり", "/sns", done)


def publish_card(name: str, text: str) -> str:
    return (f'<div class="card"><h2>SNS で知らせる</h2><p class="small">この記事の無料エリアから、X・Threads 向けの投稿文の下書きを作れます。</p>'
            f'<a class="btn" href="{e(qurl("/sns", article=name))}">SNS の画面へ</a></div>')


def publish_post(h, path: str, form) -> bool:
    return False


def route_get(path: str, qs: dict):
    if path == "/sns":
        return page_sns(qs), "SNS", "sns", ""
    if path == "/sns/health":
        return page_health(), "健康診断", "sns", ""
    m = re.fullmatch(r"/sns/jobs/(XJ\d+)", path)
    if m:
        page, head = page_job(S.JOB.get(m[1]))
        return page, "投稿文づくり", "sns", head
    return None


def back(path: str) -> str:
    return "/sns/health" if path == "/sns/health" else "/sns"


def post(h, path: str, form) -> bool:
    if path == "/sns/confirm":
        p = S.prepare(form.get("article"), form.get("type"))
        hidden = f'<input type="hidden" name="article" value="{e(form.get("article"))}"><input type="hidden" name="type" value="{e(form.get("type"))}">'
        h._page("渡す前の確認", confirm_page("Claude に渡す前の確認",
                                         f'記事「{e(form.get("article"))}」の無料エリアから、型「{e(form.get("type"))}」の投稿文を作らせます。',
                                         p, "/sns/run", "/sns", hidden), "sns")
    elif path == "/sns/run":
        jid = S.start(form.get("article"), form.get("type"), form.get("confirm_token"))
        h._redirect_to(f"/sns/jobs/{jid}", "Claude に渡しました。終わるまでお待ちください")
    elif path == "/sns/new":
        x = S.add_post(form.get("platform"), form.get("type"), form.get("text", strip=False))
        h._redirect_to(f"/sns#{x['id']}", f"{x['id']} を下書きとして保存しました")
    elif path == "/sns/health":
        cfg = S.config()
        rec = S.save_health(form.get("month"), form.get("platform"), {k: form.get(k) for k, _ in cfg["health_fields"]})
        h._redirect_to("/sns/health", f"{rec['month']} の記録を保存しました")
    else:
        m = re.fullmatch(r"/sns/(S\d+)(/posted|/delete)?", path)
        if not m:
            return False
        if m[2] == "/posted":
            S.mark_posted(m[1], form.get("date"))
            h._redirect_to(f"/sns#{m[1]}", "投稿済みとして記録しました")
        elif m[2] == "/delete":
            S.delete_post(m[1])
            h._redirect_to("/sns", f"{m[1]} を削除しました")
        else:
            S.update_post(m[1], form.get("text", strip=False), form.get("type"))
            h._redirect_to(f"/sns#{m[1]}", "保存しました")
    return True
