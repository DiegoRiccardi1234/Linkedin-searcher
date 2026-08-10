"""Unit tests for `app.services.chat.context` helpers.

Covers the new CV-derived chip suggester and the legacy keyword picker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import Database
from app.services.chat.context import (
    CHIP_TEMPLATES,
    build_profile_context,
    suggest_chat_prompts,
    suggest_keywords_from_profile,
    suggest_locations,
)


@pytest.fixture
def empty_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "ctx.db")
    yield db
    db.close()


def _save_cv(db: Database, markdown: str) -> None:
    profile_id = db.save_candidate_profile(source_name="cv.md", markdown=markdown, summary={})
    db.set_active_profile(profile_id)


def test_suggest_chat_prompts_no_cv_returns_onboarding(empty_db: Database) -> None:
    prompts = suggest_chat_prompts(empty_db, lang="it")
    assert len(prompts) == 3
    for p in prompts:
        assert p in CHIP_TEMPLATES["it"].values()


def test_suggest_chat_prompts_detects_python_and_data(empty_db: Database) -> None:
    _save_cv(empty_db, "Sviluppatore con esperienza in Python, SQL e data warehouse.")
    prompts = suggest_chat_prompts(empty_db, lang="en", limit=5)
    assert len(prompts) == 5
    assert CHIP_TEMPLATES["en"]["python_ai"] in prompts
    assert CHIP_TEMPLATES["en"]["data"] in prompts


def test_suggest_chat_prompts_unknown_lang_falls_back_to_en(empty_db: Database) -> None:
    _save_cv(empty_db, "Python developer")
    prompts = suggest_chat_prompts(empty_db, lang="jp", limit=4)
    # fallback returns english templates
    assert any(CHIP_TEMPLATES["en"]["python_ai"] == p for p in prompts)


def test_suggest_chat_prompts_limit_is_respected(empty_db: Database) -> None:
    _save_cv(
        empty_db,
        "Python Java JavaScript React SQL AI ML cybersecurity cloud AWS QA automation network Cisco",
    )
    prompts = suggest_chat_prompts(empty_db, lang="en", limit=3)
    assert len(prompts) == 3


def test_suggest_chat_prompts_deduplicates(empty_db: Database) -> None:
    _save_cv(empty_db, "Python Python python")
    prompts = suggest_chat_prompts(empty_db, lang="en", limit=6)
    assert len(prompts) == len(set(prompts))


def test_suggest_keywords_from_profile_defaults_without_cv(empty_db: Database) -> None:
    result = suggest_keywords_from_profile(empty_db, limit=4)
    # generic fallback returns a non-empty IT-friendly keyword list
    assert isinstance(result, list)
    assert len(result) > 0


def test_suggest_keywords_from_profile_uses_python_signal(empty_db: Database) -> None:
    _save_cv(empty_db, "Backend engineer with Python and SQL experience.")
    result = suggest_keywords_from_profile(empty_db, limit=4)
    joined = " ".join(result).lower()
    assert "python" in joined or "data" in joined


def test_suggest_locations_defaults_to_italy(empty_db: Database) -> None:
    assert suggest_locations(empty_db) == ["Italy"]


def test_suggest_locations_respects_full_remote(empty_db: Database) -> None:
    empty_db.set_preference("remote_mode", "full_remote")
    assert suggest_locations(empty_db) == ["Italy"]


def test_suggest_locations_uses_last_scan_location(empty_db: Database) -> None:
    empty_db.set_preference("last_scan_location", "Milano")
    assert suggest_locations(empty_db) == ["Milano"]


def test_the_chat_gets_the_pasted_linkedin_text_not_just_the_link(empty_db: Database) -> None:
    """A URL in a prompt is noise: the model cannot open it.

    The chat and the scan each built their own suffix appending the bare URL,
    while the profile text the user pasted — the only part a model can read —
    reached neither. Both now share ``onboarding.linkedin_suffix``.
    """
    _save_cv(empty_db, "Backend engineer.")
    empty_db.set_preference("linkedin_url", "https://www.linkedin.com/in/example")
    empty_db.set_preference("linkedin_profile_text", "Certificazione Anthropic Academy")

    context = build_profile_context(empty_db)
    assert "Certificazione Anthropic Academy" in context


def test_the_bare_url_was_useless_twice_over(empty_db: Database) -> None:
    """Without pasted text there is nothing to send, and the link is redacted anyway.

    Privacy Mode is on by default and strips URLs from the CV context, so the
    old "LinkedIn: <url>" line reached the model as the literal string ``[URL]``.
    Pinned here because it is the measurement that makes the fix worth having,
    not a behaviour worth restoring.
    """
    _save_cv(empty_db, "Backend engineer.")
    empty_db.set_preference("linkedin_url", "https://www.linkedin.com/in/example")
    context = build_profile_context(empty_db)
    assert "linkedin.com/in/example" not in context
    assert "[URL]" in context
