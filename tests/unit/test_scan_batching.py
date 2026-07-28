"""Batch scoring: analyze_offers_batch (array parse + per-offer fallback) and
the run_scan batch path (chunking, drain, score coercion)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from app.config import load_settings
from app.db import Database
from app.models import ScanRequest
from app.services import scanner_service as ss

_COLS = [
    "title",
    "company",
    "description",
    "location",
    "site",
    "job_url",
    "min_amount",
    "max_amount",
]


# Long enough to clear MIN_DESCRIPTION_CHARS — a real JD, not a blurb.
_FULL_JD = (
    "React and TypeScript role in un team di prodotto: sviluppo frontend, "
    "integrazione REST API, test end-to-end con Playwright e code review. "
    "Requisiti: esperienza con React, TypeScript e Git; gradita conoscenza "
    "di Node.js e CI/CD. Offriamo smart working e formazione continua. "
    "Sede a Torino con possibilita' di lavoro ibrido e buoni pasto. "
)


def _offer(titolo: str) -> dict[str, Any]:
    return {"titolo": titolo, "azienda": "Co", "descrizione": _FULL_JD}


class _BatchPM:
    """Provider-manager stub. ``payload`` is returned by complete_json (or raised
    if it's an Exception). No ``.settings`` → analyze_offer(s) use policy_override."""

    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def preview_scoring_model(self, _policy: Any) -> str:
        return "fake/model:free"

    def clear_model_penalties(self, reason: str | None = None) -> None:
        pass

    def complete_json(self, prompt: str, max_tokens: int = 700, **kwargs: Any) -> Any:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_batch_empty_offers_returns_empty() -> None:
    assert ss.analyze_offers_batch(_BatchPM({}), "CV", []) == []


_LONG_DESC = "Python, machine learning e data engineering in team AI. " * 20


def test_analyze_offer_empty_dict_is_unevaluated() -> None:
    """A ``{}`` reply must NOT be returned as a valid analysis: it would be
    persisted with punteggio=0 + analyzed_at set and never re-scored. It is a
    non-answer, so the offer carries no score — rather than an invented one."""
    res = ss.analyze_offer(_BatchPM({}), "CV con Python", "AI Engineer", "Co", _LONG_DESC)
    assert res != {}
    assert res["punteggio"] is None
    assert "non_valutato" in res["blocchi"]


def test_analyze_offer_dict_without_score_is_unevaluated() -> None:
    """A dict missing ``punteggio`` (partial/garbled reply) is not an analysis."""
    res = ss.analyze_offer(
        _BatchPM({"riassunto": "bla"}), "CV con Python", "AI Engineer", "Co", _LONG_DESC
    )
    assert res["punteggio"] is None
    assert "non_valutato" in res["blocchi"]


def test_batch_happy_path_returns_all_in_order() -> None:
    pm = _BatchPM({"valutazioni": [{"punteggio": 8}, {"punteggio": 3}, {"punteggio": 6}]})
    out = ss.analyze_offers_batch(pm, "CV", [_offer("A"), _offer("B"), _offer("C")])
    assert [o["punteggio"] for o in out] == [8, 3, 6]


def test_batch_short_array_falls_back_per_offer(monkeypatch: pytest.MonkeyPatch) -> None:
    # Model returned only 2 of 3 → the 3rd slot is filled by a single call.
    pm = _BatchPM({"valutazioni": [{"punteggio": 8}, {"punteggio": 3}]})
    sentinel = {"punteggio": 99, "_fallback": True}
    monkeypatch.setattr(ss, "analyze_offer", lambda **k: sentinel)
    out = ss.analyze_offers_batch(pm, "CV", [_offer("A"), _offer("B"), _offer("C")])
    assert out[0]["punteggio"] == 8
    assert out[1]["punteggio"] == 3
    assert out[2] is sentinel


def test_batch_invalid_elements_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # A non-dict element and a dict missing "punteggio" both trigger fallback.
    pm = _BatchPM({"valutazioni": [{"punteggio": 8}, "garbage", {"nope": 1}]})
    sentinel = {"punteggio": 55}
    monkeypatch.setattr(ss, "analyze_offer", lambda **k: sentinel)
    out = ss.analyze_offers_batch(pm, "CV", [_offer("A"), _offer("B"), _offer("C")])
    assert out[0]["punteggio"] == 8
    assert out[1] is sentinel  # non-dict
    assert out[2] is sentinel  # dict without punteggio


def test_batch_exception_falls_back_all(monkeypatch: pytest.MonkeyPatch) -> None:
    pm = _BatchPM(RuntimeError("boom"))
    sentinel = {"punteggio": 7}
    monkeypatch.setattr(ss, "analyze_offer", lambda **k: sentinel)
    out = ss.analyze_offers_batch(pm, "CV", [_offer("A"), _offer("B")])
    assert len(out) == 2
    assert all(o is sentinel for o in out)


def test_batch_non_dict_response_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # complete_json returns a dict without the array key → all slots fall back.
    pm = _BatchPM({"unexpected": "shape"})
    sentinel = {"punteggio": 4}
    monkeypatch.setattr(ss, "analyze_offer", lambda **k: sentinel)
    out = ss.analyze_offers_batch(pm, "CV", [_offer("A"), _offer("B")])
    assert all(o is sentinel for o in out)


# ── run_scan batch path (end-to-end) ─────────────────────────────────────────


def _fake_df(n: int) -> pd.DataFrame:
    rows = [
        {
            "title": f"React Developer {i}",
            "company": f"Co{i}",
            "description": _FULL_JD,
            "location": "Torino",
            "site": "linkedin",
            "job_url": f"http://x/{i}",  # no linkedin.com host → no recruiter fetch
            "min_amount": None,
            "max_amount": None,
        }
        for i in range(n)
    ]
    return pd.DataFrame(rows, columns=_COLS)


def _payload() -> ScanRequest:
    return ScanRequest(search_terms=["react developer"], sites=["linkedin"], location="Torino")


class _ScanBatchPM:
    """PM that answers both batch prompts (array) and single prompts (object),
    returning a non-int score to also exercise coercion."""

    def __init__(self) -> None:
        self.batch_calls = 0
        self.single_calls = 0

    def preview_scoring_model(self, _policy: Any) -> str:
        return "fake/model:free"

    def clear_model_penalties(self, reason: str | None = None) -> None:
        pass

    def complete_json(self, prompt: str, max_tokens: int = 700, **kwargs: Any) -> Any:
        n = prompt.count("--- OFFERTA ")
        if n == 0:
            self.single_calls += 1
            return {"punteggio": "8/10"}
        self.batch_calls += 1
        return {"valutazioni": [{"punteggio": "8/10"} for _ in range(n)]}


def test_run_scan_batch_scores_all_and_coerces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ss, "scrape_jobs", lambda **k: _fake_df(5))
    pm = _ScanBatchPM()
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0.0
    settings.scan_concurrency = 3
    settings.scan_batch_size = 2
    db = Database(tmp_path / "s.db")
    try:
        events = list(ss.run_scan(db, settings, pm, _payload()))
    finally:
        db.close()

    analyzed = [e for e in events if e.get("status") == "analyzed"]
    complete = next(e for e in events if e.get("status") == "complete")
    assert len(analyzed) == 5
    assert complete["totale_analizzati"] == 5
    # 5 jobs, batch_size 2 → units [2, 2, 1]: two batched calls + one single call.
    assert pm.batch_calls == 2
    assert pm.single_calls == 1
    # "8/10" coerced to int 8 for every job.
    assert all(e["job"]["score"] == 8 for e in analyzed)


# ── anti copy-paste guard (v1.7.6) ───────────────────────────────────────────


def test_batch_cloned_axes_are_rescored_singly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two different jobs with byte-identical match_axes = a copied verdict.

    Real case: three postings came back with the same five axes, two of them
    scored 10. Those slots are dropped and re-scored one by one."""
    axes = {
        "skills_match": 10,
        "seniority_match": 8,
        "remote_match": 9,
        "salary_match": 5,
        "contract_match": 7,
    }
    pm = _BatchPM(
        {
            "valutazioni": [
                {"punteggio": 10, "match_axes": dict(axes)},
                {"punteggio": 10, "match_axes": dict(axes)},
                {"punteggio": 4, "match_axes": dict(axes, skills_match=3)},
            ]
        }
    )
    sentinel = {"punteggio": 5, "_rescored": True}
    monkeypatch.setattr(ss, "analyze_offer", lambda **k: sentinel)
    out = ss.analyze_offers_batch(pm, "CV", [_offer("A"), _offer("B"), _offer("C")])
    assert out[0] is sentinel  # cloned → re-scored
    assert out[1] is sentinel  # cloned → re-scored
    assert out[2]["punteggio"] == 4  # unique axes → kept


def test_batch_missing_axes_are_not_treated_as_clones() -> None:
    pm = _BatchPM({"valutazioni": [{"punteggio": 8}, {"punteggio": 3}]})
    out = ss.analyze_offers_batch(pm, "CV", [_offer("A"), _offer("B")])
    assert [o["punteggio"] for o in out] == [8, 3]


def test_cloned_slots_helper() -> None:
    axes = {"skills_match": 7}
    assert ss._cloned_slots([{"match_axes": axes}, {"match_axes": dict(axes)}]) == {0, 1}
    assert ss._cloned_slots([{"match_axes": axes}, {"match_axes": {"skills_match": 8}}]) == set()
    assert ss._cloned_slots(["garbage", {"punteggio": 1}]) == set()
