"""Persistence helpers for the user's role shortlist.

The shortlist is stored as a JSON list of strings under the ``role_shortlist``
key in the ``preferences`` table. Entries are deduplicated case-insensitively
while preserving the casing of the first occurrence.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from app.db import Database

PREF_KEY = "role_shortlist"


def load(db: Database) -> list[str]:
    raw = db.get_preference(PREF_KEY, "") or ""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return _dedup_preserving_order(str(item) for item in data)


def save(db: Database, roles: list[str]) -> None:
    db.set_preference(PREF_KEY, json.dumps(roles, ensure_ascii=False))


def add(db: Database, roles: Iterable[str]) -> list[str]:
    current = load(db)
    existing = {r.lower() for r in current}
    for role in roles:
        clean = str(role).strip()
        if not clean or clean.lower() in existing:
            continue
        existing.add(clean.lower())
        current.append(clean)
    save(db, current)
    return current


def remove(db: Database, role: str) -> list[str]:
    target = role.strip().lower()
    remaining = [r for r in load(db) if r.lower() != target]
    save(db, remaining)
    return remaining


def _dedup_preserving_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        clean = v.strip()
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out
