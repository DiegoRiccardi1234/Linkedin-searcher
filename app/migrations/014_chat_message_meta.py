"""Room for the extras that travel with a chat message.

The coach's replies can carry suggested job roles, which the UI renders as
clickable pills. They lived only in the HTTP response, so reloading the page or
switching session silently dropped them: the same message came back as plain
text and the suggestions were gone.
"""

from __future__ import annotations

import sqlite3

VERSION = 14
DESCRIPTION = "chat_messages.meta_json — extras attached to a message (suggested roles)"


def upgrade(conn: sqlite3.Connection) -> None:
    # A baselined DB can be missing the table entirely (the migration runner
    # marks pre-tracker databases at the baseline version and runs everything
    # after it, whatever they actually contain).
    cols = {row[1] for row in conn.execute("PRAGMA table_info(chat_messages)")}
    if cols and "meta_json" not in cols:
        conn.execute("ALTER TABLE chat_messages ADD COLUMN meta_json TEXT")
