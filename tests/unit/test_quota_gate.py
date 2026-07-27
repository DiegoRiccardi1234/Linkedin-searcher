"""The daily request ceiling, and the scan that refuses to start past it.

A free OpenRouter account gets 1000 requests a day, shared across every key on
it, and one logical scoring call can become several HTTP requests. Nothing in
the app knew that number: a long scan walked into the ceiling and finished by
scoring the rest with the local heuristic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from app.config import load_settings
from app.db import Database
from app.models import ScanRequest
from app.services import quota
from app.services import scanner_service as ss


def _log_calls(db: Database, count: int, *, when: datetime | None = None) -> None:
    ts = (when or datetime.now(UTC)).isoformat(timespec="seconds")
    for _ in range(count):
        db.conn.execute(
            "INSERT INTO usage_log (ts, provider, model, endpoint, prompt_tokens, "
            "completion_tokens, total_tokens, success, error_type, duration_ms) "
            "VALUES (?, 'openrouter', 'm', 'complete_json', 0, 0, 0, 1, NULL, 100)",
            (ts,),
        )
    db.conn.commit()


def test_counts_only_today(tmp_path: Path) -> None:
    db = Database(tmp_path / "q.db")
    try:
        _log_calls(db, 5)
        _log_calls(db, 40, when=datetime.now(UTC) - timedelta(days=2))
        assert quota.requests_today(db) == 5
    finally:
        db.close()


def test_default_ceiling_is_the_free_tier_cap(tmp_path: Path) -> None:
    db = Database(tmp_path / "q.db")
    try:
        assert quota.daily_limit(db) == quota.DEFAULT_DAILY_LIMIT == 1000
        db.set_preference(quota.LIMIT_PREFERENCE, "250")
        assert quota.daily_limit(db) == 250
        db.set_preference(quota.LIMIT_PREFERENCE, "0")  # opt out
        assert quota.daily_limit(db) == 0
        assert quota.blocks_scan(db) is None
    finally:
        db.close()


def test_scan_is_blocked_when_the_budget_is_spent(tmp_path: Path) -> None:
    db = Database(tmp_path / "q.db")
    try:
        db.set_preference(quota.LIMIT_PREFERENCE, "50")
        _log_calls(db, 50)
        assert (quota.blocks_scan(db) or "").startswith("daily_limit_reached")
        assert quota.status(db)["exhausted"] is True
    finally:
        db.close()


def test_scan_is_blocked_when_too_little_is_left(tmp_path: Path) -> None:
    """Starting a scan with five requests left leaves a half-scored archive."""
    db = Database(tmp_path / "q.db")
    try:
        db.set_preference(quota.LIMIT_PREFERENCE, "50")
        _log_calls(db, 45)
        assert (quota.blocks_scan(db) or "").startswith("daily_limit_near")
    finally:
        db.close()


class _PM:
    def preview_scoring_model(self, _p: object) -> str:
        return "m"

    def clear_model_penalties(self, reason: str | None = None) -> None:
        pass


def test_run_scan_stops_before_scraping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scraped = []
    monkeypatch.setattr(ss, "scrape_jobs", lambda **k: scraped.append(k) or pd.DataFrame())
    settings = load_settings(tmp_path)
    db = Database(tmp_path / "q.db")
    try:
        db.set_preference(quota.LIMIT_PREFERENCE, "10")
        _log_calls(db, 10)
        events = list(ss.run_scan(db, settings, _PM(), ScanRequest(search_terms=["x"])))
        assert scraped == []  # not even the scrape ran
        assert events[0]["error"].startswith("daily_limit_reached")
        assert events[0]["quota"]["used"] == 10
    finally:
        db.close()
