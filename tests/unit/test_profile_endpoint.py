"""Tests for the /api/profile and /api/profiles endpoints."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


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


def _seed_profile(tmp_path: Path, summary: dict) -> int:
    from app.db import Database

    db = Database(tmp_path / "data" / "searcher.db")
    pid = db.save_candidate_profile(
        source_name="cv.pdf",
        markdown="# Diego\nSenior dev",
        summary=summary,
    )
    db.set_active_profile(pid)
    db.close()
    return pid


def test_get_profile_empty_when_no_cv(client: TestClient) -> None:
    res = client.get("/api/profile")
    assert res.status_code == 200
    body = res.json()
    assert body["profile"] is None


def test_patch_profile_404_when_no_profile(client: TestClient) -> None:
    res = client.patch("/api/profile", json={"preferred_roles": ["X"]})
    assert res.status_code == 404
    assert res.json()["detail"] == "no_profile"


def test_get_profile_after_seed(client: TestClient, tmp_path: Path) -> None:
    _seed_profile(tmp_path, {"preferred_roles": ["Python Dev"], "skills": ["sql"]})
    res = client.get("/api/profile")
    body = res.json()
    assert body["profile"]["summary_json"]["preferred_roles"] == ["Python Dev"]
    assert body["profile"]["summary_json"]["skills"] == ["sql"]


def test_patch_profile_updates_roles_and_skills(client: TestClient, tmp_path: Path) -> None:
    _seed_profile(tmp_path, {"preferred_roles": ["Old"], "skills": ["x"]})
    res = client.patch(
        "/api/profile",
        json={"preferred_roles": ["QA", "Backend"], "skills": ["python", "fastapi"]},
    )
    assert res.status_code == 200
    summary = res.json()["profile"]["summary_json"]
    assert summary["preferred_roles"] == ["QA", "Backend"]
    assert summary["skills"] == ["python", "fastapi"]


def test_patch_profile_strips_blanks(client: TestClient, tmp_path: Path) -> None:
    _seed_profile(tmp_path, {"preferred_roles": ["A"]})
    res = client.patch(
        "/api/profile",
        json={"preferred_roles": ["", "  ", "QA", " "]},
    )
    summary = res.json()["profile"]["summary_json"]
    assert summary["preferred_roles"] == ["QA"]


def test_patch_profile_syncs_preferred_roles_preference(client: TestClient, tmp_path: Path) -> None:
    _seed_profile(tmp_path, {"preferred_roles": ["Old"]})
    client.patch("/api/profile", json={"preferred_roles": ["NewRole"]})

    from app.db import Database

    db = Database(tmp_path / "data" / "searcher.db")
    raw = db.get_preference("preferred_roles", "")
    db.close()
    assert json.loads(raw) == ["NewRole"]


def test_get_profiles_lists_all(client: TestClient, tmp_path: Path) -> None:
    pid1 = _seed_profile(tmp_path, {"preferred_roles": ["A"]})
    from app.db import Database

    db = Database(tmp_path / "data" / "searcher.db")
    pid2 = db.save_candidate_profile(source_name="cv2.pdf", markdown="# v2", summary={})
    db.close()
    res = client.get("/api/profiles")
    ids = [p["id"] for p in res.json()["profiles"]]
    assert pid1 in ids and pid2 in ids


def test_activate_profile_changes_active(client: TestClient, tmp_path: Path) -> None:
    _seed_profile(tmp_path, {"preferred_roles": ["A"]})
    from app.db import Database

    db = Database(tmp_path / "data" / "searcher.db")
    pid2 = db.save_candidate_profile(source_name="cv2.pdf", markdown="# v2", summary={"x": 1})
    db.close()

    res = client.post(f"/api/profiles/{pid2}/activate")
    assert res.status_code == 200
    assert res.json()["active_profile_id"] == pid2

    profile = client.get("/api/profile").json()["profile"]
    assert profile["id"] == pid2


def test_activate_unknown_profile_returns_404(client: TestClient) -> None:
    res = client.post("/api/profiles/9999/activate")
    assert res.status_code == 404


def test_delete_profile_removes_row(client: TestClient, tmp_path: Path) -> None:
    pid = _seed_profile(tmp_path, {"preferred_roles": ["X"]})
    res = client.delete(f"/api/profiles/{pid}")
    assert res.status_code == 200
    assert res.json()["deleted_id"] == pid
    listed = client.get("/api/profiles").json()["profiles"]
    assert all(p["id"] != pid for p in listed)


def test_delete_unknown_profile_returns_404(client: TestClient) -> None:
    res = client.delete("/api/profiles/9999")
    assert res.status_code == 404


def test_delete_active_profile_promotes_latest_remaining(
    client: TestClient, tmp_path: Path
) -> None:
    pid1 = _seed_profile(tmp_path, {"preferred_roles": ["A"]})
    from app.db import Database

    db = Database(tmp_path / "data" / "searcher.db")
    pid2 = db.save_candidate_profile(source_name="cv2.pdf", markdown="# v2", summary={})
    db.set_active_profile(pid2)
    db.close()

    res = client.delete(f"/api/profiles/{pid2}")
    assert res.status_code == 200
    assert res.json()["active_profile_id"] == str(pid1)


# ── the matching facts: writing one, and getting rid of it ───────────────────
# None of this was covered before, which is how an override of "0 years" outlived
# the CV that said half a year: emptying the box in the UI was a silent no-op.


def test_a_fractional_year_survives_the_round_trip(
    client: TestClient, tmp_path: Path
) -> None:
    """An int field answered 422 to "0.5", then floored it on the way in."""
    _seed_profile(tmp_path, {"years_experience": 3})
    res = client.patch("/api/profile", json={"years_experience": 0.5})
    assert res.status_code == 200, res.text
    facts = client.get("/api/profile/matching-facts").json()
    assert facts["years_experience"] == 0.5
    assert facts["sources"]["years_experience"] == "manuale"


def test_an_override_can_be_cleared_and_the_cv_takes_over(
    client: TestClient, tmp_path: Path
) -> None:
    """Present-and-null clears; the fact goes back to whatever the CV says."""
    _seed_profile(tmp_path, {"years_experience": 0.5})
    client.patch("/api/profile", json={"years_experience": 4})
    assert client.get("/api/profile/matching-facts").json()["sources"][
        "years_experience"
    ] == "manuale"

    res = client.patch("/api/profile", json={"years_experience": None})
    assert res.status_code == 200, res.text
    facts = client.get("/api/profile/matching-facts").json()
    assert facts["years_experience"] == 0.5, "the CV is back in charge"
    assert facts["sources"]["years_experience"] == "cv"


def test_a_payload_that_omits_a_fact_leaves_it_alone(
    client: TestClient, tmp_path: Path
) -> None:
    """Absent is not the same as null — the chat coach sends one key at a time."""
    _seed_profile(tmp_path, {"years_experience": 0.5})
    client.patch("/api/profile", json={"years_experience": 4})
    client.patch("/api/profile", json={"grade": 95})
    facts = client.get("/api/profile/matching-facts").json()
    assert facts["years_experience"] == 4, "untouched by a payload that never named it"
    assert facts["grade"] == 95


def test_agreeing_with_the_cv_does_not_turn_it_into_an_override(
    client: TestClient, tmp_path: Path
) -> None:
    """The rule the post-upload review card exists to honour.

    Confirming a value the CV supplied must leave it sourced to the CV. Writing
    it back as a manual correction would make it immune to the next upload —
    which is the defect this release fixed for years of experience, and
    re-creating it deliberately for another field would be worse.
    """
    _seed_profile(tmp_path, {"years_experience": 0.5, "education_level": "Triennale"})
    before = client.get("/api/profile/matching-facts").json()
    assert before["sources"]["years_experience"] == "cv"

    # The card sends only what the user actually changed. Agreeing with
    # everything therefore sends nothing at all.
    assert client.patch("/api/profile", json={}).status_code == 200

    after = client.get("/api/profile/matching-facts").json()
    assert after["years_experience"] == 0.5
    assert after["sources"]["years_experience"] == "cv", "confirming is not overriding"


def test_the_three_facts_with_no_ui_can_now_be_answered(
    client: TestClient, tmp_path: Path
) -> None:
    """Degree subject, licence and register had an API and no field anywhere."""
    _seed_profile(tmp_path, {"years_experience": 1})
    res = client.patch(
        "/api/profile",
        json={
            "degree_fields": ["informatica"],
            "driving_licence": False,
            "protected_category": False,
        },
    )
    assert res.status_code == 200, res.text
    facts = client.get("/api/profile/matching-facts").json()
    assert facts["degree_fields"] == ["informatica"]
    assert facts["driving_licence"] is False
    assert facts["protected_category"] is False
    for key in ("degree_fields", "driving_licence", "protected_category"):
        assert facts["sources"][key] == "manuale"
        assert key not in facts["missing"]
