"""取り込み・状態遷移・参照クエリ。

状態遷移（チェックポイント）はここで強制する:
  テーマ: candidate → selected / hold / rejected（人間が判断）
          selected → planning（企画ドラフト取り込み）→ planned（人間が企画を承認）
  企画:   draft → revision_requested / approved / superseded
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from . import schemas

THEME_STATUS = {
    "candidate": "確認待ち",
    "hold": "保留",
    "rejected": "却下",
    "selected": "採用・企画待ち",
    "planning": "企画確認待ち",
    "planned": "企画確定",
}
PLAN_STATUS = {
    "draft": "確認待ち",
    "revision_requested": "修正依頼中",
    "approved": "承認済み",
    "superseded": "旧版",
}
DECISIONS = {"select": "selected", "hold": "hold", "reject": "rejected", "reopen": "candidate"}
PLANNABLE = {"selected", "planning", "planned"}


class CheckpointError(Exception):
    """人間の確認を経ていない段階に進もうとしたときのエラー。"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def loads(v: str | None, default: Any = None) -> Any:
    if v in (None, ""):
        return default
    return json.loads(v)


# ---------- 取り込み ----------

def source_key(src: dict) -> str:
    """出典を一意に表すキー。書籍はURLがないので書名＋該当箇所で代用する。"""
    if src.get("type") == "book" and not (src.get("url") or "").strip():
        return f"book:{src['title'].strip()}#{(src.get('location') or '').strip()}"
    return src["url"].strip()


def _upsert_source(conn: sqlite3.Connection, src: dict) -> int:
    url = source_key(src)
    row = conn.execute("SELECT id FROM sources WHERE url = ?", (url,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO sources (url, title, publisher, type, published, first_seen) VALUES (?,?,?,?,?,?)",
        (url, src.get("title"), src.get("publisher"), src.get("type"), src.get("published"), now()),
    )
    return cur.lastrowid


def _insert_claim(conn, claim: dict, stage: str, theme_id: int, plan_id: int | None = None) -> None:
    source_id = _upsert_source(conn, claim["source"])
    conn.execute(
        "INSERT INTO claims (theme_id, plan_id, stage, text, source_id, confidence, created_at) VALUES (?,?,?,?,?,?,?)",
        (theme_id, plan_id, stage, claim["claim"], source_id, claim.get("confidence"), now()),
    )


def import_research(conn: sqlite3.Connection, data: dict, raw_path: str | None = None) -> tuple[int, list[int]]:
    errors = schemas.validate_research(data)
    if errors:
        raise ValueError("\n".join(errors))
    ts = now()
    with conn:
        run_id = conn.execute(
            "INSERT INTO research_runs (created_at, genre, brief, method, queries, raw_path) VALUES (?,?,?,?,?,?)",
            (ts, data.get("genre"), data["brief"], data.get("method"), dumps(data["queries"]), raw_path),
        ).lastrowid
        theme_ids = []
        for c in data["candidates"]:
            pr = c.get("price_range") or {}
            theme_id = conn.execute(
                """INSERT INTO themes (run_id, title, summary, target_reader, reader_pain, keywords, channels,
                       price_min, price_max, price_note, gap, differentiation_hint, primary_info_needed, risks,
                       scores, score_total, score_rationale, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'candidate', ?, ?)""",
                (
                    run_id, c["title"], c["summary"], c["target_reader"], c["reader_pain"],
                    dumps(c["keywords"]), dumps(c["channels"]), pr.get("min"), pr.get("max"), pr.get("note"),
                    c["gap"], c.get("differentiation_hint"), c["primary_info_needed"], dumps(c.get("risks", [])),
                    dumps(c["scores"]), schemas.score_total(c["scores"]), c["score_rationale"], ts, ts,
                ),
            ).lastrowid
            theme_ids.append(theme_id)
            for ev in c["demand_evidence"]:
                _insert_claim(conn, ev, "research", theme_id)
            for comp in c.get("competitors", []):
                conn.execute(
                    """INSERT INTO competitors (theme_id, title, url, platform, price, paid, quality_note,
                           price_verified, origin, created_at) VALUES (?,?,?,?,?,?,?,?, 'research', ?)""",
                    (
                        theme_id, comp["title"], comp.get("url"), comp.get("platform"), comp.get("price"),
                        None if comp.get("paid") is None else int(bool(comp["paid"])),
                        comp.get("quality_note"), int(bool(comp.get("price_verified"))), ts,
                    ),
                )
    return run_id, theme_ids


def import_plan(conn: sqlite3.Connection, data: dict, raw_path: str | None = None) -> int:
    errors = schemas.validate_plan(data)
    if errors:
        raise ValueError("\n".join(errors))
    theme = get_theme(conn, data["theme_id"])
    if theme is None:
        raise ValueError(f"テーマ T{data['theme_id']} が見つかりません")
    if theme["status"] not in PLANNABLE:
        raise CheckpointError(
            f"T{theme['id']}「{theme['title']}」は状態が「{THEME_STATUS[theme['status']]}」です。"
            "企画は、人間がテーマを『採用』してからでないと作れません。"
        )
    ts = now()
    with conn:
        version = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM plans WHERE theme_id = ?", (theme["id"],)
        ).fetchone()[0]
        conn.execute(
            "UPDATE plans SET status = 'superseded' WHERE theme_id = ? AND status IN ('draft', 'revision_requested')",
            (theme["id"],),
        )
        plan_id = conn.execute(
            """INSERT INTO plans (theme_id, version, status, value_proposition, before_after, why_paid,
                   differentiators, paywall, titles, outlines, price, open_questions, created_at, raw_path)
               VALUES (?,?, 'draft', ?,?,?,?,?,?,?,?,?,?,?)""",
            (
                theme["id"], version, data["value_proposition"], dumps(data["reader_before_after"]),
                dumps(data["why_paid"]), dumps(data["differentiators"]), dumps(data["paywall"]),
                dumps(data["titles"]), dumps(data["outlines"]), dumps(data["price"]),
                dumps(data.get("open_questions", [])), ts, raw_path,
            ),
        ).lastrowid
        for c in data.get("claims", []):
            _insert_claim(conn, c, "plan", theme["id"], plan_id)
        if theme["status"] != "planned":
            _set_theme_status(conn, theme["id"], "planning")
    return plan_id


# ---------- 人間の判断（チェックポイント） ----------

def _set_theme_status(conn, theme_id: int, status: str) -> None:
    conn.execute("UPDATE themes SET status = ?, updated_at = ? WHERE id = ?", (status, now(), theme_id))


def _log(conn, action: str, note: str | None, theme_id: int | None = None, plan_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO decisions (theme_id, plan_id, action, note, created_at) VALUES (?,?,?,?,?)",
        (theme_id, plan_id, action, note or None, now()),
    )


def decide_theme(conn, theme_id: int, action: str, note: str | None = None) -> None:
    if action not in DECISIONS:
        raise ValueError(f"action は {list(DECISIONS)} のいずれか")
    theme = get_theme(conn, theme_id)
    if theme is None:
        raise ValueError(f"テーマ T{theme_id} が見つかりません")
    new_status = DECISIONS[action]
    if action == "select" and theme["status"] in ("planning", "planned"):
        new_status = theme["status"]  # 企画が進んでいるテーマを採用し直しても巻き戻さない
    with conn:
        _set_theme_status(conn, theme_id, new_status)
        if note:
            conn.execute("UPDATE themes SET human_note = ? WHERE id = ?", (note, theme_id))
        _log(conn, f"theme:{action}", note, theme_id=theme_id)


THEME_EDITABLE = ("title", "summary", "target_reader", "reader_pain", "gap", "primary_info_needed", "human_note")


def update_theme(conn, theme_id: int, fields: dict) -> None:
    sets, vals = [], []
    for key in THEME_EDITABLE:
        if key in fields:
            sets.append(f"{key} = ?")
            vals.append(fields[key])
    if "keywords" in fields:
        kws = [k.strip() for k in fields["keywords"].replace("、", ",").split(",") if k.strip()]
        sets.append("keywords = ?")
        vals.append(dumps(kws))
    if not sets:
        return
    with conn:
        conn.execute(f"UPDATE themes SET {', '.join(sets)}, updated_at = ? WHERE id = ?", (*vals, now(), theme_id))
        _log(conn, "theme:edit", ", ".join(k for k in fields), theme_id=theme_id)


def add_competitor(conn, theme_id: int, title: str, url: str, platform: str, price: int | None,
                   note: str | None) -> None:
    with conn:
        conn.execute(
            """INSERT INTO competitors (theme_id, title, url, platform, price, paid, quality_note,
                   price_verified, origin, created_at) VALUES (?,?,?,?,?,?,?, 1, 'manual', ?)""",
            (theme_id, title, url or None, platform, price, int(bool(price)), note or None, now()),
        )


def delete_competitor(conn, competitor_id: int) -> int:
    row = conn.execute("SELECT theme_id FROM competitors WHERE id = ?", (competitor_id,)).fetchone()
    with conn:
        conn.execute("DELETE FROM competitors WHERE id = ?", (competitor_id,))
    return row["theme_id"] if row else 0


def toggle(conn, table: str, column: str, row_id: int) -> int:
    assert (table, column) in {("claims", "verified"), ("competitors", "price_verified")}
    with conn:
        conn.execute(f"UPDATE {table} SET {column} = 1 - {column} WHERE id = ?", (row_id,))
    return conn.execute(f"SELECT theme_id FROM {table} WHERE id = ?", (row_id,)).fetchone()["theme_id"]


def request_plan_revision(conn, plan_id: int, note: str) -> None:
    if not note.strip():
        raise ValueError("修正依頼の内容を書いてください")
    plan = get_plan(conn, plan_id)
    with conn:
        conn.execute("UPDATE plans SET status = 'revision_requested', human_note = ? WHERE id = ?", (note, plan_id))
        _log(conn, "plan:revise", note, theme_id=plan["theme_id"], plan_id=plan_id)


def approve_plan(conn, plan_id: int, title: str, outline_index: int, price: int, note: str | None = None) -> None:
    plan = get_plan(conn, plan_id)
    if plan is None:
        raise ValueError(f"企画 P{plan_id} が見つかりません")
    if plan["status"] == "superseded":
        raise CheckpointError("旧版の企画は承認できません。最新版を確認してください。")
    if not title.strip():
        raise ValueError("タイトルを選ぶか入力してください")
    if not 0 <= outline_index < len(plan["outlines"]):
        raise ValueError("見出し構成を選んでください")
    with conn:
        conn.execute(
            "UPDATE plans SET status = 'superseded' WHERE theme_id = ? AND id != ? AND status != 'superseded'",
            (plan["theme_id"], plan_id),
        )
        conn.execute(
            """UPDATE plans SET status = 'approved', chosen_title = ?, chosen_outline = ?, final_price = ?,
                   human_note = COALESCE(?, human_note), approved_at = ? WHERE id = ?""",
            (title.strip(), outline_index, price, note or None, now(), plan_id),
        )
        _set_theme_status(conn, plan["theme_id"], "planned")
        _log(conn, "plan:approve", f"{title} / 構成{outline_index + 1} / {price}円 {note or ''}".strip(),
             theme_id=plan["theme_id"], plan_id=plan_id)


# ---------- 参照 ----------

def _theme_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("keywords", "channels", "risks"):
        d[key] = loads(d[key], [])
    d["scores"] = loads(d["scores"], {})
    return d


def get_theme(conn, theme_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM themes WHERE id = ?", (theme_id,)).fetchone()
    return _theme_dict(row) if row else None


def theme_detail(conn, theme_id: int) -> dict | None:
    theme = get_theme(conn, theme_id)
    if theme is None:
        return None
    theme["claims"] = [dict(r) for r in conn.execute(
        """SELECT c.*, s.url, s.title AS source_title, s.publisher, s.type AS source_type, s.published
           FROM claims c LEFT JOIN sources s ON s.id = c.source_id WHERE c.theme_id = ? ORDER BY c.id""",
        (theme_id,),
    )]
    theme["competitors"] = [dict(r) for r in conn.execute(
        "SELECT * FROM competitors WHERE theme_id = ? ORDER BY id", (theme_id,))]
    theme["plans"] = [dict(r) for r in conn.execute(
        "SELECT id, version, status, chosen_title, final_price, created_at, approved_at FROM plans "
        "WHERE theme_id = ? ORDER BY version DESC", (theme_id,))]
    theme["decisions"] = [dict(r) for r in conn.execute(
        "SELECT * FROM decisions WHERE theme_id = ? ORDER BY id DESC", (theme_id,))]
    run = conn.execute("SELECT id, created_at, genre FROM research_runs WHERE id = ?", (theme["run_id"],)).fetchone()
    theme["run"] = dict(run) if run else None
    return theme


def list_themes(conn, status: str | None = None, run_id: int | None = None) -> list[dict]:
    sql = """SELECT t.*, (SELECT COUNT(*) FROM competitors c WHERE c.theme_id = t.id) AS competitor_count,
                    (SELECT COUNT(*) FROM claims c WHERE c.theme_id = t.id) AS claim_count
             FROM themes t WHERE 1=1"""
    args: list = []
    if status:
        sql += " AND t.status = ?"
        args.append(status)
    if run_id:
        sql += " AND t.run_id = ?"
        args.append(run_id)
    sql += " ORDER BY t.score_total DESC, t.id"
    out = []
    for r in conn.execute(sql, args):
        d = _theme_dict(r)
        d["competitor_count"] = r["competitor_count"]
        d["claim_count"] = r["claim_count"]
        out.append(d)
    return out


def list_runs(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT r.*, (SELECT COUNT(*) FROM themes t WHERE t.run_id = r.id) AS theme_count
           FROM research_runs r ORDER BY r.id DESC""")]


def get_run(conn, run_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM research_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["queries"] = loads(d["queries"], [])
    return d


def get_plan(conn, plan_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    for key, default in (("before_after", {}), ("why_paid", []), ("differentiators", []), ("paywall", {}),
                         ("titles", []), ("outlines", []), ("price", {}), ("open_questions", [])):
        d[key] = loads(d[key], default)
    d["claims"] = [dict(r) for r in conn.execute(
        """SELECT c.*, s.url, s.title AS source_title, s.type AS source_type
           FROM claims c LEFT JOIN sources s ON s.id = c.source_id WHERE c.plan_id = ? ORDER BY c.id""",
        (plan_id,),
    )]
    return d


def status_counts(conn) -> dict:
    counts = {k: 0 for k in THEME_STATUS}
    for r in conn.execute("SELECT status, COUNT(*) AS n FROM themes GROUP BY status"):
        counts[r["status"]] = r["n"]
    return counts


def pending_plans(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT p.id, p.version, p.status, p.created_at, t.id AS theme_id, t.title
           FROM plans p JOIN themes t ON t.id = p.theme_id
           WHERE p.status IN ('draft', 'revision_requested') ORDER BY p.id DESC""")]


def research_context(conn) -> dict:
    """次のリサーチに渡すフィードバック: 既出テーマ（重複回避）と人間の判断傾向。"""
    themes = conn.execute(
        """SELECT t.id, t.title, t.status, t.human_note, t.score_total, t.keywords
           FROM themes t ORDER BY t.id""").fetchall()
    return {
        "existing_themes": [
            {"id": r["id"], "title": r["title"], "status": r["status"], "score": r["score_total"],
             "keywords": loads(r["keywords"], []), "human_note": r["human_note"]}
            for r in themes
        ],
        "decision_log": [dict(r) for r in conn.execute(
            """SELECT d.action, d.note, d.created_at, t.title FROM decisions d
               LEFT JOIN themes t ON t.id = d.theme_id WHERE d.note IS NOT NULL ORDER BY d.id DESC LIMIT 30""")],
    }
