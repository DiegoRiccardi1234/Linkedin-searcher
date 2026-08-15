"""Record which model produced a verdict, so two verdicts can be compared.

The archive holds scores written by a dozen different models across a year of
failover — a 26B that reads the whole description, a 120B that truncates, a local
12B, whatever the free tier had that afternoon — and every one of them looks
identical in the UI: a number out of ten. Comparing them, or deciding whether a
low score is the offer or the scorer, was guesswork.

The column is only filled from here on. Backfilling it would mean attributing old
rows to a model by looking at what ``usage_log`` was doing around that timestamp,
which is a guess dressed as a fact — and a wrong attribution is worse than an
honest blank. Old rows stay NULL and the UI says nothing about them.
"""

from __future__ import annotations

import sqlite3

VERSION = 29
DESCRIPTION = "jobs.analysis_model: quale modello ha scritto il voto"


def upgrade(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "analysis_model" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN analysis_model TEXT")
