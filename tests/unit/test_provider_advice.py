"""Which provider the app tells you to use.

The rules are worth pinning because they are opinions with consequences: a
provider that closed must never be suggested, a provider that trains on prompts
must be named as such, and "we do not know" must never be rendered as "safe".
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from app.db import Database
from app.services import provider_advice as pa


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "s.db")
    try:
        yield database
    finally:
        database.close()


def _log(db: Database, provider: str, model: str, ok: bool, ms: int = 1200) -> None:
    db.conn.execute(
        "INSERT INTO usage_log (ts, provider, model, endpoint, prompt_tokens, "
        "completion_tokens, total_tokens, success, error_type, duration_ms) "
        "VALUES (?, ?, ?, 'complete_json', 0, 0, 0, ?, ?, ?)",
        (
            datetime.now(UTC).isoformat(),
            provider,
            model,
            1 if ok else 0,
            None if ok else "rate_limit:RateLimitError",
            ms,
        ),
    )
    db.conn.commit()


def test_it_recommends_a_free_provider_when_nothing_is_configured(db: Database) -> None:
    out = pa.advise(db, keys_status={})
    assert out["recommended"] is not None
    assert out["recommended"]["free"] is True
    # And it says so, rather than pretending the app is ready to scan.
    assert {"code": "no_key_configured"} in out["warnings"]


def test_a_closed_free_tier_is_never_recommended(db: Database) -> None:
    """Cerebras closes on 2026-08-17. The day after, suggesting it would send
    someone to sign up for something that no longer exists."""
    after = date(2026, 8, 18)
    out = pa.advise(db, keys_status={"cerebras_configured": True}, today=after)
    names = [row["provider"] for row in [out["recommended"], *out["alternatives"]] if row]
    assert "cerebras" not in names
    assert {"provider": "cerebras", "code": "free_tier_closed", "on": "2026-08-17"} in out[
        "warnings"
    ]


def test_a_free_tier_about_to_close_is_flagged_and_demoted(db: Database) -> None:
    before = date(2026, 8, 16)
    out = pa.advise(db, keys_status={"cerebras_configured": True}, today=before)
    assert out["recommended"]["provider"] != "cerebras"
    assert any(w.get("code") == "free_tier_closing" for w in out["warnings"])


def test_measured_evidence_outweighs_the_shipped_facts(db: Database) -> None:
    """Groq ships with the best numbers of the free tiers. If it has been failing
    here for a fortnight, the app must stop suggesting it."""
    keys = {"groq_configured": True, "cloudflare_configured": True}
    for _ in range(12):
        _log(db, "groq", "llama-3.3-70b", ok=False)
        _log(db, "cloudflare", "@cf/meta/llama-3.3-70b-instruct-fp8-fast", ok=True, ms=1200)

    out = pa.advise(db, keys_status=keys, today=date(2026, 8, 16))
    assert out["recommended"]["provider"] == "cloudflare"
    assert any(reason.startswith("measured_success") for reason in out["recommended"]["why"])


def test_a_couple_of_calls_are_not_evidence(db: Database) -> None:
    """Two failures are a bad afternoon, not a verdict."""
    _log(db, "groq", "llama-3.3-70b", ok=False)
    _log(db, "groq", "llama-3.3-70b", ok=False)
    out = pa.advise(db, keys_status={"groq_configured": True}, today=date(2026, 8, 16))
    assert out["recommended"]["provider"] == "groq"
    assert not any(r.startswith("measured_success") for r in out["recommended"]["why"])


def test_training_on_prompts_is_said_out_loud(db: Database) -> None:
    """Google's free tier is fast, free and reads your CV. It can still be the
    recommendation — it cannot be a silent one."""
    for _ in range(20):
        _log(db, "google", "gemini-3.5-flash-lite", ok=True, ms=900)
    out = pa.advise(db, keys_status={"google_configured": True}, today=date(2026, 8, 16))
    assert out["recommended"]["provider"] == "google"
    assert out["recommended"]["trains"] == "yes"
    assert {"provider": "google", "code": "trains_on_prompts"} in out["warnings"]


def test_unknown_is_reported_as_unknown(db: Database) -> None:
    """The failure mode to avoid: an app that implies a CV is safe because a list
    on GitHub did not mention training."""
    out = pa.advise(db, keys_status={"groq_configured": True}, today=date(2026, 8, 16))
    assert out["recommended"]["trains"] == "unknown"
    assert {"provider": "groq", "code": "training_unverified"} in out["warnings"]
    assert out["recommended"]["terms"].startswith("http")


def test_a_local_endpoint_is_not_suggested_to_someone_without_one(db: Database) -> None:
    out = pa.advise(db, keys_status={}, today=date(2026, 8, 16))
    assert out["recommended"]["provider"] != "custom"

    configured = pa.advise(db, keys_status={"custom_configured": True}, today=date(2026, 8, 16))
    assert configured["recommended"]["provider"] == "custom"
    assert configured["recommended"]["trains"] == "no"


def test_a_rejected_key_is_reported(db: Database) -> None:
    out = pa.advise(
        db,
        keys_status={"groq_configured": True},
        available={"groq": False},
        today=date(2026, 8, 16),
    )
    assert {"provider": "groq", "code": "key_rejected"} in out["warnings"]


def test_advice_never_raises_on_a_broken_database() -> None:
    class Broken:
        @property
        def conn(self):
            raise RuntimeError("closed")

    out = pa.advise(Broken(), keys_status={})
    assert out["recommended"] is not None


def test_paid_providers_are_never_suggested_to_someone_without_a_key(db: Database) -> None:
    """"Get an OpenAI key" is not an answer to "which free provider do I use"."""
    out = pa.advise(db, keys_status={}, today=date(2026, 8, 16))
    names = [row["provider"] for row in [out["recommended"], *out["alternatives"]] if row]
    assert "openai" not in names and "anthropic" not in names


def test_a_paid_key_that_works_can_still_win(db: Database) -> None:
    """Being free is a big head start, not an exemption from working."""
    keys = {"openai_configured": True, "groq_configured": True}
    for _ in range(15):
        _log(db, "openai", "gpt-4.1-mini", ok=True, ms=900)
        _log(db, "groq", "llama-3.3-70b", ok=False)
    out = pa.advise(db, keys_status=keys, today=date(2026, 8, 16))
    assert out["recommended"]["provider"] == "openai"


def test_the_window_is_the_last_fortnight(db: Database) -> None:
    assert pa.EVIDENCE_DAYS == 14
    assert pa.advise(db, keys_status={})["evidence_days"] == 14
    old = datetime.now(UTC) - timedelta(days=40)
    db.conn.execute(
        "INSERT INTO usage_log (ts, provider, model, endpoint, prompt_tokens, "
        "completion_tokens, total_tokens, success, error_type, duration_ms) "
        "VALUES (?, 'groq', 'm', 'complete_json', 0, 0, 0, 0, 'rate_limit', 100)",
        (old.isoformat(),),
    )
    db.conn.commit()
    out = pa.advise(db, keys_status={"groq_configured": True}, today=date(2026, 8, 16))
    assert out["recommended"]["measured_calls"] == 0
