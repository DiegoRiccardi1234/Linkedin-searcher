"""Re-file the two negotiable constraints as ceilings instead of hard blocks.

Years of experience and degree level used to cap an offer to 3 and hide it
behind the "applicable only" filter, exactly like a location the candidate
cannot reach. That was too much for requirements a junior is routinely told to
apply for anyway, and too little for the postings the detectors missed: EY's
"Junior Consultant Technology Risk" asked for a master's and sat at 9/10 with no
flag at all, because a "Fortemente gradita" in the *next* bullet was read as
being about the degree.

Both sides of that are stored data, and neither fixes itself:

* an offer whose only blockers were the two weighted ones is no longer capped at
  3 — but the number under that cap was never recorded, so it is NOT invented
  here. The offer is marked unevaluated, which puts it in front of the re-score
  path and into the "unscored" bulk scope. Same reasoning as migration 020;
* an offer that breaks a weighted constraint the corrected rules now see keeps
  its score, lowered to the ceiling. Nothing is invented in this direction
  either: a real model score is being lowered, which is all a ceiling ever does.

Idempotent: a second run recomputes the same constraints, finds the flags already
present and the scores already under the ceiling, and writes nothing.
"""

from __future__ import annotations

import json
import sqlite3

VERSION = 21
DESCRIPTION = "experience and degree lower a ceiling instead of hiding the offer"

_AXES = (
    "skills_match",
    "seniority_match",
    "remote_match",
    "salary_match",
    "contract_match",
)


def upgrade(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if not {"punteggio_ai", "consiglio", "analysis_json", "modalita", "descrizione"} <= cols:
        return

    from app.services.candidate_facts import blocking_reasons
    from app.services.scan.hard_requirements import (
        _CONSTRAINT_WEAKNESS,
        _DECLARED_CONSTRAINT_CAP,
        _WEIGHTED_CEILING,
        BLOCKING_FLAGS,
        WEIGHTED_FLAGS,
    )

    from ._shared import facts_from_connection

    facts = facts_from_connection(conn)
    if facts.years_experience is None and facts.education_level is None:
        return  # nothing known about the candidate: nothing to re-judge

    rows = conn.execute(
        "SELECT id, titolo, descrizione, sede, modalita, analysis_json, punteggio_ai FROM jobs "
        "WHERE analysis_json IS NOT NULL AND analysis_json != ''"
    ).fetchall()

    for job_id, titolo, descrizione, sede, modalita, raw, punteggio in rows:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        stored = [str(c) for c in (data.get("blocchi") or []) if isinstance(c, str)]

        # The live path scores title and description together, so the checks must
        # read the same text or they will disagree with the next scan.
        text = f"{titolo or ''} {descrizione or ''}"
        breaks = blocking_reasons(text, str(sede or ""), str(modalita or ""), facts)
        current_weighted = [(c, r) for c, r in breaks if c in WEIGHTED_FLAGS]
        hard_now = {c for c, _ in breaks if c in BLOCKING_FLAGS}
        hard_stored = set(stored) & BLOCKING_FLAGS

        had_weighted = bool(set(stored) & WEIGHTED_FLAGS)
        if not had_weighted and not current_weighted:
            continue
        if hard_now or hard_stored:
            # Still out of reach for a reason that is not negotiable: the cap at
            # 3 stands and means what it says. Nothing to re-file.
            continue

        # Flags a still-valid constraint keeps its position: a second run must
        # produce a byte-identical row, and reshuffling the list is a change.
        live = {code for code, _ in current_weighted}
        flags = [c for c in stored if c not in WEIGHTED_FLAGS or c in live]
        details = data.get("blocchi_dettaglio")
        details = details if isinstance(details, dict) else {}
        dropped = {details.pop(c, None) for c in (set(stored) & WEIGHTED_FLAGS) - live}
        skills = data.get("skills_match")
        if isinstance(skills, dict) and isinstance(skills.get("mancano"), list):
            skills["mancano"] = [m for m in skills["mancano"] if m not in dropped]

        for code, reason in current_weighted:
            if code not in flags:
                flags.append(code)
            details[code] = reason
            missing = skills.get("mancano") if isinstance(skills, dict) else None
            if isinstance(missing, list) and reason not in missing:
                missing.append(reason)
        data["blocchi"] = flags
        data["blocchi_dettaglio"] = details

        was_capped = (
            had_weighted and punteggio is not None and punteggio <= _DECLARED_CONSTRAINT_CAP
        )
        if was_capped:
            # That 3 was the cap talking. What the offer is actually worth was
            # never written down, and guessing it here is the thing this app
            # stopped doing in 1.7.9.
            data["punteggio"] = None
            data["consiglio"] = ""
            data["match_axes"] = dict.fromkeys(_AXES)
            data["fonte_analisi"] = "non_valutata"
            data["motivo_non_valutazione"] = "vincolo ora ponderato: da rivalutare"
            data.pop("scoring_v", None)
            if "non_valutato" not in flags:
                flags.append("non_valutato")
            data["blocchi"] = flags
            conn.execute(
                "UPDATE jobs SET analysis_json = ?, punteggio_ai = NULL, consiglio = '', "
                "analysis_v = NULL WHERE id = ?",
                (json.dumps(data, ensure_ascii=False), job_id),
            )
            continue

        ceiling = max(
            _DECLARED_CONSTRAINT_CAP, _WEIGHTED_CEILING - max(0, len(current_weighted) - 1)
        )
        if punteggio is not None and punteggio > ceiling:
            data["punteggio"] = ceiling
            weakness = _CONSTRAINT_WEAKNESS.get(current_weighted[0][0], "")
            previous = str(data.get("punti_deboli") or "").strip()
            if weakness and not previous.startswith(weakness):
                data["punti_deboli"] = f"{weakness} {previous}".strip()
            conn.execute(
                "UPDATE jobs SET analysis_json = ?, punteggio_ai = ? WHERE id = ?",
                (json.dumps(data, ensure_ascii=False), ceiling, job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET analysis_json = ? WHERE id = ?",
                (json.dumps(data, ensure_ascii=False), job_id),
            )
