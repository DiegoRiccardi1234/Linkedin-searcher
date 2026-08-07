"""Helpers shared by migrations. Not a migration itself — the runner only picks
up modules whose filename starts with a version number, so this one is skipped.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


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
