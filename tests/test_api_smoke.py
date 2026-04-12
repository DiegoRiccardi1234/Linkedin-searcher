from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_endpoint(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "web").mkdir(parents=True, exist_ok=True)
    (workspace / "web" / "index.html").write_text("<html>ok</html>", encoding="utf-8")

    app = create_app(workspace)
    client = TestClient(app)

    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "provider" in payload


def test_manual_job_and_detail(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "web").mkdir(parents=True, exist_ok=True)
    (workspace / "web" / "index.html").write_text("<html>ok</html>", encoding="utf-8")

    app = create_app(workspace)
    client = TestClient(app)

    response = client.post(
        "/api/jobs/manual",
        json={
            "titolo": "Junior QA Tester",
            "azienda": "ACME",
            "descrizione": "Ruolo QA",
            "sede": "Torino",
            "link": "https://example.com/job",
            "fonte": "manual",
            "ricerca_usata": "qa",
            "modalita": "Manuale",
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    detail = client.get(f"/api/jobs/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["job"]["titolo"] == "Junior QA Tester"
