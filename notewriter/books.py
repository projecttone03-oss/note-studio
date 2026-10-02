"""参考書籍（SPEC.md 機能6）: PDF を取り込み、アプリ側で文字を取り出し、指定したページから文体ルールの候補を出させる。

- PDF の文字の取り出しはアプリ側で行う（Mac は tools/pdftool、ほかは pdftotext）。Claude にはファイル操作ツールを
  持たせず、取り出した文字だけを標準入力で渡す（jobs.py。ツールなし・MCPなし）。
- 長い一節のそのままの複製は避け、手法の要約にとどめる: 候補の文が本文と copy_max_chars 字以上一致したら印を付け、
  直すまで採用できない（style_learn.py）。候補は人が採用/却下してから文体ルール集に入る（自動反映しない）。
- 書籍のファイルと取り出した文字は作業フォルダの中だけに置く（著作物なので外に出さない）。

保存先: 作業フォルダの books/B0001.json（情報）・B0001.pdf（元のファイル）・B0001.txt（ページごとの文字。\f 区切り）
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from . import jobs
from . import kakera as K
from . import store
from . import style_learn as S
from . import writing as W

JOB = jobs.JobKind("book", "BJ", "実行中の参考書籍の分析があります。終わってから実行してください。")
MAX_BYTES = 64 * 1024 * 1024
DEFAULTS = {"book_max_chars": 20000, "book_max_candidates": 8}


def _cfg() -> dict:
    c = dict(DEFAULTS)
    c.update(S.config())
    return c


def _dir() -> Path:
    return store.vault() / "books"


def _check_id(bid: str) -> str:
    if not re.fullmatch(r"B\d+", str(bid or "")):
        raise KeyError(bid)
    return bid


def list_books() -> List[dict]:
    d = _dir()
    if not d.is_dir():
        return []
    out = [store.read_json(p, {}) for p in sorted(d.glob("B*.json"), key=lambda p: K._num(p.stem))]
    return [b for b in out if b]


def get_book(bid: str) -> dict:
    b = store.read_json(_dir() / f"{_check_id(bid)}.json", None)
    if b is None:
        raise KeyError(bid)
    return b


# ---------------------------------------------------------------- 文字の取り出し

def _pdftool() -> str:
    p = os.environ.get("NW_PDFTOOL") or str(store.ROOT / "tools" / "pdftool")
    return p if Path(p).is_file() and os.access(p, os.X_OK) else ""


def _pdftotext() -> str:
    return os.environ.get("NW_PDFTOTEXT") or shutil.which("pdftotext") or ""


def extractor() -> str:
    return "pdftool" if _pdftool() else "pdftotext" if _pdftotext() else ""


def extract_pages(pdf: Path) -> List[str]:
    """PDF のページごとの文字。取り出す道具がなければ ValueError（理由と対処つき）。"""
    tool = _pdftool()
    if tool:
        info = subprocess.run([tool, "info", str(pdf)], capture_output=True, text=True, timeout=120)
        m = re.search(r"pages=(\d+)", info.stdout or "")
        if not m:
            raise ValueError("PDF のページ数を読めませんでした（暗号化されている可能性があります）。")
        r = subprocess.run([tool, "text", str(pdf), "1", m.group(1)], capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise ValueError("PDF の文字を取り出せませんでした（tools/pdftool）。")
        parts = re.split(r"^=== PDF p\.\d+ ===$", r.stdout, flags=re.M)
        return [p.strip("\n") for p in parts[1:]]
    tool = _pdftotext()
    if tool:
        r = subprocess.run([tool, "-enc", "UTF-8", "-layout", str(pdf), "-"], capture_output=True, timeout=600)
        if r.returncode != 0:
            raise ValueError("PDF の文字を取り出せませんでした（pdftotext）。暗号化されていないか確かめてください。")
        pages = r.stdout.decode("utf-8", "replace").split("\f")
        if pages and not pages[-1].strip():
            pages = pages[:-1]
        return pages
    raise ValueError("PDF の文字を取り出す道具が見つかりません。Mac では swiftc -O tools/pdftool.swift -o tools/pdftool を一度実行、"
                     "VPS では sudo apt install poppler-utils を実行してください。テキスト（.txt）で取り込むこともできます。")


def add_book(filename: str, data: bytes, title: str = "") -> dict:
    name = Path(str(filename or "")).name
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ("pdf", "txt"):
        raise ValueError("取り込めるのは PDF（.pdf）かテキスト（.txt）だけです。")
    if not data:
        raise ValueError("ファイルが空です。")
    if len(data) > MAX_BYTES:
        raise ValueError("ファイルが大きすぎます（64MB まで）。")
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    with K._locked():
        bid = K._alloc_id("B", max([K._num(b["id"]) for b in list_books()] + [0]))
    try:
        if ext == "pdf":
            if not data.startswith(b"%PDF"):
                raise ValueError("PDF ではないようです。")
            src = d / f"{bid}.pdf"
            fd, tmp = tempfile.mkstemp(prefix=".upload.", suffix=".pdf", dir=str(d))  # 一時ファイルも暗号化境界の内側
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, src)
            pages = extract_pages(src)
            how = extractor()
        else:
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise ValueError("テキストの文字コードが UTF-8 ではありません。") from None
            pages = text.replace("\r\n", "\n").split("\f")
            how = "テキスト"
        if not "".join(pages).strip():
            raise ValueError("文字が取り出せませんでした（画像だけの PDF の可能性があります。OCR 済みの PDF を使ってください）。")
        store.atomic_write(d / f"{bid}.txt", "\f".join(pages))
        meta = {"id": bid, "title": " ".join((title or name.rsplit(".", 1)[0]).split())[:120], "filename": name,
                "pages": len(pages), "chars": sum(len(p) for p in pages), "extractor": how, "added": store.now()}
        store.write_json(d / f"{bid}.json", meta)
        return meta
    except BaseException:
        for p in (d / f"{bid}.pdf", d / f"{bid}.txt", d / f"{bid}.json"):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        raise


def delete_book(bid: str) -> None:
    get_book(bid)
    for ext in ("pdf", "txt", "json"):
        try:
            (_dir() / f"{bid}.{ext}").unlink()
        except FileNotFoundError:
            pass


def _pages(bid: str) -> List[str]:
    get_book(bid)
    return (_dir() / f"{bid}.txt").read_text(encoding="utf-8").split("\f")


def full_text(bid: str) -> str:
    return "\n".join(_pages(bid))


def page_text(bid: str, start: int, end: int) -> str:
    pages = _pages(bid)
    if not (1 <= start <= end <= len(pages)):
        raise ValueError(f"ページは 1〜{len(pages)} の範囲で、始め ≦ 終わり にしてください。")
    return "\n\n".join(f"=== p.{i} ===\n{pages[i - 1].strip()}" for i in range(start, end + 1))


def find(bid: str, words: List[str], limit: int = 20) -> List[Tuple[int, str]]:
    """キーワードが出てくるページ（ページ番号, 前後の文）。"""
    out = []
    for i, p in enumerate(_pages(bid), 1):
        for w in words:
            k = p.find(w)
            if w and k != -1:
                out.append((i, re.sub(r"\s+", " ", p[max(0, k - 30): k + 50])))
                break
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- 分析

def prepare(bid: str, start: int, end: int, focus: str = "") -> dict:
    b = get_book(bid)
    cfg = _cfg()
    text = page_text(bid, int(start), int(end))
    if len(text) > int(cfg["book_max_chars"]):
        raise ValueError(f"選んだページの文字が多すぎます（{len(text)}字 / 上限{cfg['book_max_chars']}字）。ページを減らしてください。")
    focus = " ".join((focus or "").split())[:200]
    prompt = jobs.fill(jobs.template("book_style.md"), {
        "max": str(cfg["book_max_candidates"]), "focus_line": f"特に知りたいこと: {focus}" if focus else "",
        "style": W._style_for_prompt(W.load_config()), "title": b["title"], "start": str(start), "end": str(end), "text": text})
    return {"prompt": prompt, "chars": len(prompt), "confirm_token": jobs.token("book", bid, prompt),
            "job": {"book": bid, "start": int(start), "end": int(end), "focus": focus}}


def _handle(job: dict, text: str) -> None:
    rows = S.parse_candidates(text)
    for r in rows:
        if r.get("page") is not None:
            r["refs"] = [f"p.{r['page']}"]
    added = S.add_candidates(rows, f"book:{job['book']}", job["id"], full_text(job["book"]))
    job["added"] = [c["id"] for c in added]


def start(bid: str, s: int, e: int, focus: str, confirm_token: str) -> str:
    return JOB.start(prepare(bid, s, e, focus), confirm_token, _handle)


def run(bid: str, s: int, e: int, focus: str, confirm_token: str) -> dict:
    return JOB.run(prepare(bid, s, e, focus), confirm_token, _handle)
