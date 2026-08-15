"""A scan with nothing of yours to search for stops and says so.

Before 2.0.0 it did the opposite, silently: an empty form resolved to the six
terms and the city the author of the app was looking for, ran a full scan with
them, and then stored them as the user's own last search — which the scheduler
replayed and the work-rule inference read as evidence of where they live.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import load_settings
from app.db import Database
from app.models import ScanRequest
from app.services import scanner_service as ss
from app.services.search_intent import ScanRefused


def _boom(**_kwargs):
    """The scan gets as far as asking a job board, which is far enough."""
    raise RuntimeError("no network in a unit test")


class _PM:
    """A provider manager that would be very surprised to be called."""

    def clear_model_penalties(self, *_a, **_k) -> None:
        return None

    def preview_scoring_model(self, *_a, **_k) -> str:
        return "fake"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "searcher.db")
    try:
        yield database
    finally:
        database.close()


def _settings(tmp_path: Path):
    return load_settings(tmp_path)


def test_no_terms_anywhere_refuses(db: Database, tmp_path: Path) -> None:
    with pytest.raises(ScanRefused) as excinfo:
        list(ss.run_scan(db, _settings(tmp_path), _PM(), ScanRequest(location="Milano")))
    assert excinfo.value.missing == ["search_terms"]


def test_no_location_anywhere_refuses(db: Database, tmp_path: Path) -> None:
    with pytest.raises(ScanRefused) as excinfo:
        list(ss.run_scan(db, _settings(tmp_path), _PM(), ScanRequest(search_terms=["infermiere"])))
    assert excinfo.value.missing == ["location"]


def test_roles_from_the_cv_are_enough(db: Database, tmp_path: Path, monkeypatch) -> None:
    """No typing required — but the terms have to be the USER's."""
    db.set_preference("preferred_roles", "Infermiere pediatrico")
    db.set_preference("profile_fact_base_cities", "bari")
    seen: dict[str, object] = {}

    def _fake_scrape(**kwargs):
        seen.setdefault("term", kwargs.get("search_term"))
        seen.setdefault("location", kwargs.get("location"))
        raise RuntimeError("stop here: the point is what was asked for")

    monkeypatch.setattr(ss, "scrape_jobs", _fake_scrape)
    list(ss.run_scan(db, _settings(tmp_path), _PM(), ScanRequest()))
    assert "infermiere pediatrico" in str(seen.get("term", "")).lower()
    assert str(seen.get("location", "")).lower().startswith("bari")


def test_a_resolved_search_is_not_recorded_as_the_users_own(
    db: Database, tmp_path: Path, monkeypatch
) -> None:
    """The line that turned a fallback into biography."""
    db.set_preference("preferred_roles", "Infermiere pediatrico")
    db.set_preference("profile_fact_base_cities", "bari")
    monkeypatch.setattr(ss, "scrape_jobs", _boom)
    list(ss.run_scan(db, _settings(tmp_path), _PM(), ScanRequest()))
    assert db.get_preference("last_scan_terms", "") == ""
    assert db.get_preference("last_scan_locations", "") == ""


def test_what_the_user_typed_is_recorded(db: Database, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ss, "scrape_jobs", _boom)
    list(
        ss.run_scan(
            db,
            _settings(tmp_path),
            _PM(),
            ScanRequest(search_terms=["data analyst"], locations=["Bologna"]),
        )
    )
    assert json.loads(db.get_preference("last_scan_terms", "[]")) == ["data analyst"]
    assert json.loads(db.get_preference("last_scan_locations", "[]")) == ["Bologna"]


def test_a_watchlist_is_something_to_search_for(db: Database, tmp_path: Path, monkeypatch) -> None:
    """Following employers answers "what am I looking for" on its own."""
    db.set_preference("watchlist_enabled", "1")
    db.add_watchlist_company("Reply")
    monkeypatch.setattr(ss, "scrape_jobs", _boom)
    # No terms at all, and it runs: the companies are the search.
    list(ss.run_scan(db, _settings(tmp_path), _PM(), ScanRequest(location="Milano")))
