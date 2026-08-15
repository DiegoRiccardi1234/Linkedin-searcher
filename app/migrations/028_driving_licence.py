"""Apply the driving-licence block to offers already in the archive.

The note above ``BLOCKING_FLAGS`` has listed "no driving licence" among the
non-arguable constraints since it was written, and nothing ever checked one: the
barrier was assumed to be covered by the unreachable-office rule, which it is
not — a field role in your own city still needs the car. What it cost, measured:
Siemens' *Implementation Consultant PLM* ("Valid driving license and willingness
to travel within Italy") was recommended as a Tier-1 offer and applied to.

Not folded into :func:`reapply_weighted_constraints`, which deliberately skips
any row carrying a hard block — this pass exists to ADD one.

Idempotent by construction: a row that already carries the flag is skipped, so a
second run writes nothing. That matters here because ``_cap_score`` prepends to
``punti_deboli`` and would otherwise grow the sentence on every pass.

Silent unless the user has said they hold no licence: an unstated fact blocks
nothing, exactly like every other check in ``candidate_facts``.
"""

from __future__ import annotations

import json
import sqlite3

from app.migrations._shared import facts_from_connection

VERSION = 28
DESCRIPTION = "patente_richiesta: il blocco che era dichiarato e mai controllato"


def upgrade(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if not {"punteggio_ai", "consiglio", "analysis_json", "descrizione"} <= cols:
        return

    from app.services.candidate_facts import driving_licence_status
    from app.services.scan.hard_requirements import (
        _CONSTRAINT_WEAKNESS,
        _DECLARED_CONSTRAINT_CAP,
        FLAG_DRIVING_LICENCE,
        _add_flag,
        _add_missing,
        _cap_score,
    )

    facts = facts_from_connection(conn)
    if facts.driving_licence is not False:
        return  # nothing declared: nothing to apply

    rows = conn.execute(
        "SELECT id, titolo, descrizione, analysis_json, punteggio_ai FROM jobs "
        "WHERE analysis_json IS NOT NULL AND analysis_json != ''"
    ).fetchall()

    for job_id, titolo, descrizione, raw, _punteggio in rows:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        stored = [str(c) for c in (data.get("blocchi") or []) if isinstance(c, str)]
        if FLAG_DRIVING_LICENCE in stored:
            continue  # already applied — this is what makes the pass repeatable

        # Same text the live scoring path reads, or the two disagree at the next scan.
        reason = driving_licence_status(f"{titolo or ''} {descrizione or ''}", facts)[1]
        if not reason:
            continue

        _add_flag(data, FLAG_DRIVING_LICENCE, reason)
        _add_missing(data, reason)
        _cap_score(
            data,
            _DECLARED_CONSTRAINT_CAP,
            _CONSTRAINT_WEAKNESS.get(FLAG_DRIVING_LICENCE, reason),
        )
        conn.execute(
            "UPDATE jobs SET analysis_json = ?, punteggio_ai = ?, consiglio = ? WHERE id = ?",
            (
                json.dumps(data, ensure_ascii=False),
                data.get("punteggio"),
                str(data.get("consiglio") or ""),
                job_id,
            ),
        )
