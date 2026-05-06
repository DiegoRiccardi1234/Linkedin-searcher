"""Lightweight schema migrations for the SQLite database.

Each migration lives in its own file ``NNN_name.py`` inside this package and
exposes:

    VERSION: int             # monotonically increasing
    DESCRIPTION: str
    def upgrade(conn): ...

The tracker table ``schema_version(version INTEGER PRIMARY KEY, applied_at TEXT)``
is created on first run. If it's absent AND the ``jobs`` table already exists we
mark the DB as baselined at the highest known version (so pre-existing user DBs
don't attempt to re-run 001 etc.). Otherwise we run every pending migration in
order.

Design goals:
- Zero external deps.
- Idempotent (safe to call on every boot).
- Each migration runs inside a single transaction.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

_FILENAME_RE = re.compile(r"^(\d{3})_[a-z0-9_]+$")


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    upgrade: Callable[[sqlite3.Connection], None]


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _discover() -> list[Migration]:
    items: list[Migration] = []
    for info in pkgutil.iter_modules(__path__):
        match = _FILENAME_RE.match(info.name)
        if not match:
            continue
        mod = importlib.import_module(f"{__name__}.{info.name}")
        version = getattr(mod, "VERSION", None)
        description = getattr(mod, "DESCRIPTION", "")
        upgrade = getattr(mod, "upgrade", None)
        if version is None or upgrade is None:
            continue
        if int(version) != int(match.group(1)):
            raise RuntimeError(
                f"Migration file {info.name}.py declares VERSION={version} "
                f"but filename suggests {match.group(1)}"
            )
        items.append(Migration(version=int(version), description=str(description), upgrade=upgrade))
    items.sort(key=lambda m: m.version)
    return items


def _ensure_tracker_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    conn.commit()


def _current_version(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
    row = cur.fetchone()
    return int(row[0] if row else 0)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


#: Last migration version that shipped before the migration tracker was a hard
#: requirement. Any pre-existing DB without ``schema_version`` is assumed to
#: be at this version, so future migrations (>BASELINE_VERSION) still apply.
#: Bump this only after a full release cycle where every user is past it.
BASELINE_VERSION = 4


def apply_migrations(conn: sqlite3.Connection) -> int:
    """Apply pending migrations. Returns the version reached.

    Baseline detection: if ``schema_version`` is missing but the ``jobs`` table
    already exists (DB predates the migration tracker), seed the tracker at
    :data:`BASELINE_VERSION` and continue. Migrations newer than the baseline
    still run — every migration uses idempotent guards (``IF NOT EXISTS``,
    ``INSERT OR IGNORE``, ``ALTER`` with column-existence check) so re-running
    one against a partially-applied DB is safe.
    """
    migrations = _discover()

    tracker_exists = _has_table(conn, "schema_version")
    jobs_exists = _has_table(conn, "jobs")
    _ensure_tracker_table(conn)

    if not tracker_exists and jobs_exists:
        conn.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
            (BASELINE_VERSION, _iso_now()),
        )
        conn.commit()

    current = _current_version(conn)
    for migration in migrations:
        if migration.version <= current:
            continue
        migration.upgrade(conn)
        conn.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
            (migration.version, _iso_now()),
        )
        conn.commit()

    return _current_version(conn)
