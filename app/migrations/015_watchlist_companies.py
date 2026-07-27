"""Employers the user follows on purpose, scanned by name.

The scan only ever knew keywords. The employers that actually hire for this
profile — RWS/Welo for AI-data work in Italian, a handful of Turin companies
that take juniors, the task platforms — publish under job titles the keyword
list does not contain, so a generic scan never surfaced them however often they
posted. Following a company is the cheap fix: one extra search per name.

``canonical`` is the name with case, punctuation and the legal suffix stripped
("RWS Group" -> "rws"), so the rows a search returns can be matched back against
what the user actually follows. It is stored rather than computed at query time
because the match runs per scraped row, inside the scan loop.

``last_seen_at`` records the last time a scan matched a posting from this
company: an entry that never matches is a dead channel, and the user should be
able to tell that apart from one that simply has nothing open right now.
"""

from __future__ import annotations

import sqlite3

VERSION = 15
DESCRIPTION = "watchlist_companies — employers followed by name, scanned explicitly"


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist_companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            canonical TEXT NOT NULL UNIQUE,
            note TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            added_at TEXT,
            last_seen_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_active ON watchlist_companies(active)")
