"""What was actually sent: when, with which CV, and how it ended.

The funnel already tracked a *state* (open -> applied -> interviewing ->
rejected) and a timeline of events, which answers "where is this application
now" but not "what did I send, and when". Two facts were unrecoverable: which CV
version went out (the user keeps several profiles and swaps the active one), and
how a closed application actually ended — 'rejected' is one ending among several,
and "no answer for six weeks" is by far the most common one, yet it looked
identical to an application still in flight.

Columns rather than an ``applications`` table because the design here is that a
job IS the application: one posting, one candidacy. A second table would have to
be joined for every list query to answer the same questions.

``applied_at`` is a snapshot of the first "applied" event, denormalised from the
timeline so a query can sort and filter on it. ``outcome`` is deliberately
distinct from ``status``: an offer received and an offer accepted are both
``status='interviewing'`` as far as the funnel is concerned.
"""

from __future__ import annotations

import sqlite3

VERSION = 16
DESCRIPTION = "jobs.applied_at / applied_profile_id / outcome / outcome_at"

_COLUMNS = {
    "applied_at": "TEXT",
    "applied_profile_id": "INTEGER",
    "outcome": "TEXT",
    "outcome_at": "TEXT",
}


def upgrade(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    for name, decl in _COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {decl}")

    # The backfill below reads tables and columns that a pre-migration database
    # may not have yet (baseline detection runs this against a bare ``jobs``
    # table): adding the columns is the migration, filling them is a bonus.
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "job_actions" in tables:
        # Applications made before this migration recorded their "applied" event,
        # so the date is not actually lost — only the CV that went with it is
        # (nothing ever wrote that down).
        conn.execute(
            """
            UPDATE jobs SET applied_at = (
                SELECT MIN(created_at) FROM job_actions
                WHERE job_actions.job_id = jobs.id AND job_actions.action = 'applied'
            )
            WHERE applied_at IS NULL
            """
        )
    if {"status", "updated_at"} <= cols:
        conn.execute(
            "UPDATE jobs SET outcome = 'rejected', outcome_at = updated_at "
            "WHERE status = 'rejected' AND outcome IS NULL"
        )
