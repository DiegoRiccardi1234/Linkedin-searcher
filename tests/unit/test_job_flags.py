"""Why a score is what it is, as data the UI can render and filter on.

The deterministic checks capped a score and said nothing machine-readable: the
reason was a sentence glued to the front of the weakness line, so "you cannot
legally take this job" reached the user as a bare 3/10 with no badge, no filter
and no way to tell it apart from a plain bad match.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.services import scanner_service as ss

_CV = "Laurea Triennale in Informatica, votazione 95/110. Python, React."
_JD = "Ruolo AI QA in team di prodotto, valutazione modelli. " * 12


def test_geo_block_is_flagged() -> None:
    out = ss.enforce_hard_requirements(
        {"punteggio": 9, "consiglio": "Candidati subito"},
        profile_markdown=_CV,
        descrizione=_JD,
        sede="San Francisco, CA",
    )
    assert ss.FLAG_GEO_BLOCKED in out["blocchi"]
    assert out["punteggio"] <= 3


def test_grade_block_is_flagged() -> None:
    out = ss.enforce_hard_requirements(
        {"punteggio": 9},
        profile_markdown=_CV,
        descrizione=_JD + " Richiesta votazione minima 102/110.",
        sede="Torino, Italy",
    )
    assert ss.FLAG_GRADE_BLOCKED in out["blocchi"]


def test_gig_work_is_flagged_but_not_blocking() -> None:
    out = ss.enforce_hard_requirements(
        {"punteggio": 7},
        profile_markdown=_CV,
        descrizione=_JD + " Paid per task, no minimum hours.",
        sede="Remote",
        azienda="Toloka",
    )
    assert ss.FLAG_GIG in out["blocchi"]
    assert ss.FLAG_GIG not in ss.BLOCKING_FLAGS
    assert out["punteggio"] == 7  # a flag, never a cap


def test_unscored_analysis_says_so() -> None:
    out = ss.enforce_hard_requirements(
        ss._unscored_analysis("AI QA", "Acme", _JD, reason="provider down"),
        profile_markdown=_CV,
        descrizione=_JD,
        sede="Torino, Italy",
    )
    assert ss.FLAG_NOT_EVALUATED in out["blocchi"]
    assert ss.FLAG_NOT_EVALUATED not in ss.BLOCKING_FLAGS  # unjudged != inapplicable
    assert out["punteggio"] is None


def test_hard_blocked_analysis_is_scored_not_unevaluated() -> None:
    """A blocker is a verdict the app computes itself: it keeps its cap of 3."""
    out = ss.enforce_hard_requirements(
        ss._blocked_analysis(_CV, "AI QA", "Acme", _JD, "sede fuori UE"),
        profile_markdown=_CV,
        descrizione=_JD,
        sede="Austin, TX",
    )
    assert ss.FLAG_HEURISTIC in out["blocchi"]
    assert ss.FLAG_NOT_EVALUATED not in out["blocchi"]
    assert out["punteggio"] == 3


def test_clean_offer_has_no_flags() -> None:
    out = ss.enforce_hard_requirements(
        {"punteggio": 8},
        profile_markdown=_CV,
        descrizione=_JD,
        sede="Torino, Italy",
    )
    assert out["blocchi"] == []


def test_flags_survive_the_round_trip_to_the_job_list(tmp_path: Path) -> None:
    db = Database(tmp_path / "f.db")
    try:
        job_id, _, _ = db.upsert_job({"titolo": "T", "azienda": "A", "link": "l1"})
        analysis = ss.enforce_hard_requirements(
            {"punteggio": 9},
            profile_markdown=_CV,
            descrizione=_JD,
            sede="Austin, TX",
        )
        db.update_job_analysis(job_id, analysis)
        listed = db.list_jobs(limit=10)[0]
        assert ss.FLAG_GEO_BLOCKED in listed["flags"]
    finally:
        db.close()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    for key in ("CEREBRAS_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(key, None)
    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def _seed(tmp_path: Path, titolo: str, sede: str) -> None:
    """Insert directly in the workspace DB (WAL: visible to the running app)."""
    db = Database(tmp_path / "data" / "searcher.db")
    try:
        job_id, _, _ = db.upsert_job(
            {"titolo": titolo, "azienda": "A", "link": titolo, "sede": sede}
        )
        db.update_job_analysis(
            job_id,
            ss.enforce_hard_requirements(
                {"punteggio": 9}, profile_markdown=_CV, descrizione=_JD, sede=sede
            ),
        )
    finally:
        db.close()


def test_applicable_only_hides_blocked_jobs(client: TestClient, tmp_path: Path) -> None:
    _seed(tmp_path, "usa-job", "Austin, TX")
    _seed(tmp_path, "eu-job", "Torino, Italy")

    everything = client.get("/api/jobs").json()["jobs"]
    assert {j["titolo"] for j in everything} == {"usa-job", "eu-job"}

    filtered = client.get("/api/jobs?applicable_only=true").json()["jobs"]
    assert {j["titolo"] for j in filtered} == {"eu-job"}


def test_job_list_does_not_ship_the_whole_posting(client: TestClient, tmp_path: Path) -> None:
    """A full posting is ~5k chars; 200 of them on every refresh is megabytes
    the list view never renders."""
    _seed(tmp_path, "eu-job", "Torino, Italy")
    job = client.get("/api/jobs").json()["jobs"][0]
    assert "descrizione" not in job
    assert "analysis_json" not in job
    # …but the detail endpoint still serves everything.
    detail = client.get(f"/api/jobs/{job['id']}").json()["job"]
    assert "analysis" in detail


def test_the_row_cap_never_drops_an_application(tmp_path) -> None:
    """The cap is about how many OFFERS to show, not how much of your history.

    Unscored rows sort last, and an application recovered from the mailbox
    carries no score by design — there is no posting to judge. On the real
    archive that put 73 of 97 applications past the 250-row cap and the kanban's
    "Applied" column read 23.
    """
    from app.db import Database

    db = Database(tmp_path / "cap.db")
    try:
        # More scored offers than the cap, so the applications sort past it.
        for n in range(6):
            jid, _, _ = db.upsert_job(
                {"titolo": f"Offerta {n}", "azienda": "ACME", "link": f"https://x/{n}"}
            )
            db.update_job_analysis(jid, {"punteggio": 9, "consiglio": "Candidati subito"})
        mail_id = db.add_application_from_mail(
            company="Kirey", applied_at="2026-07-16T09:00:00+00:00",
            message_id="<a@b>", rule="import", role="GEN AI ENGINEER",
        )
        assert mail_id is not None

        capped = db.list_jobs(limit=3)
        assert len(capped) == 4, "three offers, plus the application the cap would have eaten"
        assert mail_id in {j["id"] for j in capped}
        assert capped[-1]["id"] == mail_id, "appended where it sorted anyway"
        # And no duplicate when it fits inside the cap on its own.
        roomy = db.list_jobs(limit=200)
        assert [j["id"] for j in roomy].count(mail_id) == 1
    finally:
        db.close()
