import json

from app.db import Database
from app.services.chat.intents import has_role_guidance_intent, has_search_intent
from app.services.chat.prompts import system_prompt
from app.services.chat.state import extract_pref_updates, get_chat_state
from app.services.chat_service import handle_chat_message


def test_extract_pref_updates_remote_and_salary() -> None:
    result = extract_pref_updates("I want full remote and min salary 35000")
    assert result["remote_mode"] == "full_remote"
    assert result["min_ral"] == "35000"


def test_extract_pref_updates_hybrid_lowercases_k_units() -> None:
    result = extract_pref_updates("I accept hybrid, RAL of 30")
    assert result["remote_mode"] == "hybrid"
    assert result["min_ral"] == "30000"


def test_pref_qa_not_blocked_by_incidental_no_substring() -> None:
    """'know' contains 'no' but must not block QA detection (the old
    `'no' not in lower` guard mis-fired on any word containing 'no')."""
    result = extract_pref_updates("you know, I'd like QA testing roles")
    assert result.get("prefer_role_qa") == "1"


def test_pref_qa_rejected_when_explicitly_negated() -> None:
    assert "prefer_role_qa" not in extract_pref_updates("no QA for me please")


def test_pref_data_requires_analytics_context() -> None:
    assert "prefer_role_data" not in extract_pref_updates("I push database updates daily")
    assert extract_pref_updates("interested in data analyst roles").get("prefer_role_data") == "1"


def test_search_intent_detection() -> None:
    assert has_search_intent("Please find Python jobs in Milan")
    assert has_search_intent("cerca lavori remoti")
    assert not has_search_intent("What is my CV summary?")


def test_role_guidance_intent_requires_all_hints() -> None:
    assert has_role_guidance_intent("Quali figure lavorative si adattano al mio CV?")
    assert not has_role_guidance_intent("Find me jobs")


def test_system_prompt_replies_in_user_message_language() -> None:
    """The bot must reply in the language of the user's message, not a forced UI
    language — so the same prompt is language-neutral for any ui_language."""
    prompt = system_prompt("onboarding", "it")
    assert "same language" in prompt.lower()
    assert "FILL_SCAN_FORM" in prompt
    assert "AI Career Coach" in prompt


def test_system_prompt_falls_back_on_unknown_state() -> None:
    prompt = system_prompt("unknown_state", "en")
    assert "AI Career Coach for IT professionals" in prompt  # advising default


def test_get_chat_state_no_cv(db: Database) -> None:
    assert get_chat_state(db) == "no_cv"


def test_handle_chat_message_uses_provider_json_envelope(tmp_path, fake_provider) -> None:
    fake_provider.chat_response = json.dumps({"answer": "hello Diego", "action": None})
    db = Database(tmp_path / "searcher.db")
    try:
        result = handle_chat_message(
            db=db,
            provider_manager=fake_provider,
            message="ciao",
            session_id="s1",
        )
    finally:
        db.close()

    assert result["answer"] == "hello Diego"
    assert result["action"] is None
    assert result["chat_state"] == "no_cv"
    assert len(fake_provider.chat_calls) == 1


def test_handle_chat_message_falls_back_when_provider_raises(tmp_path, fake_provider) -> None:
    fake_provider.raise_on_chat = True
    db = Database(tmp_path / "searcher.db")
    try:
        result = handle_chat_message(
            db=db,
            provider_manager=fake_provider,
            message="please recommend the best jobs",
            session_id="s2",
        )
    finally:
        db.close()

    assert "scan" in result["answer"].lower() or "no analyzed" in result["answer"].lower()
    assert result["chat_state"] == "no_cv"
    assert result["degraded"] is True  # fallback answer must be flagged as degraded


def test_handle_chat_message_not_degraded_on_success(tmp_path, fake_provider) -> None:
    fake_provider.chat_response = json.dumps({"answer": "real answer"})
    db = Database(tmp_path / "searcher.db")
    try:
        result = handle_chat_message(
            db=db, provider_manager=fake_provider, message="ciao", session_id="s2b"
        )
    finally:
        db.close()
    assert result["degraded"] is False


def test_handle_chat_message_includes_recent_history(tmp_path, fake_provider) -> None:
    """Prior turns must be sent to the model as their own messages, so the bot
    has real conversation context (not only a post-20-message summary)."""
    fake_provider.chat_response = json.dumps({"answer": "ok"})
    db = Database(tmp_path / "searcher.db")
    try:
        db.save_chat_message(session_id="h1", role="user", content="mi chiamo Diego")
        db.save_chat_message(session_id="h1", role="assistant", content="piacere Diego")
        handle_chat_message(
            db=db, provider_manager=fake_provider, message="come mi chiamo?", session_id="h1"
        )
    finally:
        db.close()

    msgs = fake_provider.chat_calls[0]["messages"]
    joined = "\n".join(m["content"] for m in msgs)
    assert "mi chiamo Diego" in joined
    assert "piacere Diego" in joined
    assert [m["role"] for m in msgs].count("assistant") >= 1


def test_handle_chat_message_uses_larger_max_tokens(tmp_path, fake_provider) -> None:
    db = Database(tmp_path / "searcher.db")
    try:
        handle_chat_message(db=db, provider_manager=fake_provider, message="ciao", session_id="t1")
    finally:
        db.close()
    assert fake_provider.chat_calls[0]["max_tokens"] >= 1400


def test_handle_chat_message_strips_markdown_fence(tmp_path, fake_provider) -> None:
    fake_provider.chat_response = '```json\n{"answer": "wrapped", "action": null}\n```'
    db = Database(tmp_path / "searcher.db")
    try:
        result = handle_chat_message(
            db=db,
            provider_manager=fake_provider,
            message="ciao",
            session_id="s3",
        )
    finally:
        db.close()

    assert result["answer"] == "wrapped"


def test_a_preference_mentioned_in_passing_is_not_written(tmp_path) -> None:
    """The detector still detects; nobody applies it behind the user's back."""
    from app.db import Database

    db = Database(tmp_path / "s.db")
    try:
        detected = extract_pref_updates("cerco solo full remote, minimo 25000")
        assert detected["remote_mode"] == "full_remote"
        # Nothing touched the database: extraction and persistence are now two
        # different events, and the second one needs a click.
        assert db.get_preference("remote_mode", "") == ""
        assert db.get_preference("min_ral", "") == ""
    finally:
        db.close()


def test_a_profile_filled_from_the_form_counts_as_onboarded(tmp_path) -> None:
    """The form writes onboarding_*, and the chat used to read only its own keys."""
    from app.db import Database

    db = Database(tmp_path / "s.db")
    try:
        db.save_candidate_profile(
            source_name="cv.md", markdown="# CV", summary={}, content_hash="h", name="X"
        )
        db.set_preference("onboarding_ral_min", "24000")
        assert get_chat_state(db) != "onboarding"
    finally:
        db.close()
