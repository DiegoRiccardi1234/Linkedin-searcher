"""Which messages have already been looked at, and what was decided.

Without this the same fortnight of mail is re-examined every fifteen minutes,
and a message the user dismissed comes back the next tick.

A table rather than columns on ``jobs``, because most messages match no offer at
all: this is a stream of events against the archive, not an attribute of it.

**What it deliberately does not store.** No subject, no sender, no body, no
recipient. An identifier the mail server issued, a timestamp, a verdict, and the
name of the rule that produced it. That is enough to explain any automatic
marking after the fact — "matched by sender_domain on the 6th" — without keeping
a copy of the user's mail in a SQLite file.

``mail_key`` carries the transport in it (``imap:<uidvalidity>:<uid>`` or
``graph:<id>``): IMAP uids are only unique while UIDVALIDITY holds, and folding
the server's own reset signal into the key means a reset simply produces new
keys instead of silently re-using old verdicts.
"""

from __future__ import annotations

import sqlite3

VERSION = 23
DESCRIPTION = "mail_seen: messages already examined, with the verdict and the rule"


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mail_seen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account TEXT NOT NULL,
            mail_key TEXT NOT NULL,
            message_id TEXT,
            received_at TEXT,
            verdict TEXT NOT NULL,
            job_id INTEGER,
            matched_rule TEXT,
            seen_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_seen_key ON mail_seen(account, mail_key)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mail_seen_job ON mail_seen(job_id)")
