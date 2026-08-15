"""Tests for skill-gap aggregation + endpoint."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.services.skill_gap import compute_skill_gap, suggest_learning


def _seed(db: Database) -> None:
    j1, _, _ = db.upsert_job({"titolo": "A", "azienda": "X", "link": "l1"})
    db.update_job_analysis(
        j1, {"skills_match": {"hai": ["Python"], "mancano": ["Docker", "Kubernetes"]}}
    )
    j2, _, _ = db.upsert_job({"titolo": "B", "azienda": "Y", "link": "l2"})
    db.update_job_analysis(j2, {"skills_match": {"hai": [], "mancano": ["docker", "AWS"]}})


def test_compute_skill_gap_aggregates_and_excludes_owned(tmp_path: Path) -> None:
    db = Database(tmp_path / "s.db")
    try:
        # Profile already has AWS -> it must not be reported as a gap.
        pid = db.save_candidate_profile(
            source_name="cv", markdown="cv", summary={"skills": ["AWS"]}
        )
        db.set_active_profile(pid)
        _seed(db)

        result = compute_skill_gap(db)
        gaps = {g["skill"].lower(): g["count"] for g in result["gaps"]}
        assert gaps["docker"] == 2  # case-insensitive merge
        assert gaps["kubernetes"] == 1
        assert "aws" not in gaps  # owned skill excluded
        assert result["analyzed_jobs"] == 2
        assert result["max_count"] == 2
        # Sorted by count desc -> docker first.
        assert result["gaps"][0]["skill"].lower() == "docker"
    finally:
        db.close()


def test_a_blocking_reason_is_not_a_skill_to_learn(tmp_path: Path) -> None:
    """The panel says "learning these unlocks more matches". Some of it could not.

    The deterministic blockers are copied into ``mancano`` so the offer's own
    page can say why it is capped, and this aggregate counted them as skills.
    On a real archive the top five of twelve rows were "Richiede 3+ anni di
    esperienza (il profilo ne dichiara 0)" at 99, the two-year version at 40,
    the master's at 27 and two unreachable cities at 16 and 15 — with Power BI,
    the first thing on the list anyone could actually go and learn, at 7.
    """
    db = Database(tmp_path / "b.db")
    try:
        reason = "Richiede 3+ anni di esperienza (il profilo ne dichiara 0)"
        for n in (1, 2, 3):
            jid, _, _ = db.upsert_job({"titolo": f"A{n}", "azienda": "X", "link": f"l{n}"})
            db.update_job_analysis(
                jid,
                {
                    "blocchi": ["esperienza_richiesta"],
                    "blocchi_dettaglio": {"esperienza_richiesta": reason},
                    "skills_match": {"hai": [], "mancano": [reason, "Power BI"]},
                },
            )
        gaps = {g["skill"]: g["count"] for g in compute_skill_gap(db)["gaps"]}
        assert gaps == {"Power BI": 3}, "the constraint is reported, not offered as homework"
    finally:
        db.close()


def test_compute_skill_gap_empty_when_no_analysis(tmp_path: Path) -> None:
    db = Database(tmp_path / "s.db")
    try:
        db.upsert_job({"titolo": "A", "azienda": "X", "link": "l1"})
        result = compute_skill_gap(db)
        assert result["gaps"] == []
        assert result["analyzed_jobs"] == 0
    finally:
        db.close()


class _FakeProvider:
    def __init__(self, response: object) -> None:
        self._response = response
        self.last_prompt = ""

    def complete_json(self, *, prompt: str, max_tokens: int) -> object:
        self.last_prompt = prompt
        return self._response


def test_suggest_learning_normalizes_and_filters() -> None:
    resp = {
        "Docker": [
            {"title": "Docker Deep Dive", "type": "book", "why": "Solid fundamentals"},
            {"nonsense": 1},  # no title/why -> dropped
        ],
        "Kubernetes": "not a list",  # dropped
    }
    fake = _FakeProvider(resp)
    out = suggest_learning(fake, [{"skill": "Docker"}, {"skill": "Kubernetes"}], language="it")
    assert list(out["suggestions"].keys()) == ["docker"]  # lowercased key, malformed dropped
    assert len(out["suggestions"]["docker"]) == 1
    assert out["suggestions"]["docker"][0]["title"] == "Docker Deep Dive"
    assert "Italian" in fake.last_prompt  # language instruction injected


def test_suggest_learning_empty_gaps_skips_llm() -> None:
    fake = _FakeProvider({"Docker": [{"title": "x", "why": "y"}]})
    assert suggest_learning(fake, []) == {"suggestions": {}}
    assert fake.last_prompt == ""  # never called the provider


def test_suggest_learning_degrades_on_error() -> None:
    class _Boom:
        def complete_json(self, *, prompt: str, max_tokens: int) -> object:
            raise RuntimeError("no provider")

    assert suggest_learning(_Boom(), [{"skill": "X"}]) == {"suggestions": {}}


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    for key in ("CEREBRAS_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(key, None)
    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def test_skill_gap_endpoint_ok(client: TestClient, tmp_path: Path) -> None:
    db = Database(tmp_path / "data" / "searcher.db")
    _seed(db)
    db.close()
    resp = client.get("/api/skill-gap")
    assert resp.status_code == 200
    assert resp.json()["analyzed_jobs"] == 2


def test_skill_gap_endpoint_403_when_disabled(client: TestClient) -> None:
    client.post("/api/preferences", json={"key": "feature_skill_gap", "value": "0"})
    assert client.get("/api/skill-gap").status_code == 403
