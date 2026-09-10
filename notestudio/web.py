"""確認・判断用のローカルWeb画面（標準ライブラリのみ）。

127.0.0.1 のみで待ち受け、POSTは同一オリジンからのものだけ受け付ける。
"""
from __future__ import annotations

import html
import re
import sqlite3
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from . import db, export, models
from .schemas import DIFF_TYPES, SCORE_KEYS

SOURCE_LABEL = {"official": "公的", "primary": "一次情報", "news": "ニュース", "qa": "Q&A", "sns": "SNS",
                "note": "note", "blog": "ブログ", "book": "書籍", "other": "その他"}
CONF_LABEL = {"high": "確度 高", "medium": "確度 中", "low": "確度 低"}
EFFORT_LABEL = {"low": "手間 小", "medium": "手間 中", "high": "手間 大"}
STAGES = ["需要リサーチ", "企画・差別化", "リサーチ＆執筆", "レビュー", "公開準備", "分析・改善"]
READY_STAGES = 2


def e(v) -> str:
    return html.escape("" if v is None else str(v))


def source_link(url: str, title: str) -> str:
    """出典へのリンク。書籍（book:書名#該当箇所）はリンクにせず該当箇所を添える。"""
    if url.startswith("book:"):
        loc = url.split("#", 1)[1] if "#" in url else ""
        return f"『{e(title)}』{e(loc)}"
    return f'<a href="{e(url)}" target="_blank" rel="noopener noreferrer">{e(title)}</a>'


def price_text(lo, hi) -> str:
    return export._price(lo, hi)


CSS = """
:root{--ink:#1d2330;--sub:#5b6474;--line:#e3e6ec;--bg:#f6f7f9;--card:#fff;--accent:#2f5bd3;--accent-weak:#eaf0ff;
--ok:#1a7f4b;--ok-weak:#e6f5ec;--warn:#9a6200;--warn-weak:#fff4de;--ng:#b3261e;--ng-weak:#fdeceb;--paid:#6b3fd4;--paid-weak:#f1ebff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.7 -apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header{background:var(--card);border-bottom:1px solid var(--line)}
.bar{max-width:1180px;margin:0 auto;padding:12px 24px;display:flex;align-items:center;gap:28px}
.brand{font-weight:700;font-size:16px;color:var(--ink)}
nav a{color:var(--sub);margin-right:18px;font-weight:500}nav a.on{color:var(--ink);border-bottom:2px solid var(--accent);padding-bottom:4px}
.steps{max-width:1180px;margin:0 auto;padding:0 24px 10px;display:flex;gap:6px;flex-wrap:wrap}
.step{font-size:12px;padding:2px 10px;border-radius:99px;background:var(--bg);color:#9aa1ad}
.step.ready{background:var(--accent-weak);color:var(--accent);font-weight:600}
main{max-width:1180px;margin:0 auto;padding:24px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:0 0 12px}h3{font-size:14px;margin:16px 0 6px}
.muted{color:var(--sub)}.small{font-size:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:16px;align-items:start}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
.badge{display:inline-block;font-size:11px;font-weight:600;padding:1px 8px;border-radius:99px;background:var(--bg);color:var(--sub);white-space:nowrap}
.b-candidate,.b-draft{background:var(--warn-weak);color:var(--warn)}
.b-selected,.b-planning,.b-revision_requested{background:var(--accent-weak);color:var(--accent)}
.b-planned,.b-approved,.b-ok{background:var(--ok-weak);color:var(--ok)}
.b-rejected{background:var(--ng-weak);color:var(--ng)}.b-hold,.b-superseded{background:#eef0f3;color:var(--sub)}
.b-paid{background:var(--paid-weak);color:var(--paid)}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--sub);font-weight:600}
.tablewrap{overflow-x:auto}
.num{font-variant-numeric:tabular-nums;text-align:right}
.score{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums}
.sbar{display:grid;grid-template-columns:150px 1fr 16px;gap:8px;align-items:center;font-size:12px;margin:4px 0}
.sbar i{display:block;height:6px;border-radius:3px;background:var(--line);position:relative}
.sbar i b{position:absolute;left:0;top:0;bottom:0;border-radius:3px;background:var(--accent)}
.chips span{display:inline-block;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:0 8px;margin:0 4px 4px 0;font-size:12px}
.btn{display:inline-block;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:6px 14px;font:inherit;font-weight:600;cursor:pointer}
.btn:hover{border-color:#b9c0cc;text-decoration:none}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.ok{background:var(--ok);border-color:var(--ok);color:#fff}.btn.ng{color:var(--ng)}
.btn.sm{padding:2px 10px;font-size:12px}
.btnrow{display:flex;gap:8px;flex-wrap:wrap}
textarea,input[type=text],input[type=number],input[type=url],select{width:100%;font:inherit;border:1px solid var(--line);border-radius:8px;padding:6px 10px;background:#fff}
textarea{min-height:64px}
label.f{display:block;font-size:12px;color:var(--sub);font-weight:600;margin:10px 0 2px}
.claim{border-left:3px solid var(--line);padding:4px 0 4px 12px;margin:0 0 12px}.claim.v{border-color:var(--ok)}
.flash{background:var(--ok-weak);color:var(--ok);border-radius:8px;padding:10px 14px;margin-bottom:16px;font-weight:600}
.flash.err{background:var(--ng-weak);color:var(--ng)}
.ask{background:var(--accent-weak);border-radius:8px;padding:10px 12px;margin:6px 0;display:flex;gap:10px;align-items:center;justify-content:space-between}
.ask code{font-family:inherit;font-weight:600}
.wall{border:2px dashed var(--paid);border-radius:10px;padding:10px 14px;margin:12px 0;color:var(--paid);background:var(--paid-weak);font-weight:600}
.opt{display:block;border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:8px;cursor:pointer}
.opt:has(input:checked){border-color:var(--accent);background:var(--accent-weak)}
.opt input{margin-right:8px}
.sec{display:flex;gap:8px;margin:3px 0}.sec .badge{flex:none;margin-top:3px}
.kpi{display:flex;gap:10px;flex-wrap:wrap}.kpi a{flex:1;min-width:120px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;color:var(--ink)}
.kpi b{display:block;font-size:24px;font-variant-numeric:tabular-nums}.kpi span{font-size:12px;color:var(--sub)}
details summary{cursor:pointer;color:var(--accent);font-weight:600}
ul{padding-left:20px;margin:4px 0}
"""

JS = """
document.addEventListener('click',ev=>{const b=ev.target.closest('[data-copy]');if(!b)return;
navigator.clipboard.writeText(b.dataset.copy).then(()=>{const t=b.textContent;b.textContent='コピーしました';setTimeout(()=>b.textContent=t,1200)})});
document.querySelectorAll('[data-count]').forEach(inp=>{const out=document.getElementById(inp.dataset.count);
const f=()=>out.textContent=inp.value.length+'字';inp.addEventListener('input',f);f()});
"""


def layout(title: str, body: str, active: str = "", flash: str = "", error: bool = False) -> str:
    nav = "".join(
        f'<a href="{href}" class="{"on" if key == active else ""}">{label}</a>'
        for key, href, label in (("home", "/", "ダッシュボード"), ("themes", "/themes", "テーマ"),
                                 ("runs", "/runs", "リサーチ履歴"), ("guide", "/guide", "使い方"))
    )
    steps = "".join(
        f'<span class="step {"ready" if i < READY_STAGES else ""}">{i + 1}. {s}{"" if i < READY_STAGES else "（準備中）"}</span>'
        for i, s in enumerate(STAGES)
    )
    fl = f'<div class="flash {"err" if error else ""}">{e(flash)}</div>' if flash else ""
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} — note studio</title>
<style>{CSS}</style></head><body>
<header><div class="bar"><span class="brand">note studio</span><nav>{nav}</nav></div><div class="steps">{steps}</div></header>
<main>{fl}{body}</main><script>{JS}</script></body></html>"""


def badge(status: str, labels: dict) -> str:
    return f'<span class="badge b-{e(status)}">{e(labels.get(status, status))}</span>'


def ask_box(text: str) -> str:
    return (f'<div class="ask"><span>Claude Code に依頼: <code>{e(text)}</code></span>'
            f'<button class="btn sm" data-copy="{e(text)}">コピー</button></div>')


def bullets(items) -> str:
    return "<ul>" + "".join(f"<li>{e(x)}</li>" for x in items) + "</ul>" if items else '<p class="muted">（なし）</p>'


# ---------- ページ ----------

def page_home(conn) -> str:
    counts = models.status_counts(conn)
    kpi = "".join(
        f'<a href="/themes?status={k}"><b>{counts[k]}</b><span>{label}</span></a>'
        for k, label in models.THEME_STATUS.items()
    )
    candidates = models.list_themes(conn, status="candidate")
    plans = models.pending_plans(conn)
    selected = models.list_themes(conn, status="selected")

    mine = []
    for t in candidates[:8]:
        mine.append(f'<tr><td>T{t["id"]:03d}</td><td><a href="/themes/{t["id"]}">{e(t["title"])}</a></td>'
                    f'<td class="num">{t["score_total"]}</td><td>テーマの採用／保留／却下</td></tr>')
    for p in plans:
        if p["status"] == "draft":
            mine.append(f'<tr><td>P{p["id"]:03d}</td><td><a href="/plans/{p["id"]}">{e(p["title"])}（v{p["version"]}）</a></td>'
                        f'<td></td><td>企画の承認／修正依頼</td></tr>')
    mine_html = (f'<div class="tablewrap"><table><tr><th>ID</th><th>対象</th><th class="num">スコア</th><th>やること</th></tr>'
                 f'{"".join(mine)}</table></div>') if mine else '<p class="muted">確認待ちはありません。</p>'
    if len(candidates) > 8:
        mine_html += f'<p class="small"><a href="/themes?status=candidate">残り {len(candidates) - 8} 件を見る</a></p>'

    asks = [ask_box(f"T{t['id']:03d}の企画を作って") for t in selected]
    asks += [ask_box(f"P{p['id']:03d}の修正依頼を反映して") for p in plans if p["status"] == "revision_requested"]
    if not candidates and not selected:
        asks.append(ask_box("需要リサーチして（ジャンル: おまかせ）"))
    asks_html = "".join(asks) or '<p class="muted">今は依頼することはありません。確認待ちの判断を進めてください。</p>'

    runs = models.list_runs(conn)[:5]
    runs_html = "".join(
        f'<tr><td><a href="/runs/{r["id"]}">R{r["id"]:03d}</a></td><td>{e(r["created_at"][:16].replace("T", " "))}</td>'
        f'<td>{e(r["genre"] or "おまかせ")}</td><td class="num">{r["theme_count"]}</td></tr>' for r in runs
    ) or '<tr><td colspan="4" class="muted">まだリサーチがありません</td></tr>'

    return f"""<h1>ダッシュボード</h1><p class="muted">AIは調べて提案するだけ。採用・承認はここで人が決めます。</p>
<div class="kpi" style="margin:16px 0">{kpi}</div>
<div class="grid"><div>
<div class="card"><h2>あなたの確認待ち</h2>{mine_html}</div>
<div class="card"><h2>最近のリサーチ</h2><div class="tablewrap"><table><tr><th>ID</th><th>日時</th><th>ジャンル</th><th class="num">候補数</th></tr>{runs_html}</table></div></div>
</div><div><div class="card"><h2>次に Claude Code に頼むこと</h2>{asks_html}</div></div></div>"""


def page_themes(conn, status: str | None) -> str:
    counts = models.status_counts(conn)
    tabs = [f'<a class="btn sm {"primary" if not status else ""}" href="/themes">すべて</a>']
    for k, label in models.THEME_STATUS.items():
        tabs.append(f'<a class="btn sm {"primary" if status == k else ""}" href="/themes?status={k}">{label} {counts[k]}</a>')
    rows = "".join(
        f'<tr><td>T{t["id"]:03d}</td><td><a href="/themes/{t["id"]}">{e(t["title"])}</a>'
        f'<div class="small muted">{e(t["target_reader"])}</div></td>'
        f'<td class="num"><b>{t["score_total"]}</b></td><td>{price_text(t["price_min"], t["price_max"])}</td>'
        f'<td class="num">{t["competitor_count"]}</td><td class="num">{t["claim_count"]}</td>'
        f'<td>{badge(t["status"], models.THEME_STATUS)}</td></tr>'
        for t in models.list_themes(conn, status=status)
    ) or '<tr><td colspan="7" class="muted">該当するテーマはありません</td></tr>'
    return f"""<h1>テーマ</h1><div class="btnrow" style="margin:12px 0 16px">{"".join(tabs)}</div>
<div class="card tablewrap"><table><tr><th>ID</th><th>テーマ / 想定読者</th><th class="num">スコア</th><th>競合価格帯</th>
<th class="num">競合</th><th class="num">根拠</th><th>状態</th></tr>{rows}</table></div>"""


def page_runs(conn) -> str:
    rows = "".join(
        f'<tr><td><a href="/runs/{r["id"]}">R{r["id"]:03d}</a></td><td>{e(r["created_at"][:16].replace("T", " "))}</td>'
        f'<td>{e(r["genre"] or "おまかせ")}</td><td>{e((r["brief"] or "")[:80])}</td><td class="num">{r["theme_count"]}</td></tr>'
        for r in models.list_runs(conn)
    ) or '<tr><td colspan="5" class="muted">まだリサーチがありません</td></tr>'
    return f"""<h1>リサーチ履歴</h1><div class="card tablewrap" style="margin-top:16px"><table>
<tr><th>ID</th><th>日時</th><th>ジャンル</th><th>方針</th><th class="num">候補</th></tr>{rows}</table></div>"""


def decide_buttons(t: dict, compact: bool = False) -> str:
    note = "" if compact else '<textarea name="note" placeholder="判断の理由・メモ（次のリサーチに活かされます）"></textarea>'
    btns = []
    if t["status"] not in ("selected", "planning", "planned"):
        btns.append('<button class="btn ok sm" name="action" value="select">採用</button>')
    if t["status"] != "hold":
        btns.append('<button class="btn sm" name="action" value="hold">保留</button>')
    if t["status"] != "rejected":
        btns.append('<button class="btn ng sm" name="action" value="reject">却下</button>')
    if t["status"] in ("hold", "rejected"):
        btns.append('<button class="btn sm" name="action" value="reopen">確認待ちに戻す</button>')
    return (f'<form method="post" action="/themes/{t["id"]}/decide">{note}'
            f'<div class="btnrow" style="margin-top:8px">{"".join(btns)}</div></form>')


def page_run(conn, run_id: int) -> str | None:
    run = models.get_run(conn, run_id)
    if run is None:
        return None
    themes = models.list_themes(conn, run_id=run_id)
    rows = "".join(
        f'<tr><td>T{t["id"]:03d}</td><td><a href="/themes/{t["id"]}"><b>{e(t["title"])}</b></a>'
        f'<div class="small">{e(t["summary"])}</div></td>'
        f'<td class="num"><b>{t["score_total"]}</b><div class="small muted">'
        + " ".join(f'{v}' for v in (t["scores"].get(k) for k in SCORE_KEYS)) +
        f'</div></td><td>{price_text(t["price_min"], t["price_max"])}</td>'
        f'<td>{badge(t["status"], models.THEME_STATUS)}{decide_buttons(t, compact=True)}</td></tr>'
        for t in themes
    )
    return f"""<h1>リサーチ R{run_id:03d}</h1><p class="muted">{e(run["created_at"][:16].replace("T", " "))} ／ ジャンル: {e(run["genre"] or "おまかせ")}</p>
<div class="card"><h2>探索方針</h2><p>{e(run["brief"])}</p>
<details><summary>手法と検索クエリ（{len(run["queries"])}件）</summary><p>{e(run["method"])}</p>{bullets(run["queries"])}</details></div>
<div class="card tablewrap"><h2>候補テーマ（スコア順）</h2>
<p class="small muted">スコア下段: {" / ".join(SCORE_KEYS.values())}（各1〜5）</p>
<table><tr><th>ID</th><th>テーマ</th><th class="num">スコア</th><th>競合価格帯</th><th style="width:220px">判断</th></tr>{rows}</table></div>"""


def page_theme(conn, theme_id: int) -> str | None:
    t = models.theme_detail(conn, theme_id)
    if t is None:
        return None
    sbars = "".join(
        f'<div class="sbar"><span>{label}</span><i><b style="width:{t["scores"].get(k, 0) * 20}%"></b></i>'
        f'<span class="num">{t["scores"].get(k, "-")}</span></div>'
        for k, label in SCORE_KEYS.items()
    )
    claims = "".join(
        f'<div class="claim {"v" if c["verified"] else ""}"><div>{e(c["text"])}</div>'
        f'<div class="small muted"><span class="badge">{SOURCE_LABEL.get(c["source_type"], c["source_type"])}</span> '
        f'<span class="badge">{CONF_LABEL.get(c["confidence"], "")}</span> '
        f'{source_link(c["url"], c["source_title"])} '
        f'{e(c["publisher"] or "")} {e(c["published"] or "")}</div>'
        f'<form method="post" action="/claims/{c["id"]}/toggle" style="margin-top:4px">'
        f'<button class="btn sm">{"✓ 確認済み（取り消す）" if c["verified"] else "出典を確認した"}</button></form></div>'
        for c in t["claims"]
    ) or '<p class="muted">根拠がありません</p>'

    comp_rows = ""
    for c in t["competitors"]:
        title = (f'<a href="{e(c["url"])}" target="_blank" rel="noopener noreferrer">{e(c["title"])}</a>'
                 if c["url"] else e(c["title"]))
        price = f'{c["price"]}円' if c["price"] is not None else ("無料" if c["paid"] == 0 else "不明")
        comp_rows += (
            f'<tr><td>{title}<div class="small muted">{e(c["quality_note"] or "")}</div></td><td>{e(c["platform"] or "")}</td>'
            f'<td class="num">{price}</td><td>'
            f'<form method="post" action="/competitors/{c["id"]}/toggle">'
            f'<button class="btn sm">{"✓ 確認済" if c["price_verified"] else "未確認"}</button></form></td>'
            f'<td>{"手動" if c["origin"] == "manual" else "AI"}</td><td>'
            f'<form method="post" action="/competitors/{c["id"]}/delete" onsubmit="return confirm(\'この競合を削除しますか？\')">'
            f'<button class="btn sm ng">削除</button></form></td></tr>'
        )
    comp_rows = comp_rows or '<tr><td colspan="6" class="muted">競合は見つかっていません</td></tr>'

    plans = "".join(
        f'<tr><td><a href="/plans/{p["id"]}">P{p["id"]:03d} v{p["version"]}</a></td>'
        f'<td>{badge(p["status"], models.PLAN_STATUS)}</td><td>{e(p["chosen_title"] or "")}</td></tr>'
        for p in t["plans"]
    )
    next_step = ""
    if t["status"] == "selected" and not t["plans"]:
        next_step = '<div class="card"><h2>次のステップ</h2>' + ask_box("T%03dの企画を作って" % t["id"]) + "</div>"
    plans_card = (f'<div class="card"><h2>企画</h2><table>{plans}</table></div>' if plans else "")
    log = "".join(
        f'<li><span class="small muted">{e(d["created_at"][:16].replace("T", " "))}</span> {e(d["action"])} {e(d["note"] or "")}</li>'
        for d in t["decisions"]
    )
    run_link = ' ／ <a href="/runs/{0}">R{0:03d}</a>'.format(t["run"]["id"]) if t["run"] else ""
    note_html = "<h3>あなたのメモ</h3><p>" + e(t["human_note"]) + "</p>" if t["human_note"] else ""
    log_card = '<div class="card"><h2>判断ログ</h2><ul class="small">' + log + "</ul></div>" if log else ""

    return f"""<p class="small"><a href="/themes">テーマ一覧</a>{run_link}</p>
<h1>T{t["id"]:03d} {e(t["title"])}</h1><p>{badge(t["status"], models.THEME_STATUS)} <span class="muted">{e(t["summary"])}</span></p>
<div class="grid"><div>
<div class="card"><div class="cols">
<div><h3>想定読者</h3><p>{e(t["target_reader"])}</p></div>
<div><h3>読者の悩み・今知りたい理由</h3><p>{e(t["reader_pain"])}</p></div></div>
<h3>想定検索キーワード</h3><div class="chips">{"".join(f"<span>{e(k)}</span>" for k in t["keywords"])}</div>
<h3>想定集客経路</h3><div class="chips">{"".join(f"<span>{e(k)}</span>" for k in t["channels"])}</div>
<h3>需給ギャップ（良質な有料記事が少ない理由）</h3><p>{e(t["gap"])}</p>
<h3>差別化の芽</h3><p>{e(t["differentiation_hint"] or "（未記入）")}</p>
<h3>あなたが用意すべき一次情報</h3><p>{e(t["primary_info_needed"])}</p>
<h3>リスク</h3>{bullets(t["risks"])}
<details style="margin-top:12px"><summary>内容を編集する</summary>
<form method="post" action="/themes/{t["id"]}/edit">
<label class="f">テーマ名</label><input type="text" name="title" value="{e(t["title"])}">
<label class="f">概要</label><textarea name="summary">{e(t["summary"])}</textarea>
<label class="f">想定読者</label><textarea name="target_reader">{e(t["target_reader"])}</textarea>
<label class="f">読者の悩み</label><textarea name="reader_pain">{e(t["reader_pain"])}</textarea>
<label class="f">キーワード（カンマ区切り）</label><input type="text" name="keywords" value="{e(", ".join(t["keywords"]))}">
<label class="f">需給ギャップ</label><textarea name="gap">{e(t["gap"])}</textarea>
<label class="f">用意すべき一次情報</label><textarea name="primary_info_needed">{e(t["primary_info_needed"])}</textarea>
<div style="margin-top:10px"><button class="btn primary">保存</button></div></form></details></div>

<div class="card"><h2>需要の根拠（主張と出典）</h2>
<p class="small muted">AIの要約が出典どおりか、リンク先で確かめたら「出典を確認した」を押してください。</p>{claims}</div>

<div class="card"><h2>競合記事・商品</h2>
<p class="small muted">AIが検索結果から拾った価格は未確認です。実際に見て確かめたら「未確認」を押して確認済みにしてください。</p>
<div class="tablewrap"><table><tr><th>タイトル</th><th>媒体</th><th class="num">価格</th><th>価格確認</th><th>登録</th><th></th></tr>{comp_rows}</table></div>
<details style="margin-top:12px"><summary>自分で見つけた競合を追加</summary>
<form method="post" action="/themes/{t["id"]}/competitors">
<label class="f">タイトル</label><input type="text" name="title" required>
<label class="f">URL</label><input type="url" name="url">
<div class="cols"><div><label class="f">媒体</label><select name="platform">
<option>note</option><option>brain</option><option>tips</option><option>kindle</option><option>udemy</option><option>blog</option><option>other</option></select></div>
<div><label class="f">価格（円、無料なら空欄）</label><input type="number" name="price" min="0"></div></div>
<label class="f">メモ（中身の質・鮮度など）</label><input type="text" name="note">
<div style="margin-top:10px"><button class="btn primary">追加</button></div></form></details></div>
</div>

<div>
<div class="card"><h2>判断（チェックポイント1）</h2>
<p class="small muted">採用すると、Claude Code が企画づくり（段階2）に進めるようになります。</p>
{decide_buttons(t)}
{note_html}</div>
{next_step}
<div class="card"><h2>スコア</h2><div class="score">{t["score_total"]}<span class="small muted"> / 100</span></div>
{sbars}<p class="small">{e(t["score_rationale"])}</p></div>
<div class="card"><h2>競合価格帯</h2><p><b>{price_text(t["price_min"], t["price_max"])}</b></p><p class="small muted">{e(t["price_note"] or "")}</p></div>
{plans_card}
{log_card}
</div></div>"""


def page_plan(conn, plan_id: int) -> str | None:
    p = models.get_plan(conn, plan_id)
    if p is None:
        return None
    t = models.get_theme(conn, p["theme_id"])
    pw = p["paywall"]
    editable = p["status"] in ("draft", "revision_requested")
    approved = p["status"] == "approved"

    diffs = "".join(
        f'<div class="card" style="margin:0"><span class="badge b-paid">{DIFF_TYPES.get(d["type"], d["type"])}</span> '
        f'<span class="badge">{EFFORT_LABEL.get(d["effort"], d["effort"])}</span>'
        f'<h3 style="margin-top:8px">{e(d["title"])}</h3><p>{e(d["description"])}</p>'
        f'<p class="small"><b>作り方:</b> {e(d["how_to_make"])}</p></div>'
        for d in p["differentiators"]
    )

    titles = ""
    for i, tt in enumerate(p["titles"]):
        checked = "checked" if (approved and p["chosen_title"] == tt["text"]) or (not approved and i == 0) else ""
        titles += (f'<label class="opt"><input type="radio" name="title_choice" value="{i}" {checked} {"" if editable else "disabled"}>'
                   f'<b>{e(tt["text"])}</b> <span class="small muted">{len(tt["text"])}字 ／ {e(tt["angle"])}</span></label>')
    custom_checked = approved and p["chosen_title"] not in [x["text"] for x in p["titles"]]
    if editable or custom_checked:
        titles += (f'<label class="opt"><input type="radio" name="title_choice" value="custom" {"checked" if custom_checked else ""} {"" if editable else "disabled"}>'
                   f'自分で書く <span class="small muted" id="cc">0字</span>'
                   f'<input type="text" name="custom_title" data-count="cc" value="{e(p["chosen_title"] if custom_checked else "")}" {"" if editable else "disabled"} style="margin-top:6px"></label>')

    outlines = ""
    for i, o in enumerate(p["outlines"]):
        checked = "checked" if (approved and p["chosen_outline"] == i) or (not approved and i == 0) else ""
        secs = ""
        for s in o["sections"]:
            tag = '<span class="badge b-paid">有料</span>' if s["paid"] else '<span class="badge">無料</span>'
            points = ('<div class="small muted">' + e(" ／ ".join(s["points"])) + "</div>") if s.get("points") else ""
            secs += f'<div class="sec">{tag}<div><b>{e(s["heading"])}</b>{points}</div></div>'
        outlines += (f'<label class="opt"><input type="radio" name="outline" value="{i}" {checked} {"" if editable else "disabled"}>'
                     f'<b>案{i + 1}: {e(o["name"])}</b><div class="small muted" style="margin:2px 0 8px">{e(o["concept"])}</div>{secs}</label>')

    price = p["price"]
    alt = "、".join(f"{a}円" for a in price.get("alternatives", []))
    claims = "".join(
        f'<li>{e(c["text"])} — {source_link(c["url"], c["source_title"])} '
        f'<span class="badge">{SOURCE_LABEL.get(c["source_type"], "")}</span></li>'
        for c in p["claims"]
    )

    if editable:
        decision = f"""<div class="card"><h2>判断（チェックポイント2）</h2>
<p class="small muted">左でタイトルと見出し構成を選び、価格を決めて承認してください。直してほしい点があれば修正依頼へ。</p>
<label class="f">価格（円）</label><input type="number" name="price" form="approve" min="100" value="{e(price.get("suggested"))}">
<label class="f">メモ（任意）</label><textarea name="note" form="approve"></textarea>
<div class="btnrow" style="margin-top:10px"><button class="btn ok" form="approve">この内容で企画を承認</button></div>
<form method="post" action="/plans/{p["id"]}/revise" style="margin-top:18px">
<label class="f">修正依頼（Claude Code が次の版に反映します）</label>
<textarea name="note" placeholder="例: 対象読者を共働き世帯に絞って。タイトルから『完全』を外して。" required>{e(p["human_note"] if p["status"] == "revision_requested" else "")}</textarea>
<div style="margin-top:8px"><button class="btn">修正を依頼する</button></div></form>
{ask_box(f"P{p['id']:03d}の修正依頼を反映して") if p["status"] == "revision_requested" else ""}</div>"""
    elif approved:
        decision = f"""<div class="card"><h2>承認済み</h2><p><b>{e(p["chosen_title"])}</b></p>
<p>構成: 案{p["chosen_outline"] + 1} ／ 価格: <b>{p["final_price"]}円</b></p>
<p class="small muted">{e(p["approved_at"][:16].replace("T", " "))} 承認。次の段階（リサーチ＆執筆）は準備中です。</p>
{f'<p class="small">メモ: {e(p["human_note"])}</p>' if p["human_note"] else ""}</div>"""
    else:
        decision = '<div class="card"><h2>旧版</h2><p class="muted">この企画は新しい版に置き換えられました。</p></div>'

    return f"""<p class="small"><a href="/themes/{t["id"]}">T{t["id"]:03d} {e(t["title"])}</a></p>
<h1>企画 P{p["id"]:03d} <span class="muted small">v{p["version"]}</span> {badge(p["status"], models.PLAN_STATUS)}</h1>
<form id="approve" method="post" action="/plans/{p["id"]}/approve"></form>
<div class="grid" style="margin-top:12px"><div>
<div class="card"><h2>なぜお金を払ってでも読むのか</h2><p style="font-size:15px"><b>{e(p["value_proposition"])}</b></p>
<div class="cols"><div><h3>読む前</h3><p>{e(p["before_after"].get("before"))}</p></div>
<div><h3>読んだ後</h3><p>{e(p["before_after"].get("after"))}</p></div></div>
<h3>無料の検索では手に入らないもの</h3>{bullets(p["why_paid"])}</div>
<div class="card"><h2>差別化ポイント</h2><div class="cols">{diffs}</div></div>
<div class="card"><h2>無料／有料の境界</h2>
<h3>無料で見せる</h3>{bullets(pw.get("free_part", []))}
<div class="wall">── ここから有料 ── 境界直前の一文:「{e(pw.get("boundary_hook"))}」</div>
<h3>有料部分</h3>{bullets(pw.get("paid_part", []))}
<p class="small muted">設計意図: {e(pw.get("rationale"))}</p></div>
<div class="card"><h2>タイトル案</h2><div>{titles.replace('<input ', '<input form="approve" ')}</div></div>
<div class="card"><h2>見出し構成案</h2>{outlines.replace('<input ', '<input form="approve" ')}</div>
{f'<div class="card"><h2>企画の根拠（追加調査）</h2><ul>{claims}</ul></div>' if claims else ""}
</div><div>
{decision}
<div class="card"><h2>価格の提案</h2><div class="score">{e(price.get("suggested"))}<span class="small muted"> 円</span></div>
<p class="small">{e(price.get("rationale"))}</p>{f'<p class="small muted">代替案: {alt}</p>' if alt else ""}
<p class="small muted">競合価格帯: {price_text(t["price_min"], t["price_max"])}</p></div>
<div class="card"><h2>あなたに確認したいこと</h2>{bullets(p["open_questions"])}</div>
</div></div>"""


GUIDE = """<h1>使い方</h1>
<div class="card"><h2>基本の流れ</h2><ol>
<li><b>需要リサーチ</b>: Claude Code に「需要リサーチして（ジャンル: ○○）」と頼む。候補テーマが出典付きで取り込まれます。</li>
<li><b>チェックポイント1</b>: この画面でテーマを見て、出典と競合価格を確かめ、<b>採用／保留／却下</b>する。理由のメモは次のリサーチに反映されます。</li>
<li><b>企画・差別化設計</b>: 採用したテーマについて「T003の企画を作って」と頼む。採用していないテーマの企画は作れない仕組みです。</li>
<li><b>チェックポイント2</b>: 企画画面でタイトル・見出し構成・価格を選んで<b>承認</b>、または修正を依頼する。</li>
<li>リサーチ＆執筆・レビュー・公開準備・分析は順次追加予定です。</li></ol></div>
<div class="card"><h2>データの場所</h2><ul>
<li><code>data/studio.db</code> — すべてのデータ（SQLite）</li>
<li><code>data/research/</code> — リサーチごとのMarkdown</li>
<li><code>data/themes/T001_…/</code> — テーマごとのMarkdown（theme.md、plan_v1.md …）</li>
<li><code>data/raw/</code> — 取り込んだ元のJSON（後から検証する用）</li></ul></div>
<div class="card"><h2>情報源のルール</h2><ul>
<li>note内検索・note APIにはアクセスしません（robots.txtで禁止されているため）。note記事は一般のWeb検索で探します。</li>
<li>X（旧Twitter）はスクレイピングしません。</li>
<li>AIが拾った価格・数字は「未確認」扱いです。あなたが確かめたものだけ確認済みにしてください。</li></ul></div>"""


# ---------- ルーティング ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "notestudio"

    def log_message(self, fmt, *args):  # 静かにする
        pass

    def _conn(self) -> sqlite3.Connection:
        return db.connect()

    def _send(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str, flash: str = "", error: bool = False) -> None:
        if flash:
            location += ("&" if "?" in location else "?") + ("err=" if error else "msg=") + quote(flash)
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self) -> None:
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        flash = (qs.get("msg") or qs.get("err") or [""])[0]
        err = "err" in qs
        conn = self._conn()
        try:
            path = url.path.rstrip("/") or "/"
            page, title, active = None, "", ""
            if path == "/":
                page, title, active = page_home(conn), "ダッシュボード", "home"
            elif path == "/themes":
                status = (qs.get("status") or [None])[0]
                page, title, active = page_themes(conn, status if status in models.THEME_STATUS else None), "テーマ", "themes"
            elif path == "/runs":
                page, title, active = page_runs(conn), "リサーチ履歴", "runs"
            elif path == "/guide":
                page, title, active = GUIDE, "使い方", "guide"
            elif m := re.fullmatch(r"/themes/(\d+)", path):
                page, title, active = page_theme(conn, int(m[1])), f"T{int(m[1]):03d}", "themes"
            elif m := re.fullmatch(r"/runs/(\d+)", path):
                page, title, active = page_run(conn, int(m[1])), f"R{int(m[1]):03d}", "runs"
            elif m := re.fullmatch(r"/plans/(\d+)", path):
                page, title, active = page_plan(conn, int(m[1])), f"P{int(m[1]):03d}", "themes"
            if page is None:
                self._send(layout("見つかりません", "<h1>見つかりません</h1>"), 404)
            else:
                self._send(layout(title, page, active, flash, err))
        finally:
            conn.close()

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if not origin:
            return True
        host = urlparse(origin).netloc
        return host == self.headers.get("Host")

    def do_POST(self) -> None:
        if not self._same_origin():
            self._send("forbidden", 403)
            return
        length = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True).items()}
        path = urlparse(self.path).path
        conn = self._conn()
        back = self.headers.get("Referer") or "/"
        back = urlparse(back).path or "/"
        try:
            if m := re.fullmatch(r"/themes/(\d+)/decide", path):
                tid = int(m[1])
                models.decide_theme(conn, tid, form.get("action", ""), form.get("note", "").strip() or None)
                export.refresh_theme(conn, tid)
                label = models.THEME_STATUS[models.get_theme(conn, tid)["status"]]
                self._redirect(back, f"T{tid:03d} を「{label}」にしました")
            elif m := re.fullmatch(r"/themes/(\d+)/edit", path):
                tid = int(m[1])
                models.update_theme(conn, tid, {k: v for k, v in form.items() if k in models.THEME_EDITABLE + ("keywords",)})
                export.refresh_theme(conn, tid)
                self._redirect(f"/themes/{tid}", "保存しました")
            elif m := re.fullmatch(r"/themes/(\d+)/competitors", path):
                tid = int(m[1])
                price = int(form["price"]) if form.get("price", "").strip() else None
                models.add_competitor(conn, tid, form["title"].strip(), form.get("url", "").strip(),
                                      form.get("platform", "other"), price, form.get("note", "").strip())
                export.refresh_theme(conn, tid)
                self._redirect(f"/themes/{tid}", "競合を追加しました")
            elif m := re.fullmatch(r"/competitors/(\d+)/delete", path):
                tid = models.delete_competitor(conn, int(m[1]))
                export.refresh_theme(conn, tid)
                self._redirect(f"/themes/{tid}", "削除しました")
            elif m := re.fullmatch(r"/competitors/(\d+)/toggle", path):
                tid = models.toggle(conn, "competitors", "price_verified", int(m[1]))
                export.refresh_theme(conn, tid)
                self._redirect(f"/themes/{tid}")
            elif m := re.fullmatch(r"/claims/(\d+)/toggle", path):
                tid = models.toggle(conn, "claims", "verified", int(m[1]))
                export.refresh_theme(conn, tid)
                self._redirect(back)
            elif m := re.fullmatch(r"/plans/(\d+)/approve", path):
                pid = int(m[1])
                plan = models.get_plan(conn, pid)
                choice = form.get("title_choice", "0")
                title = form.get("custom_title", "") if choice == "custom" else plan["titles"][int(choice)]["text"]
                models.approve_plan(conn, pid, title, int(form.get("outline", "0")),
                                    int(form.get("price") or plan["price"]["suggested"]), form.get("note", "").strip())
                export.refresh_theme(conn, plan["theme_id"])
                self._redirect(f"/plans/{pid}", "企画を承認しました")
            elif m := re.fullmatch(r"/plans/(\d+)/revise", path):
                pid = int(m[1])
                models.request_plan_revision(conn, pid, form.get("note", ""))
                export.refresh_theme(conn, models.get_plan(conn, pid)["theme_id"])
                self._redirect(f"/plans/{pid}", "修正依頼を保存しました。Claude Code に反映を依頼してください")
            else:
                self._send("not found", 404)
        except (ValueError, KeyError, IndexError, models.CheckpointError) as ex:
            self._redirect(back, f"エラー: {ex}", error=True)
        finally:
            conn.close()


def serve(port: int = 8765, open_browser: bool = True) -> None:
    db.connect().close()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"note studio: {url}  （止めるには Ctrl+C）")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました")
