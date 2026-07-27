"""Application metadata: when it was sent, with which CV, and how it ended."""

from __future__ import annotations

from pathlib import Path

from app.db import Database


def _job(db: Database, titolo: str = "AI QA Engineer") -> int:
    job_id, _, _ = db.upsert_job(
        {
            "titolo": titolo,
            "azienda": "RWS",
            "descrizione": "x" * 400,
            "sede": "Torino",
            "fonte": "linkedin",
            "link": f"http://x/{titolo}",
            "ricerca_usata": "ai qa",
            "modalita": "Ibrido",
        }
    )
    return job_id


def test_applying_stamps_the_date_and_the_active_cv(tmp_path: Path) -> None:
    db = Database(tmp_path / "a.db")
    try:
        profile_id = db.save_candidate_profile("cv_2026.pdf", "# CV", {})
        db.set_preference("active_profile_id", str(profile_id))
        job_id = _job(db)

        db.set_job_action(job_id, "applied")
        job = db.get_job_with_analysis(job_id)
        assert job is not None
        first_applied = job["applied_at"]
        assert first_applied
        assert job["applied_profile_id"] == profile_id
        assert job["applied_profile_name"] == "cv_2026.pdf"

        # Re-applying (or a second event) must not rewrite when it was first sent.
        db.set_job_action(job_id, "interviewing")
        db.set_job_action(job_id, "applied")
        again = db.get_job_with_analysis(job_id)
        assert again is not None
        assert again["applied_at"] == first_applied
        assert again["applied_profile_id"] == profile_id
    finally:
        db.close()


def test_a_note_does_not_look_like_an_application(tmp_path: Path) -> None:
    db = Database(tmp_path / "a.db")
    try:
        job_id = _job(db)
        db.set_job_action(job_id, "note", "chiamato il recruiter")
        job = db.get_job(job_id)
        assert job is not None
        assert job["applied_at"] is None
        assert job["status"] == "open"
    finally:
        db.close()


def test_outcome_is_separate_from_the_funnel_status(tmp_path: Path) -> None:
    db = Database(tmp_path / "a.db")
    try:
        job_id = _job(db)
        db.set_job_action(job_id, "applied")

        # An offer arrives while the funnel still says "interviewing".
        db.set_job_action(job_id, "interviewing")
        assert db.set_job_outcome(job_id, "offer") is True
        job = db.get_job(job_id)
        assert job is not None
        assert job["status"] == "interviewing"
        assert job["outcome"] == "offer"
        assert job["outcome_at"]

        # Back to undecided.
        assert db.set_job_outcome(job_id, "pending") is True
        job = db.get_job(job_id)
        assert job is not None
        assert job["outcome"] is None
        assert job["outcome_at"] is None

        assert db.set_job_outcome(job_id, "ghosted") is False  # not in OUTCOMES
    finally:
        db.close()


def test_rejecting_records_the_ending_too(tmp_path: Path) -> None:
    db = Database(tmp_path / "a.db")
    try:
        job_id = _job(db)
        db.set_job_action(job_id, "applied")
        db.set_job_action(job_id, "rejected")
        job = db.get_job(job_id)
        assert job is not None
        assert job["outcome"] == "rejected"
    finally:
        db.close()


def test_applying_without_any_cv_profile_does_not_fail(tmp_path: Path) -> None:
    """A brand-new install has no profile: the date is still worth recording."""
    db = Database(tmp_path / "a.db")
    try:
        job_id = _job(db)
        db.set_job_action(job_id, "applied")
        job = db.get_job(job_id)
        assert job is not None
        assert job["applied_at"]
        assert job["applied_profile_id"] is None
    finally:
        db.close()
