"""Explicit version column for a job's stored AI analysis.

"Has this job already been scored?" used to be answered by looking for a marker
key inside the JSON blob (``analysis_json LIKE '%eleggibilita_geografica%'``).
That marker is injected by the normaliser into EVERY analysis, heuristic
fallbacks included, so a job scored by pure keyword matching counted as analysed
and was frozen at that score forever. Bumping the marker also meant editing a
LIKE pattern, which had already been done twice.

A real column ends the guessing: NULL means "no trustworthy analysis" (never
scored, or scored by the local heuristic), an integer means "scored by a model
against schema version N". Existing rows get NULL on purpose — every analysis
written before this migration predates the versioned schema and is re-scored
once, the next time its job appears in a scan.

See :mod:`app.scoring_schema`.
"""

from __future__ import annotations

import sqlite3

VERSION = 12
DESCRIPTION = "jobs.analysis_v — schema version of the stored AI analysis"


def upgrade(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "analysis_v" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN analysis_v INTEGER")
