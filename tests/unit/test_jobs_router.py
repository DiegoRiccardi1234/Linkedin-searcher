"""Router coverage for app/routers/jobs.py (527 LOC, 23 endpoints, previously
only exercised indirectly). CRUD, actions/timeline, favorite, note, reminder,
manual add, exports — through the real FastAPI app with the real DB."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def _seed_job(tmp_path: Path, **overrides) -> int:
    """Insert a job directly in the workspace DB (WAL: visible to the app)."""
    from app.db import Database

    payload = {
        "titolo": "AI QA Analyst",
        "azienda": "Acme",
        "descrizione": "Valutazione modelli LLM, test e annotazione dati.",
        "sede": "Torino",
        "fonte": "linkedin",
        "link": "https://example.com/job/1",
        "modalita": "Hybrid",
    }
    payload.update(overrides)
    db = Database(tmp_path / "data" / "searcher.db")
    try:
        job_id, _new, _status = db.upsert_job(payload)
        analysis = overrides.get("_analysis")
        if analysis:
            db.update_job_analysis(job_id, analysis)
        return job_id
    finally:
        db.close()


# --- GET /api/jobs -----------------------------------------------------------


def test_list_jobs_empty(client: TestClient) -> None:
    assert client.get("/api/jobs").json() == {"jobs": []}


def test_list_jobs_filters(client: TestClient, tmp_path: Path) -> None:
    jid = _seed_job(tmp_path, _analysis={"punteggio": 8})
    _seed_job(
        tmp_path,
        titolo="Cuoco",
        azienda="Ristorante",
        link="https://example.com/job/2",
        _analysis={"punteggio": 3},
    )

    assert len(client.get("/api/jobs").json()["jobs"]) == 2
    assert len(client.get("/api/jobs", params={"min_score": 5}).json()["jobs"]) == 1
    assert len(client.get("/api/jobs", params={"search_text": "cuoco"}).json()["jobs"]) == 1
    assert client.get("/api/jobs", params={"status": "applied"}).json()["jobs"] == []

    client.post(f"/api/jobs/{jid}/action", json={"action": "applied"})
    applied = client.get("/api/jobs", params={"status": "applied"}).json()["jobs"]
    assert [j["id"] for j in applied] == [jid]


def test_list_jobs_validates_query_bounds(client: TestClient) -> None:
    assert client.get("/api/jobs", params={"limit": 0}).status_code == 422
    assert client.get("/api/jobs", params={"min_score": 11}).status_code == 422


# --- GET /api/jobs/{id} ------------------------------------------------------


def test_get_job_detail_and_404(client: TestClient, tmp_path: Path) -> None:
    assert client.get("/api/jobs/12345").status_code == 404
    jid = _seed_job(tmp_path, _analysis={"punteggio": 7, "riassunto": "ok"})
    body = client.get(f"/api/jobs/{jid}").json()
    assert body["job"]["id"] == jid
    assert body["job"]["analysis"]["punteggio"] == 7
    assert "recruiter" in body


# --- actions / timeline / note ----------------------------------------------


def test_action_changes_status_and_timeline(client: TestClient, tmp_path: Path) -> None:
    jid = _seed_job(tmp_path)
    assert client.post(f"/api/jobs/{jid}/action", json={"action": "applied"}).status_code == 200
    assert client.get(f"/api/jobs/{jid}").json()["job"]["status"] == "applied"
    actions = client.get(f"/api/jobs/{jid}/timeline").json()["actions"]
    assert [a["action"] for a in actions] == ["applied"]


def test_action_archived_supported(client: TestClient, tmp_path: Path) -> None:
    jid = _seed_job(tmp_path)
    assert client.post(f"/api/jobs/{jid}/action", json={"action": "archived"}).status_code == 200
    assert client.get(f"/api/jobs/{jid}").json()["job"]["status"] == "archived"


def test_action_rejects_unknown_and_missing_job(client: TestClient, tmp_path: Path) -> None:
    jid = _seed_job(tmp_path)
    assert client.post(f"/api/jobs/{jid}/action", json={"action": "yolo"}).status_code == 422
    assert client.post("/api/jobs/999/action", json={"action": "applied"}).status_code == 404


def test_note_added_to_timeline_without_status_change(client: TestClient, tmp_path: Path) -> None:
    jid = _seed_job(tmp_path)
    assert client.post(f"/api/jobs/{jid}/note", json={"notes": "chiamare HR"}).status_code == 200
    assert client.get(f"/api/jobs/{jid}").json()["job"]["status"] == "open"
    actions = client.get(f"/api/jobs/{jid}/timeline").json()["actions"]
    assert actions[-1]["action"] == "note" and actions[-1]["notes"] == "chiamare HR"


def test_empty_note_rejected(client: TestClient, tmp_path: Path) -> None:
    jid = _seed_job(tmp_path)
    assert client.post(f"/api/jobs/{jid}/note", json={"notes": "   "}).status_code == 400


# --- reminder ----------------------------------------------------------------


def test_reminder_set_list_clear(client: TestClient, tmp_path: Path) -> None:
    jid = _seed_job(tmp_path)
    resp = client.post(
        f"/api/jobs/{jid}/reminder",
        json={"reminder_at": "2020-01-01T09:00:00", "note": "follow-up"},
    )
    assert resp.status_code == 200
    body = client.get("/api/reminders").json()
    mine = [r for r in body["reminders"] if r["job_id"] == jid]
    assert mine and mine[0]["overdue"] is True and mine[0]["note"] == "follow-up"
    assert client.delete(f"/api/jobs/{jid}/reminder").status_code == 200
    body_after = client.get("/api/reminders").json()
    assert not any(r["job_id"] == jid for r in body_after["reminders"])


def test_reminder_404_on_missing_job(client: TestClient) -> None:
    assert (
        client.post("/api/jobs/999/reminder", json={"reminder_at": "2030-01-01T09:00:00"})
    ).status_code == 404
    assert client.delete("/api/jobs/999/reminder").status_code == 404


# --- favorite ----------------------------------------------------------------


def test_favorite_roundtrip(client: TestClient, tmp_path: Path) -> None:
    jid = _seed_job(tmp_path)
    assert client.post(f"/api/jobs/{jid}/favorite", json={"is_favorite": True}).status_code == 200
    favs = client.get("/api/jobs", params={"only_favorites": True}).json()["jobs"]
    assert [j["id"] for j in favs] == [jid]
    client.post(f"/api/jobs/{jid}/favorite", json={"is_favorite": False})
    assert client.get("/api/jobs", params={"only_favorites": True}).json()["jobs"] == []


# --- delete ------------------------------------------------------------------


def test_delete_job_and_404_on_second_delete(client: TestClient, tmp_path: Path) -> None:
    jid = _seed_job(tmp_path)
    client.post(f"/api/jobs/{jid}/action", json={"action": "applied"})  # child row
    assert client.delete(f"/api/jobs/{jid}").json() == {"ok": True, "deleted_id": jid}
    assert client.delete(f"/api/jobs/{jid}").status_code == 404
    assert client.get(f"/api/jobs/{jid}/timeline").json()["actions"] == []  # children gone


def test_delete_all_jobs_reports_count(client: TestClient, tmp_path: Path) -> None:
    _seed_job(tmp_path)
    _seed_job(tmp_path, titolo="Altro", azienda="Beta", link="https://example.com/job/2")
    assert client.delete("/api/jobs").json() == {"ok": True, "deleted": 2}
    assert client.get("/api/jobs").json() == {"jobs": []}


# --- manual add --------------------------------------------------------------


def test_manual_add_scores_and_persists(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.jobs.analyze_offer", lambda **k: {"punteggio": 6, "riassunto": "manuale"}
    )
    resp = client.post(
        "/api/jobs/manual",
        json={"titolo": "Manual QA", "azienda": "Acme", "descrizione": "testo annuncio"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["analysis"]["punteggio"] == 6
    job = client.get(f"/api/jobs/{body['job_id']}").json()["job"]
    assert job["analysis"]["riassunto"] == "manuale"
    assert job["fonte"] == "manual" or job["titolo"] == "Manual QA"


# --- exports -----------------------------------------------------------------


def test_export_applications_csv_and_json(client: TestClient, tmp_path: Path) -> None:
    jid = _seed_job(tmp_path)
    client.post(f"/api/jobs/{jid}/action", json={"action": "applied"})

    csv_resp = client.get("/api/applications/export")
    assert csv_resp.status_code == 200
    assert "attachment" in csv_resp.headers["content-disposition"]
    assert "AI QA Analyst" in csv_resp.text

    json_resp = client.get("/api/applications/export", params={"format": "json"})
    assert json_resp.status_code == 200
    assert json_resp.json()[0]["title"] == "AI QA Analyst"


def test_export_csv_all_jobs(client: TestClient, tmp_path: Path) -> None:
    _seed_job(tmp_path)
    resp = client.get("/api/export/csv")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert "AI QA Analyst" in resp.text


# --- watchlist / outcome / score feedback (v1.7.8) ---------------------------


def test_watchlist_round_trip(client: TestClient) -> None:
    assert client.get("/api/watchlist").json()["companies"] == []

    added = client.post("/api/watchlist", json={"name": "RWS Group", "note": "Torino"})
    assert added.status_code == 200
    company = added.json()["companies"][0]
    assert company["canonical"] == "rws"

    # Suggestions drop what is already followed, so the chips never re-offer it.
    listing = client.get("/api/watchlist").json()
    assert "RWS Group" not in listing["suggestions"]
    assert listing["enabled"] is False  # following ≠ scanning for it

    paused = client.post(f"/api/watchlist/{company['id']}/active", json={"active": False})
    assert paused.json()["companies"][0]["active"] == 0

    assert client.delete(f"/api/watchlist/{company['id']}").status_code == 200
    assert client.get("/api/watchlist").json()["companies"] == []
    assert client.delete(f"/api/watchlist/{company['id']}").status_code == 404
    assert client.post("/api/watchlist", json={"name": "S.r.l."}).status_code == 400


def test_watchlist_toggle_is_a_writable_preference(client: TestClient) -> None:
    """The scan reads this preference; the security allowlist has to let it through."""
    resp = client.post("/api/preferences", json={"key": "watchlist_enabled", "value": "1"})
    assert resp.status_code == 200
    assert client.get("/api/watchlist").json()["enabled"] is True


def test_outcome_endpoint(client: TestClient, tmp_path: Path) -> None:
    jid = _seed_job(tmp_path)
    client.post(f"/api/jobs/{jid}/action", json={"action": "applied"})

    assert (
        client.post(f"/api/jobs/{jid}/outcome", json={"outcome": "no_response"}).status_code == 200
    )
    detail = client.get(f"/api/jobs/{jid}").json()["job"]
    assert detail["outcome"] == "no_response"
    assert detail["applied_at"]

    assert client.post(f"/api/jobs/{jid}/outcome", json={"outcome": "ghosted"}).status_code == 400
    assert client.post("/api/jobs/9999/outcome", json={"outcome": "offer"}).status_code == 404


def test_score_feedback_endpoints(client: TestClient, tmp_path: Path) -> None:
    jid = _seed_job(tmp_path, _analysis={"punteggio": 9, "scoring_v": 2})

    resp = client.post(
        f"/api/jobs/{jid}/score-feedback",
        json={"verdict": "down", "expected_score": 3, "reason": "chiede 5 anni"},
    )
    assert resp.status_code == 200
    assert resp.json()["summary"] == {
        "total": 1,
        "up": 0,
        "down": 1,
        "agreement": 0,
        "avg_gap": 6.0,
        "scored_cases": 1,
    }

    # The detail endpoint carries the standing verdict so the buttons render lit.
    assert client.get(f"/api/jobs/{jid}").json()["score_feedback"]["verdict"] == "down"

    export = client.get("/api/score-feedback/export")
    assert export.status_code == 200
    assert "attachment" in export.headers["content-disposition"]
    first = export.text.splitlines()[0]
    assert '"ai_score": 9' in first and '"expected_score": 3' in first

    assert (
        client.post(f"/api/jobs/{jid}/score-feedback", json={"verdict": "sideways"}).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/jobs/{jid}/score-feedback", json={"verdict": "up", "expected_score": 42}
        ).status_code
        == 400
    )
    assert client.post("/api/jobs/9999/score-feedback", json={"verdict": "up"}).status_code == 404

    assert client.delete(f"/api/jobs/{jid}/score-feedback").json()["removed"] == 1
    assert client.get("/api/score-feedback/summary").json()["total"] == 0
