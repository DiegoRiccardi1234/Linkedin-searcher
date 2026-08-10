"""Helpers shared by migrations. Not a migration itself — the runner only picks
up modules whose filename starts with a version number, so this one is skipped.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

_AXES = (
    "skills_match",
    "seniority_match",
    "remote_match",
    "salary_match",
    "contract_match",
)


def reapply_weighted_constraints(
    conn: sqlite3.Connection, *, release_old_caps: bool = False
) -> None:
    """Re-run the deterministic constraint checks over the stored archive.

    Lives here because it is needed more than once. A stored flag is a snapshot
    of what a detector saw the day the offer was scored: correct the detector and
    the archive keeps the old answer forever. This pass reads the CURRENT rules
    and rewrites the flags and the ceiling — and only those. It never invents a
    score; the most it does is lower a real one to a ceiling.

    ``release_old_caps`` is the one-off part, and it must be asked for.
    Migration 021 turned two hard blocks into ceilings, so every offer sitting at
    3 *because of the cap* had to be freed — and since the number under that cap
    was never recorded, freeing it means marking the offer unevaluated rather
    than guessing. That step is only correct **once**, against an archive that
    still carries those caps. Run it again and it cannot tell a 3 that was a cap
    from a 3 the model actually gave: measured on the real archive, a second run
    would have wiped **40 legitimate verdicts** to "unevaluated". It also breaks
    idempotence, because the row it rewrites no longer looks the same on the pass
    after. So any later migration that just wants the flags refreshed leaves it
    off, and this function is then idempotent by construction: the same
    constraints, the flags already present, the scores already under the ceiling,
    and a byte-identical row.
    """
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
            release_old_caps
            and had_weighted
            and punteggio is not None
            and punteggio <= _DECLARED_CONSTRAINT_CAP
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


def facts_from_connection(conn: sqlite3.Connection) -> Any:
    """Build the user's facts straight from the tables a migration can see.

    ``candidate_facts`` wants a ``Database``; a migration only has a connection,
    so the same preferences and profile are read by hand and handed to the very
    same dataclasses. Importing the service keeps one definition of "does this
    offer break a constraint" instead of a copy that drifts — which matters more
    now that two migrations depend on the answer agreeing with the live path.
    """
    from app.services.candidate_facts import (
        CandidateFacts,
        WorkRule,
        candidate_education_level,
        parse_work_rule,
    )

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

    def _int(raw: Any) -> int | None:
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return None

    years = _int(prefs.get("profile_fact_years_experience"))
    if years is None:
        years = _int(summary.get("years_experience"))
    raw_protected = (prefs.get("profile_fact_protected_category") or "").strip().lower()
    protected: bool | None = None
    if raw_protected in ("1", "si", "sì", "yes", "true"):
        protected = True
    elif raw_protected in ("0", "no", "false"):
        protected = False
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
    return CandidateFacts(
        years_experience=years,
        education_level=education,
        protected_category=protected,
        work_rule=rule,
    )
