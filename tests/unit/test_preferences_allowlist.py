"""POST /api/preferences accepts the keys the UI writes — and nothing else.

The table is the app's control plane: it holds the feature toggles, including
``feature_privacy_mode``, which decides whether the CV is redacted before it
reaches a third-party model. The endpoint used to accept any key/value pair.

The allowlist has to stay in step with the frontend: the first version of it
rejected ``ui_language`` and every language switch answered 400.
"""

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
    for key in ("CEREBRAS_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(key, None)
    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


# Every key the frontend actually POSTs, gathered from web/: i18n.js (language),
# features.js (feature toggles, autoscan), profile.js (onboarding), app.js (dedup).
@pytest.mark.parametrize(
    "key",
    [
        "ui_language",
        "theme",
        "density",
        "dedup_mode",
        "feature_privacy_mode",
        "autoscan_notify",
        "onboarding_sector",
        "onboarding_ral_min",
        "last_scan_terms",
        "cv_review_cache",
        "active_profile_id",
    ],
)
def test_ui_keys_are_writable(client: TestClient, key: str) -> None:
    assert client.post("/api/preferences", json={"key": key, "value": "x"}).status_code == 200


def test_unknown_keys_are_refused(client: TestClient) -> None:
    resp = client.post("/api/preferences", json={"key": "arbitrary_key", "value": "1"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unknown_preference"


def test_oversized_values_are_refused(client: TestClient) -> None:
    resp = client.post("/api/preferences", json={"key": "theme", "value": "x" * 30_000})
    assert resp.status_code == 413
