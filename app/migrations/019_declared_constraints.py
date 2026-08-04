"""Apply the user's declared constraints to the offers already in the archive.

Until now the app checked only two things deterministically: a location outside
the EU, and a degree grade below the posting's threshold. Everything else — how
many years the posting demands, which degree, and whether the user can even get
to the office — was asked of the model in prose and routinely ignored. Measured
on a real archive (04/08/2026), of 35 offers scored 8 or more:

* eight demanded 1-2 years while the profile declared none, and the demand was
  sitting right there in the analysis' own ``anni_esperienza_richiesti`` field;
* one demanded a master's against a bachelor and scored 8;
* on-site roles in other cities were recommended, because the posting's location
  never reached the model in the first place.

The scan self-heals such jobs only when the same posting is scraped again, which
for an expired ad never happens. So the archive is corrected once, here.

Two passes, neither of which calls a model:

1. **Work mode.** ``_detect_work_mode`` used to read "possibilità di lavorare da
   remoto (fino a 2 giornate su 5)" and "soluzioni ibride di smart working" as
   Full Remote. The label is recomputed from the stored description — but only
   when the text says something definite, so rows whose mode came from the job
   board's flag (which is not stored) are left alone rather than downgraded to
   "Non specificato".
2. **Constraints.** Offers breaking a declared constraint are capped to 3 and
   flagged, exactly as the live path now does. Rows whose facts are unknown are
   untouched: an unreadable CV must never hide jobs.

Idempotent: re-running recomputes the same labels and re-adds the same flags.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

VERSION = 19
DESCRIPTION = "hide offers that break what the user declared (years, degree, location)"

_CAP = 3
_WEAKNESS = {
    "esperienza_richiesta": "Chiede più anni di esperienza di quelli dichiarati nel profilo.",
    "titolo_superiore": "Chiede un titolo di studio superiore a quello del profilo.",
    "sede_non_raggiungibile": "Sede e modalità fuori da quelle accettate.",
}


def _facts_from(conn: sqlite3.Connection) -> Any:
    """Build the user's facts straight from the tables this migration can see.

    ``candidate_facts`` wants a ``Database``; here there is only a connection,
    so the same preferences and profile are read by hand and handed to the very
    same dataclasses. Importing the service keeps one definition of "does this
    offer break a constraint" instead of a copy that drifts.
    """
    from app.services.candidate_facts import CandidateFacts, WorkRule, parse_work_rule

    prefs = dict(conn.execute("SELECT key, value FROM preferences").fetchall())
    row = conn.execute(
        "SELECT markdown, summary_json FROM candidate_profiles "
        "WHERE id = COALESCE((SELECT value FROM preferences WHERE key='active_profile_id'), id) "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    markdown = str(row[0] or "") if row else ""
    try:
        summary = json.loads(row[1]) if row and row[1] else {}
    except (TypeError, ValueError):
        summary = {}
    if not isinstance(summary, dict):
        summary = {}

    from app.services.candidate_facts import candidate_education_level

    def _int(raw: Any) -> int | None:
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return None

    years = _int(prefs.get("profile_fact_years_experience")) or _int(
        summary.get("years_experience")
    )
    if years is None:
        years = _int(summary.get("years_experience"))
    education = (prefs.get("profile_fact_education_level") or "").strip() or (
        candidate_education_level(markdown, summary)
    )
    try:
        locations = json.loads(prefs.get("last_scan_locations") or "[]")
    except (TypeError, ValueError):
        locations = []
    manual_cities = [c.strip() for c in (prefs.get("profile_fact_base_cities") or "").split(",")]
    manual_cities = [c for c in manual_cities if c]
    rule = parse_work_rule(prefs.get("onboarding_work_mode") or "", locations)
    if manual_cities:
        rule = WorkRule(
            cities=tuple(c.lower() for c in manual_cities),
            allow_onsite=rule.allow_onsite,
            allow_hybrid=rule.allow_hybrid,
            allow_remote=rule.allow_remote,
        )
    return CandidateFacts(years_experience=years, education_level=education, work_rule=rule)


def upgrade(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if not {"punteggio_ai", "consiglio", "analysis_json", "modalita", "descrizione"} <= cols:
        return

    from app.services.candidate_facts import blocking_reasons
    from app.services.scan.rows import _detect_work_mode

    facts = _facts_from(conn)

    rows = conn.execute(
        "SELECT id, descrizione, sede, modalita, analysis_json, punteggio_ai FROM jobs"
    ).fetchall()

    for job_id, descrizione, sede, modalita, raw, punteggio in rows:
        text = str(descrizione or "")
        # Pass 1: only overrule the stored mode when the text is explicit.
        detected = _detect_work_mode({}, text, "") if text else ""
        mode = detected or str(modalita or "")
        if detected and detected != modalita:
            conn.execute("UPDATE jobs SET modalita = ? WHERE id = ?", (detected, job_id))

        # Pass 2: the declared constraints.
        breaks = blocking_reasons(text, str(sede or ""), mode, facts)
        if not breaks:
            continue
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue

        flags = data.get("blocchi")
        if not isinstance(flags, list):
            flags = []
        details = data.get("blocchi_dettaglio")
        if not isinstance(details, dict):
            details = {}
        missing = None
        skills = data.get("skills_match")
        if isinstance(skills, dict):
            missing = skills.get("mancano")
            if not isinstance(missing, list):
                missing = []
                skills["mancano"] = missing

        weakness_bits = []
        for code, reason in breaks:
            if code not in flags:
                flags.append(code)
            details[code] = reason
            if missing is not None and reason not in missing:
                missing.append(reason)
            weakness_bits.append(_WEAKNESS.get(code, reason))

        data["blocchi"] = flags
        data["blocchi_dettaglio"] = details
        # A capped offer HAS been judged, deterministically: it is not "unscored".
        if "non_valutato" in flags:
            flags.remove("non_valutato")
        current = punteggio if isinstance(punteggio, int) else 0
        new_score = min(current, _CAP) if current else _CAP
        data["punteggio"] = new_score
        data["consiglio"] = "Salta"
        previous = str(data.get("punti_deboli") or "").strip()
        joined = " ".join(weakness_bits)
        if joined not in previous:
            data["punti_deboli"] = f"{joined} {previous}".strip()

        conn.execute(
            "UPDATE jobs SET analysis_json = ?, punteggio_ai = ?, consiglio = 'Salta' WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), new_score, job_id),
        )
