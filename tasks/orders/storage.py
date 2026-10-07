"""Persist requests and model-call evidence in SQLite and JSON."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import CALLS_PATH as DEFAULT_CALLS_PATH
from config import DB_PATH as DEFAULT_DB_PATH
from config import RESULTS_PATH as DEFAULT_RESULTS_PATH

# Paths can be replaced for isolated checks and tests.
DB_PATH = DEFAULT_DB_PATH
CALLS_PATH = DEFAULT_CALLS_PATH
RESULTS_PATH = DEFAULT_RESULTS_PATH


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS requests (
            request_id TEXT PRIMARY KEY,
            order_ref TEXT NOT NULL,
            text TEXT NOT NULL,
            original_text TEXT NOT NULL,
            source_file TEXT NOT NULL,
            status TEXT NOT NULL,
            reviewed INTEGER NOT NULL DEFAULT 0,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    present = {row[1] for row in conn.execute("PRAGMA table_info(requests)")}
    columns = (
        ("original_text", "TEXT NOT NULL DEFAULT ''"),
        ("source_file", "TEXT NOT NULL DEFAULT 'seed.json'"),
        ("reviewed", "INTEGER NOT NULL DEFAULT 0"),
        ("updated_at", "TEXT NOT NULL DEFAULT ''"),
    )
    for name, declaration in columns:
        if name not in present:
            conn.execute(
                f"ALTER TABLE requests ADD COLUMN {name} {declaration}"
            )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS corrections (
            correction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            field TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            status TEXT NOT NULL,
            changed_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    return conn


def append_model_call(record: dict[str, Any]) -> None:
    records = []
    if CALLS_PATH.exists():
        try:
            records = json.loads(CALLS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            records = []
    records.append(record)
    CALLS_PATH.write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def save_processed_results() -> list[dict[str, Any]]:
    conn = ensure_db()
    rows = conn.execute(
        "SELECT * FROM requests ORDER BY request_id"
    ).fetchall()
    records = []
    for row in rows:
        payload = json.loads(row["payload"])
        records.append(
            {
                "id": row["request_id"],
                "order_ref": row["order_ref"],
                "original_text": row["original_text"],
                "source_file": row["source_file"],
                "status": row["status"],
                "reviewed": bool(row["reviewed"]),
                **payload,
            }
        )
    conn.close()
    RESULTS_PATH.write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return records
