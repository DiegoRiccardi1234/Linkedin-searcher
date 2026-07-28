"""The daily ceiling models a shared cloud free tier, not your own GPU.

``requests_today`` counted every row in ``usage_log``, so a scan run entirely on
a model on the user's own machine — no quota spent, nothing leaving the PC —
still ate the 1000/day budget and could eventually refuse to start. And the
ceiling itself could not be changed: the preference was not writable through the
API and no field existed anywhere, so the only remedy was editing the DB by hand.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.services import quota
from app.services.usage_tracker import record_usage


def _log(db: Database, provider: str, n: int = 1) -> None:
    for _ in range(n):
        record_usage(
            db,
            provider=provider,
            model="m",
            endpoint="complete_json",
            last_usage=None,
            success=True,
        )


def test_local_calls_do_not_count_against_the_budget(tmp_path: Path) -> None:
    db = Database(tmp_path / "q.db")
    try:
        _log(db, "custom", 5)
        assert quota.requests_today(db) == 0
        _log(db, "openrouter", 2)
        assert quota.requests_today(db) == 2
    finally:
        db.close()


def test_a_local_only_scan_is_never_blocked(tmp_path: Path) -> None:
    db = Database(tmp_path / "b.db")
    try:
        db.set_preference(quota.LIMIT_PREFERENCE, "100")
        _log(db, "custom", 200)
        assert quota.blocks_scan(db) is None
    finally:
        db.close()


def test_cloud_calls_still_block_a_scan(tmp_path: Path) -> None:
    db = Database(tmp_path / "c.db")
    try:
        db.set_preference(quota.LIMIT_PREFERENCE, "100")
        _log(db, "openrouter", 100)
        assert quota.blocks_scan(db) is not None
    finally:
        db.close()


def test_a_small_ceiling_does_not_block_an_untouched_budget(tmp_path: Path) -> None:
    """The headroom rule used to fire at 0 used against a limit of 10."""
    db = Database(tmp_path / "s.db")
    try:
        db.set_preference(quota.LIMIT_PREFERENCE, "10")
        assert quota.blocks_scan(db) is None
        _log(db, "openrouter", 10)
        assert quota.blocks_scan(db) is not None
    finally:
        db.close()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    os.environ.pop("GROQ_API_KEY", None)
    from app.main import create_app

    with TestClient(create_app(workspace_dir=tmp_path)) as tc:
        yield tc


def test_daily_limit_is_editable(client: TestClient) -> None:
    res = client.post("/api/preferences", json={"key": "daily_request_limit", "value": "0"})
    assert res.status_code == 200
    assert res.json()["preferences"]["daily_request_limit"] == "0"
