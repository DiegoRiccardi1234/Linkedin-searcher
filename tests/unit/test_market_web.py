"""Web grounding for market questions: free, optional, and never in the way.

The model's training cutoff is the problem this solves — asked what is in demand
"now", it answers from memory. Google's Search grounding is free on the Flash
tier with a key the user may already have; the rules here are that it fires only
on a market question, caches, and never breaks a chat turn.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.db import Database
from app.services import market_web


def test_only_market_questions_qualify() -> None:
    for question in (
        "quali ruoli AI sono più richiesti adesso?",
        "quanto si guadagna come data analyst a Torino?",
        "which companies are hiring in Milan?",
        "com'è il mercato per i tirocini nel 2026?",
    ):
        assert market_web.looks_like_market_question(question) is True

    for question in (
        "riscrivi il mio CV",
        "prepara una lettera per questa offerta",
        "cosa ne pensi di questo annuncio?",
    ):
        assert market_web.looks_like_market_question(question) is False


def test_no_key_means_no_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called = []
    monkeypatch.setattr(
        market_web.requests, "post", lambda *a, **k: called.append(1)
    )  # type: ignore[union-attr]
    db = Database(tmp_path / "w.db")
    try:
        assert market_web.ask(db, None, "quali ruoli sono richiesti?") == ""
        assert called == []
    finally:
        db.close()


class _Response:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


def _payload(text: str, sources: list[str] | None = None) -> dict[str, Any]:
    candidate: dict[str, Any] = {"content": {"parts": [{"text": text}]}}
    if sources:
        candidate["groundingMetadata"] = {
            "groundingChunks": [{"web": {"title": s}} for s in sources]
        }
    return {"candidates": [candidate]}


def test_answer_and_sources_are_returned_and_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def fake_post(url: str, **kwargs: Any) -> _Response:
        calls.append(kwargs)
        return _Response(_payload("Le offerte AI QA crescono.", ["Osservatorio Lavoro"]))

    monkeypatch.setattr(market_web.requests, "post", fake_post)  # type: ignore[union-attr]
    db = Database(tmp_path / "w.db")
    try:
        answer = market_web.ask(db, "key", "quali ruoli AI sono richiesti adesso?")
        assert "AI QA" in answer
        assert "Osservatorio Lavoro" in answer
        # The Search tool is the whole point of using this endpoint.
        assert calls[0]["json"]["tools"] == [{"google_search": {}}]

        # Same question again: served from cache, no second request.
        again = market_web.ask(db, "key", "Quali ruoli AI sono richiesti adesso?  ")
        assert again == answer
        assert len(calls) == 1

        stored = json.loads(db.get_preference(market_web.CACHE_PREFERENCE, "{}"))
        assert len(stored) == 1
    finally:
        db.close()


def test_a_failed_lookup_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("network down")

    monkeypatch.setattr(market_web.requests, "post", boom)  # type: ignore[union-attr]
    db = Database(tmp_path / "w.db")
    try:
        assert market_web.ask(db, "key", "quanto si guadagna?") == ""
    finally:
        db.close()


def test_an_empty_answer_is_not_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        market_web.requests,  # type: ignore[union-attr]
        "post",
        lambda *a, **k: _Response({"candidates": []}),
    )
    db = Database(tmp_path / "w.db")
    try:
        assert market_web.ask(db, "key", "mercato?") == ""
        assert db.get_preference(market_web.CACHE_PREFERENCE, "") == ""
    finally:
        db.close()
