"""Remember that a posting was opened, so a confirmation can be matched to it.

Applying happens outside this app: the user opens the posting and finishes on
LinkedIn or on the company's own form. Nothing came back, so the archive had to
be told by hand — and it never was. ``applied_at`` was empty on all 21 shortlisted
offers, which means the archive could no longer answer "what have I already
sent".

Knowing WHEN a posting was opened turns "search my whole mailbox for anything
that looks like a confirmation" into "check the four companies I opened this
week", which is the difference between a plausible guess and a fact.

Columns rather than a new ``status``: the funnel's states are choices the user
makes, and a click is not one — a posting is often opened just to read it.
Adding a fifth state would also break the four kanban columns and every filter.
Same reasoning as migration 016: a job IS the application.

``link_opened_at`` holds the FIRST unresolved open, not the latest. Re-reading a
posting a week later would otherwise push the window past the confirmation that
already arrived. It is cleared when the offer stops being pending, whether the
app matched a message or the user answered for it.
"""

from __future__ import annotations

import sqlite3

VERSION = 22
DESCRIPTION = (
    "jobs.link_opened_at / link_open_count / apply_confirmed_by / apply_confirm_message_id"
)

_COLUMNS = {
    "link_opened_at": "TEXT",
    "link_open_count": "INTEGER DEFAULT 0",
    # NULL when a human said so, 'email' when a confirmation message did. Every
    # automatic marking has to stay listable and reversible.
    "apply_confirmed_by": "TEXT",
    # The Message-ID that triggered it: opaque, and the only way to explain an
    # automatic marking after the fact without keeping the message itself.
    "apply_confirm_message_id": "TEXT",
}


def upgrade(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    for name, decl in _COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {decl}")

    # Partial: the pending set is a handful of rows out of thousands, and this is
    # the query the mail check runs on every tick.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_link_opened ON jobs(link_opened_at) "
        "WHERE link_opened_at IS NOT NULL"
    )
