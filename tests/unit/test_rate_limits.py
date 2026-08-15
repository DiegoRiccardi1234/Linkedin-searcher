"""Asking at the rate the provider allows, and learning what that rate is.

The behaviour under test came from a measurement: a free tier of fifteen calls
a minute, asked about ninety times a minute by a scan scoring four offers in
parallel. It "worked" only because the failover caught the 429s.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.db import Database
from app.services import rate_limits as rl


@pytest.fixture(autouse=True)
def _clean_pacer():
    rl.reset_pacing()
    yield
    rl.reset_pacing()


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "s.db")
    try:
        yield database
    finally:
        database.close()


def _log(db: Database, provider: str, model: str, when: datetime, ok: bool, error: str = "") -> None:
    db.conn.execute(
        "INSERT INTO usage_log (ts, provider, model, endpoint, prompt_tokens, "
        "completion_tokens, total_tokens, success, error_type) "
        "VALUES (?, ?, ?, 'complete_json', 0, 0, 0, ?, ?)",
        (when.isoformat(), provider, model, 1 if ok else 0, error or None),
    )
    db.conn.commit()


# --- what the app ships knowing ---------------------------------------------


def test_the_most_specific_pattern_wins() -> None:
    """"flash-lite" and "flash" both match a lite model; only one is right."""
    lite = rl.default_limit("google", "gemini-3.5-flash-lite")
    heavy = rl.default_limit("google", "gemini-3.5-flash")
    assert lite is not None and heavy is not None
    assert lite.rpm == 15 and lite.rpd == 500
    # Twenty requests a day is enough to try a model, never enough to scan.
    assert heavy.rpd == 20


def test_an_unknown_provider_has_no_opinion() -> None:
    assert rl.default_limit("nonesuch", "whatever") is None


# --- what the app learns by being told "no" ---------------------------------


def test_a_429_teaches_the_ceiling(db: Database) -> None:
    now = datetime.now(UTC)
    for i in range(15):
        _log(db, "google", "m", now - timedelta(seconds=50 - i), ok=True)
    _log(db, "google", "m", now, ok=False, error="rate_limit")

    observed = rl.observed_limit(db, "google", "m")
    assert observed is not None and observed.rpm == 15
    assert observed.source == "misurato"


def test_failures_that_are_not_rate_limits_teach_nothing(db: Database) -> None:
    now = datetime.now(UTC)
    for i in range(5):
        _log(db, "google", "m", now - timedelta(seconds=30 - i), ok=True)
    _log(db, "google", "m", now, ok=False, error="truncated")
    assert rl.observed_limit(db, "google", "m") is None


def test_the_measured_ceiling_beats_the_shipped_one(db: Database) -> None:
    """The file is a starting point; the key in front of you is the fact."""
    now = datetime.now(UTC)
    for i in range(4):
        _log(db, "google", "gemini-3.5-flash-lite", now - timedelta(seconds=30 - i), ok=True)
    _log(db, "google", "gemini-3.5-flash-lite", now, ok=False, error="rate_limit")

    limit = rl.effective_limit(db, "google", "gemini-3.5-flash-lite")
    assert limit is not None and limit.rpm == 4  # not the shipped 15


def test_what_the_user_typed_wins_over_everything(db: Database) -> None:
    db.set_preference(
        rl.PREF_OVERRIDES, json.dumps({"google": {"gemini-3.5-flash-lite": {"rpm": 30, "rpd": 900}}})
    )
    limit = rl.effective_limit(db, "google", "gemini-3.5-flash-lite")
    assert limit is not None and limit.rpm == 30 and limit.source == "tuo"


# --- the pacer ---------------------------------------------------------------


def test_it_does_not_wait_while_there_is_room() -> None:
    waits: list[float] = []
    limit = rl.Limit(rpm=5)
    for _ in range(5):
        rl.pace("p", "m", limit, sleep=waits.append)
    assert waits == []


def test_it_waits_exactly_when_the_minute_is_full() -> None:
    waits: list[float] = []
    limit = rl.Limit(rpm=3)
    for _ in range(3):
        rl.pace("p", "m", limit, sleep=waits.append)
    rl.pace("p", "m", limit, sleep=waits.append)
    assert len(waits) == 1
    assert 55 <= waits[0] <= 61  # ~a minute after the first of the three


def test_a_model_with_no_known_limit_is_never_paced() -> None:
    waits: list[float] = []
    for _ in range(50):
        rl.pace("p", "m", None, sleep=waits.append)
    assert waits == []


def test_the_window_slides(monkeypatch) -> None:
    """Calls older than a minute stop counting, or the pacer would seize up."""
    clock = [1000.0]
    monkeypatch.setattr(rl.time, "time", lambda: clock[0])
    waits: list[float] = []
    limit = rl.Limit(rpm=2)
    rl.pace("p", "m", limit, sleep=waits.append)
    rl.pace("p", "m", limit, sleep=waits.append)
    clock[0] += 61
    rl.pace("p", "m", limit, sleep=waits.append)
    assert waits == []


# --- the daily allowance -----------------------------------------------------


def test_a_spent_daily_allowance_is_reported(db: Database) -> None:
    now = datetime.now(UTC)
    for i in range(20):
        _log(db, "google", "gemini-3.5-flash", now - timedelta(minutes=i), ok=True)
    assert rl.daily_exhausted(db, "google", "gemini-3.5-flash") is True
    # The lite line has five hundred a day: twenty is nothing.
    for i in range(20):
        _log(db, "google", "gemini-3.5-flash-lite", now - timedelta(minutes=i), ok=True)
    assert rl.daily_exhausted(db, "google", "gemini-3.5-flash-lite") is False


def test_yesterdays_calls_do_not_count(db: Database) -> None:
    yesterday = datetime.now(UTC) - timedelta(days=1, hours=2)
    for i in range(30):
        _log(db, "google", "gemini-3.5-flash", yesterday - timedelta(minutes=i), ok=True)
    assert rl.daily_exhausted(db, "google", "gemini-3.5-flash") is False


def test_pacing_a_real_scan_shape(monkeypatch) -> None:
    """A whole scan at fifteen a minute: thirty-four calls, about two minutes.

    The fake clock advances when the pacer sleeps, because that is what makes
    the next call cheap — a sleep that does not move time would make every
    wait look like a full minute.
    """
    clock = [1000.0]
    monkeypatch.setattr(rl.time, "time", lambda: clock[0])

    def sleep(seconds: float) -> None:
        clock[0] += seconds
        slept.append(seconds)

    slept: list[float] = []
    limit = rl.Limit(rpm=15)
    for _ in range(34):
        rl.pace("google", "gemini-3.5-flash-lite", limit, sleep=sleep)

    # The window slides, so the shape is fifteen calls, one wait, fifteen more:
    # two pauses for thirty-four calls and about two minutes in total. What
    # matters is the ceiling held — no call was ever the sixteenth in a minute.
    assert len(slept) == 2
    assert 110 <= clock[0] - 1000.0 <= 130


# --- when the shipped file is not there --------------------------------------


@pytest.fixture
def _no_cached_file():
    """``_shipped`` is lru_cached, so a test that swaps the file must clear it
    both ways or it leaks into every test that runs after."""
    rl._shipped.cache_clear()
    yield
    rl._shipped.cache_clear()


@pytest.mark.parametrize("body", ["", "{not json", "[]", '"a string"'])
def test_a_missing_or_broken_limits_file_never_raises(
    tmp_path: Path, monkeypatch, _no_cached_file, body: str
) -> None:
    """The file ships inside the bundle, so it CAN go missing: it lived outside
    git until this release. Losing it must cost the defaults, not the app."""
    broken = tmp_path / "provider_limits.json"
    if body:
        broken.write_text(body, encoding="utf-8")
    monkeypatch.setattr(rl, "_LIMITS_FILE", broken)

    assert rl._shipped() == {}
    assert rl.default_limit("google", "gemini-3.5-flash-lite") is None
    assert rl.effective_limit(None, "google", "gemini-3.5-flash-lite") is None
    # And the pacer stops pacing rather than stopping the scan.
    slept: list[float] = []
    for _ in range(50):
        rl.pace("google", "gemini-3.5-flash-lite", None, sleep=slept.append)
    assert slept == []


# --- merging two sources -----------------------------------------------------


def test_merging_keeps_the_stricter_number_field_by_field() -> None:
    shipped = rl.Limit(rpm=15, rpd=500, tpm=250_000)
    measured = rl.Limit(rpm=4, rpd=900, source="misurato")
    merged = shipped.merged_with(measured)
    assert (merged.rpm, merged.rpd, merged.tpm) == (4, 500, 250_000)
    # The source follows the rate that changed, so the panel can say where the
    # number the app obeys came from.
    assert merged.source == "misurato"


def test_merging_with_nothing_changes_nothing() -> None:
    shipped = rl.Limit(rpm=15, rpd=500, source="default")
    assert shipped.merged_with(None) is shipped
    # A measurement that only loosens is not an improvement: keep the stricter.
    looser = shipped.merged_with(rl.Limit(rpm=30, source="misurato"))
    assert (looser.rpm, looser.source) == (15, "default")


def test_a_daily_ceiling_is_learned_too(db: Database) -> None:
    """Only the per-minute ceiling was ever exercised. A 429 preceded by nine
    calls in the same DAY says the daily allowance is nine, not the shipped
    five hundred."""
    now = datetime.now(UTC)
    for i in range(9):
        _log(db, "google", "gemini-3.5-flash-lite", now - timedelta(hours=i + 1), ok=True)
    _log(db, "google", "gemini-3.5-flash-lite", now, ok=False, error="rate_limit")

    observed = rl.observed_limit(db, "google", "gemini-3.5-flash-lite")
    assert observed is not None
    assert observed.rpd == 9
    assert rl.effective_limit(db, "google", "gemini-3.5-flash-lite").rpd == 9


def test_it_learns_from_the_error_type_the_app_actually_writes(db: Database) -> None:
    """The regression this release fixed: usage_log used to hold the exception
    CLASS name, so a 429 arriving as an HTTPStatusError taught nothing. The
    factory now writes ``<cause>:<ClassName>`` — both readers match a substring.
    """
    now = datetime.now(UTC)
    for i in range(6):
        _log(db, "groq", "llama-3.3-70b", now - timedelta(seconds=50 - i), ok=True)
    _log(db, "groq", "llama-3.3-70b", now, ok=False, error="rate_limit:HTTPStatusError")

    observed = rl.observed_limit(db, "groq", "llama-3.3-70b")
    assert observed is not None and observed.rpm == 6


def test_resolve_reads_the_week_once(db: Database, monkeypatch) -> None:
    """The panel needs four numbers per model; reading them one by one walked
    a week of usage_log twice for each of forty models on every page load."""
    calls = {"n": 0}
    real = rl.observed_limit

    def counted(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(rl, "observed_limit", counted)
    default, observed, override, effective = rl.resolve(db, "google", "gemini-3.5-flash-lite")
    assert calls["n"] == 1
    assert default is not None and default.rpm == 15
    assert observed is None and override is None
    assert effective is not None and effective.rpm == 15
