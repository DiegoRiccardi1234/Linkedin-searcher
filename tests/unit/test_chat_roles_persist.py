"""Suggested roles travel with the message, not only with the HTTP response.

The coach's role pills are what turn a suggestion into a search: they used to
live only in the reply payload, so reloading the page or switching session
dropped them and the same message came back as flat text.
"""

from __future__ import annotations

from pathlib import Path

from app.db import Database

_ROLES = [
    {"label": "AI QA Engineer", "keywords": ["AI QA", "LLM evaluation"]},
    {"label": "Prompt Engineer", "keywords": ["Prompt Engineer"]},
]


def test_meta_survives_the_round_trip(tmp_path: Path) -> None:
    db = Database(tmp_path / "c.db")
    try:
        db.save_chat_message(
            session_id="default",
            role="assistant",
            content="Ecco i ruoli",
            meta={"suggested_roles": _ROLES},
        )
        (message,) = db.list_chat_messages("default")
        assert message["content"] == "Ecco i ruoli"
        assert message["meta"]["suggested_roles"] == _ROLES
        # The raw column is an implementation detail, not part of the payload.
        assert "meta_json" not in message
    finally:
        db.close()


def test_messages_without_meta_read_back_as_empty(tmp_path: Path) -> None:
    db = Database(tmp_path / "c.db")
    try:
        db.save_chat_message(session_id="default", role="user", content="ciao")
        (message,) = db.list_chat_messages("default")
        assert message["meta"] == {}
    finally:
        db.close()


def test_corrupt_meta_does_not_break_history(tmp_path: Path) -> None:
    db = Database(tmp_path / "c.db")
    try:
        db.save_chat_message(session_id="default", role="assistant", content="x")
        db.conn.execute("UPDATE chat_messages SET meta_json = 'not json'")
        db.conn.commit()
        (message,) = db.list_chat_messages("default")
        assert message["meta"] == {}
    finally:
        db.close()
