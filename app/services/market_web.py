"""Live web knowledge for market questions — free, optional, and only on demand.

The coach's model has a training cutoff. Asked "which AI roles are growing in
Turin right now?" it answers from memory, confidently and possibly a year out of
date, which on a job hunt is worse than saying nothing.

The obvious route is a paid one: OpenRouter's web plugin bills per search even
on free models. Google's is not — Gemini's Search grounding gives 1500 grounded
requests a day on the free Flash tier, with the same API key the app may already
have for the Google provider. So: if the user has that key, market questions get
grounded; if they don't, nothing happens and the local snapshot
(:mod:`app.services.market_snapshot`) still answers from their own scans.

Costs are contained by three things: it only fires on a question that actually
asks about the market, the answer is cached for half a day, and it never raises
— a failed lookup simply means the coach answers as before.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from app.log import get_logger

log = get_logger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover - requests is a pinned dependency
    requests = None  # type: ignore[assignment]

#: Gemini's OpenAI-compatible layer does not carry the Search tool, so this
#: talks to the native endpoint. Flash is the tier the free grounding quota
#: applies to.
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_MODEL = "gemini-2.5-flash"

#: Grounded answers age slowly — the job market does not change hour to hour —
#: and a repeated question must not spend a second request.
CACHE_TTL_SECONDS = 12 * 3600
CACHE_PREFERENCE = "market_web_cache"
_MAX_CACHE_ENTRIES = 12

#: Words that make a question about the market rather than about the user. Kept
#: explicit: a grounded lookup on "rewrite my CV" would spend quota for nothing.
_MARKET_SIGNALS = (
    "mercato",
    "market",
    "trend",
    "richiest",
    "in demand",
    "domanda",
    "crescita",
    "growing",
    "quanto si guadagna",
    "stipendi",
    "salary",
    "salaries",
    "ral media",
    "assumono",
    "hiring",
    "aziende che",
    "companies",
    "settore",
    "industry",
    "oggi",
    "adesso",
    "attual",
    "current",
    "2026",
)


def looks_like_market_question(message: str) -> bool:
    text = str(message or "").lower()
    return any(signal in text for signal in _MARKET_SIGNALS)


def _cache_key(question: str) -> str:
    normalized = re.sub(r"\s+", " ", question.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _read_cache(db: Any, key: str) -> str | None:
    try:
        raw = db.get_preference(CACHE_PREFERENCE, "")
        entries = json.loads(raw) if raw else {}
    except Exception:  # pragma: no cover - defensive
        return None
    entry = entries.get(key) if isinstance(entries, dict) else None
    if not isinstance(entry, dict):
        return None
    if time.time() - float(entry.get("ts") or 0) > CACHE_TTL_SECONDS:
        return None
    answer = str(entry.get("answer") or "")
    return answer or None


def _write_cache(db: Any, key: str, answer: str) -> None:
    try:
        raw = db.get_preference(CACHE_PREFERENCE, "")
        entries = json.loads(raw) if raw else {}
        if not isinstance(entries, dict):
            entries = {}
        entries[key] = {"ts": time.time(), "answer": answer}
        # Keep the newest few; this is a cache, not a history.
        if len(entries) > _MAX_CACHE_ENTRIES:
            newest = sorted(entries.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)
            entries = dict(newest[:_MAX_CACHE_ENTRIES])
        db.set_preference(CACHE_PREFERENCE, json.dumps(entries, ensure_ascii=False))
    except Exception as exc:  # pragma: no cover - caching is best-effort
        log.debug("market web cache write skipped: %s", exc)


def _extract_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
    return "\n".join(str(part.get("text") or "") for part in parts).strip()


def _extract_sources(payload: dict[str, Any]) -> list[str]:
    candidates = payload.get("candidates") or []
    if not candidates:
        return []
    grounding = (candidates[0] or {}).get("groundingMetadata") or {}
    chunks = grounding.get("groundingChunks") or []
    sources: list[str] = []
    for chunk in chunks:
        web = (chunk or {}).get("web") or {}
        title = str(web.get("title") or "").strip()
        if title and title not in sources:
            sources.append(title)
    return sources[:4]


def ask(db: Any, api_key: str | None, question: str, *, language: str = "it") -> str:
    """A short, web-grounded answer to a market question, or "".

    Never raises: no key, no network, a refusal or an unexpected shape all mean
    "no extra context", and the caller carries on with the local snapshot.
    """
    if not api_key or requests is None or not question.strip():
        return ""

    key = _cache_key(question)
    cached = _read_cache(db, key)
    if cached is not None:
        return cached

    prompt = (
        "Rispondi in modo sintetico (massimo 120 parole) e SOLO con informazioni "
        "verificabili e recenti sul mercato del lavoro. Cita periodi e fonti quando "
        "puoi. Se non trovi dati recenti, dillo esplicitamente invece di stimare.\n"
        f"Lingua della risposta: {language}.\n\n"
        f"Domanda: {question.strip()}"
    )
    try:
        response = requests.post(
            _ENDPOINT.format(model=_MODEL),
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                # This is the whole point: without the tool the model answers
                # from its training data, which is exactly what we distrust.
                "tools": [{"google_search": {}}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 500},
            },
            timeout=20,
        )
        if response.status_code != 200:
            log.info("market grounding unavailable (HTTP %s)", response.status_code)
            return ""
        payload = response.json()
    except Exception as exc:
        log.info("market grounding skipped: %s", exc)
        return ""

    answer = _extract_text(payload)
    if not answer:
        return ""
    sources = _extract_sources(payload)
    if sources:
        answer += "\nFonti: " + "; ".join(sources)
    _write_cache(db, key, answer)
    return answer
