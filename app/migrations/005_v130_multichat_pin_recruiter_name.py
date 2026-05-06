"""v1.3.0: chat_sessions, pinned_jobs, recruiters, candidate_profiles.name."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

VERSION = 5
DESCRIPTION = "v1.3.0 multi-chat sessions, pinned jobs, recruiter info, profile name"


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def upgrade(conn: sqlite3.Connection) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO chat_sessions(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("default", "", now, now),
    )

    cur = conn.execute("SELECT DISTINCT session_id FROM chat_messages")
    for (session_id,) in cur.fetchall():
        if not session_id:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO chat_sessions(id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, "", now, now),
        )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pinned_jobs (
            session_id TEXT NOT NULL,
            job_id INTEGER NOT NULL,
            pinned_at TEXT NOT NULL,
            PRIMARY KEY (session_id, job_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pinned_jobs_session ON pinned_jobs(session_id)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recruiters (
            job_id INTEGER PRIMARY KEY,
            name TEXT,
            title TEXT,
            headline TEXT,
            profile_url TEXT,
            raw_text TEXT,
            fetched_at TEXT NOT NULL,
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )
        """
    )

    if not _column_exists(conn, "candidate_profiles", "name"):
        conn.execute("ALTER TABLE candidate_profiles ADD COLUMN name TEXT")
