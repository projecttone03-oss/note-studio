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
input[type=date],input[type=password]{width:100%;font:inherit;font-size:16px;border:2px solid var(--line);border-radius:12px;padding:9px 12px;background:#fff;color:var(--ink);min-height:46px}
.btn[disabled]{opacity:.45;cursor:not-allowed;filter:grayscale(.6);transform:none;box-shadow:none}
.opt{display:flex;gap:10px;align-items:flex-start;border:2px solid var(--line);border-radius:14px;padding:10px 14px;background:#fff;cursor:pointer;margin:0 0 8px}
.opt:has(input:checked){border-color:var(--main);background:var(--main-weak)}
.opt.off{cursor:not-allowed;background:var(--bg);color:var(--sub)}
.opt input{width:20px;height:20px;margin:4px 0 0;flex:none}
.opts{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:0 10px}
pre.send{white-space:pre-wrap;word-break:break-word;background:#fbfdfe;border:2px solid var(--line);border-radius:14px;padding:12px 14px;font-size:14px;line-height:1.7;max-height:520px;overflow:auto;margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Noto Sans Mono CJK JP",monospace}
.drop{position:relative;display:block;border:3px dashed var(--main);border-radius:18px;background:var(--main-weak);padding:34px 16px;text-align:center;font-weight:800;color:var(--main-dk);cursor:pointer}
.drop.over{background:#cdeef1;border-style:solid}
.drop input[type=file]{position:absolute;top:0;left:0;width:100%;height:100%;opacity:0;cursor:pointer;min-height:0;padding:0;border:0}
.drop .dropnames{display:block;font-weight:600;color:var(--ink);margin-top:6px}
.md h3{font-size:16px}.md h4{font-size:15px;margin:14px 0 4px}.md p{margin:6px 0 10px}
.money{font-size:22px;font-weight:800;font-variant-numeric:tabular-nums}
.steps>li{margin-bottom:10px}
.b-hyp{background:var(--warm-weak);color:var(--warm-dk);border:1px dashed var(--warm)}
.icard{border-left-color:var(--sun)}.icard .idea{font-size:16px;font-weight:800;margin:4px 0 6px}
.icard dl{margin:4px 0 8px}.icard dt{font-size:12px;color:var(--sub);font-weight:700;margin-top:6px}.icard dd{margin:0}
.icard .pick{display:inline-flex;align-items:center;gap:8px;font-weight:800;cursor:pointer;min-height:40px}
.icard .pick input{width:22px;height:22px;margin:0}
.sticky{position:sticky;bottom:10px;z-index:5;background:var(--card);border-radius:16px;padding:10px 14px;box-shadow:var(--shadow)}
.example{background:var(--main-weak);border-radius:14px;padding:12px 16px;margin:0 0 12px;border-left:6px solid var(--main)}
@media(max-width:600px){main{padding:14px 12px 50px}.card{padding:16px 14px;border-radius:16px}
.card h2,.ribbon{margin-left:-22px}h1{font-size:20px}.btnrow .btn{flex:1}}
"""

JS = """
document.addEventListener('submit',function(ev){var f=ev.target;if(f.dataset&&f.dataset.confirm&&!window.confirm(f.dataset.confirm))ev.preventDefault();});
document.querySelectorAll('input[data-secmap]').forEach(function(a){var m={};try{m=JSON.parse(a.dataset.secmap)}catch(x){}
var s=document.getElementById(a.dataset.sec);if(!s)return;var f=function(){s.setAttribute('list',m[a.value]||'dl-sec-all')};a.addEventListener('input',f);f();});
document.querySelectorAll('input[data-drop]').forEach(function(i){var b=i.parentNode,o=b.querySelector('.dropnames');
i.addEventListener('change',function(){var n=[];for(var k=0;k<i.files.length;k++)n.push(i.files[k].name);if(o)o.textContent=n.length?n.length+'件: '+n.join('、'):'';});
['dragenter','dragover'].forEach(function(t){i.addEventListener(t,function(){b.classList.add('over')})});
['dragleave','drop'].forEach(function(t){i.addEventListener(t,function(){b.classList.remove('over')})});});
"""

NAV = (("home", "/", "ダッシュボード"), ("ideas", "/ideas", "ネタ出し"), ("neta", "/neta", "ネタ帳"),
       ("kakera", "/kakera", "かけら"), ("articles", "/articles", "記事と充足度"), ("research", "/research", "リサーチ"),
       ("reactions", "/reactions", "反応記録"), ("check", "/check", "チェッカー"), ("guide", "/guide", "使い方"))


def layout(title: str, body: str, active: str = "", flash: str = "", error: bool = False, nav: bool = True,
           head: str = "") -> str:
    links = "".join(f'<a href="{href}" class="{"on" if key == active else ""}">{label}</a>'
                    for key, href, label in NAV) if nav else ""
    fl = f'<div class="flash {"err" if error else ""}">{e(safe(flash))}</div>' if flash else ""
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>{e(title)} — notewriter</title>{head}<style>{CSS}</style></head><body>
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
{ideas_home_card()}
{research_home_card()}
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


# ---------- リサーチ ----------
# ロジックは research.py / secrets.py。ここは表示と入力だけ。
# 外に送るのは、人がリサーチ画面に書いた質問文（＋テンプレート）だけ。送る前に全文を見せて、OK を押してから送る。
# APIキーはどのページにも出さない（登録状態は「登録済み／未登録」だけ）。例外メッセージは safe() を通す。

def _research():
    from . import research
    return research


def _secrets():
    from . import secrets as nw_secrets
    return nw_secrets


def research_available() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("notewriter.research") is not None
    except (ImportError, ValueError):
        return False


_KEYISH = re.compile(r"pplx-[A-Za-z0-9_\-]{6,}")


def safe(text) -> str:
    """画面・ログに出す前に、APIキーらしき文字列を伏せる（secrets.redact ＋ 念のための形での伏せ字）。"""
    s = "" if text is None else str(text)
    if not s:
        return s
    try:
        s = _secrets().redact(s)
    except Exception:  # secrets.py がまだない・壊れているときも、下の伏せ字は必ずかける
        pass
    return _KEYISH.sub("pplx-****", s)


KIND_SHORT = {"trend": "トレンド", "deep": "深掘り", "import": "手動取り込み"}
COST_KIND = {"claude": "追加料金なし", "pplx_standard": "使った分だけ", "pplx_deep": "使った分だけ（高め）"}
JOB_STATUS = {"running": ("調べています", "b-st"), "done": ("完了", "b-ok"), "error": ("失敗", "b-strong"),
              "limit": ("上限で停止", "b-warm")}
COST_SOURCE = {"api": "APIの報告額", "computed": "トークン数から計算", "free": "追加料金なし", "none": "課金なし（失敗）",
               "unknown": "不明（課金されたか分からない）"}
ID_RE = r"[A-Za-z0-9][A-Za-z0-9_\-]*"


def yen(v) -> str:
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return "—"
    if 0 < n < 1:
        return "1円未満"
    return f"{int(round(n)):,}円"


def est_text(est) -> str:
    """estimate() の結果を「だいたい ◯〜◯円」に。無料なら「追加料金なし」。"""
    if isinstance(est, (int, float)):
        return f"だいたい {yen(est)}"
    if not isinstance(est, dict) or not est.get("paid"):
        return "追加料金なし"
    lo, hi = yen(est.get("jpy_low")), yen(est.get("jpy_high"))
    return f"だいたい {hi}" if lo == hi else f"だいたい {lo}〜{hi}"


def kind_label(kind: str) -> str:
    if kind == "import":
        return "手動取り込み"
    try:
        return _research().KINDS.get(kind, kind or "—")
    except Exception:
        return kind or "—"


def provider_name(key: str) -> str:
    try:
        return _research().provider_label(key)
    except Exception:
        return key or "—"


def provider_model(cfg: dict, key: str) -> str:
    return str(((cfg.get("providers") or {}).get(key) or {}).get("model") or "")


def article_options(current: str, none_label: str = "（記事なし）") -> str:
    names = [a["name"] for a in _kakera().list_articles()]
    if current and current not in names:
        names.append(current)
    return (f'<option value="">{e(none_label)}</option>' +
            "".join(f'<option value="{e(n)}"{" selected" if n == current else ""}>{e(n)}</option>' for n in names))


def usage_line(u: dict) -> str:
    return (f'今月（{e(u.get("month"))}）の使用額 <b>{yen(u.get("total_jpy"))}</b> ／ 上限 {yen(u.get("budget_jpy"))}'
            f'（残り {yen(u.get("remaining_jpy"))}）')


def research_vals(src) -> dict:
    """フォームまたはクエリ文字列から入力値を取り出す。"""
    if isinstance(src, Form):
        return {"kind": src.get("kind"), "provider": src.get("provider"), "article": src.get("article"),
                "query": src.get("query", strip=False)}
    return {k: (src.get(k) or [""])[0] for k in ("kind", "provider", "article", "query")}


def hidden_fields(vals: dict, keys=("kind", "provider", "article", "query")) -> str:
    return "".join(f'<input type="hidden" name="{k}" value="{e(vals.get(k, ""))}">' for k in keys)


def page_research(vals: dict) -> str:
    R, S = _research(), _secrets()
    cfg = R.load_config()
    kind = vals.get("kind") if vals.get("kind") in R.KINDS else "trend"
    has_key = S.has_api_key()
    usage = R.month_usage()
    blocked: Dict[str, str] = {}
    for p in R.PROVIDER_KEYS:
        if not R.is_paid(p):
            continue
        if not has_key:
            blocked[p] = 'APIキー未登録（<a href="/guide#perplexity">使い方を見る</a>・<a href="/settings/api-key">登録する</a>）'
            continue
        checks = [R.budget_check(p, k) for k in R.KINDS]
        if not any(ok for ok, _ in checks):
            blocked[p] = "今月の上限に達しています: " + e(safe(checks[0][1]))
    provider = vals.get("provider") or cfg.get("default_provider") or "claude"
    if provider not in R.PROVIDER_KEYS or provider in blocked:
        provider = next((p for p in R.PROVIDER_KEYS if p not in blocked), "")
    kinds = "".join(
        f'<label class="opt"><input type="radio" name="kind" value="{e(k)}"{" checked" if k == kind else ""}>'
        f'<span><b>{e(label)}</b><span class="small muted" style="display:block">'
        f'{"テーマの候補をいくつか出します。どれを書くかはあなたが決めます。" if k == "trend" else "記事に使う材料（数字・制度・背景）を出典つきで集めます。"}'
        f'</span></span></label>' for k, label in R.KINDS.items())
    provs = []
    for p in R.PROVIDER_KEYS:
        ests = (" ／ ".join(f"{KIND_SHORT.get(k, k)}: {est_text(R.estimate(p, k))}" for k in R.KINDS) if R.is_paid(p)
                else "Claude の契約の利用枠を使います。上限に達したら、止まって聞きます。")
        off = p in blocked
        reason = f'<span class="small" style="display:block;color:var(--ng);font-weight:700">{blocked[p]}</span>' if off else ""
        provs.append(
            f'<label class="opt{" off" if off else ""}"><input type="radio" name="provider" value="{e(p)}"'
            f'{" checked" if p == provider else ""}{" disabled" if off else ""}>'
            f'<span><b>{e(R.provider_label(p))}</b> <span class="badge {"b-ok" if not R.is_paid(p) else "b-warm"}">{e(COST_KIND.get(p, ""))}</span>'
            f'<span class="small muted" style="display:block">{e(ests)}</span>{reason}</span></label>')
    jobs = R.list_jobs(limit=5)
    job_rows = "".join(
        f'<tr><td class="small">{e((j.get("created") or "").replace("T", " "))}</td><td>{e(KIND_SHORT.get(j.get("kind"), j.get("kind")))}</td>'
        f'<td>{e(j.get("provider_label") or provider_name(j.get("provider")))}</td>'
        f'<td><a class="badge {JOB_STATUS.get(j.get("status"), ("", ""))[1]}" href="/research/jobs/{e(j.get("id"))}">'
        f'{e(JOB_STATUS.get(j.get("status"), (j.get("status"), ""))[0])}</a></td></tr>' for j in jobs)
    jobs_html = (f'<div class="card"><h2>最近の実行</h2><div class="tablewrap"><table><tr><th>日時</th><th>種類</th><th>担当</th><th>状態</th></tr>'
                 f'{job_rows}</table></div></div>' if job_rows else "")
    return f"""<h1>リサーチ</h1>
<div class="btnrow" style="margin:0 0 16px"><a class="btn" href="/research/materials">リサーチ資料</a>
<a class="btn" href="/research/usage">今月の使用額</a><a class="btn" href="/settings/api-key">APIキー</a></div>
<div class="grid"><div class="card"><h2>調べる内容</h2>
<form method="post" action="/research/confirm">
<label class="f">種類</label><div class="opts">{kinds}</div>
<label class="f">担当（1回ごとに選べます）</label>{"".join(provs)}
<label class="f" for="r-article">記事（深掘り調査は記事を選んでください。トレンド調査は「記事なし」で大丈夫です）</label>
<select id="r-article" name="article">{article_options(vals.get("article", ""))}</select>
<label class="f" for="r-query">知りたいこと（質問文）</label>
<textarea id="r-query" name="query" class="tall" required placeholder="例: 30代会社員が副業を始めるときの税金の手続き">{e(vals.get("query", ""))}</textarea>
<div class="note" style="margin-top:12px"><b>ここに書いた文章だけが外に送られます。</b>かけら・下書き・ネタ帳の中身は送りません。<br>
<span class="small">次の画面で、送る文章の全文と費用の目安を確認してから送ります（まだ送りません）。</span></div>
<div class="btnrow"><button class="btn primary">確認画面へ（まだ送りません）</button></div></form></div>
<div><div class="card warm"><h2>今月の使用額</h2><p>{usage_line(usage)}</p>
<p class="small muted">Perplexity を使った分だけかかります。Claude は追加料金なしです。</p>
<a class="btn sm" href="/research/usage">くわしく</a></div>{jobs_html}</div></div>"""


def page_research_confirm(p: dict) -> str:
    R, S = _research(), _secrets()
    cfg = R.load_config()
    usage = R.month_usage()
    prov = p.get("provider", "")
    paid = R.is_paid(prov)
    est = p.get("estimate") or {}
    problems = []
    if paid and not S.has_api_key():
        problems.append('Perplexity のAPIキーが未登録です。<a href="/settings/api-key">APIキーの登録</a>をしてから、もう一度お試しください。')
    if not p.get("budget_ok", True):
        problems.append(e(safe(p.get("budget_message") or "今月の上限に達しているため、Perplexity は使えません。")))
    can_run = not problems
    warns = p.get("warnings") or []
    if warns:
        items = "".join(
            f'<li><span class="badge b-{e(w.get("severity") if w.get("severity") in SEVERITY_LABEL else "warn")}">'
            f'{e(w.get("category"))}</span> <b>「{e(w.get("match"))}」</b>'
            f'{" （" + e(w.get("line")) + "行目）" if w.get("line") else ""} {e(w.get("message"))}'
            f'<div class="small muted">{e(w.get("excerpt"))}</div></li>' for w in warns)
        warn_html = (f'<div class="note"><b>外に出すと困るかもしれない語があります（{len(warns)}件）。止めるかどうかはあなたが決めます。</b>'
                     f'<ul>{items}</ul><span class="small">気になるときは「書き直す」で、地名・年齢・学校名などをぼかしてください。</span></div>')
    else:
        warn_html = '<p class="small muted">ぼかすべき属性などに当たりそうな語は見つかりませんでした（見つからない＝安全、ではありません）。</p>'
    if paid:
        hi = est.get("jpy_high") or 0
        after = (usage.get("total_jpy") or 0) + hi
        note = f'<p class="small muted">{e(est.get("pricing_note"))}</p>' if est.get("pricing_note") else ""
        cost_html = (f'<p class="money">{e(est_text(est))}（目安）</p>{note}'
                     f'<p>{usage_line(usage)}<br>実行したあとの見込み: 最大 <b>{yen(after)}</b></p>')
        run_label = f"OK。この内容で送る（目安{est_text(est).replace('だいたい ', '')}かかります）"
    else:
        cost_html = '<p class="money">追加料金なし</p><p class="small muted">この Mac/サーバーでログインしている Claude の契約の利用枠を使います。</p>'
        run_label = "OK。この内容で送る"
    prob_html = "".join(f'<div class="card alert"><h2>このままでは送れません</h2><p>{x}</p></div>' for x in problems)
    model = provider_model(cfg, prov)
    return f"""<h1>送る前の確認</h1>
<div class="note strong"><b>まだ何も送っていません。</b>下の内容でよければ「OK」を押してください。</div>
{prob_html}
<div class="grid"><div>
<div class="card"><h2>外に送る文章（これが全文です）</h2>
<p class="small">送り先: <b>{e(p.get("provider_label") or provider_name(prov))}</b>{"（モデル: " + e(model) + "）" if model else ""}
 ／ 種類: {e(kind_label(p.get("kind")))} ／ 記事: {e(p.get("article") or "記事なし")}</p>
<pre class="send">{e(p.get("prompt", ""))}</pre></div>
<div class="card"><h2>気をつける語</h2>{warn_html}</div></div>
<div><div class="card warm"><h2>費用</h2>{cost_html}</div>
<div class="card"><form method="post" action="/research/run">{hidden_fields(p)}
<input type="hidden" name="confirm_token" value="{e(p.get("confirm_token", ""))}">
<button class="btn {"warm" if paid else "primary"}" style="width:100%"{"" if can_run else " disabled"}>{e(run_label)}</button></form>
<form method="post" action="/research">{hidden_fields(p)}
<div class="btnrow"><button class="btn" style="width:100%">書き直す</button></div></form></div></div></div>"""


def page_research_job(job: dict) -> Tuple[str, str]:
    """ジョブの画面。戻り値: (本文, head に足すもの)。"""
    R = _research()
    status = job.get("status")
    head = ""
    info = (f'<p class="small muted">{e(kind_label(job.get("kind")))} ／ 担当: {e(job.get("provider_label") or provider_name(job.get("provider")))}'
            f' ／ 記事: {e(job.get("article") or "記事なし")} ／ 開始 {e((job.get("created") or "").replace("T", " "))}</p>')
    query = f'<details><summary>送った質問文</summary><p>{lines_html(job.get("query", ""))}</p></details>'
    if status == "running":
        head = '<meta http-equiv="refresh" content="5">'
        body = ('<div class="card"><h2>調べています（数分かかることがあります）</h2>'
                '<p>この画面は5秒ごとに自動で更新します。閉じても調べ続けます。終わったら「リサーチ資料」に入ります。</p></div>')
    elif status == "done":
        mid = job.get("material_id") or ""
        link = (f'<a class="btn primary" href="/research/materials/{e(mid)}">リサーチ資料 {e(mid)} を開く</a>' if mid
                else '<a class="btn primary" href="/research/materials">リサーチ資料を見る</a>')
        body = f'<div class="card"><h2>終わりました</h2><p>結果をリサーチ資料に保存しました。</p><div class="btnrow">{link}</div></div>'
    elif status == "limit":
        resets = job.get("limit_resets_at") or ""
        when = f"{e(str(resets).replace('T', ' '))}ごろ解除" if resets else "解除の時刻は分かりませんでした"
        pe = job.get("estimate_jpy_for_pplx")
        if not pe:
            try:
                pe = R.estimate("pplx_standard", job.get("kind") or "trend")
            except Exception:
                pe = None
        pe_text = est_text(pe).replace("だいたい ", "") if pe not in (None, "") else "不明"
        vals = {"kind": job.get("kind", ""), "provider": "pplx_standard", "article": job.get("article", ""),
                "query": job.get("query", "")}
        detail = f'<p class="small muted">{e(safe(job.get("error")))}</p>' if job.get("error") else ""
        body = f"""<div class="card alert"><h2>Claude の利用上限に達しました</h2>
<p>Claude の利用上限に達しました（{when}）。<b>Perplexity で続けますか？（目安{e(pe_text)}）</b></p>{detail}
<p class="small">自動では切り替えません。続けるときは、次の確認画面で送る文章と費用をもう一度確かめてから「OK」を押します。</p>
<form method="post" action="/research/confirm">{hidden_fields(vals)}
<div class="btnrow"><button class="btn warm">Perplexity で続ける（確認画面へ）</button>
<a class="btn" href="/research">待つ</a></div></form></div>"""
    else:
        body = (f'<div class="card alert"><h2>うまくいきませんでした</h2><p>{lines_html(safe(job.get("error") or "理由は分かりませんでした。"))}</p>'
                f'<div class="btnrow"><a class="btn" href="/research">リサーチ画面へ</a></div></div>')
    return f"<h1>リサーチの実行 {e(job.get('id'))}</h1>{info}{body}{query}", head


def material_provider(m: dict) -> str:
    return str(m.get("provider_label") or m.get("provider") or "—")


def material_badges(m: dict) -> str:
    out = [f'<span class="badge b-vp">{e(KIND_SHORT.get(m.get("kind"), m.get("kind") or "資料"))}</span>']
    if m.get("stale"):
        out.append('<span class="badge b-strong">1年以上前</span>')
    out.append(f'<span class="badge b-tag">{e(material_provider(m))}</span>')
    return "".join(out)


def page_materials(qs: dict) -> str:
    R = _research()
    article = (qs.get("article") or [""])[0].strip()
    items = sorted(R.list_materials(article or None), key=lambda m: (m.get("researched_at") or "", m.get("id") or ""), reverse=True)
    cards = []
    for m in items:
        cards.append(f"""<div class="kcard"><div class="head"><a class="kid" href="/research/materials/{e(m.get("id"))}">{e(m.get("id"))}</a>
{material_badges(m)}<span>{e(m.get("article") or "記事なし")}</span></div>
<p><b>{e(m.get("title") or short(m.get("query", ""), 80) or "（無題）")}</b></p>
<div class="small muted">調べた日 {e(m.get("researched_at") or "—")} ／ モデル {e(m.get("model") or "—")} ／ 出典 {len(m.get("sources") or [])} 件</div></div>""")
    import datetime as _dt
    today = _dt.date.today().isoformat()
    return f"""<h1>リサーチ資料</h1>
<div class="note strong">体験談の記事では、リサーチ資料は<b>書き方・背景の参考</b>にとどめます。体験談の本文に、資料の事実を新しい事実として混ぜないでください。</div>
<div class="btnrow" style="margin:0 0 16px"><a class="btn primary" href="/research">＋ リサーチする</a></div>
<div class="grid"><div>
<div class="card"><h2>絞り込み</h2><form method="get" action="/research/materials">
<select name="article">{article_options(article, "（すべての記事）")}</select>
<div class="btnrow"><button class="btn primary sm">表示</button></div></form></div>
<p class="muted"><b>{len(items)}</b> 件（新しい順）</p>
{"".join(cards) or '<div class="card"><p class="muted">まだリサーチ資料はありません。</p></div>'}</div>
<div><div class="card warm"><h2>手動で取り込む</h2>
<form method="post" action="/research/materials/import" enctype="multipart/form-data">
<label class="drop">ここに .md ファイルをドラッグ&ドロップ<br><span class="small">（または押して選ぶ・複数可）</span>
<input type="file" name="files" accept=".md,text/markdown" multiple required data-drop><span class="dropnames small"></span></label>
<label class="f" for="i-article">記事</label><select id="i-article" name="article">{article_options(article)}</select>
<label class="f" for="i-date">調べた日</label><input type="date" id="i-date" name="date" value="{e(today)}">
<div class="btnrow"><button class="btn primary">取り込む</button></div></form>
<p class="small muted">Perplexity Pro などで調べた結果を Markdown で保存したものを取り込めます。</p></div></div></div>"""


_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)|(https?://[^\s<>()\[\]「」『』、。，）]+)")


def _ext_link(url: str, text: str) -> str:
    return f'<a href="{e(url)}" target="_blank" rel="noopener noreferrer">{e(text)}</a>'


def _md_emph(escaped: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)


def md_inline(raw: str) -> str:
    """1行分をエスケープしながら、リンク（http/https のみ）と太字だけ整形する。"""
    out, pos = [], 0
    for m in _MD_LINK.finditer(raw):
        out.append(_md_emph(e(raw[pos:m.start()])))
        out.append(_ext_link(m[2], m[1]) if m[2] else _ext_link(m[3], m[3]))
        pos = m.end()
    out.append(_md_emph(e(raw[pos:])))
    return "".join(out)


def render_md(text: str) -> str:
    """Markdown を安全第一で簡易表示（見出し・リスト・リンク・太字・コード枠だけ。HTML は全部エスケープ）。"""
    out: List[str] = []
    para: List[str] = []
    code: List[str] = []
    state = {"list": "", "code": False}

    def flush() -> None:
        if para:
            out.append("<p>" + "<br>".join(md_inline(x) for x in para) + "</p>")
            para.clear()
        if state["list"]:
            out.append(f'</{state["list"]}>')
            state["list"] = ""

    for line in _normalize(text).split("\n"):
        s = line.strip()
        if s.startswith("```"):
            if state["code"]:
                out.append(f'<pre class="send">{e(chr(10).join(code))}</pre>')
                code.clear()
                state["code"] = False
            else:
                flush()
                state["code"] = True
            continue
        if state["code"]:
            code.append(line)
            continue
        if not s:
            flush()
            continue
        if s.startswith(">"):
            flush()
            out.append(f'<p class="small muted" style="border-left:4px solid var(--line);padding-left:10px;margin:4px 0">{md_inline(s.lstrip("> "))}</p>')
            continue
        m = re.match(r"(#{1,6})\s+(.*)", s)
        if m:
            flush()
            tag = "h3" if len(m[1]) <= 2 else "h4"
            out.append(f"<{tag}>{md_inline(m[2])}</{tag}>")
            continue
        m = re.match(r"(?:[-*+]|(\d+)[.)])\s+(.*)", s)
        if m:
            tag = "ol" if m[1] else "ul"
            if para:
                out.append("<p>" + "<br>".join(md_inline(x) for x in para) + "</p>")
                para.clear()
            if state["list"] != tag:
                if state["list"]:
                    out.append(f'</{state["list"]}>')
                out.append(f"<{tag}>")
                state["list"] = tag
            out.append(f"<li>{md_inline(m[2])}</li>")
            continue
        if state["list"]:
            out.append(f'</{state["list"]}>')
            state["list"] = ""
        para.append(s)
    if state["code"]:
        out.append(f'<pre class="send">{e(chr(10).join(code))}</pre>')
    flush()
    return "".join(out)


def page_material(mid: str) -> str:
    R = _research()
    m = R.get_material(mid)
    srcs = []
    for s in m.get("sources") or []:
        url = str(s.get("url") or "")
        title = s.get("title") or url or "（無題）"
        link = _ext_link(url, title) if re.match(r"https?://", url) else e(title)
        extra = f'<div class="small muted">{e(url)}{" ／ " + e(s.get("date")) if s.get("date") else ""}</div>' if url else ""
        srcs.append(f"<li>{link}{extra}</li>")
    src_html = f'<ol>{"".join(srcs)}</ol>' if srcs else '<p class="muted">出典URLは記録されていません。本文の事実は一次情報で確認してください。</p>'
    stale = (f'<div class="card alert"><h2>1年以上前の資料です</h2><p>調べた日から {e(m.get("age_days"))} 日たっています。'
             f'制度・料金・数字は変わっている可能性があります。使う前に最新の情報で確認し直してください。</p></div>' if m.get("stale") else "")
    cost = m.get("cost_jpy")
    rows = [("記事", m.get("article") or "記事なし"), ("種類", kind_label(m.get("kind")) if m.get("kind") else "—"),
            ("担当", material_provider(m)),
            ("モデル", m.get("model") or "—"),
            ("調べた日", f'{m.get("researched_at") or "—"}' + (f'（{m.get("age_days")}日前）' if m.get("age_days") not in (None, "") else "")),
            ("費用", yen(cost) if cost not in (None, "") else "—"), ("取り込み", m.get("origin") or "—")]
    table = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in rows)
    q = f'<details><summary>質問文</summary><p>{lines_html(m.get("query"))}</p></details>' if m.get("query") else ""
    return f"""<h1>{e(m.get("title") or "リサーチ資料 " + str(m.get("id")))}</h1>
<div class="note strong"><b>この資料は書き方・背景の参考です。</b>体験談の本文に、ここにある事実を新しい事実として混ぜないでください。
数字や制度を記事に書くときは、出典の一次情報で確かめてから書きます。</div>{stale}
<div class="grid"><div class="card"><h2>本文</h2><div class="md">{render_md(m.get("body", ""))}</div></div>
<div><div class="card"><h2>情報</h2><div>{material_badges(m)}</div><table>{table}</table>{q}</div>
<div class="card"><h2>出典（{len(srcs)}件）</h2>{src_html}</div>
<div class="card"><h2>削除</h2><form method="post" action="/research/materials/{e(m.get("id"))}/delete"
 data-confirm="リサーチ資料 {e(m.get("id"))} を削除します。元に戻せません。よろしいですか？">
<div class="btnrow"><button class="btn ng">この資料を削除</button><a class="btn" href="/research/materials">一覧へ</a></div></form></div></div></div>"""


def page_research_usage(qs: dict) -> str:
    R = _research()
    month = (qs.get("month") or [""])[0].strip()
    u = R.month_usage(month if re.fullmatch(r"\d{4}-\d{2}", month) else None)
    rows = []
    for r in u.get("runs") or []:
        usd = r.get("cost_usd")
        usd_html = f'<div class="small muted">${e(usd)}</div>' if usd not in (None, "") and float(usd or 0) > 0 else ""
        if r.get("cost_unknown"):
            usd_html = f'<div class="small muted">不明（目安 最大{yen(r.get("estimated_jpy_high"))}）</div>'

        rows.append(f'<tr><td class="small">{e((r.get("at") or "").replace("T", " "))}</td><td>{e(provider_name(r.get("provider")))}</td>'
                    f'<td class="small">{e(r.get("model") or "—")}</td><td>{e(KIND_SHORT.get(r.get("kind"), r.get("kind") or "—"))}</td>'
                    f'<td class="small">{e(r.get("article") or "—")}</td>'
                    f'<td class="num">{"—" if r.get("cost_unknown") else yen(r.get("cost_jpy"))}{usd_html}</td><td class="small">{e(COST_SOURCE.get(r.get("cost_source"), r.get("cost_source") or "—"))}</td></tr>')
    table = (f'<div class="tablewrap"><table><tr><th>日時</th><th>担当</th><th>モデル</th><th>種類</th><th>記事</th><th class="num">金額</th><th>算出方法</th></tr>'
             f'{"".join(rows)}<tr><th colspan="5">合計（{e(u.get("count", len(rows)))}回）</th><td class="num"><b>{yen(u.get("total_jpy"))}</b></td><td></td></tr></table></div>'
             if rows else '<p class="muted">この月の実行はまだありません。</p>')
    unknown = (f'<div class="note">金額が分からない実行（通信が切れた等）が {e(u.get("unknown_count"))} 件あります。'
               f'上限の計算では、目安の上限 {yen(u.get("unknown_estimated_jpy"))} を使ったものとして数えています。'
               f'実際の金額は Perplexity の Billing 画面で確認してください。</div>' if u.get("unknown_count") else "")
    return f"""<h1>リサーチの使用額</h1>
<div class="kpi"><div><b>{yen(u.get("total_jpy"))}</b><span>{e(u.get("month"))} の使用額</span></div>
<div><b>{yen(u.get("budget_jpy"))}</b><span>月の上限</span></div><div><b>{yen(u.get("remaining_jpy"))}</b><span>残り</span></div></div>
<div class="card"><h2>実行の一覧</h2>{table}{unknown}
<p class="small muted">「APIの報告額」は Perplexity が返した金額、「トークン数から計算」は使った量と設定ファイルの単価から出した目安です。
円は設定の為替で換算した目安で、実際のカード請求額とは違うことがあります。</p></div>
<div class="card"><h2>月の上限</h2><p>上限を超えそうなときは Perplexity を動かしません。
上限は <code>config/research.json</code>（作業フォルダ側で上書き可）の <code>monthly_budget_jpy</code> で変えられます。</p></div>"""


def page_api_key() -> str:
    S = _secrets()
    src = S.api_key_source()
    if src == "env":
        state = '<span class="badge b-ok">登録済み</span> 環境変数で設定済み'
    elif S.has_api_key():
        state = '<span class="badge b-ok">登録済み</span>'
    else:
        state = '<span class="badge b-warm">未登録</span>'
    delete = ("" if src != "file" else
              '<form method="post" action="/settings/api-key/delete" data-confirm="登録したAPIキーを削除します。よろしいですか？">'
              '<div class="btnrow"><button class="btn ng">登録したキーを削除</button></div></form>')
    env_note = ('<p class="small muted">環境変数で設定しているキーは、この画面からは消せません（起動するときの設定から外してください）。</p>'
                if src == "env" else "")
    return f"""<h1>APIキー（Perplexity）</h1>
<div class="card"><h2>登録状態</h2><p>{state}</p>{env_note}{delete}</div>
<div class="card warm"><h2>登録する</h2><form method="post" action="/settings/api-key">
<label class="f" for="api-key">Perplexity のAPIキー（pplx- で始まる文字）</label>
<input type="password" id="api-key" name="key" autocomplete="off" autocapitalize="off" spellcheck="false" required>
<div class="btnrow"><button class="btn primary">登録する</button></div></form>
<p class="small muted">キーは作業フォルダの secrets/ に保存します（GitHubには上がりません）。登録したあとは、この画面にもキーは表示しません。</p>
<p class="small">キーはパスワードと同じです。人に見せない・チャットやメールに貼らないでください。
作り方は <a href="/guide#perplexity">使い方</a> にあります。</p></div>"""


def research_home_card() -> str:
    """ダッシュボード用。リサーチ機能がまだないときは何も出さない。"""
    if not research_available():
        return ""
    try:
        R = _research()
        u = R.month_usage()
        stale = len(R.stale_materials())
    except store.VaultError:
        raise
    except Exception:
        return ""
    stale_html = (f'<a class="badge b-strong" href="/research/materials">1年以上前の資料 {stale} 件</a>' if stale
                  else '<span class="badge b-ok">1年以上前の資料 0 件</span>')
    return f"""<div class="card"><h2>リサーチ</h2>
<p>今月のリサーチ費用 <b>{yen(u.get("total_jpy"))}</b>／上限{yen(u.get("budget_jpy"))}</p><div>{stale_html}</div>
<div class="btnrow"><a class="btn sm primary" href="/research">リサーチする</a><a class="btn sm" href="/research/materials">資料</a></div></div>"""


GUIDE_RESEARCH = """
<div class="card" id="perplexity"><h2>リサーチ機能の使い方</h2>
<p>リサーチには2つの種類があります。</p>
<ul><li><b>トレンド調査</b> … 「何を書くか」を考えるための調査です。テーマの候補をいくつか出します。どれを書くかは、あなたが決めます。</li>
<li><b>深掘り調査</b> … 記事に使う材料（数字・しくみ・背景）を、出典（どこに書いてあったか）つきで集めます。</li></ul>
<p>調べる担当は、1回ごとに選べます。</p>
<ul><li><b>Claude</b> … 追加料金なし。この Mac（またはサーバー）で <code>claude</code> コマンドにログインしてあることが前提です。
Max 契約の利用上限に達したら、そこで止まって「Perplexity で続けますか？」と聞きます（勝手に切り替えません）。</li>
<li><b>Perplexity ふつう／徹底調査</b> … 使った分だけお金がかかります。送る前に「だいたい◯円」を見せるので、OK を押したときだけ動きます。</li></ul>
<p class="small">外に送るのは、リサーチ画面に書いた文章だけです。かけら・下書き・ネタ帳の中身は送りません。</p></div>

<div class="card warm"><h2>Perplexity を使えるようにする（はじめの1回だけ）</h2>
<p class="small">画面の表示が違ったら、近い名前のボタンを探してください。</p>
<ol class="steps">
<li><b>アカウントを作る</b><br>パソコンのブラウザで <code>https://console.perplexity.ai</code> を開いて、アカウントを作ります（ログインします）。
はじめてだと「API グループ」（画面によっては「Projects &amp; Billing」）を作る画面が出るので、名前（例: 自分の名前）を入れて作ります。
これを作らないと、鍵（APIキー）が作れません。<br>
<span class="small muted">※ Perplexity Pro（月いくらの有料プラン）とは別の支払いです。Pro に入っていても、API用のお金は別にチャージが必要、と考えておきましょう（無料分があるかは画面で確認してください）。</span></li>
<li><b>お金をチャージする（前払い）</b><br>左のメニューの「Billing」（支払い）→「Buy more credits」（クレジットを買う）→ 金額を選んで、カードで払います。
支払いは Stripe（ストライプ）という支払い専門の会社の画面で行います。カードを登録しただけでは、お金はかかりません。<br>
<b>最初は少ない金額（例: 10ドル）で十分です。</b></li>
<li><b>自動チャージをオフにする</b><br>Billing の画面にある「Auto reload」（残りが減ると、自動でお金を追加する機能）は<b>使いません</b>。
「Change preferences」（設定を変える）を押して、オフ（無効）になっていることを確かめます。オンになっていたら、オフにします。<br>
オフなら、残りが0円になったら止まるだけで、勝手にお金は追加されません。残りがなくなると鍵が使えなくなり、このアプリには「残高がなくなっている可能性」と出ます。</li>
<li><b>鍵（APIキー）を作る</b><br>左のメニューの「API keys」→「+ Generate API Key」（鍵を作る）を押します。
<code>pplx-</code> で始まる長い文字が<b>1回だけ</b>表示されるので、すぐコピーします。あとから見直すことはできません（なくしたら、作り直せば大丈夫です）。</li>
<li><b>このアプリに登録する</b><br>このアプリの <a href="/settings/api-key">「設定 &gt; APIキー」</a> に貼り付けて、「登録する」を押します。<br>
鍵はパスワードと同じです。人に見せない・チャットやメールに貼らないでください。もし人に見られたら、Perplexity の画面でその鍵を削除して、作り直してください。</li>
<li><b>月の上限を知っておく</b><br>このアプリには「月の上限」（最初は1000円）があります。超えそうなときは Perplexity は動きません。
変えたいときは、作業フォルダの <code>config/research.json</code> に <code>{"monthly_budget_jpy": 2000}</code> のように書きます。</li></ol>
<p class="small">料金はときどき変わります。最新の料金は Perplexity の料金ページ（<a href="https://docs.perplexity.ai/docs/getting-started/pricing" target="_blank" rel="noopener noreferrer">https://docs.perplexity.ai/docs/getting-started/pricing</a>）で確認してください。
アプリの目安は、設定ファイルの値から計算しています。</p></div>
"""


# ---------- ネタ出し ----------
# ロジックは ideas.py。材料（得意・経験リスト・ネタ帳・反応記録の要約・過去の候補）は Anthropic の Claude に渡す
# （Web検索はしない）。送る前に全文を見せて、OK を押してから渡す。かけらは材料にしない。

def _ideas():
    from . import ideas
    return ideas


def _reactions():
    from . import reactions
    return reactions


IDEA_JOB_STATUS = {"running": ("考えています", "b-st"), "done": ("完了", "b-ok"), "error": ("失敗", "b-strong"),
                   "limit": ("上限で停止", "b-warm")}
IDEA_STATUS_CLASS = {"未検討": "b-st", "調査に回した": "b-ok", "保留": "b-tag", "却下": ""}
IDEA_FILTERS = ("未検討", "調査に回した", "保留", "却下", "all")


def idea_card(c: dict, selectable: bool = True, back: str = "") -> str:
    I = _ideas()
    cid = str(c.get("cid") or "")
    status = str(c.get("status") or "")
    skills = "".join(f'<span class="badge b-vp">{e(x)}</span>' for x in c.get("skills") or [])
    pts = "".join(f"<li>{e(x)}</li>" for x in c.get("check_points") or [])
    pick = (f'<label class="pick"><input type="checkbox" name="cids" value="{e(cid)}">選ぶ</label>'
            if selectable and status != "却下" else "")
    buttons = []
    for st, label, cls in (("保留", "保留にする", ""), ("却下", "却下（次から出さない）", "ng"),
                           ("未検討", "未検討に戻す", "")):
        if st == status:
            continue
        buttons.append(f'<button class="btn sm {cls}" name="set" value="{e(cid)}|{e(st)}" formaction="/ideas/status"'
                       f' formnovalidate>{e(label)}</button>')
    back_field = f'<input type="hidden" name="back" value="{e(back)}">' if back else ""
    ctrl = (f'<div class="btnrow">{"".join(buttons)}</div>' if selectable else
            f'<form method="post" action="/ideas/status">{back_field}<div class="btnrow">{"".join(buttons)}</div></form>')
    return f"""<div class="kcard icard" id="{e(cid)}"><div class="head"><b class="kid">{e(cid)}</b>
<span class="badge b-hyp">{e(I.HYPOTHESIS)}</span><span class="badge b-tag">{e(c.get("type") or I.UNKNOWN_TYPE)}</span>
<span class="badge {IDEA_STATUS_CLASS.get(status, "")}">{e(status)}</span>
<span>{e((c.get("created") or "").replace("T", " ")[:16])}</span></div>
{pick}<p class="idea">{e(c.get("idea"))}</p>
<dl><dt>なぜ私に向いているか</dt><dd>{e(c.get("why_me") or "—")}<div>{skills}</div></dd>
<dt>想定する読者</dt><dd>{e(c.get("reader") or "—")}</dd>
<dt>トレンド調査で確かめるべき点</dt><dd>{f"<ul>{pts}</ul>" if pts else "—"}</dd></dl>{ctrl}</div>"""


def page_ideas(qs: dict) -> str:
    I = _ideas()
    show = (qs.get("status") or ["未検討"])[0]
    if show not in IDEA_FILTERS:
        show = "未検討"
    summ = I.materials_summary()
    counts = I.counts_by_status()
    total = sum(counts.values())
    items = I.list_candidates(None if show == "all" else show)
    tabs = "".join(
        f'<a class="btn sm {"primary" if show == key else ""}" href="{e(qurl("/ideas", status=key))}">'
        f'{e("すべて" if key == "all" else key)} {total if key == "all" else counts.get(key, 0)}</a>' for key in IDEA_FILTERS)
    back = qurl("/ideas", status=show)
    cards = "".join(idea_card(c, back=back) for c in items)
    if cards:
        cand_html = f"""<form method="post" action="/ideas/to-research"><input type="hidden" name="back" value="{e(back)}">{cards}
<div class="sticky"><div class="btnrow" style="margin:0"><button class="btn primary">選んだ候補をトレンド調査に回す</button></div>
<p class="small muted" style="margin:4px 0 0">リサーチの入力欄に、選んだ候補の「ネタ」と「確かめるべき点」だけが入ります。送る前に書き直せます。</p></div></form>"""
    else:
        cand_html = '<div class="card"><p class="muted">ここに表示する候補はありません。</p></div>'
    skills_state = (f'<span class="badge b-ok">{summ["skills_chars"]}字</span>' if summ["skills_chars"]
                    else '<span class="badge b-warm">まだ書いていません</span>')
    skills_note = ("" if summ["skills_chars"] else
                   '<div class="note">先に<a href="/settings/skills">得意・経験リスト</a>を書くと、あなたに合ったネタが出やすくなります。</div>')
    running = I.running_job()
    run_html = (f'<div class="note">いまネタを考えています。<a href="/ideas/jobs/{e(running.get("id"))}">様子を見る</a></div>'
                if running else "")
    return f"""<h1>ネタ出し</h1>
<p class="muted">Claude に記事のネタ候補を出してもらいます（追加料金なし・Web検索なし）。候補は<b>仮説</b>です。
本当に読みたい人がいるかは、あとで<b>トレンド調査</b>で確かめます。</p>
<div class="btnrow" style="margin:0 0 16px"><a class="btn" href="/settings/skills">得意・経験リスト</a>
<a class="btn" href="/reactions">反応記録</a><a class="btn" href="/ideas/history">過去のネタ出し</a></div>
{run_html}
<div class="grid"><div>
<div class="btnrow" style="margin:0 0 14px">{tabs}</div>
{cand_html}</div>
<div><div class="card warm"><h2>ネタを出す</h2>{skills_note}
<table><tr><th>得意・経験リスト</th><td>{skills_state}</td></tr>
<tr><th>ネタ帳のメモ</th><td>{summ["neta"]}件</td></tr><tr><th>反応記録</th><td>{summ["reactions"]}件</td></tr>
<tr><th>過去の候補</th><td>{summ["past"]}件</td></tr><tr><th>却下したネタ</th><td>{summ["rejected"]}件</td></tr></table>
<p class="small muted">かけら（体験談の素材）は使いません。ネタ帳のうち「かけら化済み」のメモも使いません。</p>
<form method="post" action="/ideas/confirm"><div class="btnrow"><button class="btn warm" style="width:100%">ネタを出す（確認画面へ・まだ送りません）</button></div></form></div>
</div></div>"""


def page_ideas_confirm(p: dict) -> str:
    I = _ideas()
    s = p.get("summary") or {}
    running = I.running_job()
    busy = (f'<div class="card alert"><h2>いま実行中です</h2><p>終わってからもう一度お試しください。'
            f'<a href="/ideas/jobs/{e(running.get("id"))}">様子を見る</a></p></div>' if running else "")
    return f"""<h1>ネタ出し: 渡す前の確認</h1>
<div class="note strong"><b>まだ何も渡していません。</b>下の全文を Anthropic の Claude に渡します。よければ「OK」を押してください。</div>
{busy}
<div class="grid"><div class="card"><h2>Claude に渡す文章（これが全文です）</h2>
<p class="small">渡す先: <b>Claude（この Mac/サーバーでログインしている Claude Code）</b> ／ ツールなし・Web検索なし ／ {e(p.get("chars", 0))}字</p>
<pre class="send">{e(p.get("prompt", ""))}</pre></div>
<div><div class="card warm"><h2>材料</h2><p>{e(I.summary_text(s))}</p>
<p class="small muted">かけら（体験談の素材）は入れていません。</p>
<p class="money">追加料金なし</p><p class="small muted">Claude の契約の利用枠を使います。Web検索はしないので、ここに書いた内容が検索として外に出ることはありません。</p>
<div class="note small">ここで出た候補を<b>トレンド調査に回すとき</b>は、送る前に<b>もう一度確認画面</b>が出ます（そちらは Web検索をするので、ぼかすべき語の警告も出ます）。</div></div>
<div class="card"><form method="post" action="/ideas/run"><input type="hidden" name="confirm_token" value="{e(p.get("confirm_token", ""))}">
<button class="btn primary" style="width:100%"{" disabled" if running else ""}>OK。ネタを出す（追加料金なし）</button></form>
<div class="btnrow"><a class="btn" style="width:100%" href="/ideas">やめる</a></div>
<p class="small muted">内容を変えたいときは、<a href="/settings/skills">得意・経験リスト</a>や<a href="/neta">ネタ帳</a>を直してから、もう一度「ネタを出す」を押してください。</p></div></div></div>"""


def page_ideas_job(job: dict) -> Tuple[str, str]:
    I = _ideas()
    status = job.get("status")
    head = ""
    info = (f'<p class="small muted">開始 {e((job.get("created") or "").replace("T", " "))} ／ '
            f'{e(I.summary_text(job.get("materials_summary") or {}))}</p>')
    retry = ('<form method="post" action="/ideas/confirm"><div class="btnrow"><button class="btn">もう一度ネタを出す（確認画面へ）</button>'
             '<a class="btn" href="/ideas">ネタ出しへ</a></div></form>')
    if status == "running":
        head = '<meta http-equiv="refresh" content="5">'
        body = ('<div class="card"><h2>ネタを考えています（1〜数分かかることがあります）</h2>'
                '<p>この画面は5秒ごとに自動で更新します。閉じても続けます。終わったら「ネタ出し」に候補が並びます。</p></div>')
    elif status == "done":
        ex = int(job.get("excluded_rejected") or 0)
        ex_html = f"<p>却下したネタと同じ候補が <b>{ex}</b> 件あったので、外しました。</p>" if ex else ""
        body = (f'<div class="card"><h2>終わりました</h2><p>ネタの候補を <b>{e(job.get("count", 0))}</b> 件出しました。</p>{ex_html}'
                f'<div class="btnrow"><a class="btn primary" href="/ideas">候補を見る</a>'
                f'<a class="btn" href="/ideas/history/{e(job.get("session_id"))}">このときの候補だけ見る</a></div></div>')
    elif status == "limit":
        body = (f'<div class="card alert"><h2>Claude の利用上限に達しました</h2>'
                f'<p>{e(I.limit_message(str(job.get("limit_resets_at") or "").replace("T", " ")))}</p>'
                f'<div class="btnrow"><a class="btn" href="/ideas">ネタ出しへ</a></div></div>')
    else:
        raw = (f'<p><a href="/ideas/history/{e(job.get("session_id"))}">返ってきた文章を見る</a></p>'
               if job.get("session_id") else "")
        body = (f'<div class="card alert"><h2>うまくいきませんでした</h2><p>{lines_html(safe(job.get("error") or "理由は分かりませんでした。"))}</p>'
                f'{raw}{retry}</div>')
    return f"<h1>ネタ出しの実行 {e(job.get('id'))}</h1>{info}{body}", head


def page_ideas_history() -> str:
    I = _ideas()
    rows = []
    for s in I.list_sessions():
        cands = s.get("candidates") or []
        st = ('<span class="badge b-strong">読み取れず</span>' if s.get("raw") and not cands
              else f'<span class="badge b-ok">{len(cands)}件</span>')
        ex = int(s.get("excluded_rejected") or 0)
        rows.append(f'<tr><td><a href="/ideas/history/{e(s.get("id"))}">{e(s.get("id"))}</a></td>'
                    f'<td class="small">{e((s.get("created") or "").replace("T", " "))}</td><td>{st}</td>'
                    f'<td class="num">{ex}</td><td class="small">{e(I.summary_text(s.get("materials_summary") or {}))}</td>'
                    f'<td class="small">{e(s.get("model") or "—")}</td></tr>')
    table = (f'<div class="tablewrap"><table><tr><th>ID</th><th>日時</th><th>候補</th><th class="num">却下と同じで除外</th>'
             f'<th>材料</th><th>モデル</th></tr>{"".join(rows)}</table></div>' if rows
             else '<p class="muted">まだネタ出しをしていません。</p>')
    return f"""<h1>過去のネタ出し</h1>
<div class="btnrow" style="margin:0 0 16px"><a class="btn primary" href="/ideas">ネタ出しへ</a></div>
<div class="card"><h2>履歴</h2>{table}
<p class="small muted">材料の本文は保存していません（件数だけ）。</p></div>"""


def page_ideas_session(sid: str) -> str:
    I = _ideas()
    s = I.get_session(sid)
    back = f"/ideas/history/{s.get('id')}"
    cards = "".join(idea_card(dict(c, created=s.get("created")), selectable=False, back=back)
                    for c in s.get("candidates") or [])
    raw = ""
    if s.get("raw"):
        raw = (f'<div class="card alert"><h2>候補を読み取れませんでした</h2><p>{e(s.get("error") or "")}</p>'
               f'<p class="small">返ってきた文章をそのまま残しています。もう一度試すときは「ネタ出し」から「ネタを出す」を押してください。</p>'
               f'<pre class="send">{e(s.get("raw"))}</pre></div>')
    ex = int(s.get("excluded_rejected") or 0)
    return f"""<h1>ネタ出し {e(s.get("id"))}</h1>
<p class="small muted">{e((s.get("created") or "").replace("T", " "))} ／ モデル {e(s.get("model") or "—")} ／
{e(I.summary_text(s.get("materials_summary") or {}))} ／ 渡した文章 {e(s.get("prompt_chars", 0))}字
{f" ／ 却下したネタと同じで除外 {ex}件" if ex else ""}</p>
<div class="btnrow" style="margin:0 0 16px"><a class="btn primary" href="/ideas">ネタ出しへ（選んで調査に回す）</a><a class="btn" href="/ideas/history">履歴へ</a></div>
{raw}{cards}"""


SKILLS_EXAMPLE = """<div class="example"><b>書き方のれい</b>
<p class="small" style="margin:4px 0">「自分が得意なこと」「今までやってきたこと」を、1行に1つずつ書きます。短くて大丈夫です。</p>
<ul><li>10年くらい、お店で働いていた</li><li>料理が得意。安い材料でたくさん作れる</li>
<li>子どものころから本を読むのが好き</li><li>引っ越しを5回した</li><li>家族の世話をしてきた</li></ul>
<p class="small" style="margin:6px 0 0"><b>本名・会社名・住所など、人に知られたくないことは書かなくて大丈夫です。</b>
「お店」「会社」「ある町」のように、ぼかして書きましょう。</p></div>"""


def page_skills() -> str:
    I = _ideas()
    text = I.get_skills()
    return f"""<h1>得意・経験リスト</h1>
<p class="muted">ネタ出しのときに Claude に渡す「あなたの得意なこと・経験してきたこと」のメモです。</p>
<div class="grid"><div class="card"><h2>書く</h2><form method="post" action="/settings/skills">
<label class="f" for="skills">得意なこと・経験してきたこと（1行に1つ）</label>
<textarea id="skills" name="skills" class="tall">{e(text)}</textarea>
<div class="btnrow"><button class="btn primary">保存する</button><a class="btn" href="/ideas">ネタ出しへ</a></div></form>
<p class="small muted">保存先は作業フォルダ（暗号化フォルダ）の中です。GitHub には上がりません。</p></div>
<div>{SKILLS_EXAMPLE}
<div class="note small">ここに書いたことは、「ネタを出す」を押したときに Claude に渡します（渡す前に全文を見せます）。
Web検索には使わないので、検索として外に出ることはありません。</div></div></div>"""


def reaction_form(r: dict, action: str, submit: str) -> str:
    import datetime as _dt
    rec = r.get("recorded") or _dt.date.today().isoformat()
    return f"""<form method="post" action="{e(action)}">
<label class="f">記事名（公開タイトル）</label><input type="text" name="title" value="{e(r.get("title", ""))}" required>
<div class="cols"><div><label class="f">公開日</label><input type="date" name="published" value="{e(r.get("published", ""))}"></div>
<div><label class="f">記録日（数えた日）</label><input type="date" name="recorded" value="{e(rec)}"></div></div>
<div class="cols"><div><label class="f">スキ数</label><input type="number" name="likes" min="0" inputmode="numeric" value="{e(r.get("likes", 0))}"></div>
<div><label class="f">コメント数</label><input type="number" name="comments" min="0" inputmode="numeric" value="{e(r.get("comments", 0))}"></div>
<div><label class="f">購入数</label><input type="number" name="purchases" min="0" inputmode="numeric" value="{e(r.get("purchases", 0))}"></div></div>
<label class="f">メモ（任意。ネタ出しには渡しません）</label><textarea name="memo" rows="2">{e(r.get("memo", ""))}</textarea>
<div class="btnrow"><button class="btn primary">{e(submit)}</button></div></form>"""


def page_reactions() -> str:
    Rx = _reactions()
    titles = []
    for g in Rx.by_article():
        rows = []
        for r in g["records"]:
            rid = e(r.get("id"))
            rows.append(f"""<tr id="{rid}"><td class="small">{e(r.get("recorded"))}</td><td class="num">{e(r.get("likes"))}</td>
<td class="num">{e(r.get("comments"))}</td><td class="num">{e(r.get("purchases"))}</td><td class="small">{lines_html(r.get("memo", ""))}
<details><summary>編集</summary>{reaction_form(r, "/reactions/" + str(r.get("id")), "保存する")}
<form method="post" action="/reactions/{rid}/delete" data-confirm="この記録（{rid}）を削除します。よろしいですか？">
<div class="btnrow"><button class="btn ng sm">この記録を削除</button></div></form></details></td></tr>""")
        titles.append(f"""<div class="card"><h2>{e(g["title"])}</h2>
<p class="small muted">公開日 {e(g.get("published") or "—")} ／ 記録 {len(g["records"])}回</p>
<div class="tablewrap"><table><tr><th>記録日</th><th class="num">スキ</th><th class="num">コメント</th><th class="num">購入</th><th>メモ</th></tr>
{"".join(rows)}</table></div></div>""")
    return f"""<h1>反応記録</h1>
<p class="muted">公開した記事の反応（スキ・コメント・購入）を、数えた日ごとに手で記録します。記事ごと・反応がよかった順に並びます。
ネタ出しには「反応がよかった順」の<b>タイトルと数だけ</b>を渡します（メモは渡しません）。</p>
<div class="grid"><div>{"".join(titles) or '<div class="card"><p class="muted">まだ記録がありません。</p></div>'}</div>
<div><div class="card warm"><h2>記録を追加</h2>{reaction_form({}, "/reactions/new", "追加する")}</div></div></div>"""


def ideas_home_card() -> str:
    try:
        n = _ideas().counts_by_status().get("未検討", 0)
    except store.VaultError:
        raise
    except Exception:
        return ""
    return f"""<div class="card"><h2>ネタ出し</h2>
<p>未検討のネタ候補 <b>{n}</b> 件</p>
<div class="btnrow"><a class="btn sm primary" href="/ideas">ネタ出しを開く</a><a class="btn sm" href="/settings/skills">得意・経験リスト</a></div></div>"""


GUIDE_IDEAS = """
<div class="card" id="ideas"><h2>ネタ出しの使い方</h2>
<p>「何を書こうかな」と思ったときに、Claude に記事のネタの候補を出してもらう機能です。<b>追加料金はかかりません。</b></p>
<ol class="steps">
<li><b>得意・経験リストを書く</b>（はじめの1回）<br><a href="/settings/skills">得意・経験リスト</a>に、得意なことや、今までやってきたことを書きます。</li>
<li><b>「ネタを出す」を押す</b><br><a href="/ideas">ネタ出し</a>の画面のボタンを押します。</li>
<li><b>渡す文章を確かめる</b><br>Claude に渡す文章が全部表示されます。よければ「OK。ネタを出す」を押します。</li>
<li><b>候補を見る</b><br>1〜数分で、ネタの候補が10個くらい並びます。</li>
<li><b>気になるものにチェックを付ける</b><br>いくつ選んでも大丈夫です。</li>
<li><b>「選んだ候補をトレンド調査に回す」を押す</b><br>リサーチの画面に、選んだネタが文章になって入ります。</li>
<li><b>文章を直して送る</b><br>そのままでも、書き直してもOKです。送る前に、もう一度確認画面が出ます。</li>
<li><b>結果を見て、書くかどうか決める</b></li></ol>
<p>合わないネタは<b>「却下」</b>を押すと、次から出てこなくなります。「保留」は、あとで考えたいときに使います。</p>
<h3>候補は「仮説」です</h3>
<p>Claude は、いまの流行をぜんぶ知っているわけではありません。だから候補には「仮説（まだ需要を確かめていない）」と書いてあります。
本当に読みたい人がいるかは、<b>トレンド調査</b>で確かめます。</p>
<h3>得意・経験リストの書き方</h3>
<p>1行に1つ、短く書きます。むずかしい言葉はいりません。</p>
<ul><li>10年くらい、お店で働いていた</li><li>料理が得意。安い材料でたくさん作れる</li>
<li>子どものころから本を読むのが好き</li><li>引っ越しを5回した</li></ul>
<p><b>本名・会社名・住所など、人に知られたくないことは書かなくて大丈夫です。</b>「お店」「ある町」のように、ぼかして書きましょう。
書いたものは作業フォルダ（暗号化フォルダ）に保存され、GitHub には上がりません。</p>
<h3>かけらはネタ出しに使いません</h3>
<p>かけらには、体験したことがくわしく書いてあります。ネタ出しに使うと、その細かい話が候補の文に混ざって、
トレンド調査のときに外（Web検索）に出てしまうかもしれません。それを防ぐため、かけらは使いません
（ネタ帳のうち「かけら化済み」のメモも使いません）。</p>
<h3>Web検索はしません</h3>
<p>ネタ出しの Claude は、Web検索もほかの道具も使えないようにしてあります。得意・経験リストやネタ帳の中身が、
検索の言葉として外に出ることはありません（Claude には渡します。渡す前に全文を見せます）。</p>
<h3>反応記録</h3>
<p><a href="/reactions">反応記録</a>に、公開した記事のスキ・コメント・購入の数を書いておくと、
「反応がよかった記事」をヒントにしたネタが出やすくなります（Claude に渡すのはタイトルと数だけです）。</p></div>
"""


# ---------- 使い方 ----------

def page_guide() -> str:
    return f"""<h1>使い方</h1>
<div class="card"><h2>流れ</h2>
<p class="small">記事のネタに迷ったら、<a href="#ideas">ネタ出し</a> → トレンド調査 → 深掘り調査 の順に進めます。</p><ol>
<li><b>ネタ帳</b>に、思い出したことを1行でもメモします（記事に紐付けなくてOK）。</li>
<li>ネタを<b>「かけらにする」</b>で、記事・区間・観点（五感・体の反応・セリフ・分岐点など）を付けます。直接<b>かけらを書く</b>こともできます。</li>
<li><b>記事と充足度</b>で記事ごとに区間を決めると、区間×観点の表で「まだ書けていないところ」（0件の赤い枠）が分かります。</li>
<li>下書きができたら<b>チェッカー</b>で、禁句・個別助言に読める言い回し・ぼかすべき属性・出典のない統計・シリーズ内の矛盾を確認します。
警告は判断材料です。直すかどうかは人が決めます（自動で書き換える機能はありません）。</li></ol>
<p class="small muted">かけらを削除するときは、そのかけらを根拠に生成した段落・記事（生成来歴）を先に表示します。</p></div>
<div class="card"><h2>コマンド</h2><ul class="small">
<li><code>./nw serve</code> … この画面（既定 127.0.0.1:8766）。スマホから使うときは <code>./nw serve --host 100.x.y.z</code>（Tailscale のIP）</li>
<li><code>./nw kakera add / list / search / show / rm</code>、<code>./nw neta add / list / promote</code>、<code>./nw article add / list</code></li>
<li><code>./nw coverage 記事名</code>、<code>./nw check 下書き.md ...</code>、<code>./nw rules</code></li>
<li><code>./nw research run --kind trend|deep [--provider claude|pplx_standard|pplx_deep] [--article 記事] 質問文</code>（送る前に全文を表示し、yes と打ったときだけ送ります）</li>
<li><code>./nw research list / show / import / usage</code>、<code>./nw research key set / status / delete</code></li>
<li><code>./nw ideas run [--show-prompt]</code>（渡す文章の文字数と材料の件数を表示し、yes と打ったときだけネタを出します）、
<code>./nw ideas list [--status 未検討]</code>、<code>./nw ideas set 候補ID 却下|保留|未検討|調査に回した</code>、
<code>./nw ideas to-research 候補ID ...</code>（トレンド調査の質問文を表示するだけ）</li>
<li><code>./nw skills show / edit</code>、<code>./nw reaction add / list</code></li></ul></div>
<h1 style="margin-top:28px">ネタ出し</h1>{GUIDE_IDEAS}
<h1 style="margin-top:28px">リサーチ</h1>{GUIDE_RESEARCH}
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
              nav: bool = True, head: str = "") -> None:
        self._send(layout(title, body, active, flash, error, nav, head), status)

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
            page, title, active, head = None, "", "", ""
            if path == "/research/confirm":
                self._redirect_to("/research")
                return
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
            elif path == "/research":
                page, title, active = page_research(research_vals(qs)), "リサーチ", "research"
            elif m := re.fullmatch(rf"/research/jobs/({ID_RE})", path):
                page, head = page_research_job(_research().get_job(m[1]))
                title, active = "リサーチの実行", "research"
            elif path == "/research/materials":
                page, title, active = page_materials(qs), "リサーチ資料", "research"
            elif m := re.fullmatch(rf"/research/materials/({ID_RE})", path):
                page, title, active = page_material(m[1]), f"リサーチ資料 {m[1]}", "research"
            elif path == "/research/usage":
                page, title, active = page_research_usage(qs), "リサーチの使用額", "research"
            elif path == "/settings/api-key":
                page, title, active = page_api_key(), "APIキー", "research"
            elif path == "/ideas":
                page, title, active = page_ideas(qs), "ネタ出し", "ideas"
            elif path == "/ideas/confirm":
                self._redirect_to("/ideas")
                return
            elif path == "/ideas/history":
                page, title, active = page_ideas_history(), "過去のネタ出し", "ideas"
            elif m := re.fullmatch(r"/ideas/history/(I\d+)", path):
                page, title, active = page_ideas_session(m[1]), f"ネタ出し {m[1]}", "ideas"
            elif m := re.fullmatch(r"/ideas/jobs/(IJ\d+)", path):
                page, head = page_ideas_job(_ideas().get_job(m[1]))
                title, active = "ネタ出しの実行", "ideas"
            elif path == "/settings/skills":
                page, title, active = page_skills(), "得意・経験リスト", "ideas"
            elif path == "/reactions":
                page, title, active = page_reactions(), "反応記録", "reactions"
            if page is None:
                self._page("見つかりません", "<h1>見つかりません</h1>", status=404)
            else:
                self._page(title, page, active, flash=flash, error=err, head=head)
        except KeyError:
            self._page("見つかりません", "<h1>見つかりません</h1><p>指定したものはありません（削除された可能性があります）。</p>", status=404)
        except store.VaultError as ex:
            self._page("作業フォルダが使えません", page_vault_error(ex), status=503, nav=False)
        except Exception as ex:  # 本文を含みうるのでトレースバックは出さない
            sys.stderr.write(f"error: {type(ex).__name__}\n")
            self._page("エラー", f"<h1>エラーが起きました</h1><p>{e(type(ex).__name__)}: {e(safe(ex))}</p>", status=500)

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
            self._redirect_to(self._back(path), safe(f"エラー: {msg}"), error=True)
        except store.VaultError as ex:
            self._page("作業フォルダが使えません", page_vault_error(ex), status=503, nav=False)
        except Exception as ex:  # 送信内容（APIキー・本文）を含みうるのでトレースバックは出さない
            sys.stderr.write(f"error: {type(ex).__name__}\n")
            self._page("エラー", f"<h1>エラーが起きました</h1><p>{e(type(ex).__name__)}: {e(safe(ex))}</p>", status=500)

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
        if path.startswith("/research/materials"):
            return "/research/materials"
        if path.startswith("/research"):
            return "/research"
        if path.startswith("/settings/api-key"):
            return "/settings/api-key"
        if path.startswith("/settings/skills"):
            return "/settings/skills"
        if path.startswith("/ideas"):
            return "/ideas"
        if path.startswith("/reactions"):
            return "/reactions"
        return "/"

    def _post(self, path: str, form: Form, vdir: Path) -> None:
        K = _kakera()
        if path == "/check":
            self._post_check(form, vdir)
            return
        if path.startswith("/ideas") or path.startswith("/reactions") or path == "/settings/skills":
            self._post_ideas(path, form)
            return
        if path.startswith("/research") or path.startswith("/settings/"):
            self._post_research(path, form)
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


    def _post_ideas(self, path: str, form: Form) -> None:
        I, Rx = _ideas(), _reactions()
        back = form.get("back")
        if not back.startswith("/ideas") or back.startswith("//"):
            back = "/ideas"
        back = quote(back.partition("#")[0], safe="/?=&%")
        if path == "/ideas/confirm":  # まだ渡さない。渡す全文を見せる
            self._page("ネタ出し: 渡す前の確認", page_ideas_confirm(I.prepare()), "ideas")
        elif path == "/ideas/run":
            try:
                jid = I.start_job(form.get("confirm_token"))
            except I.ConfirmMismatch as ex:
                self._redirect_to("/ideas", f"エラー: {ex}", error=True)
                return
            self._redirect_to(f"/ideas/jobs/{quote(str(jid), safe='')}", "Claude に渡しました。候補が出るまでお待ちください")
        elif path == "/ideas/status":
            cid, _, status = form.get("set").partition("|")
            if not cid:
                cid, status = form.get("cid"), form.get("status")
            c = I.set_status(cid, status)
            self._redirect_to(back + "#" + quote(c["cid"], safe=""), f"{c['cid']} を「{status}」にしました")
        elif path == "/ideas/to-research":
            cids = form.list("cids")
            if not cids:
                raise ValueError("トレンド調査に回す候補を、チェックを付けて選んでください。")
            query = I.research_query(cids)
            I.set_statuses(cids, "調査に回した")
            self._research_form_page({"kind": "trend", "provider": "", "article": "", "query": query},
                                     f"選んだ候補 {len(cids)} 件を「調査に回した」にしました。"
                                     "質問文を確かめて（書き直してもOK）、確認画面へ進んでください")
        elif path == "/settings/skills":
            I.save_skills(form.get("skills", strip=False))
            self._redirect_to("/settings/skills", "得意・経験リストを保存しました")
        elif path == "/reactions/new":
            r = Rx.add_reaction(**self._reaction_fields(form))
            self._redirect_to(f"/reactions#{quote(r['id'], safe='')}", f"「{r['title']}」の反応を記録しました")
        elif m := re.fullmatch(r"/reactions/(R\d+)/delete", path):
            Rx.delete_reaction(m[1])
            self._redirect_to("/reactions", f"記録 {m[1]} を削除しました")
        elif m := re.fullmatch(r"/reactions/(R\d+)", path):
            r = Rx.update_reaction(m[1], **self._reaction_fields(form))
            self._redirect_to(f"/reactions#{quote(r['id'], safe='')}", "保存しました")
        else:
            self._send("not found", 404)

    @staticmethod
    def _reaction_fields(form: Form) -> dict:
        return {"title": form.get("title"), "published": form.get("published"), "recorded": form.get("recorded"),
                "likes": form.get("likes"), "comments": form.get("comments"), "purchases": form.get("purchases"),
                "memo": form.get("memo", strip=False)}

    def _research_form_page(self, vals: dict, flash: str = "", error: bool = False, status: int = 200) -> None:
        """入力画面を、値を入れたまま表示する（書き直す・エラー時。何も保存しない）。"""
        self._page("リサーチ", page_research(vals), "research", status=status, flash=safe(flash), error=error)

    def _post_research(self, path: str, form: Form) -> None:
        R = _research()
        if path == "/research":  # 「書き直す」: 入力画面に値を戻すだけ
            self._research_form_page(research_vals(form))
        elif path == "/research/confirm":  # まだ送らない。送る全文・警告・費用を見せる
            vals = research_vals(form)
            if not vals["query"].strip():
                self._research_form_page(vals, "エラー: 知りたいこと（質問文）を書いてください。", error=True)
                return
            if vals["kind"] == "deep" and not vals["article"]:
                self._research_form_page(vals, "エラー: 深掘り調査は、記事を選んでください。", error=True)
                return
            try:
                prepared = R.prepare(vals["kind"], vals["provider"], vals["query"], article=vals["article"])
            except (ValueError, KeyError) as ex:
                self._research_form_page(vals, f"エラー: {ex}", error=True)
                return
            self._page("送る前の確認", page_research_confirm(prepared), "research")
        elif path == "/research/run":
            vals = research_vals(form)
            try:
                job_id = R.start_job(vals["kind"], vals["provider"], vals["query"], vals["article"], form.get("confirm_token"))
            except R.ConfirmMismatch:
                self._research_form_page(vals, "エラー: 確認した内容と違うため、送りませんでした。もう一度確認画面から進めてください。",
                                         error=True)
                return
            except (R.BudgetExceeded, ValueError) as ex:
                self._research_form_page(vals, f"エラー: {ex}", error=True)
                return
            self._redirect_to(f"/research/jobs/{quote(str(job_id), safe='')}", "送りました。結果が出るまでお待ちください")
        elif path == "/research/materials/import":
            ups = [(fn or "無題.md", data) for _, fn, data in form.files if data]
            if not ups:
                raise ValueError("ファイルを選んでください。")
            bad = [fn for fn, _ in ups if Path(fn).suffix.lower() != ".md"]
            if bad:
                raise ValueError("Markdown（.md）ファイルだけ取り込めます: " + "、".join(Path(b).name for b in bad))
            date = form.get("date")
            if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                raise ValueError("調べた日は YYYY-MM-DD の形で入れてください。")
            done = []
            for fn, data in sorted(ups, key=lambda x: x[0]):
                try:
                    text = data.decode("utf-8-sig")
                except UnicodeDecodeError:
                    raise ValueError(f"{Path(fn).name} は文字コードが UTF-8 ではないため取り込めません。") from None
                m = R.import_material(Path(fn).name, _normalize(text),
                                      article=form.get("article"), researched_at=date or None)
                done.append(str(m.get("id")))
            art = form.get("article")
            self._redirect_to(qurl("/research/materials", article=art), f"{len(done)} 件取り込みました（{'、'.join(done)}）")
        elif m := re.fullmatch(rf"/research/materials/({ID_RE})/delete", path):
            R.delete_material(m[1])
            self._redirect_to("/research/materials", f"リサーチ資料 {m[1]} を削除しました")
        elif path == "/settings/api-key":
            key = form.get("key")
            form.fields.pop("key", None)
            if not key:
                raise ValueError("APIキーを入力してください。")
            warns = _secrets().set_api_key(key)
            key = ""
            msg = "APIキーを登録しました"
            if warns:
                msg += "（" + " / ".join(str(w) for w in warns) + "）"
            self._redirect_to("/settings/api-key", safe(msg))
        elif path == "/settings/api-key/delete":
            ok = _secrets().delete_api_key()
            self._redirect_to("/settings/api-key", "APIキーを削除しました" if ok else "削除するキーはありませんでした")
        else:
            self._send("not found", 404)


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
