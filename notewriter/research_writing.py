"""リサーチ型記事の下書き（SPEC.md 機能5）。

- 材料は、その記事に取り込んだリサーチ資料（research.py の M0001…）のうち、人が選んだものだけ。かけら・ネタ帳は渡さない。
- Claude はツールなし・MCPなし（jobs.py）。比較表1つと、区間ごとの段落（段落ごとに根拠の資料ID）を JSON で受け取る。
- AI の出力はそのまま信用しない: 資料IDの検証、資料に見当たらない数字・カギかっこ・カタカナ語に「要確認」の印、
  鮮度切れ（freshness_days 超）の資料を根拠にした段落に警告の印（本文は書き換えない＝判断は人）。
- 下書きは体験談と同じ版の仕組み（writing.py）に保存する。版には使った資料IDを記録する。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from . import jobs
from . import kakera as K
from . import research as R
from . import writing as W

JOB = jobs.JobKind("rdraft", "RJ", "実行中のリサーチ型の下書き作成があります。終わってから実行してください。")
DEFAULT_MAX_CHARS = 60000


def materials_for(article: str, ids: Optional[Sequence[str]] = None) -> List[dict]:
    name = W._article(article)["name"]
    if ids:
        out = []
        for mid in ids:
            m = R.get_material(mid)
            out.append(m)
        return out
    return [R.get_material(m["id"]) for m in R.list_materials(name)]


def _materials_text(ms: List[dict]) -> str:
    parts = []
    for m in ms:
        srcs = "、".join(s["url"] for s in (m.get("sources") or [])[:8]) or "（URLなし）"
        parts.append(f"[{m['id']}]（調べた日: {m['researched_at']}／題: {m['title']}／出典URL: {srcs}）\n{m['body'].strip()}")
    return "\n\n---\n\n".join(parts)


def prepare(article: str, ids: Optional[Sequence[str]] = None) -> dict:
    a = W._article(article)
    if not a["sections"]:
        raise ValueError("記事に区間がありません（記事と区間の画面で、見出しにする区間を登録してください）。")
    W._ensure_file_clean(a["name"])
    ms = materials_for(a["name"], ids)
    if not ms:
        raise ValueError("この記事のリサーチ資料がありません（リサーチ資料の画面で取り込むか、記事を選んで調べてください）。")
    cfg = W.load_config()
    text = _materials_text(ms)
    limit = int(cfg.get("research_max_chars") or DEFAULT_MAX_CHARS)
    if len(text) > limit:
        raise ValueError(f"選んだ資料が長すぎます（{len(text)}字 / 上限{limit}字）。資料を減らすか、config/writing.json の research_max_chars を見直してください。")
    prompt = jobs.fill(jobs.template("research_draft.md"), {
        "article": a["name"], "sections": " → ".join(a["sections"]), "style": W._style_for_prompt(cfg), "materials": text})
    vs = W.versions(a["name"])
    blocks = W.current_blocks(a["name"])
    return {"article": a["name"], "prompt": prompt, "chars": len(prompt), "material_ids": [m["id"] for m in ms],
            "stale": [{"id": m["id"], "researched_at": m["researched_at"], "age_days": m["age_days"]} for m in ms if m["stale"]],
            "replaces": any(b["kind"] == "para" for b in blocks), "base_version": vs[-1]["v"] if vs else 0,
            "confirm_token": jobs.token("rdraft", a["name"], prompt),
            "job": {"article": a["name"], "material_ids": [m["id"] for m in ms], "base_version": vs[-1]["v"] if vs else 0}}


def parse(text: str) -> dict:
    data = jobs.json_obj(text)
    paras = data.get("paragraphs")
    if not isinstance(paras, list) or not paras:
        raise ValueError("Claude の返答に段落（paragraphs）がありませんでした。")
    out = []
    for p in paras:
        if isinstance(p, dict) and str(p.get("text") or "").strip():
            body = str(p["text"]).strip().lstrip("#").strip() if str(p["text"]).lstrip().startswith("#") else str(p["text"]).strip()
            out.append({"section": str(p.get("section") or "").strip(), "text": body,
                        "materials": K._as_list(p.get("materials") or [])})
    if not out:
        raise ValueError("Claude の返答の段落がすべて空でした。")
    table = data.get("table") if isinstance(data.get("table"), dict) else None
    if table and str(table.get("markdown") or "").strip():
        table = {"section": str(table.get("section") or "").strip(), "text": str(table["markdown"]).strip(),
                 "materials": K._as_list(table.get("materials") or [])}
    else:
        table = None
    return {"table": table, "paragraphs": out}


def check_block(p: dict, mmap: Dict[str, dict], fresh_days: int) -> dict:
    text = p["text"]
    flags: List[str] = []
    ids = p.get("materials") or []
    unknown = [m for m in ids if m not in mmap]
    if unknown:
        flags.append("材料にない資料IDを挙げていたので外しました: " + "、".join(unknown))
    ids = [m for m in ids if m in mmap]
    only_missing = bool(W._MISSING.fullmatch(text))
    if not ids and not only_missing:
        flags.append("根拠の資料IDがありません（資料にない内容の可能性）")
    srcs = [mmap[m]["body"] for m in ids] if ids else [m["body"] for m in mmap.values()]
    terms = W.unsupported_terms(text, srcs)
    if terms:
        flags.append("根拠の資料に見当たらない語（要確認）: " + "、".join(terms))
    stale = [m for m in ids if mmap[m].get("stale")]
    if stale:
        flags.append("鮮度切れの資料を根拠にしています（" + "、".join(
            f"{m}: 調べた日 {mmap[m]['researched_at']}・{mmap[m]['age_days']}日前" for m in stale)
                     + f"。{fresh_days}日を超えています）。最新の情報で確かめ直してください。")
    if "[要追加" in text or "[要確認" in text:
        flags.append("[要追加]／[要確認] があります（調べ足すか、人が判断）")
    b = W._block(text, "para", (), "ai", flags)
    b["materials"] = ids
    return b


def build_blocks(article: str, parsed: dict, mmap: Dict[str, dict]) -> List[dict]:
    a = W._article(article)
    fresh = int(R.load_config().get("freshness_days") or 365)
    blocks = [W._block(f"# {a['name']}", "heading", origin="skeleton")]
    by_sec: Dict[str, List[dict]] = {s: [] for s in a["sections"]}
    first = a["sections"][0]
    for p in parsed["paragraphs"]:
        sec = p["section"] if p["section"] in by_sec else first
        by_sec[sec].append(check_block(p, mmap, fresh))
    table = parsed.get("table")
    for s in a["sections"]:
        blocks.append(W._block(f"## {s}", "heading", origin="skeleton"))
        if table and (table["section"] == s or (table["section"] not in by_sec and s == first)):
            blocks.append(check_block(table, mmap, fresh))
        blocks.extend(by_sec[s])
    return blocks


def _save(article: str, blocks: List[dict], material_ids: List[str], base_version: int, force: bool) -> dict:
    with K._locked():
        W._ensure_file_clean(article)
        vs = W.versions(article)
        latest = vs[-1]["v"] if vs else 0
        if latest != base_version and not force:
            raise W.Conflict("作成中に下書きが変わりました。結果は保留にしてあります。今の本文を確かめてから「この結果で置き換える」を選んでください。")
        cited = []
        for b in blocks:
            for m in b.get("materials") or []:
                if m not in cited:
                    cited.append(m)
        return W._save_version(article, blocks, "ai_research", "リサーチ資料から下書きを作成",
                               material_ids=cited or material_ids)


def _handle(job: dict, text: str) -> None:
    parsed = parse(text)
    mmap = {m["id"]: m for m in materials_for(job["article"], job["material_ids"])}
    blocks = build_blocks(job["article"], parsed, mmap)
    job["blocks"] = blocks
    try:
        meta = _save(job["article"], blocks, job["material_ids"], int(job["base_version"]), False)
    except W.Conflict as ex:
        job["status"] = "conflict"
        job["error"] = str(ex)
        return
    job["version"] = meta["v"]


def start(article: str, ids, confirm_token: str) -> str:
    return JOB.start(prepare(article, ids), confirm_token, _handle)


def run(article: str, ids, confirm_token: str) -> dict:
    return JOB.run(prepare(article, ids), confirm_token, _handle)


def apply_conflicted(jid: str) -> dict:
    job = JOB.get(jid)
    if job.get("status") != "conflict" or not job.get("blocks") or job.get("discarded"):
        raise ValueError("保留中の結果がありません。")
    meta = _save(job["article"], job["blocks"], job["material_ids"], int(job["base_version"]), True)
    job.update({"status": "done", "version": meta["v"], "error": ""})
    JOB.save(job)
    return meta


def discard(jid: str) -> None:
    job = JOB.get(jid)
    job["discarded"] = True
    JOB.save(job)
