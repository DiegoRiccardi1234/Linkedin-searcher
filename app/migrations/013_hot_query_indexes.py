"""Indexes for the queries that run on every page load.

Three gaps, all found by reading the query plans rather than by feeling slow —
at a few hundred rows nothing is slow, but these run constantly and the fix is
one statement each:

- ``chat_messages(session_id)``: every chat turn selects and counts by session.
- ``job_actions(job_id)``: the job list LEFT JOINs it, the timeline selects by
  it, and migration 011 sweeps orphans through it.
- ``jobs(punteggio_ai DESC, last_seen_at DESC)``: migration 010 created a
  composite starting with ``status``, but the default list call passes no status
  filter — with no equality predicate on the leading column SQLite cannot use it
  to satisfy the ORDER BY, so the app's most-executed query still did a full
  scan and sort. The status-first index stays (it serves the filtered calls).
"""

from __future__ import annotations

import sqlite3

VERSION = 13
DESCRIPTION = "indexes for chat history, job actions and the unfiltered job list"


def upgrade(conn: sqlite3.Connection) -> None:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    if "chat_messages" in tables:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(chat_messages)")}
        if "session_id" in cols:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_messages_session "
                "ON chat_messages(session_id, id DESC)"
            )

    if "job_actions" in tables:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(job_actions)")}
        if "job_id" in cols:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_job_actions_job ON job_actions(job_id)")

    if "jobs" in tables:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        if {"punteggio_ai", "last_seen_at"} <= cols:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_rank "
                "ON jobs(punteggio_ai DESC, last_seen_at DESC)"
            )
