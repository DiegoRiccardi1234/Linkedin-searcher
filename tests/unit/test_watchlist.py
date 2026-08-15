"""Following an employer by name: normalisation, matching, and the extra scan
pass those two feed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from app.config import load_settings
from app.db import Database
from app.models import ScanRequest
from app.services import scanner_service as ss
from app.services.scan.companies import canonical_company, company_matches

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

# Long enough to clear MIN_DESCRIPTION_CHARS, and deliberately NOT technical:
# this is the text the relevance gate would throw away for a keyword search.
_LINGUIST_JD = (
    "Cerchiamo un linguista madrelingua italiano per un progetto di revisione "
    "di contenuti editoriali. Attivita': revisione di testi, controllo di stile "
    "e coerenza terminologica, redazione di linee guida per il team. "
    "Requisiti: laurea in ambito umanistico, ottima padronanza dell'italiano "
    "scritto, attenzione al dettaglio. Sede Torino, contratto a tempo pieno. "
)


# ── canonical_company / company_matches ──────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("RWS Group S.p.A.", "rws"),
        ("R.W.S.", "rws"),
        ("bit spa", "bit"),
        ("Welocalize Inc.", "welocalize"),
        ("  Welo   Data  ", "welo data"),
        ("S.r.l.", ""),  # nothing but a legal form
        ("", ""),
    ],
)
def test_canonical_company(raw: str, expected: str) -> None:
    assert canonical_company(raw) == expected


def test_company_matches_is_bidirectional_but_token_bound() -> None:
    # The board prints more than the user typed…
    assert company_matches("RWS Moravia", "rws") is True
    # …or less.
    assert company_matches("RWS", "RWS Group") is True
    # A shared prefix is not a match: this is why matching is on whole tokens.
    assert company_matches("Bitpanda", "bit") is False
    assert company_matches("Orbit Systems", "bit") is False
    assert company_matches("", "rws") is False


# ── database round-trip ──────────────────────────────────────────────────────


def test_following_the_same_company_twice_reactivates_it(tmp_path: Path) -> None:
    db = Database(tmp_path / "w.db")
    try:
        first = db.add_watchlist_company("RWS Group S.p.A.", "AI-data in italiano")
        db.set_watchlist_active(first, False)
        again = db.add_watchlist_company("rws")
        assert again == first  # same canonical → same row, not a duplicate
        row = db.list_watchlist_companies()[0]
        assert row["active"] == 1
        assert row["note"] == "AI-data in italiano"  # a bare re-add keeps the note
        assert db.add_watchlist_company("S.r.l.") == 0  # nothing identifiable
    finally:
        db.close()


def test_watchlist_for_scan_needs_the_toggle_or_an_explicit_list(tmp_path: Path) -> None:
    db = Database(tmp_path / "w.db")
    try:
        db.add_watchlist_company("RWS")
        payload = ScanRequest(search_terms=["x"], location="Milano")
        # Following a company must not silently add a search to every scan.
        assert ss._watchlist_for_scan(db, payload) == []
        db.set_preference("watchlist_enabled", "1")
        assert [c["canonical"] for c in ss._watchlist_for_scan(db, payload)] == ["rws"]
        # An explicit list wins over the stored one.
        explicit = ScanRequest(search_terms=["x"], companies=["Toloka", "  "], location="Milano")
        assert [c["name"] for c in ss._watchlist_for_scan(db, explicit)] == ["Toloka"]
    finally:
        db.close()


def test_watchlist_is_capped(tmp_path: Path) -> None:
    db = Database(tmp_path / "w.db")
    try:
        db.set_preference("watchlist_enabled", "1")
        for i in range(ss._MAX_WATCHLIST_COMPANIES + 4):
            db.add_watchlist_company(f"Company {i}")
        assert len(ss._watchlist_for_scan(db, ScanRequest())) == ss._MAX_WATCHLIST_COMPANIES
    finally:
        db.close()


# ── the scan pass ────────────────────────────────────────────────────────────


def _watch_df() -> pd.DataFrame:
    """What a board returns for the query "RWS": the employer itself, plus an
    agency that merely mentions it."""
    rows = [
        {
            "title": "Language Specialist Italian",
            "company": "RWS Moravia",
            "description": _LINGUIST_JD,
            "location": "Torino",
            "site": "linkedin",
            "job_url": "http://x/1",
            "min_amount": None,
            "max_amount": None,
        },
        {
            "title": "Recruiter for RWS projects",
            "company": "Some Agency",
            "description": _LINGUIST_JD,
            "location": "Torino",
            "site": "linkedin",
            "job_url": "http://x/2",
            "min_amount": None,
            "max_amount": None,
        },
    ]
    return pd.DataFrame(rows, columns=_COLS)


class _WatchPM:
    """Scores anything, records the search terms it never sees (scoring only)."""

    def preview_scoring_model(self, _policy: Any) -> str:
        return "fake/model:free"

    def clear_model_penalties(self, reason: str | None = None) -> None:
        pass

    def complete_json(self, prompt: str, max_tokens: int = 700, **kwargs: Any) -> Any:
        n = prompt.count("--- OFFERTA ")
        if n == 0:
            return {"punteggio": 7}
        return {"valutazioni": [{"punteggio": 7} for _ in range(n)]}


def test_company_pass_keeps_only_that_employer_and_skips_the_relevance_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The non-technical posting survives because the EMPLOYER is the signal;
    the agency row that merely names RWS does not."""
    searched: list[str] = []

    def _fake_scrape(**kwargs: Any) -> pd.DataFrame:
        searched.append(str(kwargs.get("search_term")))
        return _watch_df()

    monkeypatch.setattr(ss, "scrape_jobs", _fake_scrape)
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0.0
    # An empty payload falls back to the configured defaults, so the company
    # pass has to be the ONLY pass for the assertions below to mean anything.
    db = Database(tmp_path / "s.db")
    try:
        db.add_watchlist_company("RWS Group")
        db.set_preference("watchlist_enabled", "1")
        payload = ScanRequest(search_terms=[], sites=["linkedin"], location="Torino")
        events = list(ss.run_scan(db, settings, _WatchPM(), payload))
        jobs = db.list_jobs()
    finally:
        db.close()

    assert "RWS Group" in searched  # searched by name, without level qualifiers
    kept = [j for j in jobs if j["azienda"] == "RWS Moravia"]
    assert len(kept) == 1
    assert kept[0]["ricerca_usata"] == "watchlist:RWS Group"
    assert not [j for j in jobs if j["azienda"] == "Some Agency"]
    assert any(e.get("status") == "complete" for e in events)


def test_company_pass_records_that_the_channel_delivered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ss, "scrape_jobs", lambda **k: _watch_df())
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0.0
    db = Database(tmp_path / "s.db")
    try:
        db.add_watchlist_company("RWS Group")
        db.set_preference("watchlist_enabled", "1")
        list(
            ss.run_scan(db, settings, _WatchPM(), ScanRequest(search_terms=[], sites=["linkedin"], location="Milano"))
        )
        row = db.list_watchlist_companies()[0]
        assert row["last_seen_at"]
    finally:
        db.close()
