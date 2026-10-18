"""SQLite connection + schema for the inspection history."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inspections (
    inspection_id      TEXT PRIMARY KEY,
    timestamp          TEXT NOT NULL,
    reference_id       TEXT NOT NULL,
    profile_kind       TEXT NOT NULL,
    status             TEXT NOT NULL,
    reason             TEXT NOT NULL,
    overall_confidence REAL NOT NULL,
    inspection_time_ms INTEGER NOT NULL,
    defect_count       INTEGER NOT NULL,
    result_json        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inspections_ts     ON inspections(timestamp);
CREATE INDEX IF NOT EXISTS idx_inspections_status ON inspections(status);
"""

# Columns added after the initial schema (Phase 1).
# Each entry is (column_name, column_definition) — the definition must be
# SQLite-compatible (no NOT NULL without a DEFAULT for existing rows).
_ADDITIVE_COLUMNS: list[tuple[str, str]] = [
    ("stage_id", "TEXT"),
    ("unit_id",  "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Add any missing columns to the inspections table without touching existing data.

    Uses ``PRAGMA table_info`` to detect existing columns so we never issue a
    duplicate ``ALTER TABLE ADD COLUMN`` (SQLite raises on duplicates).
    """
    existing: set[str] = {
        row[1]  # column name is the second field in PRAGMA table_info
        for row in conn.execute("PRAGMA table_info(inspections)").fetchall()
    }
    for col_name, col_def in _ADDITIVE_COLUMNS:
        if col_name not in existing:
            conn.execute(
                f"ALTER TABLE inspections ADD COLUMN {col_name} {col_def}"
            )
    conn.commit()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
        _migrate(conn)
    finally:
        conn.close()
