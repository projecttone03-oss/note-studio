"""公開前のチェックとプレビュー（SPEC.md 機能7・8）。どちらも本文は書き換えない。

- 無料/有料の境界: 本文中の「区切り行」（config/publish.json の paywall_markers）で無料エリアと有料エリアに分け、
  有料エリアの見出しが無料エリアで予告されているか（予告漏れ）を突き合わせて警告する。
- note スマホプレビュー: 本文幅 620px 相当・見出し・目次・有料エリアの区切り線を再現した HTML を作る（外部読み込みなし）。
"""
from __future__ import annotations

import html
import re
import unicodedata
from typing import Dict, List, Optional, Tuple

from . import store

DEFAULTS = {
    "paywall_markers": ["<!-- ここから有料 -->", "【ここから有料】", "---ここから有料---"],
    "teaser_min_ratio": 0.5,
    "free_min_chars": 300,
    "check_levels": [2, 3],
    "preview": {"width": 620, "toc": True, "toc_levels": [2, 3], "paywall_text": "ここから先は有料部分です"},
}
SEVERITY = {"strong": "強い警告", "warn": "警告", "info": "参考"}


def config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(store.read_json(store.CONFIG_DIR / "publish.json", {}) or {})
    try:
        cfg.update(store.read_json(store.vault() / "config" / "publish.json", {}) or {})
    except store.VaultError:
        pass
    pv = dict(DEFAULTS["preview"])
    pv.update(cfg.get("preview") or {})
    cfg["preview"] = pv
    return cfg


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "")).casefold()


def _lines(text: str) -> List[str]:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def find_markers(text: str, cfg: Optional[dict] = None) -> List[int]:
    """区切り行の行番号（0始まり）。"""
    cfg = cfg or config()
    marks = {_norm(m) for m in cfg["paywall_markers"] if _norm(m)}
    return [i for i, line in enumerate(_lines(text)) if _norm(line) in marks]


def split(text: str, cfg: Optional[dict] = None) -> dict:
    lines = _lines(text)
    idx = find_markers(text, cfg)
    if not idx:
        return {"free": "\n".join(lines), "paid": "", "marker_line": None, "markers": []}
    i = idx[0]
    return {"free": "\n".join(lines[:i]), "paid": "\n".join(lines[i + 1:]), "marker_line": i + 1, "markers": [x + 1 for x in idx]}


_HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def headings(text: str, offset: int = 0) -> List[Tuple[int, str, int]]:
    """(レベル, 見出し, 行番号1始まり)。コードブロックの中は見ない。"""
    out, fence = [], False
    for i, line in enumerate(_lines(text)):
        if re.match(r"^\s{0,3}(`{3,}|~{3,})", line):
            fence = not fence
            continue
        m = None if fence else _HEAD.match(line)
        if m:
            out.append((len(m.group(1)), m.group(2).strip(), i + 1 + offset))
    return out


_WORD = re.compile(r"[一-龥々〆ヵヶ]{2,}|[ァ-ヴー]{2,}|[A-Za-z0-9]{2,}|[ぁ-ん]{4,}")


def _keywords(heading: str) -> List[str]:
    h = unicodedata.normalize("NFKC", re.sub(r"^[\d０-９.．、)）\s]+", "", heading))
    return [w.casefold() for w in _WORD.findall(h)]


def teased(heading: str, free: str, min_ratio: float) -> Tuple[bool, List[str]]:
    """有料の見出しが無料エリアで予告されているか（見出しそのもの、または語の min_ratio 以上が出てくる）。"""
    f = _norm(free)
    if _norm(heading) and _norm(heading) in f:
        return True, []
    words = _keywords(heading)
    if not words:
        return True, []
    missing = [w for w in words if _norm(w) not in f]
    return (len(words) - len(missing)) / len(words) >= min_ratio, missing


def check_boundary(text: str) -> List[dict]:
    """警告の一覧 {severity, line, message}。本文は変えない。"""
    cfg = config()
    out: List[dict] = []
    sp = split(text, cfg)
    if len(sp["markers"]) > 1:
        out.append({"severity": "strong", "line": sp["markers"][1],
                    "message": f"有料の区切り行が {len(sp['markers'])} か所あります（{'、'.join(map(str, sp['markers']))}行目）。"
                               "note の有料ラインは1か所です。"})
    if sp["marker_line"] is None:
        out.append({"severity": "warn", "line": 0,
                    "message": "有料の区切り行がありません（全文が無料になります）。区切り行の書き方: "
                               + " / ".join(cfg["paywall_markers"])})
    else:
        free_body = "\n".join(l for l in _lines(sp["free"]) if not _HEAD.match(l)).strip()
        if len(_norm(free_body)) < int(cfg["free_min_chars"]):
            out.append({"severity": "info", "line": sp["marker_line"],
                        "message": f"無料エリアの本文が短めです（{len(_norm(free_body))}字）。読者が買うかどうか決められるだけの予告があるか確かめてください。"})
        if not _norm(sp["paid"]):
            out.append({"severity": "warn", "line": sp["marker_line"], "message": "区切り行のあとに本文がありません。"})
        levels = set(int(x) for x in cfg["check_levels"])
        for level, h, line in headings(sp["paid"], offset=sp["marker_line"]):
            if level not in levels:
                continue
            ok, missing = teased(h, sp["free"], float(cfg["teaser_min_ratio"]))
            if not ok:
                out.append({"severity": "warn", "line": line,
                            "message": f"有料エリアの見出し「{h}」が、無料エリアで予告されていません"
                                       + (f"（無料エリアに見当たらない語: {'、'.join(missing)}）" if missing else "")
                                       + "。目次や「この記事で分かること」に入れるか確かめてください。"})
        if not re.search(r"^\s*(?:[-*・]|\d+[.．)])\s*\S", sp["free"], re.M):
            out.append({"severity": "info", "line": sp["marker_line"],
                        "message": "無料エリアに箇条書き（この記事で分かること・目次など）がありません。"})
    for i, line in enumerate(_lines(text), 1):
        if "[要追加" in line:
            out.append({"severity": "strong", "line": i, "message": "[要追加] が残っています。公開前に書き足すか、段落を消してください。"})
        if "【足りない" in line:
            out.append({"severity": "strong", "line": i, "message": "【足りない】の印が残っています。質問に答えて書き直すか、印を消してください。"})
    order = {"strong": 0, "warn": 1, "info": 2}
    return sorted(out, key=lambda f: (order[f["severity"]], f["line"]))


# ---------------------------------------------------------------- note 風の HTML

def _inline(raw: str) -> str:
    t = html.escape(raw, quote=True)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2" rel="noopener noreferrer">\1</a>', t)
    return t


def _anchor(i: int) -> str:
    return f"h-{i}"


def md_to_note_html(text: str, toc_levels=(2, 3), with_toc: bool = True, paywall_text: str = "", price: str = "") -> str:
    """note の記事本文に近い HTML（本文のみ）。区切り行は有料ラインとして描く。"""
    cfg = config()
    marker_lines = set(find_markers(text, cfg))
    lines = _lines(text)
    out: List[str] = []
    toc: List[Tuple[int, str, str]] = []
    para: List[str] = []
    title = ""
    hn = 0
    i = 0

    def flush() -> None:
        if para:
            out.append("<p>" + "<br>".join(_inline(x) for x in para) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        if i in marker_lines:
            flush()
            label = html.escape(paywall_text or "ここから先は有料部分です")
            pr = f'<div class="price">{html.escape(price)}円</div>' if price else ""
            out.append(f'<div class="paywall"><span>{label}</span>{pr}</div>')
            i += 1
            continue
        m = _HEAD.match(line)
        if m:
            flush()
            level, h = len(m.group(1)), m.group(2).strip()
            if level == 1 and not title:
                title = h
            else:
                hn += 1
                tag = "h2" if level <= 2 else "h3"
                out.append(f'<{tag} id="{_anchor(hn)}">{_inline(h)}</{tag}>')
                if level in toc_levels:
                    toc.append((level, h, _anchor(hn)))
            i += 1
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        if re.match(r"^\s*(?:---+|\*\*\*+)\s*$", line):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if re.match(r"^\s*\|.*\|\s*$", line):
            flush()
            rows = []
            while i < len(lines) and re.match(r"^\s*\|.*\|\s*$", lines[i]):
                if not re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            body = "".join("<tr>" + "".join(f"<{'th' if r == 0 else 'td'}>{_inline(c)}</{'th' if r == 0 else 'td'}>" for c in row) + "</tr>"
                           for r, row in enumerate(rows))
            out.append(f'<div class="tbl"><table>{body}</table></div>')
            continue
        lm = re.match(r"^\s*(?:([-*・])|(\d+)[.．)])\s+(.*)$", line)
        if lm:
            flush()
            ordered = lm.group(2) is not None
            items = []
            while i < len(lines):
                lm = re.match(r"^\s*(?:([-*・])|(\d+)[.．)])\s+(.*)$", lines[i])
                if not lm or (lm.group(2) is not None) != ordered:
                    break
                items.append(f"<li>{_inline(lm.group(3))}</li>")
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue
        if line.lstrip().startswith(">"):
            flush()
            q = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                q.append(_inline(lines[i].lstrip()[1:].strip()))
                i += 1
            out.append("<blockquote>" + "<br>".join(q) + "</blockquote>")
            continue
        para.append(line.strip())
        i += 1
    flush()
    toc_html = ""
    if with_toc and toc:
        items = "".join(f'<li class="l{lv}"><a href="#{a}">{_inline(h)}</a></li>' for lv, h, a in toc)
        toc_html = f'<nav class="toc"><div class="toc-t">目次</div><ul>{items}</ul></nav>'
    head = f'<h1 class="title">{_inline(title)}</h1>' if title else ""
    # note は目次を本文の最初の見出しの手前に置く
    body = "".join(out)
    if toc_html:
        cands = [x for x in (body.find("<h2"), body.find("<h3"), body.find('<div class="paywall"')) if x != -1]
        first = min(cands) if cands else -1  # 有料ラインが先なら、その手前（無料エリア）に置く
        body = body[:first] + toc_html + body[first:] if first != -1 else toc_html + body
    return head + body


PREVIEW_CSS = """
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:#fff;color:#08131a;font-family:"Hiragino Sans","Hiragino Kaku Gothic ProN","Noto Sans JP",Meiryo,sans-serif;}
.bar{position:sticky;top:0;background:#fffbe6;border-bottom:1px solid #f0e2a8;font-size:12px;padding:6px 12px;color:#6b5a00;z-index:2}
.bar a{color:#6b5a00}
.note-body{max-width:{width}px;margin:0 auto;padding:24px 16px 80px;font-size:16px;line-height:2;letter-spacing:.04em;word-break:break-word}
.title{font-size:24px;line-height:1.5;font-weight:700;margin:12px 0 28px}
h2{font-size:20px;line-height:1.6;font-weight:700;margin:48px 0 16px}
h3{font-size:18px;line-height:1.6;font-weight:700;margin:36px 0 12px}
p{margin:0 0 1.4em}
a{color:#08131a;text-decoration:underline}
ul,ol{padding-left:1.4em;margin:0 0 1.4em}
blockquote{margin:0 0 1.4em;padding:2px 0 2px 16px;border-left:4px solid #e6e6e6;color:#4d5a62}
hr{border:0;border-top:1px solid #e6e6e6;margin:36px 0}
.tbl{overflow-x:auto;margin:0 0 1.4em}table{border-collapse:collapse;font-size:14px;line-height:1.7}
th,td{border:1px solid #dcdcdc;padding:6px 10px;text-align:left;vertical-align:top}th{background:#f5f8fa}
.toc{background:#f5f8fa;border-radius:8px;padding:16px 20px;margin:0 0 32px}
.toc-t{font-weight:700;font-size:14px;margin-bottom:6px}.toc ul{list-style:none;padding:0;margin:0;font-size:14px;line-height:1.9}
.toc li.l3{padding-left:1.2em}.toc a{text-decoration:none;color:#08131a}
.paywall{margin:48px 0;text-align:center;border-top:1px solid #dcdcdc;position:relative;padding-top:24px}
.paywall span{display:inline-block;background:#fff;padding:0 12px;position:relative;top:-36px;font-size:13px;color:#6b7680}
.paywall .price{font-size:20px;font-weight:700;margin-top:-12px}
.warn{background:#fdecea;color:#b8322b;border-radius:6px;padding:8px 12px;font-size:13px;margin:0 0 16px;line-height:1.7}
"""


def preview_page(text: str, title: str = "", price: str = "", warnings: Optional[List[dict]] = None, back: str = "") -> str:
    """プレビューの完全な HTML（外部の読み込みなし）。"""
    pv = config()["preview"]
    body = md_to_note_html(text, tuple(pv.get("toc_levels") or (2, 3)), bool(pv.get("toc", True)),
                           str(pv.get("paywall_text") or ""), price)
    css = PREVIEW_CSS.replace("{width}", str(int(pv.get("width") or 620)))
    warn = ""
    if warnings:
        n = sum(1 for w in warnings if w["severity"] != "info")
        if n:
            warn = f'<div class="warn">公開前チェックの警告が {n} 件あります（プレビューには反映していません）。</div>'
    link = f' ／ <a href="{html.escape(back)}">戻る</a>' if back else ""
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>プレビュー {html.escape(title)}</title><style>{css}</style></head><body>
<div class="bar">note スマホ表示のプレビュー（本文幅 {int(pv.get("width") or 620)}px 相当・見た目の目安）{link}</div>
<article class="note-body">{warn}{body}</article></body></html>"""


def summarize(findings: List[dict]) -> Dict[str, int]:
    out = {k: 0 for k in SEVERITY}
    for f in findings:
        out[f["severity"]] += 1
    return out
