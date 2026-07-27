"""Empirical model record read back from ``usage_log``.

Every LLM call has been logged with its outcome since v1.1.0 and nothing ever
read the table back, so the app kept re-electing models it had already watched
fail — the in-memory penalty map forgets everything on restart.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.db import Database
from app.services import model_scoreboard as sb


def _log(db: Database, **row: object) -> None:
    defaults = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "provider": "openrouter",
        "model": "m",
        "endpoint": sb.SCORING_ENDPOINT,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "success": 1,
        "error_type": None,
        "duration_ms": 500,
    }
    defaults.update(row)
    db.conn.execute(
        "INSERT INTO usage_log (ts, provider, model, endpoint, prompt_tokens, "
        "completion_tokens, total_tokens, success, error_type, duration_ms) "
        "VALUES (:ts, :provider, :model, :endpoint, :prompt_tokens, :completion_tokens, "
        ":total_tokens, :success, :error_type, :duration_ms)",
        defaults,
    )
    db.conn.commit()


def test_scoreboard_summarises_each_model(tmp_path: Path) -> None:
    db = Database(tmp_path / "u.db")
    try:
        for _ in range(4):
            _log(db, model="good", duration_ms=400)
        for _ in range(4):
            _log(db, model="bad", success=0, error_type="TruncatedCompletionError")
        records = {r.model: r for r in sb.scoreboard(db)}
        assert records["good"].success_rate == 1.0
        assert records["good"].median_ms == 400
        assert records["bad"].truncation_rate == 1.0
        assert records["bad"].is_unfit is True
        assert records["good"].is_unfit is False
        # Best-first ordering: the model that answers wins.
        assert sb.scoreboard(db)[0].model == "good"
    finally:
        db.close()


def test_a_thin_record_is_not_evidence(tmp_path: Path) -> None:
    """One bad call must not blacklist a model for two weeks."""
    db = Database(tmp_path / "u.db")
    try:
        _log(db, model="unlucky", success=0, error_type="TruncatedCompletionError")
        assert sb.unfit_ids(db, "openrouter") == set()
    finally:
        db.close()


def test_rate_limits_do_not_condemn_a_model(tmp_path: Path) -> None:
    """A 429 is the host throttling, not the model being bad — and on a busy
    free tier counting them would blacklist every decent model."""
    db = Database(tmp_path / "u.db")
    try:
        for _ in range(10):
            _log(db, model="throttled", success=0, error_type="RateLimitError")
        for _ in range(4):
            _log(db, model="throttled")
        assert sb.unfit_ids(db, "openrouter") == set()
    finally:
        db.close()


def test_only_scoring_calls_count(tmp_path: Path) -> None:
    """A model can chat fine and still never close a brace."""
    db = Database(tmp_path / "u.db")
    try:
        for _ in range(6):
            _log(db, model="chatty", endpoint="chat", success=0, error_type="ValueError")
        assert sb.unfit_ids(db, "openrouter") == set()
    finally:
        db.close()


def test_old_calls_fall_out_of_the_window(tmp_path: Path) -> None:
    db = Database(tmp_path / "u.db")
    try:
        old = (datetime.now(UTC) - timedelta(days=40)).isoformat(timespec="seconds")
        for _ in range(8):
            _log(db, ts=old, model="reformed", success=0, error_type="TruncatedCompletionError")
        assert sb.unfit_ids(db, "openrouter") == set()
    finally:
        db.close()


def test_unfit_is_scoped_to_the_provider(tmp_path: Path) -> None:
    """The same slug can truncate on one host and behave on another."""
    db = Database(tmp_path / "u.db")
    try:
        for _ in range(6):
            _log(db, provider="openrouter", model="gpt-oss-120b", success=0, error_type="Truncated")
        for _ in range(6):
            _log(db, provider="cerebras", model="gpt-oss-120b")
        assert sb.unfit_ids(db, "openrouter") == {"gpt-oss-120b"}
        assert sb.unfit_ids(db, "cerebras") == set()
    finally:
        db.close()


def test_never_raises_without_the_table(tmp_path: Path) -> None:
    class _Broken:
        @property
        def conn(self) -> object:
            raise RuntimeError("closed")

    assert sb.scoreboard(_Broken()) == []
    assert sb.unfit_ids(_Broken(), "openrouter") == set()
