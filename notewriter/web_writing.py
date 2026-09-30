"""体験談の下書き（作成・改稿・版・文体ルール）の画面。web.py の Handler から呼ぶ。

Claude に渡す前には、渡す全文を表示して人が押したときだけ実行する（ネタ出し・リサーチと同じ）。
改稿は「提案」で止め、人が採用したときだけ本文に入れる。要確認の印は警告だけで、本文は書き換えない。
"""
from __future__ import annotations

import re
from typing import List, Tuple
from urllib.parse import quote, unquote

from . import writing as W
from .web import e, lines_html, qurl, safe

JOB_STATUS = {"running": ("書いています", "b-st"), "done": ("完了", "b-ok"), "error": ("失敗", "b-strong"),
              "limit": ("利用上限", "b-strong"), "conflict": ("保留（本文が変わった）", "b-warm")}


def aurl(name: str, rest: str = "") -> str:
    return f"/drafts/{quote(name, safe='')}{rest}"


def article_from(raw: str) -> str:
    return unquote(raw)


def kakera_badges(ids) -> str:
    if not ids:
        return '<span class="badge b-warm">根拠なし</span>'
    return "".join(f'<a class="badge b-vp" href="/kakera/{e(k)}">{e(k)}</a>' for k in ids)


def flags_html(flags) -> str:
    if not flags:
        return ""
    return "".join(f'<div class="flag">⚠ {e(f)}</div>' for f in flags)


# ---------------------------------------------------------------- 一覧

def page_drafts() -> str:
    rows = []
    for r in W.list_drafts():
        lt = r["latest"]
        state = (f'v{lt["v"]}・{e(W.SOURCES.get(lt["source"], lt["source"]))}<div class="small muted">'
                 f'{e(lt["created"].replace("T", " "))}</div>') if lt else '<span class="muted">まだありません</span>'
        warn = ('<span class="badge b-warm">ファイルが直接編集されています</span>'
                if lt and W.file_changed(r["article"]) else "")
        rows.append(f'<tr><td><a href="{e(aurl(r["article"]))}"><b>{e(r["article"])}</b></a>'
                    f'<div class="small muted">{e(r["series"])}／区間 {len(r["sections"])}</div>{warn}</td>'
                    f'<td>{state}</td><td class="num">{r["count"]}</td></tr>')
    table = (f'<div class="tablewrap"><table><tr><th>記事</th><th>最新の版</th><th class="num">版の数</th></tr>{"".join(rows)}</table></div>'
             if rows else '<p class="muted">まだ記事がありません。<a href="/articles">記事と区間を登録</a>して、かけらを区間に割り当ててください。</p>')
    running = W.running_job()
    run_html = (f'<div class="note">実行中: <a href="/drafts/jobs/{e(running["id"])}">{e(running["id"])}</a>'
                f'（{e(running.get("article"))}）</div>' if running else "")
    return f"""<h1>下書き</h1>
<div class="note strong"><b>本文は「かけら」に書いたことだけから作ります。</b><br>
<span class="small">足りない所は <code>[要追加：〜]</code> と残します。根拠のかけらにない数字・セリフ・カタカナ語には「要確認」の印を付けます（本文は書き換えません）。
それでも AI が知識や推測で書く可能性はゼロにできないので、公開前に必ずご自身で確かめてください。</span></div>
{run_html}
<div class="card"><h2>記事ごとの下書き</h2>{table}</div>
<div class="card"><h2>文体ルール集</h2><p class="small">下書きを作るたびに「書き方の参考」として渡します（事実の材料にはしません）。</p>
<a class="btn" href="/style">文体ルール集を開く</a></div>"""


# ---------------------------------------------------------------- 下書き本体

def _pending(name: str) -> str:
    items = []
    for j in W.list_jobs(name):
        if j.get("discarded") or j.get("applied_version"):
            continue
        if (j.get("kind") == "revise" and j.get("status") == "done") or j.get("status") == "conflict":
            what = f"段落 [{j.get('block')}] の書き直し" if j.get("kind") == "revise" else f"区間「{j.get('section')}」の作成結果"
            items.append(f'<li><a href="/drafts/jobs/{e(j["id"])}">{e(j["id"])}</a>: {e(what)}（未処理）</li>')
    return (f'<div class="card warm"><h2>未処理の提案</h2><ul>{"".join(items)}</ul>'
            f'<p class="small">採用するまで本文は変わりません。</p></div>') if items else ""


def page_draft(name: str) -> str:
    art = W._article(name)
    cur = W.current(name)
    blocks = W.current_blocks(name)
    changed = W.file_changed(name)
    head = (f'<p class="small muted">v{cur["meta"]["v"]}（{e(W.SOURCES.get(cur["meta"]["source"], ""))}・'
            f'{e(cur["meta"]["created"].replace("T", " "))}）／<a href="{e(aurl(name, "/versions"))}">版の一覧と差分</a></p>'
            if cur else '<p class="small muted">まだ下書きがありません。区間ごとに「この区間の下書きを作る」から始めます。</p>')
    banner = ""
    if changed:
        banner = (f'<div class="card alert"><h2>下書きファイルが直接編集されています</h2>'
                  f'<p>エディタなどで <code>{e(W.draft_path(name))}</code> が書き換えられ、まだ版になっていません。'
                  f'このままでは作成・修正を止めます（手直しが消えないように）。</p>'
                  f'<form method="post" action="{e(aurl(name, "/save-file"))}"><div class="btnrow">'
                  f'<button class="btn warm">ファイルの手直しを版として保存</button></div></form></div>')
    out: List[str] = []
    n_flags = 0
    for i, b in enumerate(blocks):
        if b["kind"] == "heading":
            if b["text"].startswith("## "):
                sec = b["text"][3:].strip()
                gen = ""
                if sec in art["sections"]:
                    gen = (f'<form method="post" action="{e(aurl(name, "/generate/confirm"))}" style="display:inline">'
                           f'<input type="hidden" name="section" value="{e(sec)}">'
                           f'<button class="btn sm">この区間の下書きを作る</button></form>')
                out.append(f'<h3 id="s-{i}" class="sechead">{e(b["text"][3:])} {gen}</h3>')
            else:
                out.append(f'<h2 class="ribbon">{e(b["text"].lstrip("#").strip())}</h2>')
            continue
        n_flags += len(b.get("flags") or [])
        who = {"ai": "AI", "human": "人"}.get(b.get("origin"), "")
        out.append(f'''<div class="blk {e(b.get("origin", ""))}" id="b-{i}">
<div class="bhead"><span class="badge">[{i}] {e(who)}</span>{kakera_badges(b.get("kakera"))}</div>
<div class="btext">{lines_html(b["text"])}</div>{flags_html(b.get("flags"))}
<details><summary>この段落を直す（提案だけ作る）</summary>
<form method="post" action="{e(aurl(name, "/revise/confirm"))}"><input type="hidden" name="block" value="{i}">
<textarea name="instruction" rows="2" placeholder="例: もっと短く。かけらのセリフをそのまま使って。" required></textarea>
<div class="btnrow"><button class="btn sm">確認画面へ</button></div></form></details></div>''')
    # まだ見出しのない区間（記事に区間を足したとき）
    present = {b["text"][3:].strip() for b in blocks if b["kind"] == "heading" and b["text"].startswith("## ")}
    for sec in art["sections"]:
        if sec not in present:
            out.append(f'<h3 class="sechead">{e(sec)} <form method="post" action="{e(aurl(name, "/generate/confirm"))}" style="display:inline">'
                       f'<input type="hidden" name="section" value="{e(sec)}"><button class="btn sm">この区間の下書きを作る</button></form></h3>')
    body_text = W.render(blocks)
    check = (f'<form method="post" action="/check"><input type="hidden" name="mode" value="drafts">'
             f'<input type="hidden" name="drafts" value="{e(W.draft_rel(name))}">'
             f'<button class="btn">コンプライアンスチェック</button></form>' if cur else "")
    return f"""<p class="small"><a href="/drafts">下書き一覧</a></p>
<h1>{e(name)}</h1>{head}{banner}{_pending(name)}
<div class="grid"><div>
<div class="card draft">{"".join(out)}</div>
<div class="card"><h2>全体を手で直す</h2>
<p class="small">直した内容は新しい版として残ります。AI が書いた段落を直すと、その組を文体学習の材料として記録します。</p>
<form method="post" action="{e(aurl(name, "/save"))}"><textarea name="body" class="tall" rows="18">{e(body_text)}</textarea>
<label class="f">メモ（任意）</label><input type="text" name="note" placeholder="例: 語尾を整えた">
<div class="btnrow"><button class="btn primary">手直しを版として保存</button></div></form></div>
</div><div>
<div class="card"><h2>確認すること</h2><p>要確認の印: <b>{n_flags}</b> 件</p>
<p class="small">印は機械的な目安です。見落としも誤検知もあります。根拠のかけらと照らして判断してください。</p>
<div class="btnrow">{check}<a class="btn" href="{e(aurl(name, "/versions"))}">版の一覧</a></div></div>
<div class="card"><h2>区間</h2><ol>{"".join(f"<li>{e(s)}</li>" for s in art["sections"])}</ol>
<p class="small"><a href="/coverage/{e(quote(name, safe=""))}">充足度を見る</a>（かけらが少ない区間は [要追加] が増えます）</p></div>
</div></div>"""


# ---------------------------------------------------------------- 送る前の確認

def page_confirm(p: dict) -> str:
    name = p["article"]
    if p["kind"] == "generate":
        what = f'区間「{e(p["section"])}」の下書きを作ります。'
        hidden = f'<input type="hidden" name="section" value="{e(p["section"])}">'
        action = aurl(name, "/generate/run")
        warn = ('<div class="note strong">この区間のいまの本文は置き換わります（前の版は残るので戻せます）。</div>'
                if p.get("replaces") else "")
    else:
        what = f'段落 [{p["block"]}] の書き直しを提案させます（採用するまで本文は変わりません）。'
        hidden = (f'<input type="hidden" name="block" value="{e(p["block"])}">'
                  f'<input type="hidden" name="instruction" value="{e(p["instruction"])}">')
        action = aurl(name, "/revise/run")
        warn = f'<div class="note"><b>直す段落</b><br>{lines_html(p["old_text"])}</div>'
    return f"""<p class="small"><a href="{e(aurl(name))}">{e(name)} に戻る</a></p>
<h1>Claude に渡す前の確認</h1>
<div class="card"><h2>渡す内容</h2><p>{what}</p>{warn}
<ul class="small"><li>材料のかけら: {kakera_badges(p["kakera_ids"])}（この区間のもの。「保留」は除く）</li>
<li>ツールなし・Web検索なし・MCPなしの別セッションで起動し、指示とかけらは標準入力で渡します。</li>
<li>起動時のツール一覧を確かめ、1つでもツールがあれば結果を捨てて止めます。</li></ul>
<details><summary>渡す全文を見る（{p["chars"]}字）</summary><pre class="send">{e(p["prompt"])}</pre></details>
<form method="post" action="{e(action)}">{hidden}<input type="hidden" name="confirm_token" value="{e(p["confirm_token"])}">
<div class="btnrow"><button class="btn primary">この内容で渡す</button><a class="btn" href="{e(aurl(name))}">やめる</a></div></form></div>"""


# ---------------------------------------------------------------- 実行と提案

def page_job(job: dict) -> Tuple[str, str]:
    name = job.get("article", "")
    label, cls = JOB_STATUS.get(job.get("status"), (job.get("status"), ""))
    head = ""
    info = (f'<p class="small muted"><a href="{e(aurl(name))}">{e(name)}</a> ／ 開始 {e((job.get("created") or "").replace("T", " "))}'
            f' ／ <span class="badge {cls}">{e(label)}</span></p>')
    st = job.get("status")
    if st == "running":
        head = '<meta http-equiv="refresh" content="5">'
        body = ('<div class="card"><h2>書いています（1〜数分かかることがあります）</h2>'
                '<p>この画面は5秒ごとに自動で更新します。閉じても続けます。</p></div>')
    elif st == "done" and job.get("kind") == "generate":
        body = (f'<div class="card"><h2>できました</h2><p>区間「{e(job.get("section"))}」を v{e(job.get("version"))} として保存しました。'
                f'要確認の印を見ながら、かけらと照らして確かめてください。</p>'
                f'<div class="btnrow"><a class="btn primary" href="{e(aurl(name))}">下書きを見る</a></div></div>')
    elif st == "done":
        prop = job.get("proposal") or {}
        done = job.get("applied_version") or job.get("discarded")
        state = (f'<p><b>採用しました（v{e(job.get("applied_version"))}）</b></p>' if job.get("applied_version")
                 else '<p><b>見送りました</b></p>' if job.get("discarded") else "")
        buttons = "" if done else (
            f'<div class="btnrow"><form method="post" action="/drafts/jobs/{e(job["id"])}/accept"><button class="btn primary">この提案を本文に入れる</button></form>'
            f'<form method="post" action="/drafts/jobs/{e(job["id"])}/discard"><button class="btn">見送る</button></form></div>')
        body = (f'<div class="card"><h2>書き直しの提案</h2><p class="small">指示: {e(job.get("instruction"))}</p>{state}'
                f'<div class="cols"><div><h3>いまの段落</h3><div class="blk del">{lines_html(job.get("old_text", ""))}</div></div>'
                f'<div><h3>提案</h3><div class="blk ins">{lines_html(prop.get("text", ""))}</div>'
                f'<div class="bhead">{kakera_badges(prop.get("kakera"))}</div>{flags_html(prop.get("flags"))}</div></div>'
                f'{buttons}</div>')
    elif st == "conflict":
        paras = "".join(f'<div class="blk ins">{lines_html(p.get("text", ""))}</div>' for p in job.get("paragraphs") or [])
        done = job.get("discarded")
        buttons = "" if done else (
            f'<div class="btnrow"><form method="post" action="/drafts/jobs/{e(job["id"])}/accept"><button class="btn warm">この結果で置き換える</button></form>'
            f'<form method="post" action="/drafts/jobs/{e(job["id"])}/discard"><button class="btn">見送る</button></form></div>')
        body = (f'<div class="card warm"><h2>保留にしました</h2><p>{e(job.get("error"))}</p>{paras}{buttons}</div>')
    elif st == "limit":
        body = (f'<div class="card alert"><h2>Claude の利用上限に達しました</h2><p>{e(job.get("error"))}</p>'
                f'<div class="btnrow"><a class="btn" href="{e(aurl(name))}">下書きに戻る</a></div></div>')
    else:
        body = (f'<div class="card alert"><h2>うまくいきませんでした</h2><p>{lines_html(safe(job.get("error") or "理由は分かりませんでした。"))}</p>'
                f'<p class="small">本文は変わっていません。</p><div class="btnrow"><a class="btn" href="{e(aurl(name))}">下書きに戻る</a></div></div>')
    return f"<h1>下書きの実行 {e(job.get('id'))}</h1>{info}{body}", head


# ---------------------------------------------------------------- 版

def page_versions(name: str, qs: dict) -> str:
    vs = W.versions(name)
    if not vs:
        return f'<p class="small"><a href="{e(aurl(name))}">{e(name)} に戻る</a></p><h1>版の一覧</h1><p class="muted">まだ版がありません。</p>'
    rows = "".join(
        f'<tr><td>v{m["v"]}</td><td>{e(m["created"].replace("T", " "))}</td><td>{e(W.SOURCES.get(m["source"], m["source"]))}</td>'
        f'<td>{e(m.get("note", ""))}{" ／来歴 " + e(m["provenance_id"]) if m.get("provenance_id") else ""}</td>'
        f'<td><form method="post" action="{e(aurl(name, "/restore"))}" data-confirm="v{m["v"]} の内容を新しい版として保存します。よいですか？">'
        f'<input type="hidden" name="v" value="{m["v"]}"><button class="btn sm">この版に戻す</button></form></td></tr>'
        for m in reversed(vs))
    last = vs[-1]["v"]
    a = int((qs.get("a") or [max(1, last - 1)])[0])
    b = int((qs.get("b") or [last])[0])
    opts = lambda sel: "".join(f'<option value="{m["v"]}"{" selected" if m["v"] == sel else ""}>v{m["v"]}</option>' for m in vs)
    diff_html = render_diff(W.diff(name, a, b)) if a != b else '<p class="muted">同じ版です。</p>'
    return f"""<p class="small"><a href="{e(aurl(name))}">{e(name)} に戻る</a></p>
<h1>版の一覧と差分</h1>
<div class="card"><h2>差分</h2><form method="get" action="{e(aurl(name, "/versions"))}" class="btnrow">
<select name="a" style="width:auto">{opts(a)}</select> → <select name="b" style="width:auto">{opts(b)}</select>
<button class="btn sm">比べる</button></form>{diff_html}</div>
<div class="card"><h2>版</h2><div class="tablewrap"><table><tr><th>版</th><th>日時</th><th>種類</th><th>メモ</th><th></th></tr>{rows}</table></div></div>"""


def render_diff(ops) -> str:
    out = []
    for o in ops:
        if o["op"] == "equal":
            if len(o["old"]) > 2:
                out.append(f'<p class="small muted">（変わらない段落 {len(o["old"])} 個）</p>')
            else:
                out.extend(f'<div class="blk same">{lines_html(t)}</div>' for t in o["old"])
            continue
        out.extend(f'<div class="blk del">{lines_html(t)}</div>' for t in o["old"])
        out.extend(f'<div class="blk ins">{lines_html(t)}</div>' for t in o["new"])
    return "".join(out) or '<p class="muted">違いはありません。</p>'


# ---------------------------------------------------------------- 文体ルール

def page_style() -> str:
    edits = W.list_edits()[-10:]
    ed = "".join(f'<div class="kcard"><div class="head">{e(x.get("article"))}／{e(x.get("section"))}・{e((x.get("at") or "").replace("T", " "))}</div>'
                 f'<div class="blk del">{lines_html(x.get("ai", ""))}</div><div class="blk ins">{lines_html(x.get("human", ""))}</div></div>'
                 for x in reversed(edits))
    return f"""<h1>文体ルール集</h1>
<div class="grid"><div><div class="card"><h2>ルール</h2>
<p class="small">下書きを作るたびに「書き方の参考」として Claude に渡します。事実の材料にはしません。1行1ルールで書いてください。</p>
<form method="post" action="/style"><textarea name="rules" class="tall" rows="16">{e(W.get_style())}</textarea>
<div class="btnrow"><button class="btn primary">保存</button></div></form></div></div>
<div><div class="card"><h2>最近の手直し</h2><p class="small">AI の段落（上）を人が直した結果（下）です。ルールを書く手がかりにしてください。
直しからルールの候補を出す機能は、次の段階で追加します。</p>{ed or '<p class="muted">まだ記録がありません。</p>'}</div></div></div>"""


# ---------------------------------------------------------------- 画面の振り分け

def route_get(path: str, qs: dict):
    """(page, title, active, head) か、対象外なら None。"""
    if path == "/drafts":
        return page_drafts(), "下書き", "drafts", ""
    if path == "/style":
        return page_style(), "文体ルール集", "drafts", ""
    if m := re.fullmatch(r"/drafts/jobs/(WJ\d+)", path):
        page, head = page_job(W.get_job(m[1]))
        return page, "下書きの実行", "drafts", head
    if m := re.fullmatch(r"/drafts/([^/]+)/versions", path):
        return page_versions(article_from(m[1]), qs), "版の一覧", "drafts", ""
    if m := re.fullmatch(r"/drafts/([^/]+)", path):
        name = article_from(m[1])
        return page_draft(name), name, "drafts", ""
    return None


def back(path: str) -> str:
    if m := re.fullmatch(r"/drafts/jobs/(WJ\d+)(/.*)?", path):
        return f"/drafts/jobs/{m[1]}"
    if m := re.fullmatch(r"/drafts/([^/]+)(/.*)?", path):
        return f"/drafts/{m[1]}"
    return "/style" if path == "/style" else "/drafts"


def post(h, path: str, form) -> bool:
    """h は web.Handler。処理したら True。"""
    if path == "/style":
        W.save_style(form.get("rules", strip=False))
        h._redirect_to("/style", "文体ルール集を保存しました")
        return True
    if m := re.fullmatch(r"/drafts/jobs/(WJ\d+)/(accept|discard)", path):
        jid, act = m[1], m[2]
        job = W.get_job(jid)
        if act == "discard":
            W.discard(jid)
            h._redirect_to(aurl(job["article"]), "見送りました（本文は変わっていません）")
        else:
            meta = W.apply_conflicted(jid) if job.get("status") == "conflict" else W.accept_revision(jid)
            h._redirect_to(aurl(job["article"]) + (f"#b-{job['block']}" if job.get("block") is not None else ""),
                           f"v{meta['v']} として保存しました")
        return True
    m = re.fullmatch(r"/drafts/([^/]+)/(generate/confirm|generate/run|revise/confirm|revise/run|save|save-file|restore)", path)
    if not m:
        return False
    name, act = article_from(m[1]), m[2]
    if act == "generate/confirm":
        h._page("渡す前の確認", page_confirm(W.prepare_generate(name, form.get("section"))), "drafts")
    elif act == "revise/confirm":
        h._page("渡す前の確認", page_confirm(W.prepare_revise(name, int(form.get("block") or -1), form.get("instruction"))), "drafts")
    elif act in ("generate/run", "revise/run"):
        p = (W.prepare_generate(name, form.get("section")) if act == "generate/run"
             else W.prepare_revise(name, int(form.get("block") or -1), form.get("instruction")))
        jid = W.start_job(p, form.get("confirm_token"))
        h._redirect_to(f"/drafts/jobs/{jid}", "Claude に渡しました。終わるまでお待ちください")
    elif act == "save":
        meta = W.save_human_edit(name, form.get("body", strip=False), form.get("note"))
        h._redirect_to(aurl(name), f"手直しを v{meta['v']} として保存しました")
    elif act == "save-file":
        meta = W.save_human_edit(name, W.draft_path(name).read_text(encoding="utf-8"), "ファイルを直接編集")
        h._redirect_to(aurl(name), f"ファイルの手直しを v{meta['v']} として保存しました")
    elif act == "restore":
        meta = W.restore(name, int(form.get("v") or 0))
        h._redirect_to(aurl(name), f"v{form.get('v')} の内容を v{meta['v']} として保存しました")
    return True
