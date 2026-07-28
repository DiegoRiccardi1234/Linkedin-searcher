"""The title has to name the trade, or the posting is not worth an LLM call.

The old relevance gate read title+description and fired only on ZERO overlap
with the domain vocabulary — a bar no corporate ad ever fails, since "data",
"software" and "cloud" appear in all of them. Measured on 47 real postings: 26
were off-domain from the title alone and 11 of those still scored >=6, all of
them from one over-broad search term ("AI Specialist", which job boards happily
match against payroll, sales and partnership roles).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.config import load_settings
from app.db import Database
from app.models import ScanRequest
from app.services import scanner_service as ss

_SKILLS = {"python", "java", "typescript", "react", "postgresql", "git"}

# Real titles from the 2026-07-27 scan, with the score they were given.
_OFF_TOPIC = [
    "PAYROLL SPECIALIST",
    "Application Specialist - sistemi di Back Office",
    "Security Associate Specialist",
    "DIGITAL COMMUNICATION SPECIALIST",
    "Partnership Specialist",
    "PRODUCT INSIGHTS SPECIALIST",
    "SALES AND SERVICE SPECIALIST - BRESCIA",
    "TECHNICAL TRAINER & PROMOTER",
    "RAI Specialist",
    "Key Account Manager",
    "Aiuto Cuoco Spa",
]

_ON_TOPIC = [
    "Automation tester - AI - Augmented QA",
    "AI Specialist INTERN",
    "Data Analyst - AI Translation Quality",
    "ML Engineer – Generative AI",
    "Junior AI & Digital Automation Specialist",
    "Prompt Engineer",
    "Linguistic Annotator",
    "Software Developer",
]


@pytest.mark.parametrize("titolo", _OFF_TOPIC)
def test_off_topic_titles_are_dropped(titolo: str) -> None:
    assert ss.title_off_topic(titolo, _SKILLS) is True


@pytest.mark.parametrize("titolo", _ON_TOPIC)
def test_on_topic_titles_are_kept(titolo: str) -> None:
    assert ss.title_off_topic(titolo, _SKILLS) is False


def test_two_letter_trades_are_visible() -> None:
    """``_tokenize`` needs three characters, so a gate built on it would have
    missed "AI", "QA" and "ML" — the very words that matter here."""
    for titolo in ("AI Specialist", "QA Engineer", "ML Ops"):
        assert ss.title_off_topic(titolo, set()) is False


def test_entry_routes_survive_without_naming_the_trade() -> None:
    """"Tirocinio curriculare" names no trade, and dropping it would cut exactly
    the openings a recent graduate is looking for."""
    for titolo in ("Tirocinio curriculare", "Graduate Program 2026", "Stage in azienda"):
        assert ss.title_off_topic(titolo, set()) is False


def test_candidate_skills_widen_the_gate() -> None:
    assert ss.title_off_topic("Quarkus Consultant", set()) is True
    assert ss.title_off_topic("Quarkus Consultant", {"quarkus"}) is False


# --- bait postings and aggregators -------------------------------------------


def test_spontaneous_application_is_not_a_position() -> None:
    """It scored 8/10 on a real scan. There is no role in it to score."""
    assert ss.is_bait_posting("Candidatura Spontanea in Joinrs | RAL €22K – €27K") is True
    assert ss.is_bait_posting("Spontaneous Application - Talent Pool") is True
    assert ss.is_bait_posting("AI QA Engineer") is False


def test_aggregator_is_flagged_not_dropped() -> None:
    """A job board sometimes reposts a real role: say who is really hiring."""
    out = ss.enforce_hard_requirements(
        {"punteggio": 7},
        profile_markdown="CV",
        descrizione="Valutazione modelli. " * 20,
        sede="Torino, Italy",
        azienda="Jobbydoo",
    )
    assert ss.FLAG_AGGREGATOR in out["blocchi"]
    assert out["punteggio"] == 7, "a flag, never a cap"


# --- the gate inside a real scan ---------------------------------------------

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


def _row(title: str, company: str = "Co") -> dict[str, object]:
    return {
        "title": title,
        "company": company,
        # A full, plausible corporate description: the OLD gate let every one of
        # these through, because "dati" and "software" are in all of them.
        "description": (
            "Azienda leader cerca una figura da inserire nel team. Si occuperà di "
            "gestire i dati, usare i software aziendali e collaborare con il team. "
            "Requisiti: diploma, buone capacità relazionali, inglese. " * 3
        ),
        "location": "Milano, Italy",
        "site": "linkedin",
        "job_url": f"https://example.com/{abs(hash(title))}",
        "min_amount": None,
        "max_amount": None,
    }


class _PM:
    def __init__(self) -> None:
        self.calls = 0

    def preview_scoring_model(self, _p: object) -> str:
        return "m"

    def clear_model_penalties(self, reason: str | None = None) -> None:
        pass

    def complete_json(self, prompt: str, **_k: object) -> object:
        self.calls += 1
        n = prompt.count("--- OFFERTA ")
        return {"valutazioni": [{"punteggio": 7}] * n} if n else {"punteggio": 7}


def test_scan_drops_off_topic_titles_before_spending_a_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    df = pd.DataFrame(
        [
            _row("PAYROLL SPECIALIST", "Reverse"),
            _row("Partnership Specialist", "Sirti"),
            _row("Candidatura Spontanea in Joinrs", "Joinrs"),
            _row("AI QA Engineer", "Acme"),
        ],
        columns=_COLS,
    )
    monkeypatch.setattr(ss, "scrape_jobs", lambda **k: df)
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0.0
    pm = _PM()
    db = Database(tmp_path / "t.db")
    try:
        events = list(
            ss.run_scan(db, settings, pm, ScanRequest(search_terms=["x"], sites=["linkedin"]))
        )
    finally:
        db.close()
    complete = next(e for e in events if e.get("status") == "complete")
    analyzed = [e for e in events if e.get("status") == "analyzed"]
    assert [e["job"]["titolo"] for e in analyzed] == ["AI QA Engineer"]
    assert complete["scartati_per_titolo"] == 2  # the two Specialist roles
    assert complete["totale_scartati"] >= 3  # plus the bait posting
    assert pm.calls == 1, "three postings were dropped before any LLM call"
