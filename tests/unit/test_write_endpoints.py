"""The endpoints that CHANGE something, and had no test.

An audit of all 111 routes turned up a pattern worth fixing on its own: the
read-only endpoints were well covered, and several of the ones that write were
not covered at all — including the one that saves API keys to disk, the one that
deletes the mailbox credentials, and the one that creates a candidate profile
from free text. A GET that regresses shows you a wrong number; a POST that
regresses loses data.

These are deliberately shallow: they check that the write lands, that the
obvious bad input is refused, and that the secret does not come back out. Depth
belongs with the feature; presence belongs here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import rate_limit


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    # The limiter is per-IP and lives in the process: without this, the fifth
    # test in a file that hits the same bucket starts getting 429s.
    rate_limit.reset()
    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


# ── provider keys: the endpoint that writes secrets to disk ─────────────────


def test_saving_a_provider_key_persists_it_and_never_echoes_it(
    client: TestClient, tmp_path: Path
) -> None:
    secret = "sk-test-do-not-echo-me"
    response = client.post("/api/providers/keys", json={"cerebras_api_key": secret})
    assert response.status_code == 200
    assert secret not in response.text, "a key must never come back out of the API"

    stored = json.loads((tmp_path / "data" / "local_secrets.json").read_text(encoding="utf-8"))
    assert stored["cerebras_api_key"] == secret

    status = client.get("/api/providers/keys/status")
    assert status.json()["keys"]["cerebras_configured"] is True
    assert secret not in status.text


def test_saving_one_key_does_not_wipe_another(client: TestClient, tmp_path: Path) -> None:
    """The credential file is merged, never rewritten from scratch.

    Both live in the same file as the mailbox token; rewriting it wholesale on
    each save is the kind of loss nobody notices until the next run.
    """
    client.post("/api/providers/keys", json={"cerebras_api_key": "aaa"})
    client.post("/api/providers/keys", json={"openrouter_api_key": "bbb"})
    stored = json.loads((tmp_path / "data" / "local_secrets.json").read_text(encoding="utf-8"))
    assert stored["cerebras_api_key"] == "aaa"
    assert stored["openrouter_api_key"] == "bbb"


# ── profile: two ways to create one, neither covered ────────────────────────


def test_a_profile_can_be_created_from_text_and_becomes_active(client: TestClient) -> None:
    markdown = (
        "# Mario Rossi\n\nLaurea Triennale in Informatica.\n"
        "Esperienza: sviluppo backend con Python e SQL per due progetti universitari.\n"
    )
    created = client.post("/api/profile/from-text", json={"markdown": markdown})
    assert created.status_code == 200
    profile_id = created.json()["profile_id"]

    active = client.get("/api/profile").json()["profile"]
    assert active["id"] == profile_id


def test_a_profile_from_text_refuses_a_stub(client: TestClient) -> None:
    assert client.post("/api/profile/from-text", json={"markdown": "ciao"}).status_code == 400


def test_the_linkedin_form_keeps_pasted_text_over_the_url(client: TestClient) -> None:
    """Pasted text wins: LinkedIn blocks the server-side fetch almost always."""
    saved = client.post(
        "/api/profile/linkedin",
        json={"url": "https://www.linkedin.com/in/example", "text": "Certificazioni: 19 corsi."},
    )
    assert saved.status_code == 200
    prefs = client.get("/api/preferences").json()["preferences"]
    assert prefs["linkedin_url"] == "https://www.linkedin.com/in/example"
    assert "19 corsi" in prefs["linkedin_profile_text"]


def test_uploading_a_cv_creates_a_profile(client: TestClient) -> None:
    body = (
        "# Mario Rossi\n\nLaurea Triennale in Informatica, votazione 95/110, luglio 2025.\n\n"
        "## Competenze\nPython, SQL, React, TypeScript, PostgreSQL, MongoDB, REST, Git.\n\n"
        "## Esperienza\nTirocinio curriculare frontend di quattro mesi su React e TypeScript, "
        "con sviluppo di componenti riusabili e integrazione di API REST.\n\n"
        "## Lingue\nItaliano madrelingua, inglese B2.\n"
    )
    response = client.post(
        "/api/upload-cv",
        files={"file": ("cv.md", body.encode("utf-8"), "text/markdown")},
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/profiles").json()["profiles"], "the upload must leave a profile behind"


def test_uploading_a_cv_that_says_nothing_is_refused(client: TestClient) -> None:
    """A blurb cannot be scored, and a profile built from one poisons every match."""
    response = client.post(
        "/api/upload-cv", files={"file": ("cv.md", b"Mario Rossi, sviluppatore.", "text/markdown")}
    )
    assert response.status_code == 422


def test_uploading_an_unsupported_file_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/upload-cv",
        files={"file": ("payload.exe", b"MZ\x00\x00", "application/octet-stream")},
    )
    assert response.status_code == 415


# ── saved searches: created and deleted from the UI, tested only at DB level ─


def test_a_saved_search_round_trips_and_deletes(client: TestClient) -> None:
    created = client.post(
        "/api/saved-searches",
        json={"name": "Torino ruoli-ponte", "config": {"terms": ["analista funzionale"]}},
    )
    assert created.status_code == 201
    search_id = created.json()["id"]

    listed = client.get("/api/saved-searches").json()["searches"]
    assert [s["name"] for s in listed] == ["Torino ruoli-ponte"]

    assert client.delete(f"/api/saved-searches/{search_id}").status_code == 200
    assert client.get("/api/saved-searches").json()["searches"] == []


def test_a_saved_search_needs_a_name(client: TestClient) -> None:
    assert client.post("/api/saved-searches", json={"name": "  ", "config": {}}).status_code == 400


def test_deleting_a_search_that_is_not_there_is_a_404(client: TestClient) -> None:
    assert client.delete("/api/saved-searches/9999").status_code == 404


# ── mailbox: the destructive one ────────────────────────────────────────────


def test_disconnecting_the_mailbox_removes_the_credential(
    client: TestClient, tmp_path: Path
) -> None:
    """Disconnect is the only endpoint that deletes a credential, and it had no test."""
    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "app-password"},
    )
    assert client.get("/api/mail/status").json()["configured"] is True

    assert client.post("/api/mail/disconnect").status_code == 200

    assert client.get("/api/mail/status").json()["configured"] is False
    stored = json.loads((tmp_path / "data" / "local_secrets.json").read_text(encoding="utf-8"))
    assert "mail_secret" not in stored


def test_the_mailbox_actions_refuse_to_run_unconfigured(client: TestClient) -> None:
    """412, not a traceback: there is no mailbox to reach yet."""
    assert client.post("/api/mail/dry-run").status_code == 412
    assert client.post("/api/mail/check").status_code == 412


# ── reminders that actually remind ──────────────────────────────────────────


def test_a_due_reminder_is_announced_once_and_only_if_asked_for(tmp_path: Path) -> None:
    """Reminders were a list you had to remember to go and read.

    Two rules keep the fix from becoming noise: it is opt-in, and each reminder
    fires once — "overdue" stays true forever, so without a record of what has
    been said the tray would fire every minute until the date is changed.
    """
    from app.db import Database
    from app.notify import register_notifier
    from app.services import reminder_watch

    fired: list[tuple[str, str]] = []
    register_notifier(lambda title, message: fired.append((title, message)))
    db = Database(tmp_path / "r.db")
    try:
        job_id, _n, _s = db.upsert_job(
            {
                "titolo": "Analista funzionale",
                "azienda": "BTO",
                "link": "https://example.com/1",
                "descrizione": "Analisi.",
            }
        )
        db.set_job_reminder(job_id, "2020-01-01T09:00:00+00:00", "richiamare")

        assert reminder_watch.check_due(db) == 0, "off by default: nothing interrupts"
        assert fired == []

        db.set_preference(reminder_watch.PREF_ENABLED, "1")
        assert reminder_watch.check_due(db) == 1
        assert len(fired) == 1
        assert "Analista funzionale" in fired[0][1]

        assert reminder_watch.check_due(db) == 0, "said once, not every tick"
        assert len(fired) == 1

        # Moving the date makes it a new thing to say.
        db.set_job_reminder(job_id, "2020-02-02T09:00:00+00:00", "richiamare")
        assert reminder_watch.check_due(db) == 1
    finally:
        register_notifier(lambda *_: None)
        db.close()


def test_the_stale_nudge_window_can_finally_be_set(client: TestClient) -> None:
    """Read by list_reminders since it was written, settable nowhere at all."""
    assert (
        client.post(
            "/api/preferences", json={"key": "reminder_stale_days", "value": "21"}
        ).status_code
        == 200
    )
    prefs = client.get("/api/preferences").json()["preferences"]
    assert prefs["reminder_stale_days"] == "21"
