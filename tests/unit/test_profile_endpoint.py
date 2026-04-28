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


def test_patch_profile_syncs_preferred_roles_preference(
    client: TestClient, tmp_path: Path
) -> None:
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
    pid1 = _seed_profile(tmp_path, {"preferred_roles": ["A"]})
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
