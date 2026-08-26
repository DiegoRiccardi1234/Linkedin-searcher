"""Bring every dedup_key up to the mode the app is actually using.

``upsert_job`` hashes title+company+location under whichever ``dedup_mode`` the
preference holds, and stores the result. Change the preference and every key
already written was computed under the old rule, so a re-scraped posting stops
matching the row it belongs to — silently, with the archive growing a duplicate
instead of updating. Measured on a real archive: the same Bending Spoons role
present twice, one row keyed under ``title_company`` and one under ``city``,
title, company and location identical byte for byte.

Recomputes only. Rows that end up sharing a key are LEFT AS THEY ARE: merging
would mean deciding which score, which status and which application date lives
on, and that is a guess dressed as a fact — the mistake migration 021 already
made once on this archive. From here on, a new sighting merges into the first of
them, which is what was meant all along.
"""

from __future__ import annotations

import sqlite3

VERSION = 30
DESCRIPTION = "jobs.dedup_key ricalcolate secondo la dedup_mode corrente"

_MODES = ("exact", "city", "title_company")


def _mode(conn: sqlite3.Connection) -> str:
    """The mode in force, defaulting the way ``upsert_job`` defaults.

    Guarded on the table existing: a database old enough to be baselined can
    reach this migration before ``preferences`` has been created, and a rehash
    that crashes there takes the whole startup down with it.
    """
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "preferences" not in tables:
        return "city"
    row = conn.execute("SELECT value FROM preferences WHERE key = 'dedup_mode'").fetchone()
    mode = (row[0] if row else "") or "city"
    return mode if mode in _MODES else "city"


def upgrade(conn: sqlite3.Connection) -> None:
    # Every column the key is built from has to be there. A database old enough
    # to be baselined arrives with whatever shape it had — the legacy fixture has
    # a jobs table with no `sede` at all — and a rehash that assumes the current
    # schema takes startup down with it. Nothing to rehash is not an error.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if not {"dedup_key", "titolo", "azienda", "sede"} <= cols:
        return
    # Imported here, not at module scope: the runner loads every migration file
    # at import time and a top-level app import would make that a cycle.
    from app.db import make_dedup_key

    mode = _mode(conn)
    for job_id, titolo, azienda, sede, current in conn.execute(
        "SELECT id, titolo, azienda, sede, dedup_key FROM jobs"
    ).fetchall():
        # A key built from an empty title identifies nothing: on a real archive
        # it collapsed five separate applications to one agency into a single
        # identity. Those rows come from the mailbox importer, which does not
        # use dedup at all, so they are left exactly as they are.
        if not (titolo or "").strip():
            continue
        fresh = make_dedup_key(titolo or "", azienda or "", sede or "", mode)
        if fresh != current:
            conn.execute("UPDATE jobs SET dedup_key = ? WHERE id = ?", (fresh, job_id))
    # No conn.commit(): the runner owns the transaction.
