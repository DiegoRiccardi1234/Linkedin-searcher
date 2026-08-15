"""Indeed coverage fix (v1.7.5).

jobspy's Indeed filter builder is an if/elif: with ``hours_old`` set, the
``is_remote``/``job_type`` filters are silently ignored AND the server-side
date filter collapses results (measured live: 4 rows vs 20 for the same
query). So Indeed is scraped WITHOUT ``hours_old`` — letting remote/job-type
apply server-side — and freshness is enforced locally on ``date_posted``
(unknown dates are kept, never over-drop). LinkedIn keeps the old behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from app.config import load_settings
from app.db import Database
from app.models import ScanRequest
from app.services import scanner_service as ss

_COLS = [
    "title",
    "company",
    "description",
    "location",
    "site",
    "job_url",
    "min_amount",
    "max_amount",
    "date_posted",
]

_DESC = "Python, data engineering e machine learning in un team AI. " * 10


def _row(site: str, url: str, date_posted: Any) -> dict[str, Any]:
    return {
        "title": f"AI Engineer {url[-1]}",
        "company": f"Co {url}",
        "description": _DESC,
        "location": "Torino",
        "site": site,
        "job_url": url,
        "min_amount": None,
        "max_amount": None,
        "date_posted": date_posted,
    }


class _PM:
    def preview_scoring_model(self, _p: Any) -> str:
        return "m"

    def clear_model_penalties(self, reason: str | None = None) -> None:
        pass


def _run(monkeypatch, tmp_path: Path, sites: list[str], fake_scrape) -> list[dict]:
    monkeypatch.setattr(ss, "scrape_jobs", fake_scrape)
    monkeypatch.setattr(ss, "analyze_offer", lambda **k: {"punteggio": 5})
    monkeypatch.setattr(
        ss, "analyze_offers_batch", lambda **k: [{"punteggio": 5} for _ in k["offers"]]
    )
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0.0
    db = Database(tmp_path / "s.db")
    try:
        return list(ss.run_scan(db, settings, _PM(), ScanRequest(search_terms=["x"], sites=sites, location="Milano")))
    finally:
        db.close()


def test_mixed_sites_split_into_two_calls(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake(**k: Any) -> pd.DataFrame:
        calls.append(k)
        return pd.DataFrame([], columns=_COLS)

    _run(monkeypatch, tmp_path, ["linkedin", "indeed"], fake)
    assert len(calls) == 2
    by_site = {tuple(c["site_name"]): c for c in calls}
    indeed = by_site[("indeed",)]
    linkedin = by_site[("linkedin",)]
    assert "hours_old" not in indeed
    assert linkedin.get("hours_old")
    assert linkedin.get("linkedin_fetch_description") is True


def test_indeed_only_drops_hours_old(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake(**k: Any) -> pd.DataFrame:
        calls.append(k)
        return pd.DataFrame([], columns=_COLS)

    _run(monkeypatch, tmp_path, ["indeed"], fake)
    assert len(calls) == 1
    assert calls[0]["site_name"] == ["indeed"]
    assert "hours_old" not in calls[0]


def test_linkedin_only_unchanged(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake(**k: Any) -> pd.DataFrame:
        calls.append(k)
        return pd.DataFrame([], columns=_COLS)

    _run(monkeypatch, tmp_path, ["linkedin"], fake)
    assert len(calls) == 1
    assert calls[0].get("hours_old")


def test_local_freshness_filter_on_indeed_rows(tmp_path: Path, monkeypatch) -> None:
    today = datetime.now(UTC).date()
    ancient = today - timedelta(days=400)

    def fake(**k: Any) -> pd.DataFrame:
        site = k["site_name"][0]
        if site == "indeed":
            rows = [
                _row("indeed", "https://x/1", today),  # fresh -> kept
                _row("indeed", "https://x/2", ancient),  # stale -> dropped
                _row("indeed", "https://x/3", None),  # unknown -> kept
            ]
        else:
            rows = [_row("linkedin", "https://x/4", ancient)]  # not indeed -> kept
        return pd.DataFrame(rows, columns=_COLS)

    events = _run(monkeypatch, tmp_path, ["linkedin", "indeed"], fake)
    complete = next(e for e in events if e.get("status") == "complete")
    assert complete["totale_analizzati"] == 3  # 2 indeed kept + 1 linkedin


def test_one_site_failing_keeps_the_other(tmp_path: Path, monkeypatch) -> None:
    def fake(**k: Any) -> pd.DataFrame:
        if k["site_name"] == ["indeed"]:
            raise RuntimeError("indeed 403")
        return pd.DataFrame([_row("linkedin", "https://x/9", None)], columns=_COLS)

    events = _run(monkeypatch, tmp_path, ["linkedin", "indeed"], fake)
    complete = next(e for e in events if e.get("status") == "complete")
    assert complete["totale_analizzati"] == 1  # linkedin still processed


# ── Indeed is per-country: resolve it from the location (v1.7.6) ─────────────


def test_indeed_country_resolved_from_location() -> None:
    assert ss._indeed_country_for("Germany", "italy") == "germany"
    assert ss._indeed_country_for("Berlin, Germany", "italy") == "germany"
    assert ss._indeed_country_for("Torino, Italy", "italy") == "italy"
    assert ss._indeed_country_for("Torino", "italy") == "italy"  # bare city → scan country
    assert ss._indeed_country_for("", "italy") == "italy"


def test_indeed_skipped_for_multi_country_locations() -> None:
    for placeholder in ("European Union", "Remote", "EMEA", "worldwide"):
        assert ss._indeed_country_for(placeholder, "italy") is None


def test_scan_drops_indeed_for_multi_country_location(tmp_path: Path, monkeypatch) -> None:
    """The 2026-07-21 run asked Indeed for 'European Union' with country=italy
    and got 0 rows out of 44 jobs. Now Indeed is skipped and LinkedIn still runs."""
    calls: list[dict[str, Any]] = []

    def fake(**k: Any) -> pd.DataFrame:
        calls.append(k)
        return pd.DataFrame([], columns=_COLS)

    monkeypatch.setattr(ss, "scrape_jobs", fake)
    monkeypatch.setattr(ss, "analyze_offer", lambda **k: {"punteggio": 5})
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0.0
    db = Database(tmp_path / "s.db")
    try:
        list(
            ss.run_scan(
                db,
                settings,
                _PM(),
                ScanRequest(
                    search_terms=["x"], sites=["linkedin", "indeed"], location="European Union"
                ),
            )
        )
    finally:
        db.close()
    assert len(calls) == 1
    assert calls[0]["site_name"] == ["linkedin"]


def test_scan_uses_location_country_for_indeed(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake(**k: Any) -> pd.DataFrame:
        calls.append(k)
        return pd.DataFrame([], columns=_COLS)

    monkeypatch.setattr(ss, "scrape_jobs", fake)
    monkeypatch.setattr(ss, "analyze_offer", lambda **k: {"punteggio": 5})
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0.0
    db = Database(tmp_path / "s2.db")
    try:
        list(
            ss.run_scan(
                db,
                settings,
                _PM(),
                ScanRequest(search_terms=["x"], sites=["indeed"], location="Germany"),
            )
        )
    finally:
        db.close()
    assert calls[0]["country_indeed"] == "germany"
