"""notewriter の Web画面（標準ライブラリのみ。スマホから Tailscale 経由で使う前提）。

- 既定は 127.0.0.1 で待ち受け。Tailscale 経由で使うときは --host に Tailscale のIP（100.x.y.z）を指定する。
  0.0.0.0 / :: など「全インターフェース」は外部公開になるので拒否する（抜け道は作らない）。
- POST は同一オリジンのみ。全レスポンスに Cache-Control: no-store / X-Frame-Options: DENY /
  Referrer-Policy: no-referrer と、外部読み込みを禁止する Content-Security-Policy を付ける。
- 作業フォルダが使えない（未初期化・未マウント）ときは、どのページも理由と対処だけを表示し、何も書き込まない。
- 初回はお知らせ（Remote Control の保存仕様・Trusted Devices・実データ投入の前提）を確認するまで先に進めない。
- コンプライアンスチェッカーは警告を表示するだけ。自動修正・置換のボタンは作らない。貼り付けた本文は保存しない。
"""
from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import sys
import threading
import webbrowser
from collections import Counter
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from . import store

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
NOTICE_FILE = "notice_ack.json"
NOTICE_VERSION = 1
MAX_BODY = 8 * 1024 * 1024  # 1リクエストの上限（アップロード込み）
TAILSCALE_NETS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
SEVERITY_LABEL = {"strong": "強い警告", "warn": "警告", "info": "参考"}
SEVERITY_ORDER = {"strong": 0, "warn": 1, "info": 2}


class HostRefused(ValueError):
    """待ち受けアドレスが外部公開になる／Tailscale でも localhost でもない。"""


def check_host(host: str) -> str:
    """待ち受けてよいアドレスか確かめる。だめなら HostRefused。"""
    h = (host or "").strip().strip("[]")
    if h in ("", "*"):
        raise HostRefused("待ち受けアドレスが空です。外部公開になるので Tailscale のIP（100.x.y.z）を指定してください。")
    if h == "localhost":
        return h
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        raise HostRefused(f"「{h}」はIPアドレスではありません。127.0.0.1 か Tailscale のIP（100.x.y.z）を指定してください。")
    if ip.is_unspecified:
        raise HostRefused(f"{h} は全てのネットワークで待ち受けるため、外部公開になります。"
                          "Tailscale のIP（100.x.y.z。`tailscale ip -4` で確認）を指定してください。")
    if ip.is_loopback or any(ip in net for net in TAILSCALE_NETS if ip.version == net.version):
        return h
    raise HostRefused(f"{h} は Tailscale のアドレスではありません。Web画面は外部に公開しない約束なので、"
                      "127.0.0.1 か Tailscale のIP（100.x.y.z）を指定してください。")


# ---------- 部品 ----------

def e(v) -> str:
    return html.escape("" if v is None else str(v))


def lines_html(text: str) -> str:
    return e(text).replace("\n", "<br>")


def short(text: str, n: int = 140) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[:n] + "…"


def qurl(path: str, **params) -> str:
    params = {k: v for k, v in params.items() if v not in (None, "")}
    return path + ("?" + urlencode(params) if params else "")


def _kakera():
    from . import kakera
    return kakera


def _compliance():
    from . import compliance
    return compliance


CSS = """
:root{--ink:#3b4450;--sub:#7b8594;--line:#e4ebf0;--bg:#f2f6f9;--card:#fff;
--main:#39b8c6;--main-dk:#2a8f9c;--main-weak:#e3f6f7;--sky:#5aa8f0;--sky-weak:#e7f2fe;
--warm:#ff8a65;--warm-dk:#e0673f;--warm-weak:#fff0ea;--sun:#ffc53d;--sun-weak:#fff7dc;
--ok:#3bb77e;--ok-weak:#e5f7ee;--ng:#e5534b;--ng-weak:#fdecea;--shadow:0 4px 16px rgba(40,90,120,.10)}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.8 -apple-system,BlinkMacSystemFont,"Hiragino Maru Gothic ProN","Hiragino Sans","Noto Sans JP","Yu Gothic",sans-serif;word-break:break-word}
a{color:var(--main-dk);text-decoration:none}a:hover{text-decoration:underline}
header{background:linear-gradient(120deg,var(--main),var(--sky));color:#fff;box-shadow:0 2px 10px rgba(40,90,120,.18)}
.bar{max-width:1080px;margin:0 auto;padding:12px 16px 4px;display:flex;align-items:center;gap:10px}
.brand{font-weight:800;font-size:18px;letter-spacing:.04em;color:#fff}
.brand small{font-weight:600;font-size:11px;opacity:.9;margin-left:6px}
nav{max-width:1080px;margin:0 auto;padding:6px 12px 12px;display:flex;gap:6px;overflow-x:auto;-webkit-overflow-scrolling:touch}
nav a{flex:none;color:#fff;background:rgba(255,255,255,.18);border-radius:99px;padding:6px 14px;font-weight:700;font-size:14px;white-space:nowrap}
nav a.on,nav a:hover{background:#fff;color:var(--main-dk);text-decoration:none}
main{max-width:1080px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:22px;margin:4px 0 12px;padding:4px 0 4px 14px;border-left:7px solid var(--warm);border-radius:3px;line-height:1.5}
h2{font-size:16px;margin:0 0 14px}
h3{font-size:15px;margin:16px 0 6px;color:var(--main-dk)}
.card h2,.ribbon{position:relative;display:inline-block;background:var(--main);color:#fff;padding:5px 18px 5px 16px;
margin:0 0 14px -28px;border-radius:0 10px 10px 0;box-shadow:0 2px 6px rgba(42,143,156,.25)}
.card h2::before,.ribbon::before{content:"";position:absolute;left:0;bottom:-9px;border-top:9px solid var(--main-dk);border-left:9px solid transparent}
.card.warm h2{background:var(--warm)}.card.warm h2::before{border-top-color:var(--warm-dk)}
.card.alert h2{background:var(--ng)}.card.alert h2::before{border-top-color:#b8322b}
.muted{color:var(--sub)}.small{font-size:13px}
.card{background:var(--card);border-radius:18px;padding:18px 20px;margin-bottom:18px;box-shadow:var(--shadow)}
.card.alert{border:2px solid var(--ng);background:var(--ng-weak)}
.card.warm{background:#fffdfb}
.grid{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:18px;align-items:start}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}
@media(max-width:860px){.grid{grid-template-columns:1fr}}
@media(max-width:600px){table.findings tr:first-child{display:none}table.findings tr{display:block;border:1px solid var(--line,#e3e6ec);border-radius:14px;padding:8px 10px;margin:0 0 10px}table.findings td{display:block;border:0;padding:2px 0;white-space:normal!important}table.findings td:nth-child(2) .small{display:inline;margin-left:6px}}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:8px 0 18px}
.kpi a,.kpi div{display:block;background:var(--card);border-radius:18px;padding:14px 16px;color:var(--ink);box-shadow:var(--shadow);border-top:5px solid var(--main)}
.kpi a:nth-child(2){border-top-color:var(--warm)}.kpi a:nth-child(3){border-top-color:var(--sun)}.kpi a:nth-child(4){border-top-color:var(--sky)}
.kpi b{display:block;font-size:28px;font-variant-numeric:tabular-nums;line-height:1.3}.kpi span{font-size:13px;color:var(--sub)}
.kpi a:hover{text-decoration:none;transform:translateY(-1px)}
.badge{display:inline-block;font-size:12px;font-weight:700;padding:2px 11px;border-radius:99px;background:var(--bg);color:var(--sub);white-space:nowrap;margin:0 4px 4px 0;line-height:1.7}
a.badge:hover{text-decoration:none;filter:brightness(.96)}
.b-vp{background:var(--main-weak);color:var(--main-dk)}
.b-st{background:var(--sun-weak);color:#9a6b00}
.b-tag{background:var(--sky-weak);color:#2f6fb5}
.b-ok{background:var(--ok-weak);color:#1f8a57}
.b-warm{background:var(--warm-weak);color:var(--warm-dk)}
.b-strong{background:var(--ng);color:#fff}.b-warn{background:var(--warm-weak);color:var(--warm-dk)}.b-info{background:var(--sky-weak);color:#2f6fb5}
.kcard{background:var(--card);border-radius:16px;padding:14px 16px;margin-bottom:12px;box-shadow:var(--shadow);border-left:6px solid var(--main)}
.kcard .head{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;font-size:13px;color:var(--sub);margin-bottom:4px}
.kcard .kid{font-weight:800;font-size:15px}
.kcard p{margin:4px 0 8px}
.btn{display:inline-flex;align-items:center;justify-content:center;min-height:44px;border:2px solid var(--main);background:#fff;color:var(--main-dk);
border-radius:99px;padding:6px 20px;font:inherit;font-weight:800;cursor:pointer;box-shadow:0 3px 0 rgba(42,143,156,.25);text-align:center}
.btn:hover{text-decoration:none;filter:brightness(1.03)}
.btn:active{transform:translateY(2px);box-shadow:none}
.btn.primary{background:linear-gradient(120deg,var(--main),var(--sky));border-color:transparent;color:#fff}
.btn.warm{background:var(--warm);border-color:var(--warm);color:#fff;box-shadow:0 3px 0 var(--warm-dk)}
.btn.ng{border-color:var(--ng);color:var(--ng);box-shadow:0 3px 0 rgba(229,83,75,.25)}
.btn.ng.solid{background:var(--ng);color:#fff}
.btn.sm{min-height:36px;padding:3px 14px;font-size:13px}
.btnrow{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:12px}
textarea,input[type=text],input[type=number],input[type=search],select,input[type=file]{width:100%;font:inherit;font-size:16px;
border:2px solid var(--line);border-radius:12px;padding:9px 12px;background:#fff;color:var(--ink);min-height:46px}
textarea:focus,input:focus,select:focus{outline:none;border-color:var(--main);box-shadow:0 0 0 3px var(--main-weak)}
textarea{min-height:110px;line-height:1.7}
textarea.tall{min-height:260px}
label.f{display:block;font-size:13px;color:var(--sub);font-weight:700;margin:12px 0 4px}
.chks{display:flex;flex-wrap:wrap;gap:8px}
.chk{display:inline-flex;align-items:center;gap:6px;border:2px solid var(--line);border-radius:99px;padding:6px 14px;cursor:pointer;background:#fff;min-height:42px;font-weight:600}
.chk:has(input:checked){border-color:var(--main);background:var(--main-weak);color:var(--main-dk)}
.chk input{width:18px;height:18px;margin:0}
.flash{background:var(--ok-weak);color:#1f8a57;border-radius:14px;padding:12px 16px;margin-bottom:16px;font-weight:700}
.flash.err{background:var(--ng-weak);color:var(--ng)}
.note{background:var(--sun-weak);border-radius:14px;padding:12px 16px;margin:0 0 16px;border-left:6px solid var(--sun)}
.note.strong{background:var(--warm-weak);border-left-color:var(--warm)}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--sub);font-weight:700;white-space:nowrap}
.tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
.num{font-variant-numeric:tabular-nums;text-align:right}
.cov td.cell{text-align:center;font-weight:800;font-size:16px;min-width:56px}
.cov td.cell a{display:block;border-radius:10px;padding:6px 0;background:var(--main-weak);color:var(--main-dk)}
.cov td.cell.zero a{background:var(--ng-weak);color:var(--ng);border:2px dashed var(--ng)}
.meter{height:12px;border-radius:99px;background:var(--line);overflow:hidden;min-width:90px}
.meter i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--main),var(--sky))}
.meter.low i{background:linear-gradient(90deg,var(--warm),var(--sun))}
details{margin-top:8px}
details summary{cursor:pointer;color:var(--main-dk);font-weight:800;padding:6px 0;list-style:none}
details summary::before{content:"＋ "}details[open] summary::before{content:"－ "}
details summary::-webkit-details-marker{display:none}
ul,ol{padding-left:22px;margin:4px 0}
.src{counter-reset:none;background:#fbfdfe;border:2px solid var(--line);border-radius:14px;overflow-x:auto;font-size:14px}
.src table td{border:0;padding:1px 10px;white-space:pre-wrap;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Noto Sans Mono CJK JP",monospace}
.src td.ln{color:#a7b1bd;text-align:right;user-select:none;width:1%;white-space:nowrap;border-right:2px solid var(--line)}
.src tr.hl td{background:var(--sun-weak)}
mark{background:#ffe07a;color:inherit;border-radius:4px;padding:0 2px;box-shadow:inset 0 -2px 0 var(--warm)}
mark.strong{background:#ffc2bd;box-shadow:inset 0 -2px 0 var(--ng)}
mark.info{background:#d7ebff;box-shadow:inset 0 -2px 0 var(--sky)}
.docname{font-weight:800;color:var(--main-dk)}
.stack>*+*{margin-top:8px}
@media(max-width:600px){main{padding:14px 12px 50px}.card{padding:16px 14px;border-radius:16px}
.card h2,.ribbon{margin-left:-22px}h1{font-size:20px}.btnrow .btn{flex:1}}
"""

JS = """
document.addEventListener('submit',function(ev){var f=ev.target;if(f.dataset&&f.dataset.confirm&&!window.confirm(f.dataset.confirm))ev.preventDefault();});
document.querySelectorAll('input[data-secmap]').forEach(function(a){var m={};try{m=JSON.parse(a.dataset.secmap)}catch(x){}
var s=document.getElementById(a.dataset.sec);if(!s)return;var f=function(){s.setAttribute('list',m[a.value]||'dl-sec-all')};a.addEventListener('input',f);f();});
"""

NAV = (("home", "/", "ダッシュボード"), ("neta", "/neta", "ネタ帳"), ("kakera", "/kakera", "かけら"),
       ("articles", "/articles", "記事と充足度"), ("check", "/check", "チェッカー"), ("guide", "/guide", "使い方"))


def layout(title: str, body: str, active: str = "", flash: str = "", error: bool = False, nav: bool = True) -> str:
    links = "".join(f'<a href="{href}" class="{"on" if key == active else ""}">{label}</a>'
                    for key, href, label in NAV) if nav else ""
    fl = f'<div class="flash {"err" if error else ""}">{e(flash)}</div>' if flash else ""
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>{e(title)} — notewriter</title><style>{CSS}</style></head><body>
<header><div class="bar"><span class="brand">notewriter<small>かけら管理・チェッカー</small></span></div>
{f"<nav>{links}</nav>" if links else ""}</header>
<main>{fl}{body}</main><script>{JS}</script></body></html>"""


# ---------- お知らせ（初回） ----------

NOTICE_BODY = """
<div class="card warm"><h2>1. Remote Control の会話は Anthropic のサーバーに保存されます</h2>
<p>Anthropic Remote Control は、スマホとの会話同期のために、<b>やり取りの全文を Anthropic のサーバー側に保存</b>します。
保持期間は、<b>モデル改善への利用を許可していれば5年、許可していなければ30日</b>です。
Remote Control 経由の壁打ちに書いた内容は、この保持の対象になることを前提に使ってください。</p>
<p class="small muted">この Web 画面（notewriter）自体は外部のサービスにデータを送りません。外部のフォントや画像も読み込みません。</p></div>
<div class="card warm"><h2>2. 「Trusted Devices」を有効にしてください</h2>
<p>Anthropic アカウント設定の<b>「Trusted Devices」</b>を有効にしてください。Remote Control を使う端末を事前に登録し、
定期的に本人確認を求める機能です。スマホを紛失したときなどに、他人が会話を続けられないようにするためです。</p></div>
<div class="card alert"><h2>3. 準備が終わるまで実データを入力しないでください</h2>
<p>次の対策が<b>すべて完了するまで</b>、実データ（ダミーでない事件の詳細）は入力しないでください。
それまでは<b>ダミーデータで動作確認</b>してください。</p>
<ol><li>作業フォルダの暗号化（gocryptfs のマウント。Claude Code のセッション履歴 <code>~/.claude/projects/</code> も同じ暗号化境界に入れる）</li>
<li>ログの無害化（本文や検索語がログに残らないこと）</li><li>一時ファイル対策</li><li>スワップ対策</li>
<li>外部への暗号化バックアップ</li><li>バックアップからの復元テスト</li></ol></div>
"""


def notice_acked(vdir: Path) -> bool:
    data = store.read_json(vdir / NOTICE_FILE, None)
    return isinstance(data, dict) and int(data.get("notice_version", 0) or 0) >= NOTICE_VERSION


def page_notice(next_path: str) -> str:
    return f"""<h1>はじめにお読みください</h1>
<p class="muted">初回のみ表示します。確認後も「使い方」ページからいつでも読めます。</p>{NOTICE_BODY}
<form method="post" action="/notice/ack"><input type="hidden" name="next" value="{e(next_path)}">
<div class="btnrow"><button class="btn primary">確認しました</button></div></form>"""


def page_vault_error(ex: Exception) -> str:
    return f"""<h1>作業フォルダが使えません</h1>
<div class="card alert"><h2>理由</h2><p>{e(ex)}</p></div>
<div class="card"><h2>対処</h2><ol>
<li>本番では、先に gocryptfs で暗号化フォルダをマウントしてください（例: <code>gocryptfs ~/nw-cipher ~/nw-data</code>）。
マウント先を環境変数 <code>NW_DATA_DIR</code> に指定して起動します。</li>
<li>初めて使うときは、マウントした状態で <code>./nw init</code> を実行して作業フォルダと目印を作ってください。</li>
<li>終わったら、この画面を再読み込みしてください。</li></ol>
<p class="small muted">平文フォルダへの誤書き込みを防ぐため、作業フォルダが確認できるまで何も保存しません。</p></div>"""


# ---------- かけら ----------

def badge_list(values, cls: str, param: str = "") -> str:
    out = []
    for v in values or []:
        if param:
            out.append(f'<a class="badge {cls}" href="{e(qurl("/kakera", **{param: v}))}">{"#" if cls == "b-tag" else ""}{e(v)}</a>')
        else:
            out.append(f'<span class="badge {cls}">{"#" if cls == "b-tag" else ""}{e(v)}</span>')
    return "".join(out)


def kakera_card(k: dict) -> str:
    place = " › ".join(x for x in (k.get("article"), k.get("section")) if x) or "記事未設定"
    return f"""<div class="kcard"><div class="head"><a class="kid" href="/kakera/{e(k["id"])}">{e(k["id"])}</a>
{badge_list([k.get("status")] if k.get("status") else [], "b-st", "status")}<span>{e(place)}</span></div>
<p>{e(short(k.get("body", ""), 160))}</p>
<div>{badge_list(k.get("viewpoints"), "b-vp", "viewpoint")}{badge_list(k.get("tags"), "b-tag", "tag")}</div>
<div class="small muted">更新 {e((k.get("updated") or "").replace("T", " "))}</div></div>"""


def article_datalists(articles: List[dict]) -> Tuple[str, str]:
    """記事名の datalist と、記事ごとの区間 datalist。戻り値: (HTML, 記事名→datalist id のJSON)。"""
    opts = "".join(f'<option value="{e(a["name"])}">' for a in articles)
    all_secs: List[str] = []
    parts = [f'<datalist id="dl-articles">{opts}</datalist>']
    secmap: Dict[str, str] = {}
    for i, a in enumerate(articles):
        did = f"dl-sec-{i}"
        secmap[a["name"]] = did
        parts.append(f'<datalist id="{did}">' + "".join(f'<option value="{e(s)}">' for s in a["sections"]) + "</datalist>")
        for s in a["sections"]:
            if s not in all_secs:
                all_secs.append(s)
    parts.append('<datalist id="dl-sec-all">' + "".join(f'<option value="{e(s)}">' for s in all_secs) + "</datalist>")
    return "".join(parts), json.dumps(secmap, ensure_ascii=False)


def place_fields(prefix: str, article: str, section: str, secmap: str) -> str:
    sec_id = f"{prefix}-section"
    return f"""<div class="cols"><div><label class="f" for="{prefix}-article">記事</label>
<input type="text" id="{prefix}-article" name="article" value="{e(article)}" list="dl-articles" data-secmap="{e(secmap)}" data-sec="{sec_id}" placeholder="例: 前編" autocomplete="off"></div>
<div><label class="f" for="{sec_id}">区間</label>
<input type="text" id="{sec_id}" name="section" value="{e(section)}" list="dl-sec-all" placeholder="例: 逮捕の朝" autocomplete="off"></div></div>"""


def viewpoint_fields(cfg: dict, chosen) -> str:
    chosen = list(chosen or [])
    vps = list(cfg.get("viewpoints", []))
    checks = "".join(
        f'<label class="chk"><input type="checkbox" name="viewpoints" value="{e(v)}"{" checked" if v in chosen else ""}>{e(v)}</label>'
        for v in vps)
    extra = ", ".join(v for v in chosen if v not in vps)
    return f"""<label class="f">観点</label><div class="chks">{checks}</div>
<input type="text" name="viewpoints_extra" value="{e(extra)}" placeholder="その他の観点（自由入力・カンマ区切り）" style="margin-top:8px">"""


def kakera_form(k: dict, cfg: dict, articles: List[dict], action: str, submit: str) -> str:
    dls, secmap = article_datalists(articles)
    status = k.get("status") or "未使用"
    statuses = list(cfg.get("statuses", []))
    if status not in statuses:
        statuses.append(status)
    opts = "".join(f'<option value="{e(s)}"{" selected" if s == status else ""}>{e(s)}</option>' for s in statuses)
    return f"""<form method="post" action="{e(action)}">{dls}
<label class="f" for="k-body">本文（実際にあったこと・感じたこと・言われたこと）</label>
<textarea id="k-body" name="body" class="tall" required>{e(k.get("body", ""))}</textarea>
{place_fields("k", k.get("article", ""), k.get("section", ""), secmap)}
{viewpoint_fields(cfg, k.get("viewpoints"))}
<div class="cols"><div><label class="f" for="k-status">使用状況</label><select id="k-status" name="status">{opts}</select></div>
<div><label class="f" for="k-tags">タグ（カンマ区切り）</label><input type="text" id="k-tags" name="tags" value="{e(", ".join(k.get("tags") or []))}"></div></div>
<label class="f" for="k-source">出どころ（任意。例: ネタID、日記の日付）</label>
<input type="text" id="k-source" name="source" value="{e(k.get("source", ""))}">
<div class="btnrow"><button class="btn primary">{e(submit)}</button></div></form>"""


def kakera_fields_from_form(form: "Form") -> dict:
    vps = form.list("viewpoints") + store.split_list(form.get("viewpoints_extra"))
    return {"body": form.get("body", strip=False), "article": form.get("article"), "section": form.get("section"),
            "viewpoints": vps, "status": form.get("status") or "未使用", "tags": store.split_list(form.get("tags")),
            "source": form.get("source")}


def page_kakera_list(qs: dict) -> str:
    K = _kakera()
    cfg = K.load_config()
    articles = K.list_articles()
    f = {k: (qs.get(k) or [""])[0].strip() for k in ("q", "article", "section", "viewpoint", "status", "tag")}
    items = K.search_kakera(**f) if any(f.values()) else K.list_kakera()
    items = sorted(items, key=lambda k: k.get("updated", ""), reverse=True)
    dls, secmap = article_datalists(articles)
    vp_opts = '<option value="">（すべて）</option>' + "".join(
        f'<option value="{e(v)}"{" selected" if v == f["viewpoint"] else ""}>{e(v)}</option>' for v in cfg.get("viewpoints", []))
    st_opts = '<option value="">（すべて）</option>' + "".join(
        f'<option value="{e(v)}"{" selected" if v == f["status"] else ""}>{e(v)}</option>' for v in cfg.get("statuses", []))
    cards = "".join(kakera_card(k) for k in items) or '<div class="card"><p class="muted">該当するかけらはありません。</p></div>'
    cond = "、".join(f"{label}: {f[key]}" for key, label in (("q", "語"), ("article", "記事"), ("section", "区間"),
                                                              ("viewpoint", "観点"), ("status", "使用状況"), ("tag", "タグ")) if f[key])
    return f"""<h1>かけら</h1>
<div class="btnrow" style="margin:0 0 16px"><a class="btn primary" href="/kakera/new">＋ かけらを書く</a><a class="btn" href="/neta">ネタ帳から</a></div>
<div class="card"><h2>探す</h2><form method="get" action="/kakera">{dls}
<label class="f" for="s-q">キーワード（本文・タグ・区間・記事名。空白区切りでAND）</label>
<input type="search" id="s-q" name="q" value="{e(f["q"])}">
{place_fields("s", f["article"], f["section"], secmap)}
<div class="cols"><div><label class="f" for="s-vp">観点</label><select id="s-vp" name="viewpoint">{vp_opts}</select></div>
<div><label class="f" for="s-st">使用状況</label><select id="s-st" name="status">{st_opts}</select></div>
<div><label class="f" for="s-tag">タグ</label><input type="text" id="s-tag" name="tag" value="{e(f["tag"])}"></div></div>
<div class="btnrow"><button class="btn primary">検索</button><a class="btn" href="/kakera">条件をクリア</a></div></form></div>
<p class="muted"><b>{len(items)}</b> 件{f"（{e(cond)}）" if cond else ""}</p>{cards}"""


def page_kakera_new(qs: dict) -> str:
    K = _kakera()
    pre = {k: (qs.get(k) or [""])[0].strip() for k in ("article", "section", "viewpoint")}
    k = {"article": pre["article"], "section": pre["section"], "viewpoints": [pre["viewpoint"]] if pre["viewpoint"] else []}
    return f"""<h1>かけらを書く</h1><div class="card"><h2>新しいかけら</h2>
{kakera_form(k, K.load_config(), K.list_articles(), "/kakera/new", "保存する")}</div>"""


def provenance_table(records: List[dict]) -> str:
    rows = "".join(
        f'<tr><td>{e(r.get("article"))}</td><td>{e(r.get("target"))}</td>'
        f'<td>{e((r.get("generated_at") or "").replace("T", " "))}</td><td>{e(r.get("id"))}</td>'
        f'<td class="small">{e("、".join(r.get("kakera_ids") or []))}</td></tr>' for r in records)
    return (f'<div class="tablewrap"><table><tr><th>記事</th><th>段落・対象</th><th>生成日時</th><th>来歴ID</th>'
            f'<th>根拠にしたかけら</th></tr>{rows}</table></div>')


def page_kakera_detail(kid: str) -> str:
    K = _kakera()
    k = K.get_kakera(kid)
    refs = K.delete_warnings(k["id"])
    ref_html = (provenance_table(refs) if refs else '<p class="muted">このかけらを根拠に生成した段落・記事の記録はありません。</p>')
    src = k.get("source") or ""
    src_html = f'<a href="/neta#{e(src)}">{e(src)}</a>' if re.fullmatch(r"N\d+", src) else e(src or "—")
    return f"""<h1>かけら {e(k["id"])}</h1>
<div class="grid"><div class="card"><h2>編集</h2>{kakera_form(k, K.load_config(), K.list_articles(), "/kakera/" + k["id"], "保存する")}</div>
<div><div class="card"><h2>情報</h2><table>
<tr><th>作成</th><td>{e(k.get("created", "").replace("T", " "))}</td></tr>
<tr><th>更新</th><td>{e(k.get("updated", "").replace("T", " "))}</td></tr>
<tr><th>出どころ</th><td>{src_html}</td></tr></table>
{f'<div class="btnrow"><a class="btn sm" href="{e("/coverage/" + quote(k["article"], safe=""))}">この記事の充足度</a></div>' if k.get("article") else ""}</div>
<div class="card"><h2>生成来歴</h2>{ref_html}</div>
<div class="card"><h2>削除</h2><p class="small muted">削除の前に、このかけらを根拠にした段落・記事を確認します。</p>
<a class="btn ng" href="/kakera/{e(k["id"])}/delete">削除の確認へ</a></div></div></div>"""


def page_kakera_delete(kid: str) -> str:
    K = _kakera()
    k = K.get_kakera(kid)
    refs = K.delete_warnings(k["id"])
    if refs:
        warn = f"""<div class="card alert"><h2>このかけらを根拠に生成した段落・記事があります（{len(refs)}件）</h2>
<p>削除すると、下の段落・記事が「どのかけらから書いたか」をたどれなくなります。本文そのものは消えません。
必要なら先に本文を見直してください。</p>{provenance_table(refs)}</div>"""
        force = """<label class="chk" style="margin-top:6px"><input type="checkbox" name="force" value="1" required>
それでも削除する（生成済みの段落・記事の根拠が失われることを理解しました）</label>"""
    else:
        warn = '<div class="note">このかけらを根拠に生成した段落・記事の記録はありません。</div>'
        force = ""
    return f"""<h1>かけら {e(k["id"])} を削除しますか？</h1>{warn}
<div class="card"><h2>削除するかけら</h2>{kakera_card(k)}<p>{lines_html(k.get("body", ""))}</p></div>
<form method="post" action="/kakera/{e(k["id"])}/delete"><input type="hidden" name="confirm" value="1">{force}
<div class="btnrow"><button class="btn ng solid">削除する</button><a class="btn" href="/kakera/{e(k["id"])}">やめる</a></div></form>"""


# ---------- ネタ帳 ----------

def page_neta(qs: dict) -> str:
    K = _kakera()
    cfg = K.load_config()
    articles = K.list_articles()
    dls, secmap = article_datalists(articles)
    show = (qs.get("show") or ["未整理"])[0]
    all_neta = sorted(K.list_neta(), key=lambda n: n.get("created", ""), reverse=True)
    items = all_neta if show == "all" else [n for n in all_neta if n.get("status") == show]
    counts = Counter(n.get("status") for n in all_neta)
    tabs = "".join(
        f'<a class="btn sm {"primary" if show == key else ""}" href="{e(qurl("/neta", show=key))}">{label}</a>'
        for key, label in (("未整理", f"未整理 {counts.get('未整理', 0)}"), ("かけら化済み", f"かけら化済み {counts.get('かけら化済み', 0)}"),
                           ("all", f"すべて {len(all_neta)}")))
    cards = []
    for n in items:
        nid = e(n["id"])
        done = n.get("status") == "かけら化済み"
        status = (f'<span class="badge b-ok">かけら化済み</span> <a href="/kakera/{e(n.get("kakera_id"))}">{e(n.get("kakera_id"))}</a>'
                  if done else '<span class="badge b-warm">未整理</span>')
        promote = "" if done else f"""<details><summary>かけらにする</summary>
<form method="post" action="/neta/{nid}/promote">{place_fields("p" + nid, "", "", secmap)}
{viewpoint_fields(cfg, [])}<label class="f">追加するタグ（任意）</label><input type="text" name="tags">
<div class="btnrow"><button class="btn primary">かけらにする</button></div></form></details>"""
        cards.append(f"""<div class="kcard" id="{nid}" style="border-left-color:var(--warm)"><div class="head"><b class="kid">{nid}</b>{status}
<span>{e((n.get("created") or "").replace("T", " "))}</span></div><p>{lines_html(n.get("body", ""))}</p>
<div>{badge_list(n.get("tags"), "b-tag")}</div>{promote}
<details><summary>編集</summary><form method="post" action="/neta/{nid}">
<textarea name="body" required>{e(n.get("body", ""))}</textarea>
<label class="f">タグ</label><input type="text" name="tags" value="{e(", ".join(n.get("tags") or []))}">
<div class="btnrow"><button class="btn primary sm">保存</button></div></form>
<form method="post" action="/neta/{nid}/delete" data-confirm="ネタ {nid} を削除します。よろしいですか？">
<div class="btnrow"><button class="btn ng sm">このネタを削除</button></div></form></details></div>""")
    return f"""<h1>ネタ帳</h1>{dls}
<div class="card warm"><h2>思いついたらすぐメモ</h2><form method="post" action="/neta/new">
<textarea name="body" rows="3" placeholder="思い出したこと・書きたいことを1行でも" required></textarea>
<input type="text" name="tags" placeholder="タグ（任意・カンマ区切り）" style="margin-top:8px">
<div class="btnrow"><button class="btn warm">ネタを保存</button></div></form>
<p class="small muted">記事に紐付けなくて大丈夫です。あとで「かけらにする」で記事・区間・観点を付けます。</p></div>
<div class="btnrow" style="margin:0 0 14px">{tabs}</div>
{"".join(cards) or '<div class="card"><p class="muted">ここに表示するネタはありません。</p></div>'}"""


# ---------- 記事と充足度 ----------

def coverage_ratio(cov_section: dict, vps: List[str]) -> float:
    if not vps:
        return 0.0
    return sum(1 for v in vps if (cov_section.get("counts") or {}).get(v, 0) > 0) / len(vps)


def meter(ratio: float) -> str:
    pct = int(round(ratio * 100))
    return f'<div class="meter{" low" if ratio < 0.5 else ""}" title="{pct}%"><i style="width:{pct}%"></i></div>'


def article_ratio(name: str) -> float:
    cov = _kakera().coverage(name)
    secs = cov.get("sections") or []
    if not secs:
        return 0.0
    return sum(coverage_ratio(s, cov["viewpoints"]) for s in secs) / len(secs)


def article_form(a: dict, new: bool) -> str:
    name_field = (f'<label class="f">記事名</label><input type="text" name="name" required placeholder="例: 前編">'
                  if new else f'<input type="hidden" name="name" value="{e(a["name"])}">')
    return f"""<form method="post" action="/articles/save"><input type="hidden" name="mode" value="{"new" if new else "edit"}">{name_field}
<div class="cols"><div><label class="f">シリーズ名</label><input type="text" name="series" value="{e(a.get("series", ""))}"></div>
<div><label class="f">シリーズ内の順番</label><input type="number" name="order" value="{e(a.get("order", 0))}" inputmode="numeric"></div></div>
<label class="f">区間（1行に1つ。上から順に並びます）</label>
<textarea name="sections">{e(chr(10).join(a.get("sections") or []))}</textarea>
<div class="btnrow"><button class="btn primary">{"追加する" if new else "保存する"}</button></div></form>"""


def page_articles() -> str:
    K = _kakera()
    counts = K.stats().get("by_article", {})
    cards = []
    for a in K.list_articles():
        q = quote(a["name"], safe="")
        ratio = article_ratio(a["name"])
        secs = "".join(f'<span class="badge b-vp">{e(s)}</span>' for s in a["sections"]) or '<span class="muted small">区間未設定</span>'
        cards.append(f"""<div class="card"><h2>{e(a["name"])}</h2>
<p class="small muted">シリーズ: {e(a["series"] or "—")} ／ 順番: {e(a["order"])} ／ かけら {counts.get(a["name"], 0)} 件</p>
<div>{secs}</div><div style="margin:10px 0 4px" class="small">充足度 {int(round(ratio * 100))}%</div>{meter(ratio)}
<div class="btnrow"><a class="btn primary sm" href="/coverage/{e(q)}">充足度の表</a>
<a class="btn sm" href="{e(qurl("/kakera", article=a["name"]))}">この記事のかけら</a></div>
<details><summary>編集</summary>{article_form(a, False)}
<form method="post" action="/articles/delete" data-confirm="記事「{e(a["name"])}」を一覧から外します（かけらは消えません）。よろしいですか？">
<input type="hidden" name="name" value="{e(a["name"])}"><div class="btnrow"><button class="btn ng sm">記事を一覧から外す</button></div></form>
</details></div>""")
    return f"""<h1>記事と充足度</h1>
<p class="muted">記事ごとに「区間」を決めておくと、区間×観点でかけらが足りているかを確認できます。</p>
<div class="cols">{"".join(cards) or '<div class="card"><p class="muted">まだ記事がありません。下から追加してください。</p></div>'}</div>
<div class="card warm"><h2>記事を追加</h2>{article_form({}, True)}</div>"""


def page_coverage(name: str) -> Optional[str]:
    K = _kakera()
    names = [a["name"] for a in K.list_articles()]
    cov = K.coverage(name)
    if name not in names and not cov.get("sections") and not cov.get("unsectioned"):
        return None
    vps = cov["viewpoints"]
    head = "".join(f"<th>{e(v)}</th>" for v in vps)
    rows = []
    for s in cov["sections"]:
        cells = []
        for v in vps:
            n = s["counts"].get(v, 0)
            link = qurl("/kakera", article=cov["article"], section=s["section"], viewpoint=v)
            cells.append(f'<td class="cell{" zero" if n == 0 else ""}"><a href="{e(link)}" title="{e(s["section"])} × {e(v)}">{n}</a></td>')
        ratio = coverage_ratio(s, vps)
        missing = "、".join(s.get("missing") or [])
        rows.append(f"""<tr><th style="white-space:normal"><a href="{e(qurl("/kakera", article=cov["article"], section=s["section"]))}">{e(s["section"])}</a>
<div class="small muted">{s.get("total", 0)}件</div></th>{"".join(cells)}
<td style="min-width:120px">{meter(ratio)}<div class="small">{int(round(ratio * 100))}%</div>{f'<div class="small muted">足りない: {e(missing)}</div>' if missing else ""}</td></tr>""")
    table = (f'<div class="tablewrap"><table class="cov"><tr><th>区間</th>{head}<th>充足率</th></tr>{"".join(rows)}</table></div>'
             if rows else '<p class="muted">区間がありません。「記事と充足度」で区間を設定してください。</p>')
    uns = cov.get("unsectioned", 0)
    uns_html = (f'<div class="note">区間が未設定のかけらが <b>{uns}</b> 件あります。'
                f'<a href="{e(qurl("/kakera", article=cov["article"]))}">この記事のかけらを見る</a></div>' if uns else "")
    return f"""<h1>充足度: {e(cov["article"])}</h1>
<p class="muted">区間×観点ごとのかけらの件数です。<b style="color:var(--ng)">0件の枠</b>が、まだ書けていないところ。数字を押すとそのかけらを検索します。</p>
{uns_html}<div class="card"><h2>区間×観点</h2>{table}</div>
<div class="btnrow"><a class="btn primary" href="{e(qurl("/kakera/new", article=cov["article"]))}">＋ この記事のかけらを書く</a><a class="btn" href="/articles">記事一覧へ</a></div>"""


# ---------- ダッシュボード ----------

def page_home() -> str:
    K = _kakera()
    st = K.stats()
    articles = K.list_articles()
    recent = sorted(K.list_kakera(), key=lambda k: k.get("updated", ""), reverse=True)[:5]
    kpi = (f'<a href="/kakera"><b>{st.get("kakera_total", 0)}</b><span>かけら</span></a>'
           f'<a href="/neta"><b>{st.get("neta_unsorted", 0)}</b><span>未整理のネタ（全{st.get("neta_total", 0)}件）</span></a>'
           f'<a href="{e(qurl("/kakera", status="未使用"))}"><b>{(st.get("by_status") or {}).get("未使用", 0)}</b><span>未使用のかけら</span></a>'
           f'<a href="/articles"><b>{len(articles)}</b><span>記事</span></a>')
    by_status = "".join(f'<a class="badge b-st" href="{e(qurl("/kakera", status=s))}">{e(s)} {n}</a>'
                        for s, n in (st.get("by_status") or {}).items())
    art_rows = []
    for a in articles:
        ratio = article_ratio(a["name"])
        art_rows.append(f'<tr><td><a href="/coverage/{e(quote(a["name"], safe=""))}">{e(a["name"])}</a>'
                        f'<div class="small muted">{e(a["series"])}</div></td>'
                        f'<td class="num">{(st.get("by_article") or {}).get(a["name"], 0)}</td>'
                        f'<td style="min-width:120px">{meter(ratio)}<span class="small">{int(round(ratio * 100))}%</span></td></tr>')
    no_art = (st.get("by_article") or {}).get(getattr(K, "NO_ARTICLE", "（記事未設定）"), 0)
    art_html = (f'<div class="tablewrap"><table><tr><th>記事</th><th class="num">かけら</th><th>充足度</th></tr>{"".join(art_rows)}</table></div>'
                if art_rows else '<p class="muted">まだ記事がありません。<a href="/articles">記事と区間を登録</a>すると充足度が見られます。</p>')
    if no_art:
        art_html += f'<p class="small muted">記事が未設定のかけら: {no_art} 件</p>'
    return f"""<h1>ダッシュボード</h1>
<div class="kpi">{kpi}</div>
<div class="grid"><div>
<div class="card"><h2>最近のかけら</h2>{"".join(kakera_card(k) for k in recent) or '<p class="muted">まだかけらがありません。</p>'}
<div class="btnrow"><a class="btn primary" href="/kakera/new">＋ かけらを書く</a><a class="btn" href="/kakera">すべて見る</a></div></div>
<div class="card"><h2>記事ごとの充足度</h2>{art_html}</div></div>
<div><div class="card warm"><h2>ネタ帳</h2><form method="post" action="/neta/new">
<textarea name="body" rows="3" placeholder="思いついたことを1行でも" required></textarea>
<div class="btnrow"><button class="btn warm">ネタを保存</button></div></form>
<p class="small">未整理のネタ <b>{st.get("neta_unsorted", 0)}</b> 件 → <a href="/neta">ネタ帳を開く</a></p></div>
<div class="card"><h2>使用状況</h2><div>{by_status or '<span class="muted">—</span>'}</div></div>
<div class="card"><h2>下書きのチェック</h2><p class="small">禁句・個別助言に読める言い回し・ぼかすべき属性などを警告します（直すかどうかは人が決めます）。</p>
<a class="btn" href="/check">チェッカーを開く</a></div></div></div>"""


# ---------- コンプライアンスチェッカー ----------

def drafts_dir(vdir: Path) -> Path:
    return vdir / "drafts"


def list_drafts(vdir: Path) -> List[str]:
    d = drafts_dir(vdir)
    if not d.is_dir():
        return []
    out = []
    for p in d.rglob("*.md"):
        rel = p.relative_to(d)
        if p.is_file() and not any(part.startswith(".") for part in rel.parts):
            out.append(rel.as_posix())
    return sorted(out)


def read_draft(vdir: Path, rel: str) -> str:
    base = drafts_dir(vdir).resolve()
    p = (base / rel).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        raise KeyError(rel)
    if p.suffix != ".md" or not p.is_file():
        raise KeyError(rel)
    return p.read_text(encoding="utf-8-sig", errors="replace")


def _normalize(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def run_checks(docs: List[Tuple[str, str]], rules=None) -> list:
    """各文書の check_text と（2件以上なら）check_series を実行し、重複を除いて返す。"""
    C = _compliance()
    findings = []
    seen = set()

    def add(fs):
        for f in fs:
            key = (f.doc, f.line, f.category, f.match, f.rule, f.start, f.end, f.message)
            if key not in seen:
                seen.add(key)
                findings.append(f)

    for name, text in docs:
        add(C.check_text(text, rules=rules, name=name))
    if len(docs) > 1:
        add(C.check_series(docs, rules=rules))
    return findings


def _sev(f) -> str:
    return f.severity if f.severity in SEVERITY_LABEL else "warn"


def mark_line(line: str, spans: List[Tuple[int, int, str, str]]) -> str:
    """line の [start, end) を <mark> で囲む（重なりは詰め、各部分を個別にエスケープ）。"""
    out, pos = [], 0
    for s, t, title, cls in sorted(spans):
        s = max(s, pos)
        if t <= s:
            continue
        out.append(e(line[pos:s]))
        out.append(f'<mark class="{cls}" title="{e(title)}">{e(line[s:t])}</mark>')
        pos = t
    out.append(e(line[pos:]))
    return "".join(out)


def render_source(name: str, text: str, findings: list, idx: int = 0) -> str:
    lines = text.split("\n")
    spans: Dict[int, list] = {}
    whole: Dict[int, List[str]] = {}
    for f in findings:
        if f.doc != name or not isinstance(f.line, int) or not (1 <= f.line <= len(lines)):
            continue
        line = lines[f.line - 1]
        s, t = getattr(f, "start", -1), getattr(f, "end", -1)
        if not (isinstance(s, int) and isinstance(t, int) and 0 <= s < t <= len(line)):
            s = line.find(f.match) if f.match else -1
            t = s + len(f.match) if s >= 0 else -1
        title = f"{f.category}: {f.message}"
        if s >= 0:
            spans.setdefault(f.line, []).append((s, t, title, _sev(f)))
        else:
            whole.setdefault(f.line, []).append(title)
    rows = []
    for i, line in enumerate(lines, 1):
        hl = i in whole or i in spans
        title = f' title="{e(" / ".join(whole[i]))}"' if i in whole else ""
        rows.append(f'<tr id="{_anchor(idx, i)}" class="{"hl" if hl else ""}"{title}><td class="ln">{i}</td>'
                    f'<td>{mark_line(line, spans.get(i, []))}</td></tr>')
    return f'<div class="src"><table>{"".join(rows)}</table></div>'


def _anchor(idx: int, line: int) -> str:
    return f"d{idx}-L{line}"


def page_check_result(docs: List[Tuple[str, str]]) -> str:
    C = _compliance()
    rules = C.load_rules()
    findings = run_checks(docs, rules)
    names = [n for n, _ in docs]
    summ = C.summarize(findings)
    by_sev = Counter(_sev(f) for f in findings)
    summary_badges = "".join(f'<span class="badge b-{s}">{SEVERITY_LABEL[s]} {by_sev[s]}</span>'
                             for s in ("strong", "warn", "info") if by_sev.get(s))
    cat_rows = "".join(f"<tr><td>{e(c)}</td><td class='num'>{n}</td></tr>" for c, n in (summ.get("by_category") or {}).items())
    ordered = sorted(findings, key=lambda f: (names.index(f.doc) if f.doc in names else len(names),
                                              f.line if isinstance(f.line, int) else 0, SEVERITY_ORDER.get(_sev(f), 9)))
    frows = []
    for f in ordered:
        loc = f"{f.line}行" if isinstance(f.line, int) and f.line > 0 else "—"
        link = f'<a href="#{_anchor(names.index(f.doc), f.line)}">{loc}</a>' if f.doc in names and isinstance(f.line, int) and f.line > 0 else loc
        doc = f'<div class="small docname">{e(f.doc)}</div>' if len(docs) > 1 else ""
        frows.append(f'<tr><td style="white-space:nowrap">{doc}{link}</td><td><span class="badge b-{_sev(f)}">{e(SEVERITY_LABEL[_sev(f)])}</span>'
                     f'<div class="small">{e(f.category)}</div></td><td><b>{e(f.match)}</b>'
                     f'<div class="small muted">{e(f.excerpt)}</div></td><td>{e(f.message)}</td></tr>')
    if findings:
        summary = (f'<p>指摘 <b>{len(findings)}</b> 件 {summary_badges}</p>'
                   f'<div class="tablewrap"><table><tr><th>カテゴリ</th><th class="num">件数</th></tr>{cat_rows}</table></div>')
        table = (f'<div class="tablewrap"><table class="findings"><tr><th>場所</th><th>重さ・カテゴリ</th><th>該当語</th><th>説明</th></tr>'
                 f'{"".join(frows)}</table></div>')
    else:
        summary = '<p>ルールに当てはまる箇所は見つかりませんでした。</p><p class="small muted">見つからない＝問題がない、ではありません。公開前には必ず人が読んで確認してください。</p>'
        table = ""
    series = f'<p class="small muted">シリーズ検査（並び順）: {e(" → ".join(names))}</p>' if len(docs) > 1 else ""
    sources = "".join(render_source_block(n, t, findings, i) for i, (n, t) in enumerate(docs))
    return f"""<div class="card warm"><h2>結果のまとめ</h2>{series}{summary}</div>
{f'<div class="card"><h2>指摘一覧</h2>{table}</div>' if table else ""}{sources}"""


def render_source_block(name: str, text: str, findings: list, idx: int = 0) -> str:
    n = sum(1 for f in findings if f.doc == name)
    return f'<div class="card"><h2>{e(name)}（指摘 {n} 件）</h2>{render_source(name, text, findings, idx)}</div>'


def rules_info() -> str:
    C = _compliance()
    try:
        rules = C.load_rules()
    except Exception as ex:  # ルールファイルの書式エラーなど
        return f'<div class="card alert"><h2>ルールファイルを読めません</h2><p>{e(ex)}</p></div>'
    problems = C.validate_rules(rules)
    srcs = "".join(f"<li><code>{e(s)}</code></li>" for s in rules.get("_sources", [])) or "<li>（なし）</li>"
    prob = ("".join(f"<li>{e(p)}</li>" for p in problems) if problems else "")
    prob_html = (f'<div class="note strong"><b>ルールファイルに問題があります</b><ul>{prob}</ul></div>' if problems
                 else '<p class="small">ルールファイルの検証: 問題なし</p>')
    return f"""<details><summary>ルールファイル（編集できます）</summary><ul class="small">{srcs}</ul>{prob_html}</details>"""


def page_check(vdir: Path, result: str = "", pasted: str = "", paste_name: str = "", selected=()) -> str:
    drafts = list_drafts(vdir)
    selected = set(selected or ())
    dl = "".join(f'<label class="chk" style="width:100%;border-radius:12px"><input type="checkbox" name="drafts" value="{e(d)}"'
                 f'{" checked" if d in selected else ""}>{e(d)}</label>' for d in drafts)
    dl_html = (f'<form method="post" action="/check"><input type="hidden" name="mode" value="drafts"><div class="stack">{dl}</div>'
               f'<p class="small muted">複数選ぶとシリーズ検査（記事間の日付・年齢の矛盾など）もします。並び順はファイル名順です。</p>'
               f'<div class="btnrow"><button class="btn primary">選んだ下書きをチェック</button></div></form>'
               if drafts else f'<p class="muted small">作業フォルダの <code>drafts/</code> に .md がありません（{e(drafts_dir(vdir))}）。</p>')
    return f"""<h1>コンプライアンスチェッカー</h1>
<div class="note strong"><b>警告は判断材料です。直すかどうかは人が決めます。</b><br>
<span class="small">このチェッカーは本文を書き換えません。貼り付けた本文・アップロードしたファイルは保存せず、この画面に表示するだけです。</span></div>
{rules_info()}
{f'<div id="result">{result}</div>' if result else ""}
<div class="cols"><div class="card"><h2>貼り付けてチェック</h2><form method="post" action="/check"><input type="hidden" name="mode" value="paste">
<label class="f">名前（任意）</label><input type="text" name="name" value="{e(paste_name)}" placeholder="例: 前編">
<label class="f">Markdown本文</label><textarea name="text" class="tall" required>{e(pasted)}</textarea>
<div class="btnrow"><button class="btn primary">チェックする</button></div></form></div>
<div><div class="card"><h2>下書きから選ぶ</h2>{dl_html}</div>
<div class="card"><h2>ファイルをアップロード</h2><form method="post" action="/check" enctype="multipart/form-data">
<input type="hidden" name="mode" value="upload"><input type="file" name="files" accept=".md,.markdown,.txt,text/markdown,text/plain" multiple required>
<p class="small muted">複数選ぶとシリーズ検査もします（ファイル名順）。ファイルは保存しません。</p>
<div class="btnrow"><button class="btn primary">アップロードしてチェック</button></div></form></div></div></div>"""


# ---------- 使い方 ----------

def page_guide() -> str:
    return f"""<h1>使い方</h1>
<div class="card"><h2>流れ</h2><ol>
<li><b>ネタ帳</b>に、思い出したことを1行でもメモします（記事に紐付けなくてOK）。</li>
<li>ネタを<b>「かけらにする」</b>で、記事・区間・観点（五感・体の反応・セリフ・分岐点など）を付けます。直接<b>かけらを書く</b>こともできます。</li>
<li><b>記事と充足度</b>で記事ごとに区間を決めると、区間×観点の表で「まだ書けていないところ」（0件の赤い枠）が分かります。</li>
<li>下書きができたら<b>チェッカー</b>で、禁句・個別助言に読める言い回し・ぼかすべき属性・出典のない統計・シリーズ内の矛盾を確認します。
警告は判断材料です。直すかどうかは人が決めます（自動で書き換える機能はありません）。</li></ol>
<p class="small muted">かけらを削除するときは、そのかけらを根拠に生成した段落・記事（生成来歴）を先に表示します。</p></div>
<div class="card"><h2>コマンド</h2><ul class="small">
<li><code>./nw serve</code> … この画面（既定 127.0.0.1:8766）。スマホから使うときは <code>./nw serve --host 100.x.y.z</code>（Tailscale のIP）</li>
<li><code>./nw kakera add / list / search / show / rm</code>、<code>./nw neta add / list / promote</code>、<code>./nw article add / list</code></li>
<li><code>./nw coverage 記事名</code>、<code>./nw check 下書き.md ...</code>、<code>./nw rules</code></li></ul></div>
<h1 style="margin-top:28px">初回のお知らせ（再掲）</h1>{NOTICE_BODY}"""


# ---------- フォーム ----------

class Form:
    """POST の中身。値は \\r\\n を \\n にそろえる。files は [(フィールド名, ファイル名, bytes)]。"""

    def __init__(self, fields: Dict[str, List[str]], files: List[Tuple[str, str, bytes]]):
        self.fields = fields
        self.files = files

    def get(self, key: str, default: str = "", strip: bool = True) -> str:
        v = (self.fields.get(key) or [default])[0]
        return v.strip() if strip else v

    def list(self, key: str) -> List[str]:
        return [v.strip() for v in self.fields.get(key, []) if v.strip()]


def _decode_filename(name: str) -> str:
    try:
        return name.encode("utf-8", "surrogateescape").decode("utf-8")
    except (UnicodeError, AttributeError):
        return name or ""


def parse_form(content_type: str, body: bytes) -> Form:
    fields: Dict[str, List[str]] = {}
    files: List[Tuple[str, str, bytes]] = []
    if content_type.lower().startswith("multipart/form-data"):
        msg = BytesParser(policy=policy.HTTP).parsebytes(
            b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
        if not msg.is_multipart():
            raise ValueError("フォームの形式を読み取れませんでした。")
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            data = part.get_payload(decode=True) or b""
            filename = part.get_filename()
            if filename is not None:
                if data or filename:
                    files.append((name, _decode_filename(filename), data))
            else:
                fields.setdefault(name, []).append(_normalize(data.decode("utf-8", "replace")))
    else:
        for k, vs in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items():
            fields[k] = [_normalize(v) for v in vs]
    return Form(fields, files)


# ---------- サーバー ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "notewriter"
    sys_version = ""

    def log_message(self, fmt, *args):  # 本文・検索語・記事名がログに残らないよう、メソッドと先頭の区切りだけ
        if getattr(self.server, "quiet", False):
            return
        first = urlparse(self.path).path.split("/")[1:2]
        sys.stderr.write(f"{self.command} /{first[0] if first else ''} {args[1] if len(args) > 1 else ''}\n")

    # --- 送信 ---
    def _headers(self, status: int, ctype: str = "text/html; charset=utf-8", length: Optional[int] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; "
                         "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()

    def _send(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self._headers(status, length=len(data))
        self.wfile.write(data)

    def _page(self, title: str, body: str, active: str = "", status: int = 200, flash: str = "", error: bool = False,
              nav: bool = True) -> None:
        self._send(layout(title, body, active, flash, error, nav), status)

    def _redirect_to(self, location: str, flash: str = "", error: bool = False) -> None:
        if flash:
            loc, sep, frag = location.partition("#")
            loc += ("&" if "?" in loc else "?") + ("err=" if error else "msg=") + quote(flash)
            location = loc + sep + frag
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    # --- 確認 ---
    def _host_ok(self) -> bool:
        """Host ヘッダーが自分宛てか（DNSリバインディング対策）。Tailscale の MagicDNS 名（*.ts.net）は許す。"""
        host = self.headers.get("Host") or ""
        name = urlparse("//" + host).hostname or ""
        allowed = getattr(self.server, "allowed_names", {"127.0.0.1", "localhost", "::1"})
        return name in allowed or name.endswith(".ts.net")

    def _same_origin(self) -> bool:
        """POST が同じオリジンの画面から来たか。

        Referrer-Policy: no-referrer のもとでは、ブラウザは POST の Origin を "null" にするため、
        Sec-Fetch-Site（主要ブラウザが送る）で判定し、なければ既存どおり Origin/Referer とHostを比べる。
        """
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site != "same-origin":
            return False
        origin = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if not origin:
            return True
        if origin == "null":
            return site == "same-origin"
        return urlparse(origin).netloc == self.headers.get("Host")

    def _vault(self) -> Optional[Path]:
        try:
            return store.vault()
        except store.VaultError as ex:
            self._page("作業フォルダが使えません", page_vault_error(ex), status=503, nav=False)
            return None

    # --- GET ---
    def do_GET(self) -> None:
        if not self._host_ok():
            self._send("bad host", 400)
            return
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        flash = (qs.get("msg") or qs.get("err") or [""])[0]
        err = "err" in qs
        vdir = self._vault()
        if vdir is None:
            return
        path = url.path.rstrip("/") or "/"
        if not notice_acked(vdir):
            nxt = url.path if url.path.startswith("/") and not url.path.startswith("//") else "/"
            self._page("はじめにお読みください", page_notice(nxt), nav=False)
            return
        try:
            page, title, active = None, "", ""
            if path == "/":
                page, title, active = page_home(), "ダッシュボード", "home"
            elif path == "/kakera":
                page, title, active = page_kakera_list(qs), "かけら", "kakera"
            elif path == "/kakera/new":
                page, title, active = page_kakera_new(qs), "かけらを書く", "kakera"
            elif path == "/neta":
                page, title, active = page_neta(qs), "ネタ帳", "neta"
            elif path == "/articles":
                page, title, active = page_articles(), "記事と充足度", "articles"
            elif path == "/check":
                page, title, active = page_check(vdir), "チェッカー", "check"
            elif path in ("/guide", "/notice"):
                page, title, active = page_guide(), "使い方", "guide"
            elif m := re.fullmatch(r"/kakera/([A-Za-z0-9]+)", path):
                page, title, active = page_kakera_detail(m[1]), f"かけら {m[1]}", "kakera"
            elif m := re.fullmatch(r"/kakera/([A-Za-z0-9]+)/delete", path):
                page, title, active = page_kakera_delete(m[1]), f"かけら {m[1]} の削除", "kakera"
            elif m := re.fullmatch(r"/coverage/([^/]+)", path):
                name = unquote(m[1])
                page, title, active = page_coverage(name), f"充足度 {name}", "articles"
            if page is None:
                self._page("見つかりません", "<h1>見つかりません</h1>", status=404)
            else:
                self._page(title, page, active, flash=flash, error=err)
        except KeyError:
            self._page("見つかりません", "<h1>見つかりません</h1><p>指定したものはありません（削除された可能性があります）。</p>", status=404)
        except store.VaultError as ex:
            self._page("作業フォルダが使えません", page_vault_error(ex), status=503, nav=False)
        except Exception as ex:  # 本文を含みうるのでトレースバックは出さない
            sys.stderr.write(f"error: {type(ex).__name__}\n")
            self._page("エラー", f"<h1>エラーが起きました</h1><p>{e(type(ex).__name__)}: {e(ex)}</p>", status=500)

    # --- POST ---
    def do_POST(self) -> None:
        if not self._host_ok():
            self._send("bad host", 400)
            return
        if not self._same_origin():
            self._send("forbidden", 403)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self._send("too large", 413)
            return
        body = self.rfile.read(length)
        vdir = self._vault()
        if vdir is None:
            return
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            form = parse_form(self.headers.get("Content-Type") or "", body)
        except ValueError as ex:
            self._page("エラー", f"<h1>送信内容を読み取れません</h1><p>{e(ex)}</p>", status=400)
            return
        if path == "/notice/ack":
            store.write_json(vdir / NOTICE_FILE, {"acknowledged_at": store.now(), "notice_version": NOTICE_VERSION})
            nxt = form.get("next") or "/"
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = "/"
            self._redirect_to(nxt, "お知らせを確認しました。「使い方」からいつでも読み返せます")
            return
        if not notice_acked(vdir):
            self._redirect_to("/")
            return
        try:
            self._post(path, form, vdir)
        except (ValueError, KeyError) as ex:
            msg = ex.args[0] if isinstance(ex, KeyError) and ex.args else ex
            if isinstance(ex, KeyError):
                msg = f"見つかりません: {msg}"
            self._redirect_to(self._back(path), f"エラー: {msg}", error=True)
        except store.VaultError as ex:
            self._page("作業フォルダが使えません", page_vault_error(ex), status=503, nav=False)

    @staticmethod
    def _back(path: str) -> str:
        if re.fullmatch(r"/neta/[A-Za-z0-9]+(/.*)?", path):
            return "/neta"
        if m := re.fullmatch(r"/kakera/([A-Za-z0-9]+)/delete", path):
            return f"/kakera/{m[1]}/delete"
        if m := re.fullmatch(r"/kakera/([A-Za-z0-9]+)", path):
            return "/kakera/new" if m[1] == "new" else f"/kakera/{m[1]}"
        if path.startswith("/articles"):
            return "/articles"
        return "/"

    def _post(self, path: str, form: Form, vdir: Path) -> None:
        K = _kakera()
        if path == "/check":
            self._post_check(form, vdir)
            return
        if path == "/kakera/new":
            fields = kakera_fields_from_form(form)
            if not fields["body"].strip():
                raise ValueError("本文が空です。")
            k = K.create_kakera(**fields)
            self._redirect_to(f"/kakera/{k['id']}", f"かけら {k['id']} を保存しました")
        elif m := re.fullmatch(r"/kakera/([A-Za-z0-9]+)/delete", path):
            kid = m[1]
            K.get_kakera(kid)
            refs = K.delete_warnings(kid)
            force = form.get("force") == "1"
            if refs and not force:
                self._redirect_to(f"/kakera/{kid}/delete",
                                  "生成来歴に参照があります。「それでも削除する」にチェックを入れてください", error=True)
                return
            try:
                K.delete_kakera(kid, force=bool(refs) and force)
            except K.ReferencedError:
                self._redirect_to(f"/kakera/{kid}/delete", "生成来歴に参照が追加されました。内容を確認してください", error=True)
                return
            self._redirect_to("/kakera", f"かけら {kid} を削除しました")
        elif m := re.fullmatch(r"/kakera/([A-Za-z0-9]+)", path):
            fields = kakera_fields_from_form(form)
            if not fields["body"].strip():
                raise ValueError("本文が空です。")
            k = K.update_kakera(m[1], **fields)
            self._redirect_to(f"/kakera/{k['id']}", "保存しました")
        elif path == "/neta/new":
            text = form.get("body", strip=False)
            if not text.strip():
                raise ValueError("ネタが空です。")
            n = K.create_neta(text, tags=store.split_list(form.get("tags")))
            self._redirect_to("/neta", f"ネタ {n['id']} を保存しました")
        elif m := re.fullmatch(r"/neta/([A-Za-z0-9]+)/promote", path):
            vps = form.list("viewpoints") + store.split_list(form.get("viewpoints_extra"))
            k = K.promote_neta(m[1], article=form.get("article"), section=form.get("section"), viewpoints=vps,
                               tags=store.split_list(form.get("tags")))
            self._redirect_to(f"/kakera/{k['id']}", f"ネタ {m[1]} をかけら {k['id']} にしました")
        elif m := re.fullmatch(r"/neta/([A-Za-z0-9]+)/delete", path):
            K.delete_neta(m[1])
            self._redirect_to("/neta", f"ネタ {m[1]} を削除しました")
        elif m := re.fullmatch(r"/neta/([A-Za-z0-9]+)", path):
            text = form.get("body", strip=False)
            if not text.strip():
                raise ValueError("ネタが空です。")
            K.update_neta(m[1], body=text, tags=store.split_list(form.get("tags")))
            self._redirect_to(f"/neta?show=all#{m[1]}", "保存しました")
        elif path == "/articles/save":
            name = form.get("name")
            if form.get("mode") == "new" and any(a["name"] == name for a in K.list_articles()):
                raise ValueError(f"「{name}」という記事はすでにあります。一覧の「編集」から変更してください。")
            order_raw = form.get("order") or "0"
            try:
                order = int(order_raw)
            except ValueError:
                raise ValueError("順番は数字で入力してください。")
            sections = [s.strip() for s in form.get("sections", strip=False).split("\n") if s.strip()]
            a = K.save_article(name, series=form.get("series"), order=order, sections=sections)
            self._redirect_to("/articles", f"記事「{a['name']}」を保存しました")
        elif path == "/articles/delete":
            K.delete_article(form.get("name"))
            self._redirect_to("/articles", f"記事「{form.get('name')}」を一覧から外しました")
        else:
            self._send("not found", 404)

    def _post_check(self, form: Form, vdir: Path) -> None:
        """チェック結果をその場で表示する（本文はどこにも保存しない）。"""
        mode = form.get("mode")
        docs: List[Tuple[str, str]] = []
        pasted, paste_name, selected = "", "", []
        if mode == "drafts":
            selected = sorted(set(form.list("drafts")))
            if not selected:
                raise ValueError("下書きを1つ以上選んでください。")
            docs = [(rel, _normalize(read_draft(vdir, rel))) for rel in selected]
        elif mode == "upload":
            ups = sorted([(fn or "無題.md", data) for _, fn, data in form.files if data], key=lambda x: x[0])
            if not ups:
                raise ValueError("ファイルを選んでください。")
            names_seen: Counter = Counter()
            for fn, data in ups:
                base = Path(fn).name or "無題.md"
                names_seen[base] += 1
                name = base if names_seen[base] == 1 else f"{base} ({names_seen[base]})"
                docs.append((name, _normalize(data.decode("utf-8-sig", "replace"))))
        else:
            pasted = form.get("text", strip=False)
            paste_name = form.get("name")
            if not pasted.strip():
                raise ValueError("本文を貼り付けてください。")
            docs = [(paste_name or "貼り付けた本文", pasted)]
        result = page_check_result(docs)
        self._page("チェック結果", page_check(vdir, result, pasted, paste_name, selected), "check")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    quiet = False
    allowed_names = {"127.0.0.1", "localhost", "::1"}


class _Server6(_Server):
    address_family = socket.AF_INET6


def make_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """待ち受けアドレスを確認してサーバーを作る（外部公開になるアドレスは HostRefused）。"""
    h = check_host(host)
    cls = _Server6 if ":" in h else _Server
    httpd = cls((h, port), Handler)
    httpd.allowed_names = {"127.0.0.1", "localhost", "::1", h}
    return httpd


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, open_browser: bool = False) -> None:
    httpd = make_server(host, port)
    shown = f"[{host}]" if ":" in host else host
    url = f"http://{shown}:{httpd.server_address[1]}"
    try:
        store.vault()
    except store.VaultError as ex:
        print(f"注意: {ex}\n（画面には理由と対処だけを表示します）", file=sys.stderr)
    print(f"notewriter: {url}  （止めるには Ctrl+C）")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました")
    finally:
        httpd.server_close()
