"""Telling the user a reminder came due, instead of waiting to be looked at.

Reminders worked: you could set one, and the Dashboard listed it with an
"overdue" badge. That is the whole of it — nothing ever announced one. A reminder
you have to remember to go and read is a note, not a reminder, and the point of
setting one is precisely that you will be thinking about something else.

The channel already exists and is already used: ``app.notify`` bridges the
system tray, and the auto-scan scheduler fires through it when a scan finds
something. This rides the same once-a-minute tick, for the same reason that one
does — a second thread to check a SQLite table once a minute would be a thread
that spends its life asleep.

Two rules keep it from becoming noise:

* **Each reminder is announced once.** The job id and its due date go into a
  preference, so changing the date announces it again and leaving it alone does
  not. Without that, "overdue" is true forever and the tray fires every minute.
* **It is opt-in**, like the auto-scan notification next to it. Someone who does
  not want their afternoon interrupted turns it off and still keeps the list.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.log import get_logger
from app.notify import notify

if TYPE_CHECKING:
    from app.db import Database

log = get_logger(__name__)

#: Opt-in, and off by default: a notification is an interruption.
PREF_ENABLED = "feature_reminder_notify"
#: What has already been announced, as ``job_id:due_at`` — the date is in the key
#: so that moving a reminder makes it a new thing to announce.
PREF_ANNOUNCED = "reminder_announced"
#: How long an application sits untouched before it counts as stale. Read by
#: ``list_reminders`` and, until now, settable nowhere at all.
PREF_STALE_DAYS = "reminder_stale_days"
DEFAULT_STALE_DAYS = 7

#: A tray balloon is not a list. More than this and it stops being read.
MAX_PER_TICK = 3


def stale_days(db: Database) -> int:
    try:
        value = int(str(db.get_preference(PREF_STALE_DAYS, str(DEFAULT_STALE_DAYS))))
    except (TypeError, ValueError):
        value = DEFAULT_STALE_DAYS
    return max(1, min(90, value))


def _announced(db: Database) -> set[str]:
    try:
        raw = json.loads(str(db.get_preference(PREF_ANNOUNCED, "[]") or "[]"))
    except (TypeError, ValueError):
        return set()
    return {str(x) for x in raw} if isinstance(raw, list) else set()


def check_due(db: Database) -> int:
    """Announce reminders that came due since the last look. Returns how many.

    Cheap when there is nothing to do, which is almost always: one indexed read
    of a table that holds a handful of rows.
    """
    if str(db.get_preference(PREF_ENABLED, "0") or "0") != "1":
        return 0
    try:
        due = [
            item
            for item in db.list_reminders(stale_days(db)).get("reminders", [])
            if item.get("overdue")
        ]
    except Exception as exc:  # a notifier must never take the scheduler down
        log.debug("reminder check failed: %s", exc)
        return 0
    if not due:
        return 0

    seen = _announced(db)
    fresh: list[dict[str, Any]] = [
        item for item in due if f"{item['job_id']}:{item.get('due_at', '')}" not in seen
    ]
    if not fresh:
        return 0

    for item in fresh[:MAX_PER_TICK]:
        title = str(item.get("titolo") or "").strip() or str(item.get("azienda") or "")
        note = str(item.get("note") or "").strip()
        notify("Job Finder", f"{title}{f' — {note}' if note else ''}")
    if len(fresh) > MAX_PER_TICK:
        notify("Job Finder", f"+{len(fresh) - MAX_PER_TICK}")

    # Everything due is marked announced, including what the ceiling skipped:
    # the skipped ones are on the Dashboard, and re-announcing them next minute
    # is the noise this guard exists to prevent.
    seen |= {f"{item['job_id']}:{item.get('due_at', '')}" for item in fresh}
    # Bounded, so a year of reminders does not grow a preference without limit.
    db.set_preference(PREF_ANNOUNCED, json.dumps(sorted(seen)[-500:]))
    return len(fresh)
