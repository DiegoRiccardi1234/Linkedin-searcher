"""Judging the judge: the user's verdict on an AI score."""

from __future__ import annotations

import json
from pathlib import Path

from app.db import Database


def _scored_job(db: Database, titolo: str, score: int, analysis_v: int | None = 2) -> int:
    job_id, _, _ = db.upsert_job(
        {
            "titolo": titolo,
            "azienda": "ACME",
            "descrizione": "x" * 400,
            "sede": "Torino",
            "fonte": "linkedin",
            "link": f"http://x/{titolo}",
            "ricerca_usata": "ai qa",
            "modalita": "Ibrido",
        }
    )
    analysis: dict[str, object] = {"punteggio": score, "consiglio": "Valuta"}
    if analysis_v is not None:
        analysis["scoring_v"] = analysis_v
    db.update_job_analysis(job_id, analysis)
    return job_id


def test_feedback_captures_the_score_it_was_judging(tmp_path: Path) -> None:
    """The score is copied into the row: a re-score overwrites the job's score,
    and a judgement pointing at a number that no longer exists says nothing."""
    db = Database(tmp_path / "f.db")
    try:
        job_id = _scored_job(db, "Senior ML Engineer", 9)
        assert db.add_score_feedback(job_id, "down", expected_score=3, reason="chiede 5 anni")

        row = db.latest_score_feedback(job_id)
        assert row is not None
        assert (row["verdict"], row["ai_score"], row["expected_score"]) == ("down", 9, 3)
        assert row["titolo"] == "Senior ML Engineer"
        assert row["analysis_v"] == 2

        # Re-scored: the stored judgement still remembers the 9 it objected to.
        db.update_job_analysis(job_id, {"punteggio": 4, "scoring_v": 2})
        again = db.latest_score_feedback(job_id)
        assert again is not None
        assert again["ai_score"] == 9
    finally:
        db.close()


def test_a_new_judgement_supersedes_without_erasing(tmp_path: Path) -> None:
    db = Database(tmp_path / "f.db")
    try:
        job_id = _scored_job(db, "AI QA Engineer", 8)
        db.add_score_feedback(job_id, "down", expected_score=4)
        db.add_score_feedback(job_id, "up")

        latest = db.latest_score_feedback(job_id)
        assert latest is not None
        assert latest["verdict"] == "up"
        assert len(db.list_score_feedback()) == 2  # history kept
        # …but the summary counts each job once, so a changed mind is not two votes.
        assert db.score_feedback_summary()["total"] == 1
    finally:
        db.close()


def test_summary_measures_agreement_and_gap(tmp_path: Path) -> None:
    db = Database(tmp_path / "f.db")
    try:
        db.add_score_feedback(_scored_job(db, "A", 9), "down", expected_score=4)  # gap 5
        db.add_score_feedback(_scored_job(db, "B", 7), "up")
        db.add_score_feedback(_scored_job(db, "C", 6), "up", expected_score=7)  # gap 1

        summary = db.score_feedback_summary()
        assert summary["total"] == 3
        assert summary["up"] == 2
        assert summary["down"] == 1
        assert summary["agreement"] == 67
        assert summary["avg_gap"] == 3.0  # (5 + 1) / 2 — only the scored cases
        assert summary["scored_cases"] == 2
    finally:
        db.close()


def test_bad_input_is_refused_not_stored(tmp_path: Path) -> None:
    db = Database(tmp_path / "f.db")
    try:
        job_id = _scored_job(db, "A", 5)
        assert db.add_score_feedback(job_id, "maybe") == 0
        assert db.add_score_feedback(9999, "up") == 0  # no such job
        assert db.list_score_feedback() == []
        assert db.score_feedback_summary()["agreement"] is None
    finally:
        db.close()


def test_judgements_survive_the_job_being_deleted(tmp_path: Path) -> None:
    """A judged posting is an evaluation case. Postings expire and archives get
    wiped; the case has to outlive both, which is why title/company are copied."""
    db = Database(tmp_path / "f.db")
    try:
        job_id = _scored_job(db, "Data Annotator", 6)
        db.add_score_feedback(job_id, "down", expected_score=2, reason="lavoro a task")
        db.delete_all_jobs()

        rows = db.list_score_feedback()
        assert len(rows) == 1
        assert rows[0]["titolo"] == "Data Annotator"
        assert rows[0]["ai_score"] == 6
    finally:
        db.close()


def test_withdrawing_a_judgement_clears_the_job(tmp_path: Path) -> None:
    db = Database(tmp_path / "f.db")
    try:
        job_id = _scored_job(db, "A", 5)
        db.add_score_feedback(job_id, "up")
        db.add_score_feedback(job_id, "down")
        assert db.delete_score_feedback(job_id) == 2
        assert db.latest_score_feedback(job_id) is None
    finally:
        db.close()


def test_model_is_recorded_when_the_analysis_carries_it(tmp_path: Path) -> None:
    """Which model wrote a score is not stored today (it is reconstructed from
    usage_log). When an analysis does carry it, the judgement keeps it."""
    db = Database(tmp_path / "f.db")
    try:
        job_id = _scored_job(db, "A", 5)
        db.update_job_analysis(
            job_id, json.loads('{"punteggio": 5, "scoring_v": 2, "modello": "gemma-4-31b-it"}')
        )
        db.add_score_feedback(job_id, "up")
        row = db.latest_score_feedback(job_id)
        assert row is not None
        assert row["model"] == "gemma-4-31b-it"
    finally:
        db.close()
