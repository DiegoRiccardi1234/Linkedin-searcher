"""Chat state machine + preference extraction from free-form messages."""

from __future__ import annotations

import re

from app.db import Database


def extract_pref_updates(message: str) -> dict[str, str]:
    """Parse preference updates (remote mode, min salary, role interests) from a user message."""
    updates: dict[str, str] = {}
    lower = message.lower()

    if "full remote" in lower or "fully remote" in lower or "solo remote" in lower:
        updates["remote_mode"] = "full_remote"
    elif "hybrid" in lower or "ibrido" in lower:
        updates["remote_mode"] = "hybrid"
    elif "on-site" in lower or "onsite" in lower or "in office" in lower:
        updates["remote_mode"] = "onsite"

    ral_match = re.search(r"(?:min|minimum|salary|ral)\s*(?:of\s*)?(\d{2,6})", lower)
    if ral_match:
        value = int(ral_match.group(1))
        if value < 1000:
            value = value * 1000
        updates["min_ral"] = str(value)

    if "qa" in lower and "no" not in lower:
        updates["prefer_role_qa"] = "1"
    if "cyber" in lower and "no" not in lower:
        updates["prefer_role_cyber"] = "1"
    if "data" in lower and ("analyst" in lower or "analy" in lower):
        updates["prefer_role_data"] = "1"

    return updates


def get_chat_state(db: Database) -> str:
    """Return one of: no_cv, onboarding, ready_to_search, advising."""
    profile = db.get_active_candidate_profile()
    jobs = db.get_top_jobs(limit=1)
    prefs_set = bool(db.get_preference("remote_mode", "") or db.get_preference("min_ral", ""))

    if not profile:
        return "no_cv"
    if not prefs_set:
        return "onboarding"
    if not jobs:
        return "ready_to_search"
    return "advising"


SUPPORTED_UI_LANGS = ("en", "it", "es", "fr", "de")


def get_ui_language(db: Database) -> str:
    """Return the active UI language code (en/it/es/fr/de). Defaults to en."""
    raw = (db.get_preference("ui_language", "en") or "en").lower()
    for code in SUPPORTED_UI_LANGS:
        if raw.startswith(code):
            return code
    return "en"


def is_italian_ui(db: Database) -> bool:
    """Backwards-compatible helper."""
    return get_ui_language(db) == "it"
