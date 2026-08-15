"""The archive's slices, and the cap that used to decide what you could see.

The bug these tests exist for was measured on a real archive of 498 offers:
``status=open&applicable_only`` returned **123** rows at ``limit=250`` and
**174** at ``limit=500``, because the flag filter ran in Python AFTER the SQL
LIMIT. The row cap is meant to bound how much is rendered, not to remove
matching rows from the answer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import JOB_BUCKETS, Database

BLOCKING = ("geo_non_ue", "voto_minimo", "sede_non_raggiungibile")


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "searcher.db")
    try:
        yield database
    finally:
        database.close()


def _job(db: Database, titolo: str, *, score: int | None = None, blocchi: list[str] | None = None):
    job_id, _new, _status = db.upsert_job(
        {
            "titolo": titolo,
            "azienda": "Acme",
            "descrizione": "Una descrizione qualsiasi, abbastanza lunga da contare.",
            "sede": "Torino",
            "fonte": "linkedin",
            "link": f"https://example.com/{titolo}",
            "modalita": "Ibrido",
        }
    )
    if score is not None or blocchi is not None:
        db.update_job_analysis(
            job_id, {"punteggio": score, "consiglio": "", "blocchi": blocchi or []}
        )
    return job_id


def test_the_cap_no_longer_eats_rows_the_filter_would_have_kept(db: Database) -> None:
    # The blocked offers score higher, so they sort first: a small cap fetches
    # only those, and the old post-filter then returned almost nothing.
    for i in range(10):
        _job(db, f"bloccata-{i}", score=9, blocchi=["geo_non_ue"])
    for i in range(5):
        _job(db, f"pulita-{i}", score=5)

    capped = db.list_jobs(bucket="to_review", blocking_flags=BLOCKING, limit=10)
    roomy = db.list_jobs(bucket="to_review", blocking_flags=BLOCKING, limit=500)

    assert [j["id"] for j in capped] == [j["id"] for j in roomy]
    assert len(capped) == 5


def test_total_does_not_depend_on_the_limit(db: Database) -> None:
    for i in range(30):
        _job(db, f"offerta-{i}", score=7)
    assert db.count_jobs(bucket="to_review") == 30
    assert len(db.list_jobs(bucket="to_review", limit=5)) == 5
    assert db.count_jobs(bucket="to_review") == 30


def test_an_offer_without_an_analysis_is_never_filtered_out(db: Database) -> None:
    """Applications recovered from the mailbox have no analysis at all."""
    _job(db, "dalla-posta")
    kept = db.list_jobs(bucket="to_review", blocking_flags=BLOCKING, limit=50)
    assert [j["titolo"] for j in kept] == ["dalla-posta"]


def test_a_malformed_analysis_does_not_take_the_whole_list_down(db: Database) -> None:
    """One unparseable row used to raise "malformed JSON" for every request."""
    job_id = _job(db, "rotta", score=6)
    db.conn.execute("UPDATE jobs SET analysis_json = ? WHERE id = ?", ("{not json", job_id))
    db.conn.commit()
    _job(db, "sana", score=6)

    kept = db.list_jobs(bucket="to_review", blocking_flags=BLOCKING, limit=50)
    assert {j["titolo"] for j in kept} == {"rotta", "sana"}


def test_to_review_means_neither_applied_nor_discarded(db: Database) -> None:
    open_id = _job(db, "aperta", score=8)
    applied_id = _job(db, "candidata", score=8)
    rejected_id = _job(db, "scartata", score=8)
    archived_id = _job(db, "archiviata", score=8)
    db.set_job_action(applied_id, "applied")
    db.set_job_action(rejected_id, "rejected")
    db.set_job_action(archived_id, "archived")

    assert [j["id"] for j in db.list_jobs(bucket="to_review", limit=50)] == [open_id]
    assert [j["id"] for j in db.list_jobs(bucket="applied", limit=50)] == [applied_id]
    assert [j["id"] for j in db.list_jobs(bucket="rejected", limit=50)] == [rejected_id]
    assert [j["id"] for j in db.list_jobs(bucket="archived", limit=50)] == [archived_id]
    assert len(db.list_jobs(bucket="all", limit=50)) == 4


def test_a_reopened_application_stays_under_applications(db: Database) -> None:
    """``applied_at`` survives a reopen on purpose, and that is what decides.

    Keyed on the status alone this row fell into no bucket whatsoever: open, so
    not an application; applied to, so not left to decide.
    """
    job_id = _job(db, "ricandidabile", score=8)
    db.set_job_action(job_id, "applied")
    db.set_job_action(job_id, "reopened")

    row = db.conn.execute("SELECT status, applied_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "open" and row["applied_at"]
    assert db.list_jobs(bucket="to_review", limit=50) == []
    assert [j["id"] for j in db.list_jobs(bucket="applied", limit=50)] == [job_id]
    assert db.count_jobs(bucket="to_review") == 0


def test_the_buckets_partition_the_archive(db: Database) -> None:
    """Every row in exactly one slice, so the tab counts can be read as a total."""
    _job(db, "aperta", score=8)
    applied_id = _job(db, "candidata", score=8)
    db.set_job_action(applied_id, "applied")
    reopened_id = _job(db, "riaperta", score=8)
    db.set_job_action(reopened_id, "applied")
    db.set_job_action(reopened_id, "reopened")
    rejected_id = _job(db, "scartata", score=8)
    db.set_job_action(rejected_id, "rejected")
    archived_id = _job(db, "archiviata", score=8)
    db.set_job_action(archived_id, "archived")

    counts = db.bucket_counts()
    slices = [name for name in JOB_BUCKETS if name != "all"]
    assert sum(counts[name] for name in slices) == counts["all"] == 5


def test_open_offers_from_shares_the_to_review_predicate(db: Database) -> None:
    open_id = _job(db, "aperta-acme", score=8)
    applied_id = _job(db, "candidata-acme", score=8)
    db.set_job_action(applied_id, "applied")
    assert db.open_offers_from("Acme") == [open_id]


def test_bucket_counts_add_up(db: Database) -> None:
    _job(db, "aperta", score=8)
    applied_id = _job(db, "candidata", score=8)
    db.set_job_action(applied_id, "applied")
    interviewing_id = _job(db, "colloquio", score=8)
    db.set_job_action(interviewing_id, "interviewing")

    counts = db.bucket_counts()
    assert set(counts) == set(JOB_BUCKETS)
    assert counts["all"] == 3
    assert counts["to_review"] == 1
    # "Candidate" absorbs interviewing, or those rows belong to no slice.
    assert counts["applied"] == 2
    assert counts["rejected"] == 0


def test_bucket_counts_follow_the_other_filters(db: Database) -> None:
    _job(db, "python-dev", score=8)
    _job(db, "cuoco", score=8)
    counts = db.bucket_counts(search_text="python")
    assert counts["all"] == 1 and counts["to_review"] == 1


def test_the_python_fallback_answers_the_same_thing(db: Database) -> None:
    """A SQLite without JSON1 must degrade, not lie."""
    for i in range(6):
        _job(db, f"bloccata-{i}", score=9, blocchi=["voto_minimo"])
    for i in range(3):
        _job(db, f"pulita-{i}", score=4)

    with_json1 = [j["id"] for j in db.list_jobs(blocking_flags=BLOCKING, limit=5)]
    db._has_json1 = False
    without = [j["id"] for j in db.list_jobs(blocking_flags=BLOCKING, limit=5)]

    assert with_json1 == without
    assert len(without) == 3
    assert db.count_jobs(blocking_flags=BLOCKING) == 3


def test_flags_still_reach_the_caller(db: Database) -> None:
    _job(db, "bloccata", score=3, blocchi=["geo_non_ue"])
    (job,) = db.list_jobs(limit=5)
    assert job["flags"] == ["geo_non_ue"]
    assert json.loads(job["analysis_json"])["blocchi"] == ["geo_non_ue"]
