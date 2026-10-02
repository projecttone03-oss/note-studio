"""機能ごとの画面（web_*.py）で共通に使う部品。web.py には振り分けだけを置く。"""
from __future__ import annotations

from typing import Tuple

from .web import e, lines_html, safe

JOB_STATUS = {"running": ("実行中", "b-st"), "done": ("完了", "b-ok"), "error": ("失敗", "b-strong"),
              "limit": ("利用上限", "b-strong"), "conflict": ("保留（本文が変わった）", "b-warm")}


def status_badge(status: str) -> str:
    label, cls = JOB_STATUS.get(status, (status, ""))
    return f'<span class="badge {cls}">{e(label)}</span>'


def confirm_page(title: str, what: str, p: dict, action: str, back: str, hidden: str = "", extra: str = "") -> str:
    """Claude に渡す前の確認画面（渡す全文を見せ、押したときだけ実行）。"""
    return f"""<p class="small"><a href="{e(back)}">戻る</a></p>
<h1>{e(title)}</h1>
<div class="card"><h2>渡す内容</h2><p>{what}</p>{extra}
<ul class="small"><li>ツールなし・Web検索なし・MCPなしの別セッションで起動し、指示と材料は標準入力で渡します。</li>
<li>起動時のツール一覧を確かめ、1つでもツールがあれば結果を捨てて止めます。</li>
<li>結果は「候補」や「下書き」として置くだけです。採用・公開は人が決めます。</li></ul>
<details><summary>渡す全文を見る（{p["chars"]}字）</summary><pre class="send">{e(p["prompt"])}</pre></details>
<form method="post" action="{e(action)}">{hidden}<input type="hidden" name="confirm_token" value="{e(p["confirm_token"])}">
<div class="btnrow"><button class="btn primary">この内容で渡す</button><a class="btn" href="{e(back)}">やめる</a></div></form></div>"""


def job_page(job: dict, title: str, back: str, done_html: str) -> Tuple[str, str]:
    """ジョブの状態画面。(本文, head)。実行中は5秒ごとに自動更新する。"""
    st = job.get("status")
    head = ""
    info = (f'<p class="small muted">開始 {e((job.get("created") or "").replace("T", " "))} ／ {status_badge(st)}'
            f'{" ／ モデル " + e(job.get("model")) if job.get("model") else ""}</p>')
    if st == "running":
        head = '<meta http-equiv="refresh" content="5">'
        body = ('<div class="card"><h2>Claude が考えています（1〜数分かかることがあります）</h2>'
                '<p>この画面は5秒ごとに自動で更新します。閉じても続けます。</p></div>')
    elif st == "done":
        body = done_html
    elif st == "limit":
        body = (f'<div class="card alert"><h2>Claude の利用上限に達しました</h2><p>{e(job.get("error"))}</p>'
                f'<div class="btnrow"><a class="btn" href="{e(back)}">戻る</a></div></div>')
    elif st == "conflict":
        body = done_html
    else:
        body = (f'<div class="card alert"><h2>うまくいきませんでした</h2><p>{lines_html(safe(job.get("error") or "理由は分かりませんでした。"))}</p>'
                f'<div class="btnrow"><a class="btn" href="{e(back)}">戻る</a></div></div>')
    return f"<h1>{e(title)} {e(job.get('id'))}</h1>{info}{body}", head


def flags_html(flags) -> str:
    return "".join(f'<div class="flag">⚠ {e(f)}</div>' for f in (flags or []))
