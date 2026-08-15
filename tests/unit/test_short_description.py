"""Insufficient-description threshold (MIN_DESCRIPTION_CHARS).

A truncated/marketing-only description (the real case: an 82-char company
blurb with no requirements) must NOT be LLM-scored as if it were a full JD —
the model hallucinates requirements from nothing. Such jobs get NO score at all
(v1.7.9: not even a capped one — nobody could read them), and the relevance gate
judges them by TITLE only (a stray domain token in a 82-char blurb must not save
an off-topic job, nor missing tokens condemn a good one).
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

_SHORT_DESC = (
    "***Where talent becomes impact.*** In DGS il talento diventa impatto su progetti python"
)
_LONG_DESC = (
    "Cerchiamo un profilo junior per il team AI: python, machine learning, "
    "data pipeline e valutazione di modelli LLM. Requisiti: laurea triennale "
    "in informatica, conoscenza di python e SQL, inglese B2. Offriamo "
    "formazione continua, smart working parziale e affiancamento senior. " * 3
)


class _CountingPM:
    """PM stub counting LLM calls; returns a fixed high score when called."""

    def __init__(self) -> None:
        self.calls = 0

    def preview_scoring_model(self, _p: Any) -> str:
        return "m"

    def clear_model_penalties(self, reason: str | None = None) -> None:
        pass

    def complete_json(self, prompt: str, max_tokens: int = 700, **k: Any) -> Any:
        self.calls += 1
        n = prompt.count("--- OFFERTA ")
        if n == 0:
            return {"punteggio": 9}
        return {"valutazioni": [{"punteggio": 9} for _ in range(n)]}


def test_analyze_offer_short_description_is_unscored_no_llm() -> None:
    pm = _CountingPM()
    res = ss.analyze_offer(pm, "CV con Python", "Data Scientist", "Sidea Group", _SHORT_DESC)
    assert pm.calls == 0, "a near-empty description must not be LLM-scored"
    assert res["punteggio"] is None, "nobody read this posting: it gets no score"
    assert res["consiglio"] == ""
    assert "non_valutato" in res["blocchi"]
    assert "descrizione_breve" in res["blocchi"]
    assert "breve" in res["riassunto"].lower()


def test_analyze_offer_long_description_uses_llm() -> None:
    pm = _CountingPM()
    res = ss.analyze_offer(pm, "CV con Python", "Data Scientist", "Co", _LONG_DESC)
    assert pm.calls == 1
    assert res["punteggio"] == 9


def test_batch_short_slot_unscored_long_slot_scored() -> None:
    pm = _CountingPM()
    offers = [
        {"titolo": "AI Engineer", "azienda": "Co", "descrizione": _LONG_DESC},
        {"titolo": "Data Scientist", "azienda": "Sidea", "descrizione": _SHORT_DESC},
    ]
    out = ss.analyze_offers_batch(pm, "CV", offers)
    assert out[0]["punteggio"] == 9  # full JD: batch slot used
    assert out[1]["punteggio"] is None  # short: the batch's blind 9 is discarded
    assert "breve" in out[1]["riassunto"].lower()


def test_empty_description_keeps_unavailable_message() -> None:
    res = ss.analyze_offer(_CountingPM(), "CV", "Senior AI Engineer", "BigCo", "")
    assert res["punteggio"] is None
    assert "non disponibile" in res["riassunto"].lower()


# --- relevance gate: short descriptions are judged by title only -------------


def _run(df: pd.DataFrame, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    monkeypatch.setattr(ss, "scrape_jobs", lambda **k: df)
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0.0
    db = Database(tmp_path / "s.db")
    try:
        return list(
            ss.run_scan(
                db, settings, _CountingPM(), ScanRequest(search_terms=["Data Engineer"], sites=["linkedin"], location="Milano")
            )
        )
    finally:
        db.close()


def _row(title: str, desc: str) -> dict[str, Any]:
    return {
        "title": title,
        "company": "Co",
        "description": desc,
        "location": "Torino",
        "site": "linkedin",
        "job_url": "http://x/1",
        "min_amount": None,
        "max_amount": None,
    }


def test_gate_short_desc_offtopic_title_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Off-topic title; the stray "python" token in the 90-char blurb must NOT
    # save it once the description is too short to be trusted.
    df = pd.DataFrame([_row("Estetista centro benessere", _SHORT_DESC)], columns=_COLS)
    events = _run(df, tmp_path, monkeypatch)
    complete = next(e for e in events if e.get("status") == "complete")
    assert complete["totale_analizzati"] == 0
    assert complete["totale_scartati"] >= 1


def test_gate_short_desc_relevant_title_kept_unscored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Relevant title ("data" is one of the terms searched for) → kept, but with
    # no score: the
    # description is too thin for anyone, model or app, to judge it.
    df = pd.DataFrame([_row("Data Scientist", _SHORT_DESC)], columns=_COLS)
    events = _run(df, tmp_path, monkeypatch)
    analyzed = [e for e in events if e.get("status") == "analyzed"]
    assert len(analyzed) == 1
    assert analyzed[0]["job"]["score"] is None
