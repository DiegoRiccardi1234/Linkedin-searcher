"""Add ``content_type`` column to ``chat_messages`` to distinguish summaries."""

from __future__ import annotations

import sqlite3

VERSION = 2
DESCRIPTION = "chat_messages.content_type column"


def upgrade(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()}
    if "content_type" not in cols:
        conn.execute(
            "ALTER TABLE chat_messages ADD COLUMN content_type TEXT NOT NULL DEFAULT 'message'"
        )
