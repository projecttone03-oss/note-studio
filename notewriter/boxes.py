"""ボックスとつぶやき（かけらをつぶやくように投稿する）。

- ボックス = 何についての思い出かを分ける入れ物。中身は今までの「記事」（articles.json）と同じもの。
  1つのボックスから記事が1本できる。区間（見出し）は下書きを作るときに Claude が決める。
- つぶやき = 本文だけでかけらを作る。区間・観点・タグは求めない（観点はキーワードで自動、区間は下書きのときに自動）。
- 前回選んだボックスは作業フォルダの ui_state.json に覚えておく。
- かけらは、あとから別のボックスへ移せる（1つずつ・まとめて）。移すと区間は空に戻す（区間はボックスごとのものなので）。
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from . import kakera as K
from . import kakera_suggest as KS
from . import store

UNBOXED = "（ボックスなし）"
SOURCE = "つぶやき"


def _state_path():
    return store.vault() / "ui_state.json"


def last_box() -> str:
    st = store.read_json(_state_path(), {}) or {}
    name = str(st.get("last_box") or "")
    return name if name in box_names() else ""


def remember_box(name: str) -> None:
    st = store.read_json(_state_path(), {}) or {}
    st["last_box"] = name
    store.write_json(_state_path(), st)


def ensure_registered() -> None:
    """かけらに書かれているのに一覧にないボックス（古いデータ）を一覧に足す。"""
    names = {a["name"] for a in K.list_articles()}
    missing = []
    for k in K.list_kakera():
        if k["article"] and k["article"] not in names and k["article"] not in missing:
            missing.append(k["article"])
    for n in missing:
        K.save_article(n)


def box_names() -> List[str]:
    return [a["name"] for a in K.list_articles()]


def list_boxes() -> List[dict]:
    ensure_registered()
    ks = K.list_kakera()
    out = []
    for a in K.list_articles():
        mine = [k for k in ks if k["article"] == a["name"]]
        out.append({"name": a["name"], "count": len(mine),
                    "latest": max([k["created"] for k in mine] or [""])})
    unboxed = sum(1 for k in ks if not k["article"])
    if unboxed:
        out.append({"name": UNBOXED, "count": unboxed, "latest": "", "unboxed": True})
    return out


def create_box(name: str) -> dict:
    name = K._text(name)
    if not name:
        raise ValueError("ボックスの名前を入れてください。")
    if name == UNBOXED:
        raise ValueError("その名前は使えません。")
    if name in box_names():
        raise ValueError(f"「{name}」というボックスはもうあります。")
    a = K.save_article(name)
    remember_box(name)
    return a


def delete_box(name: str) -> None:
    if any(k["article"] == name for k in K.list_kakera()):
        raise ValueError("中にかけらがあるボックスは消せません（先にかけらを別のボックスへ移してください）。")
    K.delete_article(name)


def kakera_in(name: str) -> List[dict]:
    """ボックスのかけら（新しい順）。"""
    target = "" if name == UNBOXED else name
    return sorted([k for k in K.list_kakera() if k["article"] == target], key=lambda k: (k["created"], K._num(k["id"])),
                  reverse=True)


def post(body: str, box: str, section: str = "", viewpoints: Optional[Sequence[str]] = None) -> dict:
    """つぶやきを、かけらとして保存する。観点を選ばなければ、キーワードの候補を自動で付ける（あとで直せる）。"""
    text = (body or "").strip()
    if not text:
        raise ValueError("何も書かれていません。")
    if box and box != UNBOXED and box not in box_names():
        raise ValueError(f"ボックス「{box}」がありません。")
    box = "" if box == UNBOXED else box
    vps = list(viewpoints) if viewpoints else [s["viewpoint"] for s in KS.suggest(text)]
    k = K.create_kakera(text, article=box, section=section, viewpoints=vps, source=SOURCE)
    if box:
        remember_box(box)
    return k


def move(kids: Sequence[str], to_box: str) -> List[str]:
    """かけらを別のボックスへ移す。区間は空に戻す。移した ID の一覧。"""
    if to_box != UNBOXED and to_box not in box_names():
        raise ValueError(f"ボックス「{to_box}」がありません。")
    target = "" if to_box == UNBOXED else to_box
    ids = [str(x) for x in kids if str(x).strip()]
    if not ids:
        raise ValueError("移すかけらを選んでください。")
    moved = []
    for kid in ids:
        k = K.get_kakera(kid)
        if k["article"] == target:
            continue
        K.update_kakera(kid, article=target, section="")
        moved.append(kid)
    return moved
