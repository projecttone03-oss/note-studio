"""SQLite接続とスキーマ管理。

スキーマ変更は MIGRATIONS に追記するだけにする（PRAGMA user_version で適用済みを管理）。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("NS_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "studio.db"

# この目印ファイルがあるマシン（あなたのMac）だけが、DBへの書き込み・人の判断・同期を行える。
# クラウド（Claude Code on the web）には存在しない（.gitignore 済み）ので、クラウドは読み取り専用になる。
LOCAL_MARKER = ROOT / ".ns-local"


def is_local() -> bool:
    return LOCAL_MARKER.exists()

MIGRATIONS = [
    # v1: 段階1（需要リサーチ）と段階2（企画・差別化設計）
    """
    CREATE TABLE research_runs (
        id          INTEGER PRIMARY KEY,
        created_at  TEXT NOT NULL,
        genre       TEXT,
        brief       TEXT,
        method      TEXT,
        queries     TEXT,            -- JSON配列
        raw_path    TEXT,
        md_path     TEXT
    );

    CREATE TABLE themes (
        id                   INTEGER PRIMARY KEY,
        run_id               INTEGER REFERENCES research_runs(id),
        title                TEXT NOT NULL,
        summary              TEXT,
        target_reader        TEXT,
        reader_pain          TEXT,
        keywords             TEXT,   -- JSON配列
        channels             TEXT,   -- JSON配列（想定集客経路）
        price_min            INTEGER,
        price_max            INTEGER,
        price_note           TEXT,
        gap                  TEXT,
        differentiation_hint TEXT,
        primary_info_needed  TEXT,
        risks                TEXT,   -- JSON配列
        scores               TEXT,   -- JSON {demand, gap, ...}
        score_total          REAL,
        score_rationale      TEXT,
        status               TEXT NOT NULL DEFAULT 'candidate',
        human_note           TEXT,
        created_at           TEXT NOT NULL,
        updated_at           TEXT NOT NULL
    );
    CREATE INDEX idx_themes_status ON themes(status);

    -- 人間の判断ログ（後で「何を採用・却下したか」を分析に使う）
    CREATE TABLE decisions (
        id          INTEGER PRIMARY KEY,
        theme_id    INTEGER REFERENCES themes(id),
        plan_id     INTEGER REFERENCES plans(id),
        action      TEXT NOT NULL,
        note        TEXT,
        created_at  TEXT NOT NULL
    );

    -- 出典と主張は分けて持つ（1つの出典を複数の主張が参照できる）
    CREATE TABLE sources (
        id          INTEGER PRIMARY KEY,
        url         TEXT UNIQUE,
        title       TEXT,
        publisher   TEXT,
        type        TEXT,            -- official / primary / news / qa / sns / note / blog / other
        published   TEXT,
        first_seen  TEXT NOT NULL
    );

    CREATE TABLE claims (
        id          INTEGER PRIMARY KEY,
        theme_id    INTEGER REFERENCES themes(id),
        plan_id     INTEGER REFERENCES plans(id),
        stage       TEXT NOT NULL,   -- research / plan / writing ...
        text        TEXT NOT NULL,
        source_id   INTEGER REFERENCES sources(id),
        confidence  TEXT,            -- high / medium / low
        verified    INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL
    );

    CREATE TABLE competitors (
        id             INTEGER PRIMARY KEY,
        theme_id       INTEGER NOT NULL REFERENCES themes(id),
        title          TEXT,
        url            TEXT,
        platform       TEXT,
        price          INTEGER,
        paid           INTEGER,
        quality_note   TEXT,
        price_verified INTEGER NOT NULL DEFAULT 0,
        origin         TEXT NOT NULL DEFAULT 'research',  -- research / manual
        created_at     TEXT NOT NULL
    );

    CREATE TABLE plans (
        id                INTEGER PRIMARY KEY,
        theme_id          INTEGER NOT NULL REFERENCES themes(id),
        version           INTEGER NOT NULL,
        status            TEXT NOT NULL DEFAULT 'draft', -- draft / revision_requested / approved / superseded
        value_proposition TEXT,
        before_after      TEXT,  -- JSON
        why_paid          TEXT,  -- JSON配列
        differentiators   TEXT,  -- JSON配列
        paywall           TEXT,  -- JSON
        titles            TEXT,  -- JSON配列
        outlines          TEXT,  -- JSON配列
        price             TEXT,  -- JSON
        open_questions    TEXT,  -- JSON配列
        chosen_title      TEXT,
        chosen_outline    INTEGER,
        final_price       INTEGER,
        human_note        TEXT,
        created_at        TEXT NOT NULL,
        approved_at       TEXT,
        raw_path          TEXT,
        md_path           TEXT
    );
    """,
]


def connect(path: Path | None = None, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path or DB_PATH)
    if readonly:
        if not path.exists():
            raise FileNotFoundError(f"DBがありません: {path}")
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, sql in enumerate(MIGRATIONS[current:], start=current + 1):
        conn.executescript(sql)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
