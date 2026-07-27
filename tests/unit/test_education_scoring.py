"""Education-aware scoring (v1.7.5).

Real case: a posting requiring a laurea magistrale scored 9 for a candidate
with a triennale, with no visible gap. The scoring prompt must instruct the
model to weigh degree/level requirements, the schema must carry the field,
the heuristic fallback must penalize it, and analyses produced BEFORE this
schema (no ``titolo_studio_richiesto`` key) must count as missing so
re-appearing jobs get re-scored once.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.db import Database
from app.scoring_schema import ANALYSIS_VERSION_KEY, CURRENT_ANALYSIS_VERSION
from app.services import scanner_service as ss

# --- prompt + schema ----------------------------------------------------------


def test_per_offer_schema_has_education_field() -> None:
    assert "titolo_studio_richiesto" in ss._PER_OFFER_SCHEMA


def test_single_prompt_instructs_education_weighing() -> None:
    p = ss._analysis_prompt("CV", "T", "A", "d" * 400)
    assert "titolo_studio_richiesto" in p
    assert "titolo di studio" in p.lower()


def test_batch_prompt_instructs_education_weighing() -> None:
    p = ss._batch_analysis_prompt("CV", [{"titolo": "T", "azienda": "A", "descrizione": "d"}])
    assert "titolo_studio_richiesto" in p
    assert "titolo di studio" in p.lower()


# --- heuristic fallback -------------------------------------------------------

_BASE = "Analisi dati e supporto al team su progetti interni."


def test_fallback_penalizes_masters_requirement() -> None:
    plain = ss._fallback_analysis("t", "CV", "Analyst", "Co", _BASE)
    masters = ss._fallback_analysis(
        "t", "CV", "Analyst", "Co", _BASE + " Requisiti: laurea magistrale in informatica."
    )
    assert masters["punteggio"] < plain["punteggio"]
    assert masters["titolo_studio_richiesto"] == "Magistrale"
    assert "magistrale" in masters["punti_deboli_per_diego"].lower()


def test_fallback_penalizes_phd_requirement() -> None:
    phd = ss._fallback_analysis("t", "CV", "Researcher", "Co", _BASE + " Requisiti: PhD in NLP.")
    assert phd["titolo_studio_richiesto"] == "PhD"


def test_fallback_without_requirement_says_unspecified() -> None:
    plain = ss._fallback_analysis("t", "CV", "Analyst", "Co", _BASE)
    assert plain["titolo_studio_richiesto"] == "Non specificato"


def test_capped_paths_carry_education_marker() -> None:
    """Capped analyses must carry the marker key too, or the staleness check
    would re-score them on every scan."""
    res = ss._insufficient_description_analysis("CV", "T", "A", "troppo corta")
    assert "titolo_studio_richiesto" in res


# --- staleness: legacy analyses count as absent --------------------------------


def test_job_has_analysis_false_for_legacy_schema(tmp_path: Path) -> None:
    db = Database(tmp_path / "d.db")
    try:
        jid, _, _ = db.upsert_job({"titolo": "T", "azienda": "A", "link": "https://x/1"})
        assert db.job_has_analysis(jid) is False
        db.update_job_analysis(jid, {"punteggio": 9})  # pre-v1.7.5 shape
        assert db.job_has_analysis(jid) is False  # stale -> re-score on next scan
        # Education-era shape: still stale, it can't have weighed geo/grade blockers.
        db.update_job_analysis(jid, {"punteggio": 7, "titolo_studio_richiesto": "Triennale"})
        assert db.job_has_analysis(jid) is False
        # Eligibility-era shape: also stale — the marker key it used to be
        # recognised by is injected into heuristic fallbacks too.
        db.update_job_analysis(jid, {"punteggio": 7, "eleggibilita_geografica": "Italia/UE"})
        assert db.job_has_analysis(jid) is False
        db.update_job_analysis(
            jid, {"punteggio": 7, ANALYSIS_VERSION_KEY: CURRENT_ANALYSIS_VERSION}
        )
        assert db.job_has_analysis(jid) is True
    finally:
        db.close()


def test_heuristic_analysis_never_counts_as_scored(tmp_path: Path) -> None:
    """A keyword-only analysis must not freeze the job at that score.

    It used to: the normaliser injects the marker key into EVERY analysis, so a
    job that fell back to the heuristic (e.g. because the model truncated) was
    "already analysed" forever.
    """
    db = Database(tmp_path / "h.db")
    try:
        jid, _, _ = db.upsert_job({"titolo": "AI QA", "azienda": "A", "link": "https://x/2"})
        heuristic = ss.enforce_hard_requirements(
            ss._heuristic_analysis("CV python", "AI QA Engineer", "A", "descrizione"),
            profile_markdown="CV python",
            descrizione="descrizione",
        )
        assert ANALYSIS_VERSION_KEY not in heuristic
        db.update_job_analysis(jid, heuristic)
        assert db.job_has_analysis(jid) is False

        scored = ss.enforce_hard_requirements(
            {"punteggio": 8, "consiglio": "Valutabile"},
            profile_markdown="CV python",
            descrizione="descrizione",
        )
        assert scored[ANALYSIS_VERSION_KEY] == CURRENT_ANALYSIS_VERSION
        db.update_job_analysis(jid, scored)
        assert db.job_has_analysis(jid) is True
    finally:
        db.close()


def test_run_scan_rescored_legacy_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A re-seen job whose analysis predates the education schema is re-scored."""
    from app.config import load_settings
    from app.models import ScanRequest

    desc = "Python, machine learning e valutazione modelli in team AI. " * 10
    df = pd.DataFrame(
        [
            {
                "title": "AI Engineer",
                "company": "Acme",
                "description": desc,
                "location": "Torino",
                "site": "linkedin",
                "job_url": "https://example.com/1",
                "min_amount": None,
                "max_amount": None,
            }
        ],
        columns=[
            "title",
            "company",
            "description",
            "location",
            "site",
            "job_url",
            "min_amount",
            "max_amount",
        ],
    )
    monkeypatch.setattr(ss, "scrape_jobs", lambda **k: df)
    monkeypatch.setattr(
        ss, "analyze_offer", lambda **k: {"punteggio": 5, "titolo_studio_richiesto": "Triennale"}
    )
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0.0
    settings.scan_batch_size = 1

    db = Database(tmp_path / "s.db")
    try:
        # First scan seeds the job, then simulate a legacy analysis on it.
        list(ss.run_scan(db, settings, _PM(), ScanRequest(search_terms=["x"], sites=["linkedin"])))
        job = db.list_jobs(limit=10)[0]
        db.update_job_analysis(job["id"], {"punteggio": 9})  # legacy shape, inflated
        db.mark_jobs_not_new() if hasattr(db, "mark_jobs_not_new") else None

        events = list(
            ss.run_scan(db, settings, _PM(), ScanRequest(search_terms=["x"], sites=["linkedin"]))
        )
        analyzed = [e for e in events if e.get("status") == "analyzed"]
        assert len(analyzed) == 1  # legacy analysis -> re-scored, not skipped
        refreshed = db.get_job_with_analysis(job["id"])
        assert refreshed["analysis"]["punteggio"] == 5
    finally:
        db.close()


class _PM:
    def preview_scoring_model(self, _p: object) -> str:
        return "m"

    def clear_model_penalties(self, reason: str | None = None) -> None:
        pass
