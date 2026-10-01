"""壁打ちセッションの画面（/kabeuchi）。起動・停止と、スマホから続ける方法の案内。"""
from __future__ import annotations

import re
import shlex
from urllib.parse import quote, unquote

from . import kabeuchi as KB
from .web import e

PREFIXES = ("/kabeuchi",)


def page_kabeuchi() -> str:
    rows = []
    has_tmux = bool(KB.tmux_path())
    for r in KB.overview():
        pf = r["preflight"]
        pf_html = (f'<span class="badge b-ok">確認済み</span><div class="small muted">{e(pf["at"].replace("T", " "))}・ツール {len(pf.get("tools") or [])} 個</div>'
                   if pf else '<span class="muted small">まだ</span>')
        state = '<span class="badge b-ok">起動中</span>' if r["running"] else '<span class="badge">停止</span>'
        url = "/kabeuchi/" + quote(r["article"], safe="")
        if r["running"]:
            act = (f'<form method="post" action="{e(url)}/stop" data-confirm="壁打ちセッションを止めます。よいですか？">'
                   f'<button class="btn sm ng">止める</button></form>')
        else:
            act = (f'<form method="post" action="{e(url)}/start"><button class="btn sm primary"'
                   f'{"" if has_tmux else " disabled"}>確認実行して起動</button></form>')
        rows.append(f'<tr><td><b>{e(r["article"])}</b><div class="small muted">スマホでの名前: {e(r["session"])}</div></td>'
                    f'<td>{state}</td><td>{pf_html}</td><td>{act}</td></tr>')
    table = (f'<div class="tablewrap"><table><tr><th>記事</th><th>状態</th><th>起動前の確認</th><th></th></tr>{"".join(rows)}</table></div>'
             if rows else '<p class="muted">まだ記事がありません。<a href="/articles">記事と区間</a>を登録してください。</p>')
    no_tmux = ("" if has_tmux else
               '<div class="note strong">この機械に tmux が見つからないため、画面からは起動できません。'
               '端末で <code>./nw kabeuchi start 記事名</code> を実行すると、その端末で起動します（VPS では tmux を入れてください）。</div>')
    return f"""<h1>壁打ち</h1>
<div class="note strong"><b>壁打ちは、スマホの Claude アプリから続ける「相談相手」です。本文は確定しません。</b><br>
<span class="small">本文の確定は「下書き」の画面で行います（かけらだけが材料）。会話で出た新しい事実は、自分でかけらに保存してください。</span></div>
{no_tmux}
<div class="card"><h2>記事ごとの壁打ちセッション</h2>{table}
<p class="small">「確認実行して起動」を押すと、起動と<b>同じフラグ・同じ材料ファイル</b>で一度だけ確認実行し、ツールが0個・MCPなしであることを
確かめてから起動します。確かめられなければ起動しません。</p></div>
<div class="card"><h2>スマホから続ける</h2><ol class="steps">
<li>上で起動する（記事ごとに1つ）。</li>
<li>スマホの Claude アプリを開き、Code の一覧から「<b>nw-記事名</b>」のセッションを選ぶ。</li>
<li>話しかける。音声入力は、スマホのキーボードのマイクを使えます。</li></ol></div>
<div class="card alert"><h2>保存について（必ずお読みください）</h2><ul>
<li>Remote Control 経由の会話は、<b>全文が Anthropic のサーバーに保存</b>されます（モデル改善への利用を許可していれば5年、していなければ30日）。
Anthropic アカウントの <b>Trusted Devices</b> を有効にしてください。詳しくは<a href="/guide">使い方（初回のお知らせ）</a>。</li>
<li>この機械にも会話の履歴が <code>~/.claude/projects/</code> に残ります。VPS では、このフォルダも暗号化フォルダ（gocryptfs）の中に
置いてください（手順は <code>notewriter/ops/README.md</code>）。</li>
<li>材料（かけら・下書き・文体ルール）は作業フォルダの中のファイルで渡し、コマンドラインには入れません。</li></ul></div>"""


def route_get(path: str, qs: dict):
    if path == "/kabeuchi":
        return page_kabeuchi(), "壁打ち", "kabeuchi", ""
    return None


def back(path: str) -> str:
    return "/kabeuchi"


def post(h, path: str, form) -> bool:
    m = re.fullmatch(r"/kabeuchi/([^/]+)/(start|stop)", path)
    if not m:
        return False
    name = unquote(m[1])
    if m[2] == "start":
        if not KB.tmux_path():
            raise ValueError("tmux がないため画面からは起動できません。端末で ./nw kabeuchi start "
                             + shlex.quote(name) + " を実行してください。")
        info = KB.start(name)
        h._redirect_to("/kabeuchi", f"確認実行（ツール {len(info['preflight']['tools'])} 個）のあと、"
                                    f"「{info['session']}」を起動しました。スマホの Claude アプリから開けます")
    else:
        ok = KB.stop(name)
        h._redirect_to("/kabeuchi", "止めました" if ok else "起動していませんでした")
    return True
