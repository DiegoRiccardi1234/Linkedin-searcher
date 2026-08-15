"""Cancelling a scan stops the spending, not just the reporting.

``pool.shutdown(cancel_futures=True)`` only drops futures that have not started;
every worker already running went on to call the model and pay for a result the
run would then discard.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from app.config import load_settings
from app.db import Database
from app.models import ScanRequest
from app.services import scanner_service as ss

_JD = "Ruolo AI QA con valutazione di modelli linguistici e analisi errori. " * 12

_COLS = [
    "title",
    "company",
    "description",
    "location",
    "site",
    "job_url",
    "min_amount",
    "max_amount",
]


def _df(n: int) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "title": f"AI QA Engineer {i}",
                "company": "Acme",
                "description": _JD,
                "location": "Torino",
                "site": "linkedin",
                "job_url": f"https://example.com/{i}",
                "min_amount": None,
                "max_amount": None,
            }
            for i in range(n)
        ],
        columns=_COLS,
    )


class _CountingPM:
    """Counts scoring calls; every one of them costs real quota in production."""

    def __init__(self) -> None:
        self.calls = 0

    def preview_scoring_model(self, _policy: Any) -> str:
        return "fake/model"

    def clear_model_penalties(self, reason: str | None = None) -> None:
        pass

    def complete_json(self, prompt: str, max_tokens: int = 0, **_kw: Any) -> dict[str, Any]:
        self.calls += 1
        return {"punteggio": 7, "consiglio": "Valutabile"}


def test_cancelled_scan_makes_no_further_model_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ss, "scrape_jobs", lambda **k: _df(6))
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0.0
    settings.scan_batch_size = 1  # one call per job, so the count is unambiguous
    settings.scan_concurrency = 1

    pm = _CountingPM()
    db = Database(tmp_path / "s.db")
    try:
        # Cancelled from the very first check: nothing should be scored.
        events = list(
            ss.run_scan(
                db,
                settings,
                pm,  # type: ignore[arg-type]
                ScanRequest(search_terms=["ai qa"], sites=["linkedin"], location="Milano"),
                cancel_check=lambda: True,
            )
        )
        assert pm.calls == 0
        assert any(e.get("status") == "cancelled" or e.get("cancelled") for e in events)
    finally:
        db.close()


def test_a_normal_scan_still_scores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard must not stop a run nobody cancelled."""
    monkeypatch.setattr(ss, "scrape_jobs", lambda **k: _df(3))
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0.0
    settings.scan_batch_size = 1
    settings.scan_concurrency = 1

    pm = _CountingPM()
    db = Database(tmp_path / "s.db")
    try:
        list(
            ss.run_scan(
                db,
                settings,
                pm,  # type: ignore[arg-type]
                ScanRequest(search_terms=["ai qa"], sites=["linkedin"], location="Milano"),
            )
        )
        assert pm.calls == 3
    finally:
        db.close()
