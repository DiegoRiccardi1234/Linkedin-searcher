"""POST /api/providers/{name}/probe — the default free stats report (no
inference) vs the opt-in confirm micro-probe (top-3, inference)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
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

    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def _stub_cerebras(monkeypatch: pytest.MonkeyPatch, models: list[str]) -> None:
    from app.providers.cerebras_provider import CerebrasProvider

    monkeypatch.setattr(CerebrasProvider, "is_available", lambda self: True, raising=False)
    monkeypatch.setattr(CerebrasProvider, "list_models", lambda self: models, raising=False)


def test_probe_default_is_stats_and_runs_no_inference(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_cerebras(monkeypatch, ["a-70b-instruct", "b-8b-instruct"])
    from app.routers import providers as prov_router
    from app.services import model_stats

    monkeypatch.setattr(
        model_stats,
        "get_model_health",
        lambda prov, ids: {
            ids[0]: {
                "status": 0,
                "up5m": 100.0,
                "up30m": 99.5,
                "lat_ms": 200.0,
                "tput": 70.0,
                "ctx": 131072,
                "maxc": 32768,
            }
        },
    )

    def _no_probe(*a: Any, **k: Any) -> Any:
        raise AssertionError("probe_models must NOT run in stats mode")

    monkeypatch.setattr(prov_router, "probe_models", _no_probe)

    res = client.post("/api/providers/cerebras/probe")
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "stats"
    assert body["results"]
    assert any(r.get("up5m") == 100.0 for r in body["results"])


def test_probe_confirm_micro_probes_top_three(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_cerebras(monkeypatch, [f"m{i}-8b-instruct" for i in range(6)])
    from app.routers import providers as prov_router
    from app.services import model_stats

    monkeypatch.setattr(model_stats, "get_model_health", lambda prov, ids: {})

    seen: dict[str, Any] = {}

    def fake_probe(provider: Any, model_ids: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        seen["ids"] = list(model_ids)
        seen["kwargs"] = kwargs
        return [
            {
                "model": m,
                "ok": True,
                "json_ok": True,
                "schema_ok": True,
                "latency_ms": 90,
                "empty": False,
                "error": None,
            }
            for m in model_ids
        ]

    monkeypatch.setattr(prov_router, "probe_models", fake_probe)

    res = client.post("/api/providers/cerebras/probe?confirm=true")
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "probe"
    assert len(seen["ids"]) <= 3  # only the top few get a real inference call
    # The probe must exercise the REAL scoring prompt: a model that answers
    # {"ok": true} has proven nothing about emitting the app's schema.
    assert seen["kwargs"].get("scoring") is True
    assert body["results"]
    assert all(r["json_ok"] for r in body["results"])
    assert "scoreboard" in body


def test_probe_best_respects_quality_floor(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report's ``best`` must not headline a model too small for real work,
    even when that tiny model is the healthiest/fastest — the scan-scoring path
    de-ranks sub-floor sizes and the report has to agree, or it recommends a
    model the scorer would never pick."""
    _stub_cerebras(monkeypatch, ["tiny-3b-instruct", "big-70b-instruct"])
    from app.services import model_stats

    monkeypatch.setattr(
        model_stats,
        "get_model_health",
        lambda prov, ids: {
            # tiny is the healthiest/fastest -> old behaviour picked it as best
            "tiny-3b-instruct": {
                "status": 0,
                "up5m": 100.0,
                "up30m": 100.0,
                "lat_ms": 90.0,
                "tput": 90.0,
            },
            "big-70b-instruct": {
                "status": 0,
                "up5m": 100.0,
                "up30m": 100.0,
                "lat_ms": 300.0,
                "tput": 50.0,
            },
        },
    )

    res = client.post("/api/providers/cerebras/probe")
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "stats"
    # both healthy; tiny is faster, but below the quality floor -> the capable
    # 70B must be recommended instead.
    assert body["best"] == "big-70b-instruct"
    # the tiny model is still listed (a health report shows every size)
    assert any(r["model"] == "tiny-3b-instruct" for r in body["results"])


def test_probe_best_allows_mid_size_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the lowered 26B floor, a clean 30B model is a valid ``best`` over a
    tiny 3B — the report agrees with scan-scoring that mid-size models qualify."""
    _stub_cerebras(monkeypatch, ["tiny-3b-instruct", "mid-30b-instruct"])
    from app.services import model_stats

    monkeypatch.setattr(
        model_stats,
        "get_model_health",
        lambda prov, ids: {
            "tiny-3b-instruct": {
                "status": 0,
                "up5m": 100.0,
                "up30m": 100.0,
                "lat_ms": 80.0,
                "tput": 90.0,
            },
            "mid-30b-instruct": {
                "status": 0,
                "up5m": 100.0,
                "up30m": 100.0,
                "lat_ms": 300.0,
                "tput": 50.0,
            },
        },
    )

    res = client.post("/api/providers/cerebras/probe")
    assert res.status_code == 200
    assert res.json()["best"] == "mid-30b-instruct"


def test_probe_unknown_provider_404(client: TestClient) -> None:
    assert client.post("/api/providers/nope/probe").status_code == 404
