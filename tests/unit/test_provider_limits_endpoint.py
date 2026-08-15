"""The two endpoints behind the rate-limits panel.

The panel is the only place where a user can contradict the app: their console
has the real numbers for their project, and the app's shipped defaults cannot be
right for everyone. So the write path matters as much as the read one — and it
had no test at all.
"""

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
    for key in (
        "CEREBRAS_API_KEY",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        os.environ.pop(key, None)

    from app import rate_limit
    from app.main import create_app

    rate_limit.reset()  # per-IP and process-wide: without this the 5th test 429s
    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture
def with_cerebras(monkeypatch):
    """A provider with a catalog, so the panel has rows to show."""
    from app.providers.cerebras_provider import CerebrasProvider

    monkeypatch.setattr(CerebrasProvider, "is_available", lambda self: True, raising=False)
    monkeypatch.setattr(
        CerebrasProvider,
        "list_models",
        lambda self: ["qwen-3-235b-a22b-instruct-2507"],
        raising=False,
    )


def test_the_panel_lists_what_a_model_is_allowed(client: TestClient, with_cerebras) -> None:
    rows = client.get("/api/providers/limits").json()["limits"]
    row = next(r for r in rows if r["provider"] == "cerebras")
    assert row["model"] == "qwen-3-235b-a22b-instruct-2507"
    # Shipped default, no measurement yet, nothing typed by the user.
    assert row["default"]["rpm"] == 30
    assert row["observed"] is None
    assert row["override"] is None
    assert row["used_today"] == 0
    assert row["exhausted"] is False


def test_a_model_with_no_known_limit_is_not_listed(client: TestClient, monkeypatch) -> None:
    """An empty row would suggest the app knows something it does not."""
    from app.providers.openai_provider import OpenAIProvider

    monkeypatch.setattr(OpenAIProvider, "is_available", lambda self: True, raising=False)
    monkeypatch.setattr(OpenAIProvider, "list_models", lambda self: ["gpt-4.1"], raising=False)
    rows = client.get("/api/providers/limits").json()["limits"]
    assert not [r for r in rows if r["provider"] == "openai"]


def test_what_the_user_types_wins_and_survives_a_reload(
    client: TestClient, with_cerebras
) -> None:
    saved = client.post(
        "/api/providers/limits",
        json={"provider": "cerebras", "model": "qwen-3-235b-a22b-instruct-2507", "rpm": 5},
    )
    assert saved.status_code == 200
    assert saved.json()["ok"] is True

    row = next(
        r for r in client.get("/api/providers/limits").json()["limits"] if r["provider"] == "cerebras"
    )
    assert row["override"]["rpm"] == 5
    assert row["override"]["source"] == "tuo"
    # The shipped number stays visible next to it: the panel shows where each
    # number comes from rather than replacing one with another.
    assert row["default"]["rpm"] == 30


def test_zero_clears_the_override(client: TestClient, with_cerebras) -> None:
    body = {"provider": "cerebras", "model": "qwen-3-235b-a22b-instruct-2507"}
    client.post("/api/providers/limits", json={**body, "rpm": 5})
    cleared = client.post("/api/providers/limits", json={**body, "rpm": 0, "rpd": 0})
    assert cleared.status_code == 200

    row = next(
        r for r in client.get("/api/providers/limits").json()["limits"] if r["provider"] == "cerebras"
    )
    assert row["override"] is None


def test_a_negative_limit_is_refused(client: TestClient) -> None:
    res = client.post(
        "/api/providers/limits",
        json={"provider": "cerebras", "model": "whatever", "rpm": -1},
    )
    assert res.status_code == 422
