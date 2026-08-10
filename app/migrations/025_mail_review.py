"""The queue of messages waiting for a human, kept where a restart cannot reach.

It used to be a Python list on the watcher. On 7 August a 90-day sweep of a real
mailbox put **110 proposals** in it, the app was restarted, and every one of them
was gone — with no trace that they had ever existed. Worse than losing them: the
card then read "recovery done" and "0 to review", which says "I looked and there
was nothing", and the user believed it.

**Why a table of its own, and not columns on ``mail_seen``.** That table has
``UNIQUE(account, mail_key)``, one row per message, and an ambiguous proposal
points at several offers. It also means something different: a row in
``mail_seen`` is a closed question, and ``filter_unseen_mail`` uses it to never
fetch that message again. A pending question and a closed one do not belong in
the same table under the same index.

**The promise, restated rather than weakened.** No subject, no body, no
recipient, no sender address. What is kept is the EMPLOYER NAME read out of the
subject — the same class of fact as ``jobs.azienda``, which the archive already
holds for hundreds of rows, and precisely the thing this feature exists to
record: "on the 6th you applied to Reply". Storing that is the feature. Storing
"Diego, la tua candidatura è stata inviata a Reply" would be keeping a copy of
someone's mail, and is not done.

``sender`` is the registrable domain **only when it is one of the 25 in
``_KNOWN_SENDER_DOMAINS``** — linkedin.com, greenhouse.io, and so on — and the
empty string otherwise. So it can say where a confirmation came from and can
never be a person's address, and the claim is checkable by reading a list in the
source. A test walks every text column of every table looking for a sentinel
subject, and fails if it finds one.

``candidates_json`` is a plain array of job ids, like ``sources_json``: resolved
against ``jobs`` when the queue is read, so an offer the user deletes drops out
of its proposal instead of leaving a dead row behind. That happens: this archive
has had 159 offers deleted by hand.
"""

from __future__ import annotations

import sqlite3

VERSION = 25
DESCRIPTION = "mail_review: proposals waiting for a human, surviving a restart"


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mail_review (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account TEXT NOT NULL,
            mail_key TEXT NOT NULL,
            message_id TEXT,
            received_at TEXT,
            kind TEXT NOT NULL,
            company TEXT,
            sender TEXT,
            rule TEXT,
            candidates_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_review_key ON mail_review(account, mail_key)"
    )
