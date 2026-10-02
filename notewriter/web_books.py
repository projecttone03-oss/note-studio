"""参考書籍の画面（/books）。PDF を取り込み、ページを選んで文体ルールの候補を出させる（採用は /style/candidates で人が行う）。"""
from __future__ import annotations

import re

from . import books as B
from .web import e
from .web_ext import confirm_page, job_page

PREFIXES = ("/books",)


def page_books() -> str:
    rows = "".join(f'<a class="pick-row" href="/books/{e(b["id"])}"><span><b>{e(b["title"])}</b><br>'
                   f'<span class="small muted">{e(b["id"])}・{b["pages"]}ページ・{e(b["extractor"])}で取り出し</span></span>'
                   f'<span class="badge b-vp">ページを選ぶ</span></a>' for b in B.list_books())
    ex = B.extractor()
    tool = ({"pdftool": "Mac の tools/pdftool", "pdftotext": "pdftotext"}.get(ex) if ex else "")
    tool_html = (f'<p class="small">PDF の文字は {e(tool)} で取り出します。</p>' if tool else
                 '<div class="note strong">PDF の文字を取り出す道具が見つかりません。Mac では <code>swiftc -O tools/pdftool.swift -o tools/pdftool</code> を一度実行、'
                 'VPS では <code>sudo apt install poppler-utils</code>。テキスト（.txt）なら今すぐ取り込めます。</div>')
    return f"""<p class="small"><a href="/style/candidates">文体ルールの候補</a></p>
<h1>参考書籍</h1>
<div class="note"><b>書籍は「書き方の手法」の参考にだけ使います。</b>候補は手法の要約にとどめ、本文と長く同じ文が続く候補には印を付けて、
直すまで採用できないようにします。書籍のファイルは作業フォルダの中だけに置きます。</div>
<div class="grid"><div><div class="card"><h2>取り込んだ書籍</h2><div class="stack">{rows or '<p class="muted">まだありません。</p>'}</div></div></div>
<div><div class="card warm"><h2>書籍を取り込む</h2>{tool_html}
<form method="post" action="/books/upload" enctype="multipart/form-data">
<label class="drop">ここに PDF をドラッグ&ドロップ<br><span class="small">（または押して選ぶ。OCR 済みの PDF か .txt）</span>
<input type="file" name="file" accept=".pdf,.txt" data-drop required><span class="dropnames"></span></label>
<label class="f">書名（任意）</label><input type="text" name="title">
<div class="btnrow"><button class="btn primary">取り込む</button></div></form></div></div></div>"""


def page_book(bid: str, qs: dict) -> str:
    b = B.get_book(bid)
    q = (qs.get("q") or [""])[0]
    hits = ""
    if q:
        found = B.find(bid, q.split())
        hits = ("<ul class='small'>" + "".join(f"<li><b>p.{p}</b> …{e(t)}…</li>" for p, t in found) + "</ul>") if found else "<p class='muted'>見つかりませんでした。</p>"
    return f"""<p class="small"><a href="/books">参考書籍</a></p>
<h1>{e(b["title"])}</h1><p class="small muted">{e(b["id"])}・{b["pages"]}ページ・{b["chars"]}字</p>
<div class="grid"><div><div class="card"><h2>ページを選んで候補を出す</h2>
<form method="post" action="/books/{e(bid)}/confirm"><div class="cols">
<div><label class="f">はじめのページ</label><input type="number" name="start" min="1" max="{b["pages"]}" value="1" required></div>
<div><label class="f">おわりのページ</label><input type="number" name="end" min="1" max="{b["pages"]}" value="{min(b["pages"], 5)}" required></div></div>
<label class="f">特に知りたいこと（任意）</label><input type="text" name="focus" placeholder="例: 体験談の書き出し・会話文の入れ方">
<div class="btnrow"><button class="btn primary">確認画面へ</button></div></form></div></div>
<div><div class="card"><h2>ページを探す</h2><form method="get" action="/books/{e(bid)}"><input type="search" name="q" value="{e(q)}" placeholder="キーワード（空白区切り）">
<div class="btnrow"><button class="btn sm">探す</button></div></form>{hits}</div>
<div class="card"><form method="post" action="/books/{e(bid)}/delete" data-confirm="この書籍を作業フォルダから削除します。よいですか？">
<button class="btn ng sm">この書籍を削除</button></form></div></div></div>"""


def page_job(job: dict):
    added = job.get("added") or []
    done = (f'<div class="card"><h2>できました</h2><p>候補を {len(added)} 件追加しました。本文と長く同じ文が続く候補には印が付いています。</p>'
            f'<div class="btnrow"><a class="btn primary" href="/style/candidates">候補を確かめて採用・却下する</a></div></div>')
    return job_page(job, "参考書籍の分析", "/books", done)


def route_get(path: str, qs: dict):
    if path == "/books":
        return page_books(), "参考書籍", "drafts", ""
    m = re.fullmatch(r"/books/jobs/(BJ\d+)", path)
    if m:
        page, head = page_job(B.JOB.get(m[1]))
        return page, "参考書籍の分析", "drafts", head
    m = re.fullmatch(r"/books/(B\d+)", path)
    if m:
        return page_book(m[1], qs), "参考書籍", "drafts", ""
    return None


def back(path: str) -> str:
    m = re.fullmatch(r"/books/(B\d+)(/.*)?", path)
    return f"/books/{m[1]}" if m else "/books"


def post(h, path: str, form) -> bool:
    if path == "/books/upload":
        ups = [(fn, data) for _, fn, data in form.files if data]
        if not ups:
            raise ValueError("ファイルを選んでください。")
        b = B.add_book(ups[0][0], ups[0][1], form.get("title"))
        h._redirect_to(f"/books/{b['id']}", f"「{b['title']}」を取り込みました（{b['pages']}ページ）")
        return True
    m = re.fullmatch(r"/books/(B\d+)/(confirm|run|delete)", path)
    if not m:
        return False
    bid = m[1]
    if m[2] == "delete":
        B.delete_book(bid)
        h._redirect_to("/books", f"{bid} を削除しました")
        return True
    try:
        s, t = int(form.get("start") or 0), int(form.get("end") or 0)
    except ValueError:
        raise ValueError("ページは数字で入れてください。")
    if m[2] == "confirm":
        p = B.prepare(bid, s, t, form.get("focus"))
        hidden = (f'<input type="hidden" name="start" value="{s}"><input type="hidden" name="end" value="{t}">'
                  f'<input type="hidden" name="focus" value="{e(form.get("focus"))}">')
        h._page("渡す前の確認", confirm_page("Claude に渡す前の確認", f"p.{s}〜{t} の文字（アプリが取り出したもの）から、書き方の手法を候補として出させます。",
                                         p, f"/books/{bid}/run", f"/books/{bid}", hidden), "drafts")
    else:
        jid = B.start(bid, s, t, form.get("focus"), form.get("confirm_token"))
        h._redirect_to(f"/books/jobs/{jid}", "Claude に渡しました。終わるまでお待ちください")
    return True
