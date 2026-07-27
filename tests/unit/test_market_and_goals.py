"""Market context from the app's own scans, and the goals suggester on top of it.

The coach was asked market questions with no market data: it knew the CV and a
few job rows, not which companies keep hiring, which search terms return
anything, or what the postings pay — although every scan had stored exactly
that.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.routers.profile import _GOAL_FIELDS, _parse_labelled_lines, _strip_labelled_lines
from app.services import market_snapshot


def _seed(db: Database, titolo: str, azienda: str, term: str, score: int, missing: list[str]):
    job_id, _, _ = db.upsert_job(
        {
            "titolo": titolo,
            "azienda": azienda,
            "sede": "Torino, Italy",
            "link": f"https://x/{titolo}",
            "ricerca_usata": term,
        }
    )
    db.update_job_analysis(
        job_id,
        {
            "punteggio": score,
            "skills_match": {"hai": ["python"], "mancano": missing},
            "ral_stimata": "30.000€-35.000€",
            "tipo_ingaggio": "Dipendente",
        },
    )


def test_snapshot_summarises_the_recent_scans(tmp_path: Path) -> None:
    db = Database(tmp_path / "m.db")
    try:
        _seed(db, "AI QA Engineer", "Nordic Labs", "AI QA", 8, ["kubernetes"])
        _seed(db, "AI QA Analyst", "Nordic Labs", "AI QA", 7, ["kubernetes"])
        _seed(db, "Data Entry", "Acme", "data entry", 3, ["excel"])

        data = market_snapshot.collect(db)
        assert data["postings"] == 3
        assert data["top_companies"][0] == ("Nordic Labs", 2)
        # "AI QA" produced two good results, "data entry" none: that is the
        # difference between a term worth keeping and one worth dropping.
        by_term = {term: good for term, good, _total in data["productive_terms"]}
        assert by_term["AI QA"] == 2
        assert by_term["data entry"] == 0
        assert ("kubernetes", 2) in data["missing_skills"]
    finally:
        db.close()


def test_snapshot_block_is_empty_without_data(tmp_path: Path) -> None:
    """A brand-new install must not inject an empty "market" section."""
    db = Database(tmp_path / "m.db")
    try:
        assert market_snapshot.as_prompt_block(db) == ""
    finally:
        db.close()


def test_snapshot_block_is_short_and_readable(tmp_path: Path) -> None:
    db = Database(tmp_path / "m.db")
    try:
        for i in range(12):
            _seed(db, f"AI QA {i}", f"Co{i % 3}", "AI QA", 7, ["kubernetes"])
        block = market_snapshot.as_prompt_block(db)
        assert "AI QA" in block
        # It rides along with the CV and a posting on small models.
        assert len(block) < 1200
    finally:
        db.close()


def test_goal_lines_are_parsed_and_stripped() -> None:
    content = (
        "SETTORE=AI/Data\n"
        "OBIETTIVO=Entrare come AI QA in un team che valuta modelli\n"
        "SENIORITY=junior\n"
        "MODALITA=ibrido\n\n"
        "Perche': i tuoi scan mostrano che..."
    )
    values = _parse_labelled_lines(content, _GOAL_FIELDS)
    assert values == {
        "sector": "AI/Data",
        "goal": "Entrare come AI QA in un team che valuta modelli",
        "seniority": "junior",
        "work_mode": "ibrido",
    }
    rationale = _strip_labelled_lines(content, _GOAL_FIELDS)
    assert rationale.startswith("Perche'")
    assert "SETTORE=" not in rationale


def test_goal_lines_missing_entirely() -> None:
    values = _parse_labelled_lines("Just some prose, no fields.", _GOAL_FIELDS)
    assert not any(values.values())


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


def test_cached_goals_endpoint_is_empty_without_a_profile(client: TestClient) -> None:
    body = client.get("/api/profile/goals-suggest").json()
    assert body == {"sector": "", "goal": "", "seniority": "", "work_mode": "", "rationale": ""}


def test_cached_goals_are_scoped_to_the_profile(client: TestClient, tmp_path: Path) -> None:
    """A suggestion made for one CV must not resurface under another."""
    db = Database(tmp_path / "data" / "searcher.db")
    try:
        pid = db.save_candidate_profile(source_name="cv.md", markdown="text", summary={}, name="A")
        db.set_active_profile(pid)
        db.set_preference(
            "goals_suggestion_cache",
            json.dumps({"profile_id": pid + 99, "sector": "Ghost", "rationale": "stale"}),
        )
    finally:
        db.close()
    assert client.get("/api/profile/goals-suggest").json()["sector"] == ""
