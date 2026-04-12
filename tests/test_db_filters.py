from pathlib import Path

from app.db import Database


def test_db_job_filters(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    db = Database(db_path)

    job1, _, _ = db.upsert_job(
        {
            "titolo": "Junior QA Tester",
            "azienda": "ACME",
            "descrizione": "QA automation",
            "sede": "Torino",
            "fonte": "manual",
            "link": "https://example.com/1",
            "ricerca_usata": "qa",
            "modalita": "Torino",
        }
    )
    db.update_job_analysis(job1, {"punteggio": 8, "consiglio": "Candidati subito"})

    job2, _, _ = db.upsert_job(
        {
            "titolo": "Senior Developer",
            "azienda": "Beta",
            "descrizione": "Senior role",
            "sede": "Milano",
            "fonte": "manual",
            "link": "https://example.com/2",
            "ricerca_usata": "dev",
            "modalita": "Remote",
        }
    )
    db.update_job_analysis(job2, {"punteggio": 3, "consiglio": "Salta"})

    filtered = db.list_jobs(search_text="qa", min_score=7)
    assert len(filtered) == 1
    assert filtered[0]["id"] == job1

    db.set_job_action(job1, "applied", "test")
    applied = db.list_jobs(status="applied")
    assert len(applied) == 1
    assert applied[0]["id"] == job1

    db.close()
