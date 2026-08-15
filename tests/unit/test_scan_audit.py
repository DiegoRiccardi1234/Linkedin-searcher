"""The end-of-scan audit must actually re-score what it flags.

``audit_scan`` was unit-tested as a pure function and looked healthy, while the
wiring around it had never been exercised: the pool held RESULTS and the
re-score wanted the OFFER, so every suspect raised ``KeyError: 'descrizione'``
and ``rivalutate`` was structurally zero — the UI reported "no anomaly to fix"
on runs full of them. This file tests the wiring, which is where it broke.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.db import Database
from app.services import scanner_service as ss

_CLONE = "Ottima opportunità in ambito consulenza, perfettamente in linea con il profilo."
_FRESH = "Analisi rifatta una per una, con motivazioni specifiche per questa singola offerta."


class _PM:
    def preview_scoring_model(self, _p: object) -> str:
        return "m"

    def clear_model_penalties(self, reason: str | None = None) -> None:
        pass


def _rows(n: int) -> pd.DataFrame:
    desc = "Attività di QA e test automation su pipeline dati, analisi dei requisiti. " * 8
    return pd.DataFrame(
        [
            {
                "title": f"QA Data Analyst {i}",
                "company": f"Acme {i}",
                "description": desc,
                "location": "Torino",
                "site": "linkedin",
                "job_url": f"https://example.com/{i}",
                "min_amount": None,
                "max_amount": None,
            }
            for i in range(n)
        ],
        columns=[
            "title", "company", "description", "location",
            "site", "job_url", "min_amount", "max_amount",
        ],
    )


def test_audit_rescores_the_offers_it_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import load_settings
    from app.models import ScanRequest

    calls: list[int] = []

    def fake_analyze(**kwargs: object) -> dict[str, object]:
        calls.append(1)
        # First pass: the same summary pasted on every posting — exactly the
        # anomaly the audit exists to catch. Re-scores answer properly.
        first_pass = len(calls) <= 5
        return {
            "punteggio": 6 if first_pass else 7,
            "riassunto": _CLONE if first_pass else _FRESH,
            "consiglio": "Valutabile",
        }

    monkeypatch.setattr(ss, "scrape_jobs", lambda **k: _rows(5))
    monkeypatch.setattr(ss, "analyze_offer", fake_analyze)
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0.0
    settings.scan_batch_size = 1

    db = Database(tmp_path / "s.db")
    try:
        list(ss.run_scan(db, settings, _PM(), ScanRequest(search_terms=["x"], sites=["linkedin"], location="Milano")))
        assert len(calls) > 5, "the audit flagged clones but never asked again"
        summaries = [
            db.get_job_with_analysis(j["id"])["analysis"].get("riassunto")
            for j in db.list_jobs(limit=10)
        ]
        assert _FRESH in summaries, "a re-scored offer must be written back to the DB"
    finally:
        db.close()


def test_audit_leaves_a_healthy_scan_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No anomaly, no extra calls: the re-score must not run on every scan."""
    from app.config import load_settings
    from app.models import ScanRequest

    calls: list[int] = []

    def fake_analyze(**kwargs: object) -> dict[str, object]:
        calls.append(1)
        n = len(calls)
        return {
            "punteggio": 4 + (n % 5),
            "riassunto": f"Sintesi specifica numero {n} per questa offerta, sufficientemente lunga.",
            "consiglio": "Valutabile",
        }

    monkeypatch.setattr(ss, "scrape_jobs", lambda **k: _rows(5))
    monkeypatch.setattr(ss, "analyze_offer", fake_analyze)
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0.0
    settings.scan_batch_size = 1

    db = Database(tmp_path / "h.db")
    try:
        list(ss.run_scan(db, settings, _PM(), ScanRequest(search_terms=["x"], sites=["linkedin"], location="Milano")))
        assert len(calls) == 5
    finally:
        db.close()
