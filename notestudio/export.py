"""DBの内容をMarkdownに書き出す。DBが正、Markdownは読み返し・共有用の写し。"""
from __future__ import annotations

import re
from pathlib import Path

from . import models
from .db import DATA_DIR
from .schemas import DIFF_TYPES, SCORE_KEYS

RESEARCH_DIR = DATA_DIR / "research"
THEMES_DIR = DATA_DIR / "themes"


def slug(text: str, limit: int = 28) -> str:
    s = re.sub(r'[\\/:*?"<>|\s　「」『』【】（）()]+', "_", text).strip("_")
    return s[:limit] or "untitled"


def theme_dir(theme: dict) -> Path:
    existing = sorted(THEMES_DIR.glob(f"T{theme['id']:03d}_*"))
    if existing:
        return existing[0]
    return THEMES_DIR / f"T{theme['id']:03d}_{slug(theme['title'])}"


def _price(lo, hi) -> str:
    if lo is None and hi is None:
        return "不明"
    if lo == hi or hi is None:
        return f"{lo}円"
    if lo is None:
        return f"〜{hi}円"
    return f"{lo}〜{hi}円"


def _source(url: str, title: str) -> str:
    if url.startswith("book:"):
        loc = url.split("#", 1)[1] if "#" in url else ""
        return f"『{title}』{loc}"
    return f"[{title}]({url})"


def _bullets(items) -> str:
    return "\n".join(f"- {x}" for x in items) or "- （なし）"


def render_theme(theme: dict) -> str:
    status = models.THEME_STATUS.get(theme["status"], theme["status"])
    lines = [
        f"# T{theme['id']:03d} {theme['title']}",
        "",
        f"- 状態: **{status}**",
        f"- スコア: **{theme['score_total']}** / 100",
        f"- 想定価格帯（競合）: {_price(theme['price_min'], theme['price_max'])} {theme.get('price_note') or ''}",
        f"- キーワード: {', '.join(theme['keywords'])}",
        f"- 想定集客経路: {', '.join(theme['channels'])}",
        f"- 出典リサーチ: R{theme['run_id']:03d}" if theme.get("run_id") else "",
        "",
        "## 概要", theme["summary"] or "", "",
        "## 想定読者", theme["target_reader"] or "", "",
        "## 読者の悩み・今知りたい理由", theme["reader_pain"] or "", "",
        "## 需給ギャップ（良質な有料記事が少ない理由）", theme["gap"] or "", "",
        "## 差別化の芽", theme.get("differentiation_hint") or "（未記入）", "",
        "## あなたが用意すべき一次情報", theme["primary_info_needed"] or "", "",
        "## リスク", _bullets(theme["risks"]), "",
        "## スコア内訳",
    ]
    for key, label in SCORE_KEYS.items():
        lines.append(f"- {label}: {theme['scores'].get(key, '-')}/5")
    lines += ["", theme["score_rationale"] or "", "", "## 需要の根拠（主張と出典）"]
    for c in theme.get("claims", []):
        mark = "✅" if c["verified"] else "⬜"
        lines.append(f"- {mark} {c['text']}（確度: {c['confidence']}）")
        lines.append(f"  - 出典: {_source(c['url'], c['source_title'])} [{c['source_type']}]")
    lines += ["", "## 競合記事", "", "| タイトル | 媒体 | 価格 | 価格確認 | メモ |", "|---|---|---|---|---|"]
    for comp in theme.get("competitors", []):
        title = f"[{comp['title']}]({comp['url']})" if comp["url"] else comp["title"]
        price = f"{comp['price']}円" if comp["price"] is not None else ("無料" if comp["paid"] == 0 else "不明")
        lines.append(
            f"| {title} | {comp['platform'] or ''} | {price} | {'済' if comp['price_verified'] else '未'} "
            f"| {(comp['quality_note'] or '').replace('|', '/')} |"
        )
    if theme.get("human_note"):
        lines += ["", "## あなたのメモ", theme["human_note"]]
    if theme.get("decisions"):
        lines += ["", "## 判断ログ"]
        for d in theme["decisions"]:
            lines.append(f"- {d['created_at']} {d['action']} {d['note'] or ''}")
    return "\n".join(lines).rstrip() + "\n"


def render_plan(plan: dict, theme: dict) -> str:
    status = models.PLAN_STATUS.get(plan["status"], plan["status"])
    pw = plan["paywall"]
    lines = [
        f"# 企画 P{plan['id']:03d}（T{theme['id']:03d} {theme['title']} / v{plan['version']}）",
        "",
        f"- 状態: **{status}**",
    ]
    if plan["status"] == "approved":
        outline = plan["outlines"][plan["chosen_outline"]]
        lines += [
            f"- 確定タイトル: **{plan['chosen_title']}**",
            f"- 確定構成: {outline['name']}",
            f"- 確定価格: {plan['final_price']}円",
            f"- 承認日時: {plan['approved_at']}",
        ]
    lines += [
        "",
        "## なぜお金を払ってでも読むべきか", plan["value_proposition"], "",
        f"- 読む前: {plan['before_after'].get('before', '')}",
        f"- 読んだ後: {plan['before_after'].get('after', '')}",
        "", "### 無料情報では得られないもの", _bullets(plan["why_paid"]), "",
        "## 差別化ポイント",
    ]
    for d in plan["differentiators"]:
        lines += [
            f"### [{DIFF_TYPES.get(d['type'], d['type'])}] {d['title']}（手間: {d['effort']}）",
            d["description"], "", f"作り方: {d['how_to_make']}", "",
        ]
    lines += [
        "## 無料／有料の境界設計",
        "### 無料部分", _bullets(pw.get("free_part", [])), "",
        f"> 境界直前の一文: {pw.get('boundary_hook', '')}", "",
        "### 有料部分", _bullets(pw.get("paid_part", [])), "",
        f"設計意図: {pw.get('rationale', '')}", "",
        "## タイトル案",
    ]
    for i, t in enumerate(plan["titles"], 1):
        chosen = " ← 採用" if plan.get("chosen_title") == t["text"] else ""
        lines.append(f"{i}. {t['text']}（{t['angle']} / {len(t['text'])}字）{chosen}")
    lines += ["", "## 見出し構成案"]
    for i, o in enumerate(plan["outlines"]):
        chosen = " ← 採用" if plan["status"] == "approved" and plan["chosen_outline"] == i else ""
        lines += [f"### 案{i + 1}: {o['name']}{chosen}", o["concept"], ""]
        for s in o["sections"]:
            lines.append(f"- {'【有料】' if s['paid'] else '【無料】'} {s['heading']}")
            for p in s.get("points", []):
                lines.append(f"  - {p}")
        lines.append("")
    price = plan["price"]
    lines += [
        "## 価格", f"提案: {price.get('suggested')}円 — {price.get('rationale', '')}",
        f"代替案: {', '.join(str(p) + '円' for p in price.get('alternatives', []))}", "",
        "## 人間に確認したいこと", _bullets(plan["open_questions"]),
    ]
    if plan.get("claims"):
        lines += ["", "## 追加の根拠（主張と出典）"]
        for c in plan["claims"]:
            lines.append(f"- {c['text']} — {_source(c['url'], c['source_title'])}")
    if plan.get("human_note"):
        lines += ["", "## あなたのメモ・修正依頼", plan["human_note"]]
    return "\n".join(lines).rstrip() + "\n"


def render_run(run: dict, themes: list[dict]) -> str:
    lines = [
        f"# リサーチ R{run['id']:03d}（{run['created_at'][:10]}）",
        "",
        f"- ジャンル: {run.get('genre') or 'おまかせ'}",
        "", "## 探索方針", run["brief"] or "", "",
        "## 手法", run.get("method") or "", "",
        "## 使った検索クエリ", _bullets(run["queries"]), "",
        "## 候補テーマ（スコア順）", "",
        "| ID | テーマ | スコア | 競合価格帯 | 状態 |", "|---|---|---|---|---|",
    ]
    for t in themes:
        lines.append(
            f"| T{t['id']:03d} | {t['title']} | {t['score_total']} | {_price(t['price_min'], t['price_max'])} "
            f"| {models.THEME_STATUS.get(t['status'])} |"
        )
    lines += ["", "各テーマの詳細は `data/themes/` 以下を参照。"]
    return "\n".join(lines) + "\n"


def write_theme(conn, theme_id: int) -> Path:
    theme = models.theme_detail(conn, theme_id)
    d = theme_dir(theme)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "theme.md"
    path.write_text(render_theme(theme), encoding="utf-8")
    return path


def write_plan(conn, plan_id: int) -> Path:
    plan = models.get_plan(conn, plan_id)
    theme = models.get_theme(conn, plan["theme_id"])
    d = theme_dir(theme)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"plan_v{plan['version']}.md"
    path.write_text(render_plan(plan, theme), encoding="utf-8")
    with conn:
        conn.execute("UPDATE plans SET md_path = ? WHERE id = ?", (str(path.relative_to(DATA_DIR)), plan_id))
    return path


def write_run(conn, run_id: int) -> Path:
    run = models.get_run(conn, run_id)
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    path = RESEARCH_DIR / f"R{run_id:03d}_{run['created_at'][:10]}.md"
    path.write_text(render_run(run, models.list_themes(conn, run_id=run_id)), encoding="utf-8")
    with conn:
        conn.execute("UPDATE research_runs SET md_path = ? WHERE id = ?", (str(path.relative_to(DATA_DIR)), run_id))
    return path


def refresh_theme(conn, theme_id: int) -> None:
    """テーマに関わるMarkdown（テーマ・所属リサーチ・企画）をまとめて更新する。"""
    write_theme(conn, theme_id)
    theme = models.get_theme(conn, theme_id)
    if theme["run_id"]:
        write_run(conn, theme["run_id"])
    for r in conn.execute("SELECT id FROM plans WHERE theme_id = ?", (theme_id,)).fetchall():
        write_plan(conn, r["id"])


def export_all(conn) -> int:
    n = 0
    for r in conn.execute("SELECT id FROM themes").fetchall():
        refresh_theme(conn, r["id"])
        n += 1
    return n
