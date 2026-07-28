"""Reading the user's machine is asked for, not assumed.

Answering ``GET /api/local/status`` used to mean running ``nvidia-smi``, a
PowerShell query for the RAM, a lookup for the Ollama binary and — with a GPU
present — a request to huggingface.co. The frontend called it from ``bootstrap``,
so all of that ran on every single launch, before anyone had opened Settings.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.routers.providers import _HARDWARE_PROBE_PREFERENCE

_SNAPSHOT = {
    "hardware": {"gpu_name": "NVIDIA GeForce RTX 5070", "vram_gb": 12.0, "ram_gb": 16.0},
    "recommendation": {"verdict": "good", "max_params_b": 18, "models": [], "reason": "ok"},
    "ollama": {"running": True, "installed": True, "models": [], "host": "http://localhost:11434"},
    "ready": [],
    "discovered": [],
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    for key in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "GOOGLE_API_KEY"):
        os.environ.pop(key, None)

    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def test_status_touches_nothing_before_consent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import local_models

    def _forbidden() -> Any:
        raise AssertionError("the machine must not be probed without consent")

    monkeypatch.setattr(local_models, "snapshot", _forbidden)

    response = client.get("/api/local/status")
    assert response.status_code == 200
    assert response.json() == {"consent": False}


def test_probing_grants_and_answers_in_one_round_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import local_models

    monkeypatch.setattr(local_models, "snapshot", lambda: dict(_SNAPSHOT))

    granted = client.post("/api/local/probe")
    assert granted.status_code == 200
    body = granted.json()
    assert body["consent"] is True
    # The click that gives permission also gets the answer: making the user wait
    # for a second request to see their own hardware would be theatre.
    assert body["hardware"]["vram_gb"] == 12.0

    later = client.get("/api/local/status")
    assert later.json()["consent"] is True
    assert later.json()["ready"] == []


def test_consent_is_remembered_as_a_preference(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-install, like the machine it describes — and not in the secrets file."""
    from app.services import local_models

    monkeypatch.setattr(local_models, "snapshot", lambda: dict(_SNAPSHOT))
    client.post("/api/local/probe")

    stored = client.get("/api/preferences").json()["preferences"]
    assert stored.get(_HARDWARE_PROBE_PREFERENCE) == "1"
