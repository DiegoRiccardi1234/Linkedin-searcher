"""Drop declared-constraint flags the rules no longer produce.

Migration 019 only ever ADDS: it flags offers that break a declared constraint
and caps them to 3. That is the right behaviour while the rules are right, but
the rules had three bugs, each of which hid offers the user could actually take
(measured on a real 238-offer archive, 05/08/2026):

* job boards escape markdown, so "almeno **1\\-2 anni**" reached the year
  detector as "2 anni" — the exact threshold that blocks — and "3\\-5 anni" as
  five. Twelve offers were held back by a range read at its upper bound;
* "sei mesi maturati **negli ultimi 2 anni**" is a time window, and it hid an
  apprenticeship, which is the single most accessible kind of posting there is;
* "una realtà con **oltre 30 anni** di esperienza" is the company's age, and it
  hid a posting whose title literally reads "JUNIOR CONSULTANT - Neolaureato/a".

Fixing the detectors is not enough on its own: the flags are stored, and 019
never removes one. This pass recomputes the three declared constraints and drops
whatever the current rules no longer justify.

An offer that comes out with no hard block left is NOT given a score back. Its 3
was the cap, and the number underneath it was never recorded — so inventing one
here would be exactly the guesswork the app stopped doing in v1.7.9. It is marked
unevaluated instead, which puts it in front of the re-score path and in the
"unscored" bulk scope.

Idempotent: a second run recomputes the same constraints and finds nothing stale.
"""

from __future__ import annotations

import json
import sqlite3

VERSION = 20
DESCRIPTION = "drop declared-constraint flags the corrected rules no longer produce"

#: The three this migration owns. ``geo_non_ue``/``voto_minimo`` come from other
#: checks and are none of its business.
_DECLARED = ("esperienza_richiesta", "titolo_superiore", "sede_non_raggiungibile")

#: A cap from any of these is still a real verdict, so the score stays.
_OTHER_BLOCKING = ("geo_non_ue", "voto_minimo")

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

    from ._shared import facts_from_connection

    facts = facts_from_connection(conn)
    if facts.years_experience is None and facts.education_level is None:
        return  # nothing known about the candidate: nothing to re-judge

    rows = conn.execute(
        "SELECT id, titolo, descrizione, sede, modalita, analysis_json FROM jobs "
        "WHERE analysis_json IS NOT NULL AND analysis_json != ''"
    ).fetchall()

    for job_id, titolo, descrizione, sede, modalita, raw in rows:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        flags = [str(code) for code in (data.get("blocchi") or []) if isinstance(code, str)]
        stored = [code for code in flags if code in _DECLARED]
        if not stored:
            continue

        # The live path scores title + description together, so the check must
        # read the same text or it will disagree with the next scan.
        text = f"{titolo or ''} {descrizione or ''}"
        current = {
            code for code, _ in blocking_reasons(text, str(sede or ""), str(modalita or ""), facts)
        }
        stale = [code for code in stored if code not in current]
        if not stale:
            continue

        flags = [code for code in flags if code not in stale]
        details = data.get("blocchi_dettaglio")
        removed_reasons = set()
        if isinstance(details, dict):
            for code in stale:
                reason = details.pop(code, None)
                if isinstance(reason, str):
                    removed_reasons.add(reason)
            data["blocchi_dettaglio"] = details
        # 019 also copied each reason into "mancano"; leaving it there would keep
        # showing the user a gap the app no longer believes in.
        skills = data.get("skills_match")
        if isinstance(skills, dict) and isinstance(skills.get("mancano"), list):
            skills["mancano"] = [m for m in skills["mancano"] if m not in removed_reasons]

        data["blocchi"] = flags
        if set(flags) & set(_OTHER_BLOCKING) or set(flags) & set(_DECLARED):
            # Still blocked by something: the cap stands, only the wrong reason went.
            conn.execute(
                "UPDATE jobs SET analysis_json = ? WHERE id = ?",
                (json.dumps(data, ensure_ascii=False), job_id),
            )
            continue

        data["punteggio"] = None
        data["consiglio"] = ""
        data["match_axes"] = dict.fromkeys(_AXES)
        data["fonte_analisi"] = "non_valutata"
        data["motivo_non_valutazione"] = "blocco rimosso: da rivalutare"
        data.pop("scoring_v", None)
        if "non_valutato" not in flags:
            flags.append("non_valutato")
        data["blocchi"] = flags
        conn.execute(
            "UPDATE jobs SET analysis_json = ?, punteggio_ai = NULL, consiglio = '', "
            "analysis_v = NULL WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), job_id),
        )
