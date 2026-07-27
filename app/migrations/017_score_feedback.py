"""The user's verdict on the AI's verdict.

Every score in this app is a model's opinion, and nothing ever measured whether
those opinions are any good. The only signals were indirect (a favourite, an
application) and none of them says "this 9 is wrong, it is a 4 — the role wants
five years".

One row per judgement, not a column on ``jobs``: bumping
``CURRENT_ANALYSIS_VERSION`` re-scores the whole archive, and the interesting
question afterwards is whether the new scores disagree with the user in the same
places the old ones did. Overwriting the previous judgement would destroy exactly
that comparison, so each row carries the ``analysis_v`` (and, when known, the
model) it was passed on.

``expected_score`` is optional on purpose: a thumbs-down costs one click, and
demanding a number for it would mean collecting far fewer of them. When it is
there, the gap between it and the model's score is a measurable error rather
than a sentiment.

The title and company are copied in rather than joined: a judged posting is an
evaluation case, and it has to survive the job being deleted (postings expire,
and the archive gets wiped). This is the one table in the schema deliberately
NOT cleared along with its job.
"""

from __future__ import annotations

import sqlite3

VERSION = 17
DESCRIPTION = "score_feedback — the user's up/down verdict on an AI score"


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS score_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            titolo TEXT DEFAULT '',
            azienda TEXT DEFAULT '',
            verdict TEXT NOT NULL,
            ai_score INTEGER,
            expected_score INTEGER,
            reason TEXT DEFAULT '',
            analysis_v INTEGER,
            model TEXT DEFAULT '',
            created_at TEXT,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_score_feedback_job ON score_feedback(job_id, id DESC)"
    )
