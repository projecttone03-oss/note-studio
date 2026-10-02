"""ボックスのかけらから「読める記事」を1本書く（体験談の下書き・作り直し版）。

- 材料は、そのボックスのかけら（「保留」は除く）と、前の版で本人が書いた・直した文（H1…）だけ。
  Claude はツールなし・MCPなし（jobs.py）、指示と材料は標準入力で渡す。
- Claude は見出しの構成を考え、かけらの文を読みやすく書き直し、つなぎの文を足してよい。事実は作らない。
- 1文ずつ根拠（かけらID / H）を記録する。根拠のない文は「足した文」（bridge）として画面で色を付け、公開前チェックで数を出す。
  足した文・根拠のある文に、材料に見当たらない数字・カギかっこ・カタカナ語があれば「要確認」の印（本文は書き換えない）。
- 材料が足りない所は【足りない①】の印。印ごとの質問（答えやすい形・大事な順）を story/<ボックス>.json に置き、
  答えはそのボックスのかけらとして保存する。「答えた分を入れて書き直す」で作り直す（前の版は版の履歴に残る）。
- 版は writing.py の仕組み（drafts/<ボックス>.md・版の履歴・生成来歴・手直しの記録）をそのまま使う。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from . import boxes as BX
from . import jobs
from . import kakera as K
from . import kakera_suggest as KS
from . import store
from . import writing as W

JOB = jobs.JobKind("story", "TJ", "実行中の下書きづくりがあります。終わってから実行してください。")
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
DEFAULTS = {"max_gaps": 8, "kakera_max_chars": 40000}
_MARK = re.compile(r"【足りない([①-⑳]|\d+)】")


def gap_label(n: int) -> str:
    return f"【足りない{CIRCLED[n - 1] if 1 <= n <= len(CIRCLED) else n}】"


def config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(W.load_config().get("story") or {})
    return cfg


# ---------------------------------------------------------------- 設定（送る前の確認）

def _settings_path():
    return store.vault() / "story" / "settings.json"


def confirm_before_send() -> bool:
    return bool((store.read_json(_settings_path(), {}) or {}).get("confirm_before_send", True))


def set_confirm_before_send(v: bool) -> None:
    store.write_json(_settings_path(), {"confirm_before_send": bool(v)})


# ---------------------------------------------------------------- 状態（質問リスト）

def _state_path(box: str):
    return store.vault() / "story" / f"{W.slug(box)}.json"


def state(box: str) -> dict:
    st = store.read_json(_state_path(box), None) or {}
    return {"box": box, "version": st.get("version"), "gaps": list(st.get("gaps", [])), "created": st.get("created", "")}


def _save_state(st: dict) -> None:
    store.write_json(_state_path(st["box"]), st)


# ---------------------------------------------------------------- 文に分ける

_SENT = re.compile(r"[^。！？!?\n]*[。！？!?]+[」』）)]*|[^。！？!?\n]+")


def split_sentences(text: str) -> List[str]:
    """文に分ける（。！？で区切る。【足りない①】は1つの文として切り出す）。"""
    out: List[str] = []
    for m in _SENT.finditer(text or ""):
        for p in re.split(r"(【足りない(?:[①-⑳]|\d+)】)", m.group(0)):
            if p.strip():
                out.append(p.strip())
    return out


# ---------------------------------------------------------------- 材料

def _box_kakera(box: str) -> List[dict]:
    return sorted([k for k in K.list_kakera() if k["article"] == box and k["status"] != W.EXCLUDED_STATUS],
                  key=lambda k: (k["created"], K._num(k["id"])))


def human_sentences(box: str) -> List[dict]:
    """いまの版で本人が書いた・直した文（H1…）。見出しは除く。"""
    cur = W.current(box)
    if not cur:
        return []
    out: List[dict] = []
    for b in cur["blocks"]:
        if b["kind"] != "para":
            continue
        if b.get("sentences"):
            texts = [s["text"] for s in b["sentences"] if s.get("origin") == "human" and not s.get("gap")]
        elif b.get("origin") == "human":
            texts = [b["text"]]
        else:
            texts = []
        for t in texts:
            t = _MARK.sub("", t).strip()
            if t and not t.startswith("[要追加"):
                out.append({"id": f"H{len(out) + 1}", "text": t})
    return out


def prepare(box: str) -> dict:
    a = W._article(box)
    name = a["name"]
    W._ensure_file_clean(name)
    ks = _box_kakera(name)
    if not ks:
        raise ValueError(f"ボックス「{name}」に使えるかけらがありません（「保留」のかけらは使いません）。")
    cfg = config()
    ktext = "\n\n".join(f"[{k['id']}]（{k['created'][:10]}）\n{k['body'].strip()}" for k in ks)
    if len(ktext) > int(cfg["kakera_max_chars"]):
        raise ValueError(f"かけらが長すぎます（{len(ktext)}字 / 上限{cfg['kakera_max_chars']}字）。途中で切ると事実が抜けるため送りません。")
    hs = human_sentences(name)
    htext = "\n".join(f"[{h['id']}] {h['text']}" for h in hs) or "（まだありません）"
    prompt = jobs.fill(jobs.template("story.md"), {
        "box": name, "style": W._style_for_prompt(W.load_config()), "human": htext, "kakera": ktext,
        "max_gaps": str(cfg["max_gaps"])})
    vs = W.versions(name)
    base = vs[-1]["v"] if vs else 0
    return {"box": name, "prompt": prompt, "chars": len(prompt), "kakera_ids": [k["id"] for k in ks], "human": hs,
            "replaces": bool(vs), "answered": sum(1 for g in state(name)["gaps"] if g.get("answered")),
            "confirm_token": jobs.token("story", name, prompt),
            "job": {"box": name, "kakera_ids": [k["id"] for k in ks], "human": hs, "base_version": base}}


# ---------------------------------------------------------------- 応答を記事にする

def parse(text: str) -> dict:
    data = jobs.json_obj(text)
    secs = data.get("sections")
    if not isinstance(secs, list) or not secs:
        raise ValueError("Claude の返答に見出しと本文（sections）がありませんでした。")
    gaps = data.get("gaps") if isinstance(data.get("gaps"), list) else []
    return {"sections": [s for s in secs if isinstance(s, dict)], "gaps": [g for g in gaps if isinstance(g, dict)]}


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def build(box: str, parsed: dict, kmap: Dict[str, dict], hmap: Dict[str, str]) -> Tuple[List[dict], List[dict], Dict[str, str]]:
    """(版のブロック, 質問リスト, かけらID→見出し)。"""
    all_src = [k["body"] for k in kmap.values()] + list(hmap.values())
    blocks = [W._block(f"# {box}", "heading", origin="skeleton")]
    gap_sec: Dict[int, str] = {}
    cited_sec: Dict[str, str] = {}
    for si, sec in enumerate(parsed["sections"], 1):
        heading = " ".join(str(sec.get("heading") or "").split()).lstrip("#").strip() or f"見出し{si}"
        blocks.append(W._block(f"## {heading}", "heading", origin="skeleton"))
        for p in sec.get("paragraphs") or []:
            sents = p.get("sentences") if isinstance(p, dict) else None
            if not isinstance(sents, list):
                continue
            out = []
            for s in sents:
                if not isinstance(s, dict):
                    continue
                if s.get("gap") is not None:
                    n = _int(s.get("gap"))
                    if n > 0:
                        out.append({"text": gap_label(n), "gap": n, "kakera": [], "bridge": False, "origin": "ai", "flags": []})
                        gap_sec.setdefault(n, heading)
                    continue
                t = " ".join(str(s.get("text") or "").split())
                if not t:
                    continue
                ids = K._as_list(s.get("kakera") or [])
                flags = []
                unknown = [x for x in ids if x not in kmap and x not in hmap]
                if unknown:
                    flags.append("材料にないIDを挙げていたので外しました: " + "、".join(unknown))
                ids = [x for x in ids if x in kmap or x in hmap]
                srcs = [kmap[x]["body"] if x in kmap else hmap[x] for x in ids] or all_src
                terms = W.unsupported_terms(t, srcs)
                if terms:
                    flags.append("かけらに見当たらない語（要確認）: " + "、".join(terms))
                for x in ids:
                    if x in kmap:
                        cited_sec.setdefault(x, heading)
                out.append({"text": t, "kakera": ids, "bridge": not ids, "origin": "ai", "flags": flags})
            if not out:
                continue
            kids = []
            for s in out:
                for x in s["kakera"]:
                    if x in kmap and x not in kids:
                        kids.append(x)
            b = W._block("".join(s["text"] for s in out), "para", kids, "ai", [f for s in out for f in s["flags"]])
            b["sentences"] = out
            blocks.append(b)
    if len(blocks) <= 1:
        raise ValueError("Claude の返答の本文が空でした。")
    gaps = []
    for g in parsed["gaps"]:
        n = _int(g.get("no"))
        q = " ".join(str(g.get("question") or "").split())[:300]
        if n <= 0 or not q:
            continue
        gaps.append({"no": n, "question": q, "why": " ".join(str(g.get("why") or "").split())[:300],
                     "priority": _int(g.get("priority"), 99), "section": gap_sec.get(n, ""), "answered": ""})
    placed = {n for n in gap_sec}
    for n in sorted(placed - {g["no"] for g in gaps}):  # 印だけあって質問がないもの
        gaps.append({"no": n, "question": f"{gap_label(n)} の所について、覚えていることを教えてください。", "why": "",
                     "priority": 99, "section": gap_sec[n], "answered": ""})
    gaps.sort(key=lambda g: (g["priority"], g["no"]))
    return blocks, gaps, cited_sec


def _save(box: str, blocks: List[dict], gaps: List[dict], cited_sec: Dict[str, str], base_version: int,
          force: bool) -> dict:
    with K._locked():
        W._ensure_file_clean(box)
        vs = W.versions(box)
        latest = vs[-1]["v"] if vs else 0
        if latest != base_version and not force:
            raise W.Conflict("作っているあいだに下書きが変わりました。結果は保留にしてあります。"
                             "今の下書きを確かめてから「この結果で置き換える」を選んでください。")
        cited = [x for x in cited_sec]
        prov = K.record_provenance(box, f"下書き（v{latest + 1}）", cited) if cited else None
        meta = W._save_version(box, blocks, "ai_story", "ボックスのかけらから下書きを作成",
                               provenance_id=(prov or {}).get("id", ""), kakera_ids=cited)
        # 区間（見出し）が空のかけらに、使われた見出しを自動で付ける（人が付けた区間は変えない）
        for kid, heading in cited_sec.items():
            try:
                if not K.get_kakera(kid)["section"]:
                    K.update_kakera(kid, section=heading)
            except KeyError:
                continue
        _save_state({"box": box, "version": meta["v"], "gaps": gaps, "created": store.now()})
        return meta


def _handle(job: dict, text: str) -> None:
    parsed = parse(text)
    kmap = {k["id"]: k for k in K.list_kakera() if k["id"] in set(job["kakera_ids"])}
    hmap = {h["id"]: h["text"] for h in job.get("human") or []}
    blocks, gaps, cited_sec = build(job["box"], parsed, kmap, hmap)
    job["result"] = {"blocks": blocks, "gaps": gaps, "cited_sec": cited_sec}
    try:
        meta = _save(job["box"], blocks, gaps, cited_sec, int(job["base_version"]), False)
    except W.Conflict as ex:
        job["status"] = "conflict"
        job["error"] = str(ex)
        return
    job["version"] = meta["v"]
    job["gap_count"] = len(gaps)
    job["bridge_count"] = sum(1 for b in blocks for s in b.get("sentences") or [] if s.get("bridge"))


def start(box: str, confirm_token: str) -> str:
    return JOB.start(prepare(box), confirm_token, _handle)


def run(box: str, confirm_token: str) -> dict:
    return JOB.run(prepare(box), confirm_token, _handle)


def apply_conflicted(jid: str) -> dict:
    job = JOB.get(jid)
    r = job.get("result") or {}
    if job.get("status") != "conflict" or not r or job.get("discarded"):
        raise ValueError("保留中の結果がありません。")
    meta = _save(job["box"], r["blocks"], r["gaps"], r["cited_sec"], int(job["base_version"]), True)
    job.update({"status": "done", "version": meta["v"], "error": ""})
    JOB.save(job)
    return meta


def discard(jid: str) -> None:
    job = JOB.get(jid)
    job["discarded"] = True
    JOB.save(job)


# ---------------------------------------------------------------- 質問に答える

def answer(box: str, no: int, text: str) -> dict:
    name = W._article(box)["name"]
    if not (text or "").strip():
        raise ValueError("答えが空です。")
    with K._locked():
        st = state(name)
        g = next((x for x in st["gaps"] if int(x["no"]) == int(no)), None)
        if g is None:
            raise ValueError("その質問はもうありません（下書きが作り直された可能性があります）。")
        k = K.create_kakera(text.strip(), article=name, section=g.get("section", ""),
                            viewpoints=[s["viewpoint"] for s in KS.suggest(text)],
                            source=f"質問への答え: {g['question'][:80]}")
        g["answered"] = k["id"]
        _save_state(st)
        BX.remember_box(name)
    return k


# ---------------------------------------------------------------- 画面・公開前チェック用

def current_blocks(box: str) -> List[dict]:
    cur = W.current(box)
    return [dict(b) for b in cur["blocks"]] if cur else []


def bridge_sentences(box: str) -> List[Tuple[int, dict]]:
    """いまの版で、Claude が足した（根拠のない）文の一覧（段落の番号, 文）。"""
    out = []
    for i, b in enumerate(current_blocks(box)):
        for s in b.get("sentences") or []:
            if s.get("bridge") and not s.get("gap"):
                out.append((i, s))
    return out


def gap_marks(text: str) -> int:
    return len(_MARK.findall(text or ""))


def replace_paragraph(box: str, index: int, new_text: str) -> dict:
    """1段落だけを手で直して、新しい版として保存する（空にすると段落を消す）。"""
    blocks = current_blocks(box)
    if not 0 <= index < len(blocks) or blocks[index]["kind"] != "para":
        raise ValueError("直す段落を選び直してください。")
    texts = [b["text"] for b in blocks]
    new_text = (new_text or "").strip()
    if new_text:
        texts[index] = new_text
    else:
        del texts[index]
    return W.save_human_edit(box, "\n\n".join(texts) + "\n", "段落を手で直した")
