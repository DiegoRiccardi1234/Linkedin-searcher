"""Re-scoring an offer already in the archive, one at a time or in bulk.

Two defects live here, both found by reading what the endpoint actually passed:

* the single-offer path never handed the scorer ``facts`` or ``modalita``, so the
  three checks the app advertises — years, degree, reachable location — were
  structurally dead on it: an on-site role in another city could be re-scored to
  9 by the same app that hides it from the list;
* there was no bulk path at all, and the button only appeared on offers nobody
  had judged, so an archive scored by a model that turned out to be wrong could
  not be refreshed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.services import candidate_facts as cf

_CV = "Laurea Triennale in Informatica, votazione 95/110. Python, React, SQL."
_JD = "Analisi funzionale, raccolta requisiti, integrazione fra sistemi e test. " * 8


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    # The bulk bucket allows 4 runs per 5 minutes and the counter is per-IP, so
    # without this every test after the fourth sees a 429 from its predecessors.
    from app import rate_limit
    import app.services.rescore_service as rescore_service

    rate_limit.reset()
    # The real pause is there for free-tier rate limits; waiting it out in the
    # suite buys nothing. Its own behaviour is covered separately below.
    monkeypatch.setattr(rescore_service, "_PAUSE_BETWEEN_CALLS", 0.0)
    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def _seed(tmp_path: Path, jobs: list[dict[str, Any]]) -> list[int]:
    """Seed a CV, the declared work mode, and some offers. Returns the job ids."""
    db = Database(tmp_path / "data" / "searcher.db")
    try:
        db.save_candidate_profile(
            source_name="cv.pdf", markdown=_CV, summary={"years_experience": 0}
        )
        db.set_preference("onboarding_work_mode", "Remoto, Torino in sede oppure ibrido su Torino")
        db.set_preference("last_scan_locations", json.dumps(["Torino"]))
        ids = []
        for i, job in enumerate(jobs):
            job_id, _, _ = db.upsert_job({"link": f"x{i}", "descrizione": _JD, **job})
            ids.append(job_id)
        return ids
    finally:
        db.close()


def _stub_score(monkeypatch: pytest.MonkeyPatch, punteggio: int = 9) -> None:
    """A model that loves every offer, so only the checks can lower the score."""
    import app.services.rescore_service as rescore_service

    monkeypatch.setattr(
        rescore_service,
        "analyze_offer",
        lambda **k: __import__(
            "app.services.scanner_service", fromlist=["x"]
        ).enforce_hard_requirements(
            {"punteggio": punteggio, "consiglio": "Candidati subito", "riassunto": "Ottima."},
            profile_markdown=k.get("profile_markdown", _CV),
            descrizione=k.get("descrizione", _JD),
            sede=k.get("sede", ""),
            modalita=k.get("modalita", ""),
            facts=k.get("facts"),
        ),
    )


def test_rescore_applies_the_declared_constraints(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An on-site job in another city cannot come back recommended."""
    (far,) = _seed(
        tmp_path,
        [{"titolo": "Analista funzionale", "azienda": "Acme", "sede": "Milan, Lombardy, Italy",
          "modalita": "In sede"}],
    )
    _stub_score(monkeypatch)
    res = client.post(f"/api/jobs/{far}/analyze")
    assert res.status_code == 200
    body = res.json()
    assert body["punteggio"] == 3, "the location check must cap it"
    assert cf.__name__  # keeps the import meaningful for readers
    assert "sede_non_raggiungibile" in body["analysis"]["blocchi"]


def test_rescore_leaves_a_reachable_job_alone(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (near,) = _seed(
        tmp_path,
        [{"titolo": "Analista funzionale", "azienda": "Acme", "sede": "Turin, Piedmont, Italy",
          "modalita": "Ibrido"}],
    )
    _stub_score(monkeypatch)
    body = client.post(f"/api/jobs/{near}/analyze").json()
    assert body["punteggio"] == 9
    assert not body["analysis"]["blocchi"]


def _events(client: TestClient, url: str) -> list[dict[str, Any]]:
    with client.stream("GET", url) as res:
        assert res.status_code == 200
        out = []
        for line in res.iter_lines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
        return out


def test_bulk_rescore_updates_every_offer(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed(
        tmp_path,
        [
            {"titolo": f"Analista funzionale {i}", "azienda": f"Co{i}",
             "sede": "Turin, Piedmont, Italy", "modalita": "Ibrido"}
            for i in range(3)
        ],
    )
    _stub_score(monkeypatch, punteggio=8)
    events = _events(client, "/api/jobs/reanalyze/stream?scope=all")
    complete = next(e for e in events if e.get("status") == "complete")
    assert complete["rivalutate"] == 3
    assert complete["fallite"] == 0
    for job_id in ids:
        assert client.get(f"/api/jobs/{job_id}").json()["job"]["punteggio_ai"] == 8


def test_bulk_rescore_skips_hard_blocked_offers_on_applicable_scope(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asking a model about an offer the cap will floor anyway is a wasted call."""
    near, far = _seed(
        tmp_path,
        [
            {"titolo": "Analista funzionale", "azienda": "Vicina",
             "sede": "Turin, Piedmont, Italy", "modalita": "Ibrido"},
            {"titolo": "Analista funzionale", "azienda": "Lontana",
             "sede": "Rome, Latium, Italy", "modalita": "In sede"},
        ],
    )
    _stub_score(monkeypatch, punteggio=8)
    # First pass over everything: the far one gets its blocking flag stored.
    _events(client, "/api/jobs/reanalyze/stream?scope=all")
    events = _events(client, "/api/jobs/reanalyze/stream?scope=applicable")
    scored = [e["job_id"] for e in events if e.get("status") == "scored"]
    assert scored == [near]
    assert far not in scored


def test_bulk_rescore_preview_counts_without_spending(
    client: TestClient, tmp_path: Path
) -> None:
    _seed(tmp_path, [{"titolo": "Analista funzionale", "azienda": "Acme"}])
    res = client.get("/api/jobs/reanalyze/preview?scope=unscored")
    assert res.status_code == 200
    assert res.json()["count"] == 1


def test_bulk_rescore_rejects_an_unknown_scope(client: TestClient) -> None:
    assert client.get("/api/jobs/reanalyze/stream?scope=everything").status_code == 400


def test_bulk_rescore_paces_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fast host answers in ~1s, and free tiers cap requests per MINUTE.

    Without a pause a bulk run hits the cap a third of the way through and turns
    the rest of the archive into 429s. The wait is skipped when the call itself
    was already slow enough.
    """
    import app.services.rescore_service as rescore_service

    (tmp_path / "data").mkdir(exist_ok=True)
    _seed(tmp_path, [{"titolo": f"Analista {i}", "azienda": "Co"} for i in range(3)])
    db = Database(tmp_path / "data" / "searcher.db")
    slept: list[float] = []
    monkeypatch.setattr(rescore_service.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(rescore_service, "analyze_offer", lambda **k: {"punteggio": 6})
    try:
        ids = [j["id"] for j in db.list_jobs(limit=10)]
        list(rescore_service.rescore_jobs(db, object(), job_ids=ids, privacy=False))
    finally:
        db.close()
    # One pause between offers, none after the last one.
    assert len(slept) == len(ids) - 1
    assert all(0 < s <= rescore_service._PAUSE_BETWEEN_CALLS for s in slept)


def test_bulk_rescore_survives_a_dead_provider(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One offer blowing up must not abort the others."""
    ids = _seed(
        tmp_path,
        [{"titolo": f"Analista funzionale {i}", "azienda": f"Co{i}"} for i in range(3)],
    )
    import app.services.rescore_service as rescore_service

    calls: list[int] = []

    def boom(**k: Any) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("provider down")
        return {"punteggio": 6, "consiglio": "Valutabile"}

    monkeypatch.setattr(rescore_service, "analyze_offer", boom)
    events = _events(client, "/api/jobs/reanalyze/stream?scope=all")
    complete = next(e for e in events if e.get("status") == "complete")
    assert complete["fallite"] == 1
    assert complete["rivalutate"] == len(ids)
