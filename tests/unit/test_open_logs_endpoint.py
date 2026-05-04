"""Tests for the POST /api/system/open-logs endpoint."""

from __future__ import annotations

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


def test_open_logs_creates_dir_and_calls_startfile_on_win32(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    """On Windows the endpoint creates data/logs/ and calls os.startfile."""
    import sys

    monkeypatch.setattr(sys, "platform", "win32")
    calls: list[str] = []
    monkeypatch.setattr(os, "startfile", lambda p: calls.append(p), raising=False)

    res = client.post("/api/system/open-logs")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["path"].endswith("logs") or "logs" in body["path"]
    assert (tmp_path / "data" / "logs").exists()
    assert len(calls) == 1
    assert "logs" in calls[0]


def test_open_logs_returns_501_on_non_windows(
    client: TestClient, monkeypatch
) -> None:
    """Non-Windows platforms get a 501 Not Implemented."""
    import sys

    monkeypatch.setattr(sys, "platform", "linux")
    res = client.post("/api/system/open-logs")
    assert res.status_code == 501
    assert res.json()["detail"] == "open_logs_unsupported_on_platform"
