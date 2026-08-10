"""The job title, once it has been read out of a confirmation body.

Kept on the queue row rather than fetched again at display time: reading it
means opening the mailbox, and a list that reconnects to render is a list that
goes blank when the network does.

It belongs to the same class of data as ``company`` — a fact extracted from the
message, not the message. "AI Developer" is what ``jobs.titolo`` holds for every
other row in the archive.
"""

from __future__ import annotations

import sqlite3

VERSION = 26
DESCRIPTION = "mail_review.role: the job title read from a confirmation body"


def upgrade(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(mail_review)")}
    if "role" not in columns:
        conn.execute("ALTER TABLE mail_review ADD COLUMN role TEXT")
