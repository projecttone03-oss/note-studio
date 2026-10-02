"""インタビューモード（SPEC.md 機能2）: 一問一答で答えると、回答がかけらとして保存される。

- 質問は、充足度で空いている観点（区間 × 観点）と、下書きに残っている [要追加：〜] から作る（AI は使わない）。
- 回答は、その記事・区間・観点のかけら（タグ「インタビュー」）として保存する。内容は書き換えない。
- 音声入力は、スマホのキーボード（OS）の音声入力を使う（アプリは音声を扱わない＝音声データを外に送らない）。

保存先: 作業フォルダの interview/<記事>.json（スキップした質問と、回答したかけらID）
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional

from . import kakera as K
from . import store
from . import writing as W

DEFAULTS = {"viewpoint_questions": {}, "missing_question": "「{what}」について、覚えていることを教えてください。",
            "missing_viewpoint": "出来事", "tag": "インタビュー"}
_MISSING = re.compile(r"\[要追加[：:]([^\]]*)\]")


def config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(store.read_json(store.CONFIG_DIR / "interview.json", {}) or {})
    try:
        cfg.update(store.read_json(store.vault() / "config" / "interview.json", {}) or {})
    except store.VaultError:
        pass
    return cfg


def _state_path(article: str):
    return store.vault() / "interview" / f"{W.slug(article)}.json"


def _state(article: str) -> dict:
    st = store.read_json(_state_path(article), None) or {}
    return {"article": article, "skipped": list(st.get("skipped", [])), "answered": dict(st.get("answered", {})),
            "history": list(st.get("history", []))}


def _save_state(st: dict) -> None:
    store.write_json(_state_path(st["article"]), st)


def _fill(tpl: str, **kw) -> str:
    return re.sub(r"\{(section|what)\}", lambda m: kw.get(m.group(1), ""), tpl)


def all_questions(article: str) -> List[dict]:
    """[要追加] から作る質問を先に、続けて空いている観点の質問（区間の順）。"""
    a = W._article(article)
    cfg = config()
    out: List[dict] = []
    if W.current(a["name"]):
        blocks = W.current_blocks(a["name"])
        seen = set()
        for i, b in enumerate(blocks):
            if b["kind"] != "para":
                continue
            for m in _MISSING.finditer(b["text"]):
                what = m.group(1).strip() or "足りない所"
                sec = W.section_of(blocks, i)
                key = "add:" + hashlib.sha1(f"{sec}\n{what}".encode("utf-8")).hexdigest()[:12]
                if key in seen:
                    continue
                seen.add(key)
                out.append({"key": key, "section": sec, "viewpoint": cfg["missing_viewpoint"], "kind": "要追加",
                            "text": _fill(cfg["missing_question"], section=sec, what=what)})
    qs = cfg.get("viewpoint_questions") or {}
    for s in K.coverage(a["name"])["sections"]:
        for vp in s["missing"]:
            tpl = qs.get(vp) or "「{section}」について、「" + vp + "」で覚えていることを教えてください。"
            out.append({"key": f"vp:{s['section']}:{vp}", "section": s["section"], "viewpoint": vp, "kind": "空いている観点",
                        "text": _fill(tpl, section=s["section"])})
    return out


def pending(article: str) -> List[dict]:
    st = _state(W._article(article)["name"])
    done = set(st["skipped"]) | set(st["answered"])
    return [q for q in all_questions(article) if q["key"] not in done]


def next_question(article: str) -> Optional[dict]:
    p = pending(article)
    return p[0] if p else None


def _find(article: str, key: str) -> dict:
    for q in all_questions(article):
        if q["key"] == key:
            return q
    raise ValueError("この質問はもうありません（かけらが足りたか、下書きが変わりました）。次の質問に進んでください。")


def answer(article: str, key: str, text: str, viewpoints: Optional[List[str]] = None) -> dict:
    """回答をかけらとして保存する（本文は書いたまま）。"""
    name = W._article(article)["name"]
    if not (text or "").strip():
        raise ValueError("回答が空です（答えにくければ「スキップ」を押してください）。")
    q = _find(name, key)
    cfg = config()
    vps = list(viewpoints) if viewpoints else ([q["viewpoint"]] if q["viewpoint"] else [])
    k = K.create_kakera(text.strip(), article=name, section=q["section"], viewpoints=vps,
                        tags=[cfg.get("tag") or "インタビュー"], source=f"インタビュー: {q['text'][:80]}")
    with K._locked():
        st = _state(name)
        if key.startswith("add:"):  # 空いている観点の質問は、かけらが増えれば充足度から自然に消える
            st["answered"][key] = k["id"]
        st["history"].append(k["id"])
        _save_state(st)
    return k


def skip(article: str, key: str) -> None:
    name = W._article(article)["name"]
    with K._locked():
        st = _state(name)
        if key not in st["skipped"]:
            st["skipped"].append(key)
        _save_state(st)


def reset_skipped(article: str) -> int:
    name = W._article(article)["name"]
    with K._locked():
        st = _state(name)
        n = len(st["skipped"])
        st["skipped"] = []
        _save_state(st)
    return n


def answered(article: str) -> List[dict]:
    st = _state(W._article(article)["name"])
    out = []
    for kid in reversed(st["history"][-20:]):
        try:
            out.append(K.get_kakera(kid))
        except KeyError:
            continue
    return out
