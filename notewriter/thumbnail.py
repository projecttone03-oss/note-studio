"""サムネイル（SPEC.md 機能9）: 1280×670px（1.91:1）のひな形にタイトルとキーワードを差し込む。

- ひな形は config/thumbnails.json（2〜3種類）。同じ配置から SVG と HTML/CSS を作る。
- PNG は、Python の playwright があればそれで、なければ設定の Chromium/Chrome（chrome_path・環境変数 NW_CHROME）で作る。
  どちらもなければ HTML と SVG だけ作る（ブラウザで開いてスクリーンショットしてもよい）。
- AI 画像生成は後付けできるように、背景を返す関数の登録口（BACKGROUND_PROVIDERS）だけ用意する（既定では使わない）。

保存先: 作業フォルダの thumbnails/<記事>/<ひな形>.svg・.html・.png
"""
from __future__ import annotations

import base64
import html
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import store
from . import writing as W

WIDTH, HEIGHT = 1280, 670
# AI 画像生成などを後付けするときの登録口: 名前 -> (タイトル, 設定) を受け取り PNG/JPEG の bytes か None を返す関数
BACKGROUND_PROVIDERS: Dict[str, Callable[[str, dict], Optional[bytes]]] = {}
DEFAULTS = {"templates": [{"name": "center", "label": "中央", "layout": "center", "bg": "#fff7dc", "fg": "#3b4450", "accent": "#ff8a65"}],
            "font_family": "sans-serif", "author": "", "chrome_path": "", "background_image": "", "background_provider": ""}


def config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(store.read_json(store.CONFIG_DIR / "thumbnails.json", {}) or {})
    try:
        cfg.update(store.read_json(store.vault() / "config" / "thumbnails.json", {}) or {})
    except store.VaultError:
        pass
    return cfg


def template(name: str) -> dict:
    for t in config()["templates"]:
        if t["name"] == name:
            return t
    raise ValueError(f"ひな形「{name}」は設定にありません。")


# ---------------------------------------------------------------- キーワード

_BRACKET = re.compile(r"[「『]([^」』]{2,30})[」』]")
_LABEL = re.compile(r"[【\[［(（][^】\]］)）]{1,12}[】\]］)）]")  # 【保存版】【2026年版】などの飾りは選ばない
_RUN = re.compile(r"[一-龥々〆ヵヶァ-ヴーA-Za-z0-9]{2,}")  # 漢字・カタカナ・英数字が続く所（ひらがなで切る）


def keyword(title: str) -> str:
    """タイトルから大きく見せる語を1つ選ぶ: かっこの中 → 一番長い漢字・カタカナ・英数字の並び。"""
    t = unicodedata.normalize("NFKC", title or "").strip()
    m = _BRACKET.search(t)
    if m:
        return m.group(1).strip()
    runs = _RUN.findall(_LABEL.sub(" ", t)) or _RUN.findall(t)
    return max(runs, key=len) if runs else t[:12]


def _width(s: str) -> float:
    return sum(0.55 if ord(c) < 0x2E80 else 1.0 for c in s)


def wrap(text: str, max_em: float, max_lines: int) -> List[str]:
    """日本語の折り返し（行頭に句読点を置かない程度の簡単なもの）。入りきらなければ最後を…にする。"""
    lines, cur = [], ""
    for ch in text:
        if _width(cur + ch) > max_em and cur:
            if ch in "、。，．）」』】!?！？ー" and len(cur) > 1:
                lines.append(cur[:-1])
                cur = cur[-1] + ch
            else:
                lines.append(cur)
                cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def _fit(text: str, box_w: int, max_lines: int, max_size: int, min_size: int) -> Tuple[int, List[str]]:
    size = max_size
    while size > min_size:
        lines = wrap(text, box_w / size, max_lines)
        if "".join(lines).replace("…", "") == text and len(lines) <= max_lines:
            return size, lines
        size -= 4
    return min_size, wrap(text, box_w / min_size, max_lines)


# ---------------------------------------------------------------- 配置（SVG と HTML で共通）

def layout(title: str, tpl: dict, kw: Optional[str] = None, sub: Optional[str] = None) -> dict:
    title = " ".join((title or "").split())
    if not title:
        raise ValueError("タイトルが空です。")
    kw = (kw if kw is not None else keyword(title)).strip()
    sub = (sub if sub is not None else title).strip()
    lay = tpl.get("layout", "center")
    if lay == "split":
        box_x, box_w = 470, 730
        kw_size, kw_lines = _fit(kw, 300, 3, 110, 48)
        sub_size, sub_lines = _fit(sub, box_w, 4, 52, 30)
    else:
        box_x, box_w = 100, 1080
        kw_size, kw_lines = _fit(kw, box_w, 2, 132, 64)
        sub_size, sub_lines = _fit(sub, box_w, 3, 48, 28)
    return {"title": title, "kw": kw, "sub": sub, "layout": lay, "box_x": box_x, "box_w": box_w,
            "kw_size": kw_size, "kw_lines": kw_lines, "sub_size": sub_size, "sub_lines": sub_lines}


def _bg_data_uri(title: str) -> str:
    cfg = config()
    data, mime = None, "image/png"
    prov = str(cfg.get("background_provider") or "")
    if prov and prov in BACKGROUND_PROVIDERS:
        data = BACKGROUND_PROVIDERS[prov](title, cfg)
    elif cfg.get("background_image"):
        p = (store.vault() / str(cfg["background_image"])).resolve()
        if store.vault().resolve() not in p.parents or not p.is_file():
            raise ValueError("background_image は作業フォルダ内の画像ファイルを指定してください。")
        data = p.read_bytes()
        mime = mimetypes.guess_type(str(p))[0] or mime
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii") if data else ""


def svg(title: str, tpl: dict, kw: Optional[str] = None, sub: Optional[str] = None) -> str:
    L = layout(title, tpl, kw, sub)
    cfg = config()
    font = html.escape(cfg["font_family"], quote=True)
    bg, fg, ac = (html.escape(tpl.get(k, d)) for k, d in (("bg", "#fff"), ("fg", "#333"), ("accent", "#f80")))
    img = _bg_data_uri(L["title"])
    parts = [f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{bg}"/>']
    if img:
        parts.append(f'<image href="{img}" width="{WIDTH}" height="{HEIGHT}" preserveAspectRatio="xMidYMid slice" opacity="0.35"/>')
    kw_color, sub_color = ac, fg
    if L["layout"] == "band":
        parts.append(f'<rect x="0" y="150" width="{WIDTH}" height="370" fill="{ac}"/>')
        kw_color, sub_color = fg, fg
        kw_y0 = 150 + (370 - (len(L["kw_lines"]) * L["kw_size"] * 1.15 + len(L["sub_lines"]) * L["sub_size"] * 1.4 + 24)) / 2
    elif L["layout"] == "split":
        parts.append(f'<rect x="0" y="0" width="420" height="{HEIGHT}" fill="{ac}"/>')
        kw_color = "#ffffff"
        kw_y0 = (HEIGHT - len(L["kw_lines"]) * L["kw_size"] * 1.15) / 2
    else:
        parts.append(f'<rect x="40" y="40" width="{WIDTH - 80}" height="{HEIGHT - 80}" rx="36" fill="none" stroke="{ac}" stroke-width="10"/>')
        kw_y0 = (HEIGHT - (len(L["kw_lines"]) * L["kw_size"] * 1.15 + len(L["sub_lines"]) * L["sub_size"] * 1.4 + 24)) / 2
    y = kw_y0
    kx = 60 if L["layout"] == "split" else L["box_x"]
    for line in L["kw_lines"]:
        y += L["kw_size"] * 1.05
        parts.append(f'<text x="{kx}" y="{y:.0f}" font-size="{L["kw_size"]}" font-weight="800" fill="{kw_color}">{html.escape(line)}</text>')
        y += L["kw_size"] * 0.10
    if L["layout"] == "split":
        y = (HEIGHT - len(L["sub_lines"]) * L["sub_size"] * 1.4) / 2
        sx = L["box_x"]
    else:
        y += 24
        sx = L["box_x"]
    for line in L["sub_lines"]:
        y += L["sub_size"] * 1.25
        parts.append(f'<text x="{sx}" y="{y:.0f}" font-size="{L["sub_size"]}" font-weight="700" fill="{sub_color}">{html.escape(line)}</text>')
        y += L["sub_size"] * 0.15
    if cfg.get("author"):
        parts.append(f'<text x="{WIDTH - 70}" y="{HEIGHT - 60}" font-size="28" text-anchor="end" fill="{sub_color}" opacity=".8">'
                     f'{html.escape(str(cfg["author"]))}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" '
            f'font-family="{font}">' + "".join(parts) + "</svg>")


def html_page(title: str, tpl: dict, kw: Optional[str] = None, sub: Optional[str] = None) -> str:
    """スクリーンショット用の HTML（SVG をそのまま 1280×670 で置く。外部読み込みなし）。"""
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            f'<style>html,body{{margin:0;padding:0;width:{WIDTH}px;height:{HEIGHT}px;overflow:hidden;background:#fff}}</style></head>'
            f'<body>{svg(title, tpl, kw, sub)}</body></html>')


# ---------------------------------------------------------------- PNG

def _chrome() -> str:
    c = os.environ.get("NW_CHROME") or str(config().get("chrome_path") or "")
    if c and (Path(c).is_file() or shutil.which(c)):
        return c
    return ""


def png_engine() -> str:
    try:
        import playwright.sync_api  # noqa: F401
        return "playwright"
    except ImportError:
        pass
    return "chrome" if _chrome() else ""


def to_png(html_text: str, out: Path) -> bool:
    eng = png_engine()
    if eng == "playwright":
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            try:
                pg = b.new_page(viewport={"width": WIDTH, "height": HEIGHT})
                pg.set_content(html_text)
                data = pg.screenshot(type="png")
            finally:
                b.close()
        out.write_bytes(data)
        return True
    if eng == "chrome":
        # 一時ファイルも作業フォルダ（暗号化境界）の内側に作る
        with tempfile.TemporaryDirectory(dir=str(out.parent)) as td:
            src = Path(td) / "t.html"
            src.write_text(html_text, encoding="utf-8")
            # ウィンドウの枠の分だけ表示域が狭くなる版があるので、縦長に撮ってから上の 670px を切り出す
            r = subprocess.run([_chrome(), "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                                f"--user-data-dir={td}/profile", f"--window-size={WIDTH},{HEIGHT + 400}",
                                f"--screenshot={out}", src.as_uri()], capture_output=True, text=True, timeout=90)
            if r.returncode != 0 or not out.is_file():
                return False
            out.write_bytes(crop_png_height(out.read_bytes(), HEIGHT))
            return True
    return False


def crop_png_height(data: bytes, height: int) -> bytes:
    """PNG の上から height 行だけを残す（標準ライブラリだけ。行のフィルタは上の行しか参照しないので、下を切るだけでよい）。"""
    import struct
    import zlib
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("PNG ではありません。")
    pos, chunks = 8, []
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        chunks.append((typ, data[pos + 8:pos + 8 + n]))
        pos += 12 + n
    ihdr = chunks[0][1]
    w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", ihdr)
    if h <= height:
        return data
    if depth != 8 or ctype not in (2, 6) or interlace:
        raise ValueError("切り出せない形式の PNG です。")
    stride = 1 + w * (4 if ctype == 6 else 3)
    raw = zlib.decompress(b"".join(c for t, c in chunks if t == b"IDAT"))[: stride * height]

    def chunk(t: bytes, c: bytes) -> bytes:
        return struct.pack(">I", len(c)) + t + c + struct.pack(">I", zlib.crc32(t + c) & 0xFFFFFFFF)

    new_ihdr = struct.pack(">IIBBBBB", w, height, depth, ctype, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", new_ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------- 保存

def _dir(article: str) -> Path:
    return store.vault() / "thumbnails" / W.slug(article)


def generate(article: str, title: str, names: List[str], kw: Optional[str] = None, sub: Optional[str] = None,
             png: bool = True) -> List[dict]:
    W._article(article)
    if not names:
        raise ValueError("ひな形を1つ以上選んでください。")
    d = _dir(article)
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for n in names:
        tpl = template(n)
        s = svg(title, tpl, kw or None, sub or None)
        h = html_page(title, tpl, kw or None, sub or None)
        store.atomic_write(d / f"{n}.svg", s)
        store.atomic_write(d / f"{n}.html", h)
        made_png = False
        if png:
            try:
                made_png = to_png(h, d / f"{n}.png")
            except Exception:  # PNG は「あれば」。失敗しても SVG・HTML は残す
                made_png = False
        out.append({"name": n, "svg": f"{n}.svg", "html": f"{n}.html", "png": f"{n}.png" if made_png else ""})
    return out


def list_files(article: str) -> List[dict]:
    d = _dir(article)
    out = []
    for t in config()["templates"]:
        files = {ext: (d / f"{t['name']}.{ext}") for ext in ("svg", "html", "png")}
        if files["svg"].is_file():
            out.append({"name": t["name"], "label": t.get("label", t["name"]),
                        **{ext: (p.name if p.is_file() else "") for ext, p in files.items()}})
    return out


def read_file(article: str, fname: str) -> Tuple[bytes, str]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.(svg|html|png)", fname or ""):
        raise KeyError(fname)
    p = _dir(article) / fname
    if not p.is_file():
        raise KeyError(fname)
    ctype = {"svg": "image/svg+xml", "html": "text/html; charset=utf-8", "png": "image/png"}[fname.rsplit(".", 1)[1]]
    return p.read_bytes(), ctype
