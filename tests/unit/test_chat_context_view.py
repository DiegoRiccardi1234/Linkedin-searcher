"""The coach knows which page the question was asked from.

It answered every question from the same place before: a "why is this offer at
3?" typed on the archive page and a "which provider should I use?" typed in the
settings got the same career-coach framing, because the only thing the prompt
knew was how far along the job hunt was.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import Database
from app.services.chat.context import suggest_chat_prompts
from app.services.chat.handler import _clean_action
from app.services.chat.prompts import system_prompt


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "s.db")
    try:
        yield database
    finally:
        database.close()


def test_the_page_is_a_second_axis_not_a_replacement() -> None:
    """State says where the hunt is; view says what is on screen."""
    base = system_prompt("advising")
    with_view = system_prompt("advising", view="settings")
    assert base in with_view or "AI Career Coach" in with_view
    assert "settings" in with_view.lower()
    assert len(with_view) > len(base)


def test_an_unknown_page_adds_nothing() -> None:
    assert system_prompt("advising", view="nope") == system_prompt("advising")


@pytest.mark.parametrize(
    "view", ["dashboard", "job-search", "jobs", "mail", "profile", "settings"]
)
def test_every_page_has_something_to_say(view: str) -> None:
    prompt = system_prompt("advising", view=view)
    assert "WHERE THE USER IS" in prompt


def test_the_suggestions_follow_the_page(db: Database) -> None:
    settings = suggest_chat_prompts(db, lang="it", view="settings")
    mail = suggest_chat_prompts(db, lang="it", view="mail")
    assert settings and mail and settings[0] != mail[0]
    assert "provider" in settings[0].lower()
    assert "posta" in mail[0].lower()


def test_suggestions_still_work_with_no_page_and_no_cv(db: Database) -> None:
    prompts = suggest_chat_prompts(db, lang="en")
    assert len(prompts) >= 2


# --- what the coach is allowed to propose ------------------------------------


def test_a_known_action_survives() -> None:
    action = _clean_action({"type": "fill_scan_form", "keywords": ["python"]})
    assert action == {"type": "FILL_SCAN_FORM", "keywords": ["python"]}


def test_an_invented_action_is_dropped() -> None:
    assert _clean_action({"type": "DELETE_EVERYTHING"}) is None
    assert _clean_action("not even an object") is None
    assert _clean_action(None) is None


def test_writing_an_unlisted_profile_field_is_dropped() -> None:
    """The list is the contract: a confused model cannot invent a control."""
    assert _clean_action({"type": "SET_PROFILE_FIELD", "field": "markdown", "value": "x"}) is None
    ok = _clean_action({"type": "SET_PROFILE_FIELD", "field": "base_cities", "value": ["Bari"]})
    assert ok is not None and ok["field"] == "base_cities"
