"""Add ``content_hash`` column + index to ``candidate_profiles`` for upload dedup."""

from __future__ import annotations

import sqlite3

VERSION = 3
DESCRIPTION = "candidate_profiles.content_hash column + index"


def upgrade(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(candidate_profiles)").fetchall()}
    if "content_hash" not in cols:
        conn.execute("ALTER TABLE candidate_profiles ADD COLUMN content_hash TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidate_profiles_hash ON candidate_profiles(content_hash)"
    )
