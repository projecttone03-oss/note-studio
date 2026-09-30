"""体験談の下書き作成と改稿（SPEC.md 機能4の「本文確定処理」・版履歴、機能6の差分記録）。

- 下書きは区間ごとに作る。材料はその区間に割り当てたかけらだけ（「保留」は除く）。ネタ帳・リサーチ資料・
  ほかの区間のかけらは渡さない。Claude はツールなし・MCPなしで起動し、指示は標準入力で渡す（claude_runner）。
- AI の出力はそのまま信用しない: 段落ごとの根拠かけらIDを検証し、かけらに見当たらない数字・セリフ・
  カタカナ語を「要確認」として印を付ける（本文は書き換えない＝判断は人）。
- 生成・改稿・人の手直し・版の復元は、すべて新しい版として残す。本文確定のたびに生成来歴を記録する。
- 人が AI の段落を直したら、その組（AI の文章・人の文章）を文体学習の材料として記録する。

保存先（すべて作業フォルダ store.vault() 配下）:
- drafts/<記事>.md           いまの下書き（コンプライアンスチェッカーもここを読む）
- writing/<記事>/index.json   版の一覧
- writing/<記事>/v0001.json   版ごとの段落（text・kind・kakera・origin・flags）
- writing/jobs/WJ0001.json    実行の記録（改稿の提案もここに置き、人が採用したときだけ本文に入れる）
- style/rules.md             文体ルール集（人が編集。生成のたびに参照）
- style/edits.jsonl          AI の段落と人の手直しの組
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import threading
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import kakera as K
from . import secrets, store
from .claude_runner import ClaudeLimitError, ClaudeRunError, run_claude
from .kakera import _alloc_id, _locked, _num

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

DEFAULT_CONFIG = {
    "command": "claude", "model": "", "max_turns": 2, "timeout_sec": 600,
    "kakera_max_chars": 30000, "style_max_chars": 6000, "limit_patterns": [], "disallowed_tools": [],
}
EXCLUDED_STATUS = "保留"  # 下書きの材料に使わないかけらの使用状況
SOURCES = {"skeleton": "骨組み", "ai_generate": "AIが区間を作成", "ai_revise": "AIが段落を修正",
           "human_edit": "人の手直し", "restore": "前の版に戻した"}
STYLE_HEADER = ("# 文体ルール集\n\n"
                "<!-- 下書きを作るたびに、この内容を「書き方の参考」として Claude に渡します。事実の材料にはしません。\n"
                "     1行1ルールで、自由に書き足し・削除してください。 -->\n")


class ConfirmMismatch(ValueError):
    """確認した内容と、いま送る内容が違う。"""


class Conflict(ValueError):
    """生成・改稿のあいだに本文が変わった。"""


# ---------------------------------------------------------------- 設定と保存先

def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(store.read_json(store.CONFIG_DIR / "writing.json", {}) or {})
    try:
        cfg.update(store.read_json(store.vault() / "config" / "writing.json", {}) or {})
    except store.VaultError:
        pass
    return cfg


def _int(cfg: dict, key: str) -> int:
    try:
        return int(cfg.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def slug(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s\x00-\x1f]+', "_", str(name or "")).strip("._")
    return s[:60] or "article"


def _article(name: str) -> dict:
    name = K._text(name)
    for a in K.list_articles():
        if a["name"] == name:
            return a
    raise KeyError(name)


def _art_dir(name: str) -> Path:
    return store.vault() / "writing" / slug(name)


def draft_path(name: str) -> Path:
    return store.vault() / "drafts" / f"{slug(name)}.md"


def draft_rel(name: str) -> str:
    """コンプライアンスチェッカーに渡す drafts/ からの相対パス。"""
    return f"{slug(name)}.md"


def _load_index(name: str) -> dict:
    idx = store.read_json(_art_dir(name) / "index.json", None)
    if idx is None:
        return {"article": K._text(name), "versions": []}
    if idx.get("article") != K._text(name):
        raise ValueError(f"保存先 {slug(name)} は別の記事「{idx.get('article')}」が使っています。記事名を変えてください。")
    return idx


def _jobs_dir() -> Path:
    return store.vault() / "writing" / "jobs"


def style_path() -> Path:
    return store.vault() / "style" / "rules.md"


def edits_path() -> Path:
    return store.vault() / "style" / "edits.jsonl"


# ---------------------------------------------------------------- 段落（ブロック）

def split_blocks(text: str) -> List[dict]:
    """Markdown を段落に分ける。空行区切り。# で始まる行は単独の見出しブロックにする。"""
    blocks: List[dict] = []
    buf: List[str] = []

    def flush() -> None:
        if buf:
            blocks.append({"text": "\n".join(buf).strip(), "kind": "para"})
            buf.clear()

    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line.strip():
            flush()
        elif re.match(r"#{1,6}\s", line):
            flush()
            blocks.append({"text": line.strip(), "kind": "heading"})
        else:
            buf.append(line.rstrip())
    flush()
    return [b for b in blocks if b["text"]]


def render(blocks: Sequence[dict]) -> str:
    return "\n\n".join(b["text"] for b in blocks).rstrip() + "\n"


def _block(text: str, kind: str = "para", kakera=(), origin: str = "human", flags=()) -> dict:
    return {"text": text, "kind": kind, "kakera": list(kakera), "origin": origin, "flags": list(flags)}


def section_of(blocks: Sequence[dict], i: int) -> str:
    """i 番目のブロックが属する区間名（直前の ## 見出し）。"""
    for b in reversed(list(blocks[: i + 1])):
        if b["kind"] == "heading" and b["text"].startswith("## "):
            return b["text"][3:].strip()
    return ""


def _section_range(blocks: Sequence[dict], section: str) -> Optional[Tuple[int, int]]:
    """区間の見出しの位置 h と、区間の終わり（次の # / ## 見出しの手前）e。本文は h+1..e-1。"""
    for h, b in enumerate(blocks):
        if b["kind"] == "heading" and b["text"].startswith("## ") and b["text"][3:].strip() == section:
            e = h + 1
            while e < len(blocks) and not (blocks[e]["kind"] == "heading" and re.match(r"#{1,2}\s", blocks[e]["text"])):
                e += 1
            return h, e
    return None


def skeleton(name: str) -> List[dict]:
    a = _article(name)
    blocks = [_block(f"# {a['name']}", "heading", origin="skeleton")]
    for s in a["sections"]:
        blocks.append(_block(f"## {s}", "heading", origin="skeleton"))
    return blocks


# ---------------------------------------------------------------- 版

def versions(name: str) -> List[dict]:
    return list(_load_index(name).get("versions", []))


def get_version(name: str, v: int) -> dict:
    p = _art_dir(name) / f"v{int(v):04d}.json"
    data = store.read_json(p, None)
    if data is None:
        raise KeyError(f"{name} v{v}")
    return data


def current(name: str) -> Optional[dict]:
    vs = versions(name)
    return get_version(name, vs[-1]["v"]) if vs else None


def current_blocks(name: str) -> List[dict]:
    cur = current(name)
    return [dict(b) for b in cur["blocks"]] if cur else skeleton(name)


def file_changed(name: str) -> bool:
    """drafts/ の下書きファイルが、最新の版から直接書き換えられているか。"""
    cur = current(name)
    p = draft_path(name)
    if cur is None or not p.is_file():
        return False
    return p.read_text(encoding="utf-8").replace("\r\n", "\n") != render(cur["blocks"])


def _ensure_file_clean(name: str) -> None:
    if file_changed(name):
        raise Conflict("下書きファイル（drafts/）が直接書き換えられていて、まだ版として保存されていません。"
                       "先に「手直しを版として保存」してください（そのまま進めると手直しが消えるため止めました）。")


def _save_version(name: str, blocks: List[dict], source: str, note: str = "", **extra) -> dict:
    """新しい版を保存し、drafts/ の下書きを更新する（_locked の中で呼ぶ）。"""
    idx = _load_index(name)
    vs = idx.setdefault("versions", [])
    n = (vs[-1]["v"] if vs else 0) + 1
    meta = {"v": n, "created": store.now(), "source": source, "note": K._text(note)}
    meta.update({k: v for k, v in extra.items() if v not in (None, "", [])})
    store.write_json(_art_dir(name) / f"v{n:04d}.json", {"meta": meta, "blocks": blocks})
    vs.append(meta)
    store.write_json(_art_dir(name) / "index.json", idx)
    store.atomic_write(draft_path(name), render(blocks))
    return meta


def list_drafts() -> List[dict]:
    """下書きのある記事（記事一覧の順）と最新の版。"""
    out = []
    for a in K.list_articles():
        try:
            vs = versions(a["name"])
        except ValueError:
            continue
        out.append({"article": a["name"], "series": a["series"], "sections": a["sections"],
                    "latest": vs[-1] if vs else None, "count": len(vs)})
    return out


def save_human_edit(name: str, text: str, note: str = "") -> dict:
    """人の手直しを新しい版として保存する。AI の段落が直されたら、その組を文体学習用に記録する。"""
    _article(name)
    new_texts = split_blocks(text)
    if not new_texts:
        raise ValueError("本文が空です。")
    with _locked():
        old = current_blocks(name)
        if render(old) == render(new_texts):
            raise ValueError("前の版から変わっていません。")
        blocks, pairs = _align(old, new_texts)
        meta = _save_version(name, blocks, "human_edit", note)
        for sec, ai, human in pairs:
            _log_edit(name, sec, ai, human, meta["v"])
    return meta


def _align(old: List[dict], new: List[dict]) -> Tuple[List[dict], List[Tuple[str, str, str]]]:
    """前の版の段落と新しい本文を突き合わせ、変わらない段落は根拠・印を引き継ぐ。

    直された段落は origin=human にし、根拠かけらIDは元の段落から引き継ぐ（生成来歴は「最初に参照した
    かけら」の記録なので、手直しの後に再検証はしない＝SPEC.md 機能4）。
    返り値の pairs は（区間, AIの文章, 人の文章）で、文体学習の材料。
    """
    sm = difflib.SequenceMatcher(a=[b["text"] for b in old], b=[b["text"] for b in new], autojunk=False)
    out: List[dict] = []
    pairs: List[Tuple[str, str, str]] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out.extend(dict(b) for b in old[i1:i2])
            continue
        for k in range(j2 - j1):
            nb = new[j1 + k]
            ob = old[i1 + k] if op == "replace" and i1 + k < i2 else None
            if ob is not None and ob["kind"] == nb["kind"]:
                out.append(_block(nb["text"], nb["kind"], ob.get("kakera", ()), "human"))
                if ob.get("origin") == "ai" and nb["kind"] == "para":
                    pairs.append((section_of(old, i1 + k), ob["text"], nb["text"]))
            else:
                out.append(_block(nb["text"], nb["kind"], (), "human"))
    return out, pairs


def _log_edit(name: str, section: str, ai: str, human: str, v: int) -> None:
    p = edits_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"article": name, "section": section, "ai": ai, "human": human, "version": v,
                       "at": store.now()}, ensure_ascii=False)
    prev = p.read_text(encoding="utf-8") if p.is_file() else ""
    store.atomic_write(p, prev + line + "\n")


def list_edits() -> List[dict]:
    p = edits_path()
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def restore(name: str, v: int, note: str = "") -> dict:
    with _locked():
        _ensure_file_clean(name)
        old = get_version(name, v)
        return _save_version(name, [dict(b) for b in old["blocks"]], "restore", note or f"v{int(v)} に戻した",
                             restored_from=int(v))


def diff(name: str, a: int, b: int) -> List[dict]:
    """2つの版の段落単位の差分。op は equal / replace / insert / delete。"""
    A = [x["text"] for x in get_version(name, a)["blocks"]]
    B = [x["text"] for x in get_version(name, b)["blocks"]]
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=A, b=B, autojunk=False).get_opcodes():
        out.append({"op": op, "old": A[i1:i2], "new": B[j1:j2]})
    return out


# ---------------------------------------------------------------- 文体ルール

def get_style() -> str:
    p = style_path()
    return p.read_text(encoding="utf-8") if p.is_file() else STYLE_HEADER


def save_style(text: str) -> None:
    store.atomic_write(style_path(), (text or "").replace("\r\n", "\n").rstrip() + "\n")


def _style_for_prompt(cfg: dict) -> str:
    body = re.sub(r"<!--.*?-->", "", get_style(), flags=re.S)
    body = "\n".join(l for l in body.splitlines() if l.strip() and not l.startswith("# 文体ルール集")).strip()
    if not body:
        return "（まだありません。自然で読みやすい書き言葉で書く）"
    limit = _int(cfg, "style_max_chars") or 6000
    return body if len(body) <= limit else body[:limit] + "\n（長いため途中まで）"


# ---------------------------------------------------------------- 根拠チェック（警告だけ）

_MISSING = re.compile(r"\[要追加[：:][^\]]*\]")
_QUOTE = re.compile(r"「([^「」]{1,200})」")
_NUM = re.compile(r"[0-9０-９]+(?:[.,．][0-9０-９]+)?\s*(?:か月|ヶ月|カ月|ケ月|年|月|日|歳|才|時間|時|分|秒|円|回|人|件|万|億|千|個|杯|本|度|%|％|キロ|km|kg)?")
_KANNUM = re.compile(r"[〇一二三四五六七八九十百千万]+(?:か月|ヶ月|カ月|年|月|日|歳|才|時間|時|分|円|回|人|件|個|杯|本|度)")
_KATA = re.compile(r"[ァ-ヴー]{3,}")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def unsupported_terms(text: str, sources: Sequence[str]) -> List[str]:
    """本文のうち、根拠のかけらに見当たらない数字・セリフ・カタカナ語（最大10件）。

    機械的な目安で、見落としも誤検知もある。最終確認は人（SPEC.md 絶対1）。
    """
    body = _MISSING.sub("", text or "")
    src = _norm("\n".join(sources))
    terms: List[str] = []
    for m in _QUOTE.finditer(body):
        terms.append("「" + m.group(1) + "」")
    for rx in (_NUM, _KANNUM, _KATA):
        terms.extend(m.group(0) for m in rx.finditer(body))
    out: List[str] = []
    for t in terms:
        key = _norm(t.strip("「」")) if t.startswith("「") else _norm(t)
        if key and key not in src and t not in out:
            out.append(t)
    return out[:10]


def check_paragraph(p: dict, kmap: Dict[str, dict]) -> dict:
    """AI の段落1つを検証し、印（flags）を付けたブロックにする。"""
    text = str(p.get("text") or "").strip()
    ids = K._as_list(p.get("kakera") or [])
    flags: List[str] = []
    unknown = [k for k in ids if k not in kmap]
    if unknown:
        flags.append("材料にないかけらIDを挙げていたので外しました: " + "、".join(unknown))
    ids = [k for k in ids if k in kmap]
    only_missing = bool(_MISSING.fullmatch(text))
    if not ids and not only_missing:
        flags.append("根拠のかけらIDがありません（かけらにない内容の可能性）")
    terms = unsupported_terms(text, [kmap[k]["body"] for k in ids] if ids else [k["body"] for k in kmap.values()])
    if terms:
        flags.append("根拠のかけらに見当たらない語（要確認）: " + "、".join(terms))
    if "[要追加" in text:
        flags.append("[要追加] があります（書き足すか、かけらを追加）")
    return _block(text, "para", ids, "ai", flags)


# ---------------------------------------------------------------- プロンプト

def section_kakera(name: str, section: str) -> List[dict]:
    return [k for k in K.list_kakera()
            if k["article"] == name and k["section"] == section and k["status"] != EXCLUDED_STATUS]


def _kakera_text(ks: Sequence[dict], cfg: dict) -> str:
    parts = []
    for k in ks:
        vp = "、".join(k["viewpoints"]) or "—"
        parts.append(f"[{k['id']}]（観点: {vp}）\n{k['body'].strip()}")
    text = "\n\n".join(parts)
    limit = _int(cfg, "kakera_max_chars") or 30000
    if len(text) > limit:
        raise ValueError(f"この区間のかけらが長すぎます（{len(text)}字 / 上限{limit}字）。途中で切ると事実が抜けるため"
                         "送りません。区間を分けるか、config/writing.json の kakera_max_chars を見直してください。")
    return text


def _fill(tpl: str, values: Dict[str, str]) -> str:
    # 1回の置き換えで入れる（かけらに {section} 等の文字があっても二重に置き換わらない）
    keys = "|".join(re.escape(k) for k in values)
    return re.sub(r"\{(" + keys + r")\}", lambda m: values[m.group(1)], tpl)


def _template(fname: str) -> str:
    return (store.CONFIG_DIR / "writing_prompts" / fname).read_text(encoding="utf-8")


def _token(*parts: str) -> str:
    return hashlib.sha256("\n\x00".join(parts).encode("utf-8")).hexdigest()


def _build_generate(name: str, section: str, cfg: dict) -> Tuple[str, List[dict]]:
    a = _article(name)
    if section not in a["sections"]:
        raise ValueError(f"区間「{section}」は記事「{name}」に登録されていません（記事と区間の画面で追加できます）。")
    ks = section_kakera(name, section)
    if not ks:
        raise ValueError(f"区間「{section}」に使えるかけらがありません（「{EXCLUDED_STATUS}」のかけらは使いません）。")
    prompt = _fill(_template("section.md"), {
        "article": a["name"], "section": section, "section_count": str(len(a["sections"])),
        "section_no": str(a["sections"].index(section) + 1), "section_list": " → ".join(a["sections"]),
        "style": _style_for_prompt(cfg), "kakera": _kakera_text(ks, cfg)})
    return prompt, ks


def prepare_generate(name: str, section: str) -> dict:
    """送る前の確認用。prompt は Claude に渡す全文。"""
    name, section = K._text(name), K._text(section)
    _ensure_file_clean(name)
    prompt, ks = _build_generate(name, section, load_config())
    vs = versions(name)
    blocks = current_blocks(name)
    rng = _section_range(blocks, section)
    has_text = bool(rng and any(b["kind"] == "para" for b in blocks[rng[0] + 1:rng[1]]))
    return {"kind": "generate", "article": name, "section": section, "prompt": prompt, "chars": len(prompt),
            "kakera_ids": [k["id"] for k in ks], "replaces": has_text, "base_version": vs[-1]["v"] if vs else 0,
            "confirm_token": _token("generate", name, section, prompt)}


def _build_revise(name: str, index: int, instruction: str, cfg: dict) -> Tuple[str, List[dict], dict]:
    blocks = current_blocks(name)
    if not 0 <= index < len(blocks) or blocks[index]["kind"] != "para":
        raise ValueError("直す段落を選び直してください（見出しは直せません）。")
    instruction = (instruction or "").strip()
    if not instruction:
        raise ValueError("直しの指示を書いてください。")
    section = section_of(blocks, index)
    ks = section_kakera(name, section) if section else []
    if not ks:
        raise ValueError("この段落の区間に使えるかけらがありません。")

    def near(j: int) -> str:
        return blocks[j]["text"] if 0 <= j < len(blocks) and blocks[j]["kind"] == "para" else "（なし）"

    prompt = _fill(_template("revise.md"), {
        "article": name, "section": section, "instruction": instruction, "block": blocks[index]["text"],
        "prev": near(index - 1), "next": near(index + 1), "style": _style_for_prompt(cfg),
        "kakera": _kakera_text(ks, cfg)})
    return prompt, ks, blocks[index]


def prepare_revise(name: str, index: int, instruction: str) -> dict:
    name = K._text(name)
    _ensure_file_clean(name)
    prompt, ks, blk = _build_revise(name, int(index), instruction, load_config())
    cur = versions(name)[-1]["v"]
    return {"kind": "revise", "article": name, "block": int(index), "instruction": instruction.strip(),
            "old_text": blk["text"], "base_version": cur, "prompt": prompt, "chars": len(prompt),
            "kakera_ids": [k["id"] for k in ks],
            "confirm_token": _token("revise", name, str(index), str(cur), prompt)}


# ---------------------------------------------------------------- 応答の読み取り

_FENCE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)\n?```", re.S)


def _json_obj(text: str) -> dict:
    t = (text or "").strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("Claude の返答から JSON を読み取れませんでした。")
    try:
        data = json.loads(t[start:end + 1])
    except ValueError:
        raise ValueError("Claude の返答の JSON が壊れていました。") from None
    if not isinstance(data, dict):
        raise ValueError("Claude の返答の形が想定と違いました。")
    return data


def parse_paragraphs(text: str) -> List[dict]:
    data = _json_obj(text)
    paras = data.get("paragraphs")
    if not isinstance(paras, list) or not paras:
        raise ValueError("Claude の返答に段落（paragraphs）がありませんでした。")
    out = []
    for p in paras:
        if isinstance(p, dict) and str(p.get("text") or "").strip():
            body = str(p["text"]).strip()
            if re.match(r"#{1,6}\s", body):  # 見出しは書かない約束。混ざったら段落として扱う
                body = body.lstrip("#").strip()
            out.append({"text": body, "kakera": p.get("kakera") or []})
    if not out:
        raise ValueError("Claude の返答の段落がすべて空でした。")
    return out


def parse_revision(text: str) -> dict:
    data = _json_obj(text)
    body = str(data.get("text") or "").strip()
    if not body:
        raise ValueError("Claude の返答に書き直した段落（text）がありませんでした。")
    return {"text": body, "kakera": data.get("kakera") or []}


# ---------------------------------------------------------------- 本文への反映

def _apply_generate(name: str, section: str, paras: List[dict], ks: List[dict], base_version: int,
                    force: bool = False) -> dict:
    kmap = {k["id"]: k for k in ks}
    new_blocks = [check_paragraph(p, kmap) for p in paras]
    with _locked():
        _ensure_file_clean(name)
        blocks = current_blocks(name)
        vs = versions(name)
        latest = vs[-1]["v"] if vs else 0
        if latest != base_version and not force:
            base = get_version(name, base_version)["blocks"] if base_version else skeleton(name)
            rb, rc = _section_range(base, section), _section_range(blocks, section)
            sec_base = [b["text"] for b in base[rb[0]:rb[1]]] if rb else []
            sec_cur = [b["text"] for b in blocks[rc[0]:rc[1]]] if rc else []
            if sec_base != sec_cur:
                raise Conflict(f"作成中に区間「{section}」の本文が変わりました。結果は保留にしてあります。"
                               "今の本文を確かめてから「この結果で置き換える」を選んでください。")
        rng = _section_range(blocks, section)
        if rng is None:  # 区間の見出しがなければ、記事の区間の並びに合わせて差し込む
            order = _article(name)["sections"]
            pos = len(blocks)
            for later in order[order.index(section) + 1:]:
                r = _section_range(blocks, later)
                if r:
                    pos = r[0]
                    break
            blocks[pos:pos] = [_block(f"## {section}", "heading", origin="skeleton")]
            rng = (pos, pos + 1)
        h, e = rng
        blocks[h + 1:e] = new_blocks
        cited = []
        for b in new_blocks:
            for kid in b["kakera"]:
                if kid not in cited:
                    cited.append(kid)
        n_next = latest + 1
        prov = K.record_provenance(name, f"{section}（v{n_next}）", cited) if cited else None
        meta = _save_version(name, blocks, "ai_generate", f"区間「{section}」を作成", section=section,
                             provenance_id=(prov or {}).get("id", ""), kakera_ids=cited)
        return meta


def _apply_revise(name: str, index: int, proposal: dict, base_version: int, old_text: str,
                  instruction: str) -> dict:
    with _locked():
        _ensure_file_clean(name)
        blocks = current_blocks(name)
        if versions(name)[-1]["v"] != base_version or index >= len(blocks) or blocks[index]["text"] != old_text:
            raise Conflict("提案を作ったあとで本文が変わったため、そのままでは入れられません。"
                           "今の本文でもう一度「この段落を直す」からやり直してください。")
        blocks[index] = dict(proposal)
        section = section_of(blocks, index)
        cited = list(proposal.get("kakera") or [])
        prov = K.record_provenance(name, f"{section}／段落{index}（v{base_version + 1}）", cited) if cited else None
        return _save_version(name, blocks, "ai_revise", f"段落を修正: {instruction[:60]}", section=section,
                             block=index, provenance_id=(prov or {}).get("id", ""), kakera_ids=cited)


# ---------------------------------------------------------------- 実行（ジョブ）

_state_lock = threading.Lock()
_active: set = set()


def _job_path(jid: str) -> Path:
    return _jobs_dir() / f"{jid}.json"


def _save_job(job: dict) -> None:
    store.write_json(_job_path(job["id"]), job)


def get_job(jid: str) -> dict:
    jid = str(jid or "")
    if not re.fullmatch(r"WJ\d+", jid):
        raise KeyError(jid)
    job = store.read_json(_job_path(jid), None)
    if job is None:
        raise KeyError(jid)
    if job.get("status") == "running" and jid not in _active and _lock_is_free():
        job["status"] = "error"
        job["error"] = "実行が途中で止まりました（アプリの再起動など）。もう一度やり直してください。"
    return job


def list_jobs(name: str = "") -> List[dict]:
    d = _jobs_dir()
    if not d.is_dir():
        return []
    jobs = []
    for p in sorted(d.glob("WJ*.json"), key=lambda p: _num(p.stem), reverse=True):
        try:
            j = get_job(p.stem)
        except KeyError:
            continue
        if not name or j.get("article") == name:
            jobs.append(j)
    return jobs


def _run_lock_path() -> Path:
    d = store.vault() / "writing"
    d.mkdir(parents=True, exist_ok=True)
    return d / ".run-lock"


def _try_run_lock() -> Optional[int]:
    fd = os.open(str(_run_lock_path()), os.O_RDWR | os.O_CREAT, 0o600)
    if fcntl is None:  # pragma: no cover
        return fd
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _release(fd: Optional[int]) -> None:
    if fd is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _lock_is_free() -> bool:
    fd = _try_run_lock()
    if fd is None:
        return False
    _release(fd)
    return True


def running_job() -> Optional[dict]:
    for j in list_jobs():
        if j.get("status") == "running":
            return j
    return None


def _begin(p: dict, confirm_token: str) -> Tuple[dict, int]:
    if not confirm_token or confirm_token != p["confirm_token"]:
        raise ConfirmMismatch("確認した内容と、いま送る内容が違います（かけらや本文が変わった可能性があります）。"
                              "もう一度確認画面からやり直してください。")
    busy = "実行中の下書き作成・修正があります。終わってから実行してください。"
    with _state_lock:
        if _active:
            raise ValueError(busy)
        fd = _try_run_lock()
        if fd is None:
            raise ValueError(busy)
        try:
            with _locked():
                d = _jobs_dir()
                existing = [_num(x.stem) for x in d.glob("WJ*.json")] if d.is_dir() else []
                jid = _alloc_id("WJ", max(existing + [0]))
                job = {"id": jid, "kind": p["kind"], "status": "running", "created": store.now(), "finished": "",
                       "article": p["article"], "section": p.get("section", ""), "block": p.get("block"),
                       "instruction": p.get("instruction", ""), "old_text": p.get("old_text", ""),
                       "base_version": int(p.get("base_version") or 0),
                       "kakera_ids": p["kakera_ids"], "prompt_chars": p["chars"], "model": "",
                       "version": None, "proposal": None, "paragraphs": None, "error": "", "limit_resets_at": "",
                       "applied_version": None, "discarded": False}
                _save_job(job)
            _active.add(jid)
        except BaseException:
            _release(fd)
            raise
    return job, fd


def _finish(job: dict, fd: int) -> dict:
    job["finished"] = store.now()
    try:
        _save_job(job)
    finally:
        with _state_lock:
            _active.discard(job["id"])
            _release(fd)
    return job


def limit_message(resets_at: str = "") -> str:
    when = f"（{resets_at}ごろ解除）" if resets_at else "（解除の時刻は分かりませんでした）"
    return f"Claude の利用上限に達しました{when}。時間をおいてもう一度お試しください。"


def _execute(job: dict, prompt: str, fd: int) -> dict:
    try:
        cfg = load_config()
        res = run_claude(prompt, (), max_turns=_int(cfg, "max_turns") or 2,
                         timeout_sec=_int(cfg, "timeout_sec") or 600, cwd=store.vault() / "writing" / ".claude-cwd",
                         model=str(cfg.get("model") or ""), command=str(cfg.get("command") or "claude"),
                         disallowed_tools=tuple(cfg.get("disallowed_tools") or ()),
                         limit_patterns=tuple(cfg.get("limit_patterns") or ()))
        job["model"] = res.model or str(cfg.get("model") or "") or "（既定のモデル）"
        ks = [k for k in K.list_kakera() if k["id"] in set(job["kakera_ids"])]
        if job["kind"] == "generate":
            paras = parse_paragraphs(res.text)
            job["paragraphs"] = paras
            try:
                meta = _apply_generate(job["article"], job["section"], paras, ks, job["base_version"])
            except Conflict as ex:
                job["status"] = "conflict"
                job["error"] = str(ex)
                return _finish(job, fd)
            job["version"] = meta["v"]
        else:
            prop = parse_revision(res.text)
            job["proposal"] = check_paragraph(prop, {k["id"]: k for k in ks})
        job["status"] = "done"
    except ClaudeLimitError as ex:
        job["status"] = "limit"
        job["limit_resets_at"] = getattr(ex, "resets_at", "") or ""
        job["error"] = limit_message(job["limit_resets_at"])
    except (ClaudeRunError, ValueError, store.VaultError, OSError, KeyError) as ex:
        job["status"] = "error"
        job["error"] = secrets.redact(str(ex))
    except Exception as ex:  # 想定外でもジョブは必ず終わらせる
        job["status"] = "error"
        job["error"] = secrets.redact(f"予期しないエラー（{type(ex).__name__}）: {ex}")
    return _finish(job, fd)


def start_job(p: dict, confirm_token: str) -> str:
    """確認済みの内容 p（prepare_generate / prepare_revise の結果）を、裏で実行する。"""
    job, fd = _begin(p, confirm_token)
    t = threading.Thread(target=_execute, args=(job, p["prompt"], fd), name=f"writing-{job['id']}", daemon=True)
    try:
        t.start()
    except BaseException as ex:
        job["status"] = "error"
        job["error"] = f"実行を開始できませんでした（{type(ex).__name__}）。"
        _finish(job, fd)
        raise
    return job["id"]


def run_job(p: dict, confirm_token: str) -> dict:
    job, fd = _begin(p, confirm_token)
    return _execute(job, p["prompt"], fd)


def accept_revision(jid: str) -> dict:
    """改稿の提案を人が採用したときだけ本文に入れる。"""
    job = get_job(jid)
    if job.get("kind") != "revise" or job.get("status") != "done" or not job.get("proposal"):
        raise ValueError("採用できる提案がありません。")
    if job.get("applied_version") or job.get("discarded"):
        raise ValueError("この提案はもう処理済みです。")
    meta = _apply_revise(job["article"], int(job["block"]), job["proposal"], int(job["base_version"]),
                         job["old_text"], job.get("instruction", ""))
    job["applied_version"] = meta["v"]
    _save_job(job)
    return meta


def apply_conflicted(jid: str) -> dict:
    """作成中に本文が変わって保留になった結果を、人の判断で今の本文に入れる。"""
    job = get_job(jid)
    if job.get("kind") != "generate" or job.get("status") != "conflict" or not job.get("paragraphs"):
        raise ValueError("保留中の結果がありません。")
    ks = [k for k in K.list_kakera() if k["id"] in set(job["kakera_ids"])]
    meta = _apply_generate(job["article"], job["section"], job["paragraphs"], ks, job["base_version"], force=True)
    job.update({"status": "done", "version": meta["v"], "error": ""})
    _save_job(job)
    return meta


def discard(jid: str) -> None:
    job = get_job(jid)
    if job.get("applied_version"):
        raise ValueError("この提案はもう本文に入っています（戻すなら版の一覧から前の版に戻してください）。")
    job["discarded"] = True
    _save_job(job)
