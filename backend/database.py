"""Minimal SQLite history store. Every call is defensive — a DB failure must
never break the analyze endpoint.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

log = logging.getLogger("gc-pulse.db")
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    mode        TEXT    NOT NULL,
    model       TEXT,
    risk_score  INTEGER NOT NULL DEFAULT 0,
    risk_level  TEXT    NOT NULL DEFAULT 'low',
    summary     TEXT    NOT NULL DEFAULT '',
    payload     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_created ON analyses (created_at DESC);
"""


@contextmanager
def _connect(db_path: str) -> Iterator[sqlite3.Connection]:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    try:
        with _connect(db_path) as conn:
            conn.executescript(SCHEMA)
    except Exception as exc:  # pragma: no cover
        log.warning("Could not initialise history DB: %s", exc)


def save_analysis(db_path: str, record: dict) -> int | None:
    analysis = record.get("analysis") or {}
    try:
        with _lock, _connect(db_path) as conn:
            cursor = conn.execute(
                """INSERT INTO analyses
                   (created_at, title, mode, model, risk_score, risk_level, summary, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.get("created_at", ""),
                    record.get("title", "Untitled"),
                    record.get("mode", "heuristic"),
                    record.get("model"),
                    int(analysis.get("risk_score", 0)),
                    analysis.get("risk_level", "low"),
                    (analysis.get("executive_summary") or "")[:400],
                    json.dumps(record, separators=(",", ":")),
                ),
            )
            return int(cursor.lastrowid)
    except Exception as exc:
        log.warning("Failed to persist analysis: %s", exc)
        return None


def list_analyses(db_path: str, limit: int = 25) -> list[dict]:
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                """SELECT id, created_at, title, mode, model, risk_score, risk_level, summary
                   FROM analyses ORDER BY id DESC LIMIT ?""",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception as exc:
        log.warning("Failed to list history: %s", exc)
        return []


def get_analysis(db_path: str, analysis_id: int) -> dict | None:
    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT payload FROM analyses WHERE id = ?", (analysis_id,)
            ).fetchone()
        if not row:
            return None
        data = json.loads(row["payload"])
        data["id"] = analysis_id
        return data
    except Exception as exc:
        log.warning("Failed to load analysis %s: %s", analysis_id, exc)
        return None


def delete_analysis(db_path: str, analysis_id: int) -> bool:
    try:
        with _lock, _connect(db_path) as conn:
            cursor = conn.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,))
            return cursor.rowcount > 0
    except Exception as exc:
        log.warning("Failed to delete analysis %s: %s", analysis_id, exc)
        return False


def clear_history(db_path: str) -> int:
    try:
        with _lock, _connect(db_path) as conn:
            cursor = conn.execute("DELETE FROM analyses")
            return int(cursor.rowcount)
    except Exception as exc:
        log.warning("Failed to clear history: %s", exc)
        return 0