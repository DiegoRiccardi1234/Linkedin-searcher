"""An offer nobody judged carries no score — and says so everywhere.

Until v1.7.9 every failure path produced a number anyway, by counting words the
posting shared with the CV. On a real scan that put seven postings no model had
ever read at 6/10 or above (a PAYROLL SPECIALIST at 6, a DIGITAL COMMUNICATION
SPECIALIST at 8), sitting at the top of the list next to genuine matches.

These tests pin the replacement: no score, no advice, no axes, a flag that says
why, and — crucially — every consumer downstream treating "no score" as absent
rather than as zero.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.scoring_schema import ANALYSIS_VERSION_KEY
from app.services import scanner_service as ss

_CV = "Laurea Triennale in Informatica, votazione 95/110. Python, React."
_JD = "Ruolo AI QA in team di prodotto, valutazione modelli e prompt. " * 12


class _BrokenPM:
    """A provider that is down: every scoring call raises."""

    def preview_scoring_model(self, _policy: Any) -> str:
        return "m"

    def clear_model_penalties(self, reason: str | None = None) -> None:
        pass

    def complete_json(self, *a: Any, **k: Any) -> Any:
        raise RuntimeError("429 rate-limited upstream")


def _unscored() -> dict[str, Any]:
    return ss.enforce_hard_requirements(
        ss._unscored_analysis("AI QA Engineer", "Acme", _JD, reason="429"),
        profile_markdown=_CV,
        descrizione=_JD,
        sede="Torino, Italy",
    )


# --- the analysis itself ------------------------------------------------------


def test_provider_failure_produces_no_score() -> None:
    res = ss.analyze_offer(_BrokenPM(), _CV, "AI QA Engineer", "Acme", _JD)
    assert res["punteggio"] is None
    assert res["consiglio"] == ""
    assert ss.FLAG_NOT_EVALUATED in res["blocchi"]
    assert ANALYSIS_VERSION_KEY not in res, "no version = re-scored on the next scan"


def test_unscored_has_no_invented_axes() -> None:
    """A radar drawn from invented axes is the most convincing lie of the lot."""
    res = _unscored()
    assert set(res["match_axes"]) == set(ss._MATCH_AXES_KEYS)
    assert all(value is None for value in res["match_axes"].values())


def test_unscored_keeps_the_facts_it_could_read() -> None:
    """Contract, work mode and required degree are read from the text, not guessed."""
    res = ss._unscored_analysis(
        "AI QA",
        "Acme",
        "Full remote, tempo indeterminato. Requisiti: laurea magistrale.",
        reason="429",
    )
    assert res["titolo_studio_richiesto"] == "Magistrale"
    assert res["smart_working"] == "Sì"
    assert res["contratto"] == "Dipendente"
    assert res["punteggio"] is None


def test_batch_slot_that_fails_is_unscored() -> None:
    """A batch that comes back unusable must not fall back to an invented score."""
    offers = [{"titolo": f"AI QA {i}", "azienda": "Co", "descrizione": _JD} for i in range(3)]
    out = ss.analyze_offers_batch(_BrokenPM(), _CV, offers)
    assert len(out) == 3
    assert all(o["punteggio"] is None for o in out)


# --- persistence and queries --------------------------------------------------


def test_unscored_persists_as_null_not_zero(tmp_path: Path) -> None:
    db = Database(tmp_path / "u.db")
    try:
        job_id, _, _ = db.upsert_job({"titolo": "AI QA", "azienda": "A", "link": "u1"})
        db.update_job_analysis(job_id, _unscored())
        listed = db.list_jobs(limit=10)[0]
        assert listed["punteggio_ai"] is None
        assert listed["consiglio"] == ""
        assert ss.FLAG_NOT_EVALUATED in listed["flags"]
        assert db.job_has_analysis(job_id) is False
    finally:
        db.close()


def _seed_pair(db: Database) -> None:
    """One judged offer at 3/10, one nobody judged."""
    low, _, _ = db.upsert_job({"titolo": "judged", "azienda": "A", "link": "j1"})
    db.update_job_analysis(
        low,
        ss.enforce_hard_requirements(
            {"punteggio": 3, "consiglio": "Salta"},
            profile_markdown=_CV,
            descrizione=_JD,
            sede="Torino, Italy",
        ),
    )
    none_id, _, _ = db.upsert_job({"titolo": "unjudged", "azienda": "A", "link": "j2"})
    db.update_job_analysis(none_id, _unscored())


def test_min_score_filter_excludes_unscored(tmp_path: Path) -> None:
    """Even ``min_score=0``: a quality threshold filters verdicts, and there is none."""
    db = Database(tmp_path / "m.db")
    try:
        _seed_pair(db)
        assert {j["titolo"] for j in db.list_jobs(min_score=0)} == {"judged"}
        assert {j["titolo"] for j in db.list_jobs()} == {"judged", "unjudged"}
    finally:
        db.close()


def test_unscored_sorts_below_a_bad_score(tmp_path: Path) -> None:
    """The whole point: postings nobody read must not sit above judged ones."""
    db = Database(tmp_path / "s.db")
    try:
        _seed_pair(db)
        assert [j["titolo"] for j in db.list_jobs()] == ["judged", "unjudged"]
    finally:
        db.close()


def test_recommendations_exclude_unscored(tmp_path: Path) -> None:
    db = Database(tmp_path / "r.db")
    try:
        _seed_pair(db)
        assert {j["titolo"] for j in db.get_recommended_jobs(limit=5)} == {"judged"}
    finally:
        db.close()


def test_analytics_counts_unscored_apart(tmp_path: Path) -> None:
    db = Database(tmp_path / "a.db")
    try:
        _seed_pair(db)
        stats = db.get_analytics()
        assert stats["unscored"] == 1
        assert stats["score_distribution"]["0"] == 0, "an unjudged job is not a zero"
        assert stats["score_distribution"]["3"] == 1
    finally:
        db.close()


# --- the re-score endpoint ----------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def _seed_unscored(tmp_path: Path) -> int:
    db = Database(tmp_path / "data" / "searcher.db")
    try:
        job_id, _, _ = db.upsert_job(
            {"titolo": "AI QA", "azienda": "A", "link": "x1", "descrizione": _JD}
        )
        db.update_job_analysis(job_id, _unscored())
        return job_id
    finally:
        db.close()


def test_analyze_endpoint_rescores_a_job(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _seed_unscored(tmp_path)
    import app.routers.jobs as jobs_router

    monkeypatch.setattr(
        jobs_router,
        "analyze_offer",
        lambda **k: ss.enforce_hard_requirements(
            {"punteggio": 7, "consiglio": "Valutabile"},
            profile_markdown=_CV,
            descrizione=_JD,
            sede="Torino, Italy",
        ),
    )
    res = client.post(f"/api/jobs/{job_id}/analyze")
    assert res.status_code == 200
    assert res.json()["evaluated"] is True
    assert res.json()["punteggio"] == 7
    assert client.get(f"/api/jobs/{job_id}").json()["job"]["punteggio_ai"] == 7


def test_analyze_endpoint_reports_a_failed_retry(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provider is still down: 200 with ``evaluated: false``, not a 502.

    The app did its part; saying "error" would send the user hunting for a bug
    in the wrong place.
    """
    job_id = _seed_unscored(tmp_path)
    import app.routers.jobs as jobs_router

    monkeypatch.setattr(jobs_router, "analyze_offer", lambda **k: _unscored())
    res = client.post(f"/api/jobs/{job_id}/analyze")
    assert res.status_code == 200
    assert res.json()["evaluated"] is False
    assert res.json()["punteggio"] is None


def test_analyze_endpoint_404_on_missing_job(client: TestClient) -> None:
    assert client.post("/api/jobs/99999/analyze").status_code == 404


def test_analyze_endpoint_412_without_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    for key in ("CEREBRAS_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        os.environ.pop(key, None)
    from app.main import create_app

    job_id = _seed_unscored(tmp_path)
    with TestClient(create_app(workspace_dir=tmp_path)) as tc:
        res = tc.post(f"/api/jobs/{job_id}/analyze")
    assert res.status_code == 412
