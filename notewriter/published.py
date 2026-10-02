"""公開済みの記事の登録（クロスセル導線・値付けの参考・執筆ペースに使う。SPEC.md 機能11）。

note から自動で取ってこない（公式 API がなく、note の /api/* はアクセスしない）。公開した記事は人が登録する。
クロスセルの案内文は、登録した記事のうちタグ・タイトルの語が近いものから、ひな形（config/crosssell.json）で作る。
下書きに入れるかどうかは人が決める（ボタンを押したときだけ、新しい版として末尾に足す）。

保存先: 作業フォルダの published.json（{"items": [...]}）
"""
from __future__ import annotations

import re
from datetime import date
from typing import List, Optional

from . import kakera as K
from . import publish as P
from . import store

FIELDS = ("title", "url", "price", "tags", "published", "summary", "article")
DEFAULT_TPL = {"header": "## あわせて読みたい", "item": "- [{title}]({url})（{price}）", "summary_line": "  {summary}",
               "footer": "", "max_items": 3, "free_label": "無料"}


def _path():
    return store.vault() / "published.json"


def _load() -> List[dict]:
    return list((store.read_json(_path(), {}) or {}).get("items", []))


def _save(items: List[dict]) -> None:
    store.write_json(_path(), {"items": items})


def _clean(f: dict) -> dict:
    title = " ".join(str(f.get("title") or "").split())[:200]
    if not title:
        raise ValueError("タイトルを入れてください。")
    url = str(f.get("url") or "").strip()
    if url and not re.fullmatch(r"https://[^\s]+", url):
        raise ValueError("URL は https:// から始まる形で入れてください。")
    price_raw = str(f.get("price") if f.get("price") is not None else "").strip()
    try:
        price = int(price_raw) if price_raw else 0
    except ValueError:
        raise ValueError("価格は数字で入れてください（無料は 0）。") from None
    if price < 0:
        raise ValueError("価格は 0 以上にしてください。")
    pub = str(f.get("published") or "").strip()[:10] or date.today().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", pub):
        raise ValueError("公開日は YYYY-MM-DD の形で入れてください。")
    tags = f.get("tags")
    tags = store.split_list(tags) if isinstance(tags, str) else [str(t).strip() for t in (tags or []) if str(t).strip()]
    return {"title": title, "url": url, "price": price, "tags": tags[:10], "published": pub,
            "summary": " ".join(str(f.get("summary") or "").split())[:200], "article": K._text(f.get("article") or "")}


def list_published() -> List[dict]:
    return sorted(_load(), key=lambda x: (x.get("published", ""), K._num(x["id"])), reverse=True)


def get(pid: str) -> dict:
    for x in _load():
        if x["id"] == pid:
            return x
    raise KeyError(pid)


def add(**f) -> dict:
    item = _clean(f)
    with K._locked():
        items = _load()
        item = dict(id=K._alloc_id("PB", max([K._num(x["id"]) for x in items] + [0])), created=store.now(), **item)
        items.append(item)
        _save(items)
    return item


def update(pid: str, **f) -> dict:
    item = _clean(f)
    with K._locked():
        items = _load()
        for x in items:
            if x["id"] == pid:
                x.update(item)
                _save(items)
                return x
    raise KeyError(pid)


def delete(pid: str) -> None:
    with K._locked():
        items = _load()
        rest = [x for x in items if x["id"] != pid]
        if len(rest) == len(items):
            raise KeyError(pid)
        _save(rest)


def tpl() -> dict:
    t = dict(DEFAULT_TPL)
    t.update(store.read_json(store.CONFIG_DIR / "crosssell.json", {}) or {})
    try:
        t.update(store.read_json(store.vault() / "config" / "crosssell.json", {}) or {})
    except store.VaultError:
        pass
    return t


def related(title: str, tags=(), exclude_article: str = "", n: Optional[int] = None) -> List[dict]:
    """タグの一致（2点）とタイトルの語の一致（1点）で近い順。0点は出さない。"""
    words = set(P._keywords(title))
    tags = set(tags or ())
    scored = []
    for x in _load():
        if exclude_article and x.get("article") == exclude_article:
            continue
        if x.get("title") == title:
            continue
        s = 2 * len(tags & set(x.get("tags") or [])) + len(words & set(P._keywords(x["title"])))
        if s > 0:
            scored.append((s, x.get("published", ""), x))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [x for _, _, x in scored[: int(n or tpl()["max_items"])]]


def crosssell_text(title: str, tags=(), exclude_article: str = "", items: Optional[List[dict]] = None) -> str:
    t = tpl()
    items = items if items is not None else related(title, tags, exclude_article)
    if not items:
        return ""
    lines = [t["header"], ""] if t.get("header") else []
    for x in items:
        price = t["free_label"] if not x.get("price") else f"{x['price']}円"
        lines.append(t["item"].replace("{title}", x["title"]).replace("{url}", x.get("url") or "").replace("{price}", price))
        if x.get("summary") and t.get("summary_line"):
            lines.append(t["summary_line"].replace("{summary}", x["summary"]))
    if t.get("footer"):
        lines += ["", t["footer"]]
    return "\n".join(lines).rstrip() + "\n"


def title_of(text: str, fallback: str) -> str:
    for lv, h, _ in P.headings(text):
        if lv == 1:
            return h
    return fallback
