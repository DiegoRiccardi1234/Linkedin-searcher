"""Active model probe: benchmark candidate models with a tiny JSON prompt to
learn which ones actually respond fast and return valid JSON.

The name heuristic in :mod:`app.providers.model_selector` can't see runtime
truth — some free models 200 with empty content, some are credit-gated (403),
some never emit JSON. This probes them empirically. Used by the Settings
"test models" action to rank models and to seed the factory's penalty map.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.providers.base import LLMProvider

# Minimal, unambiguous JSON ask — a healthy chat/instruct model answers in well
# under a second of tokens; reasoning-only models tend to return empty content.
PROBE_PROMPT = 'Rispondi SOLO con JSON valido e nulla altro: {"ok": true, "n": 7}'

# A model that can produce {"ok": true, "n": 7} has proven almost nothing about
# the job it is actually hired for: a two-dozen-field object about a 2600-char
# posting, which is the workload that truncates. The scoring probe therefore
# runs the REAL prompt against a fixed sample offer kept here (no network, no
# scraping, identical across runs so results are comparable).
# Invented, and on purpose: this shipped for a while as the real CV of the
# person who wrote the app — degree mark included — in a public repository, and
# it is shown to whatever model every user happens to be testing. The length is
# what matters to a probe, not whose life it describes.
SAMPLE_CV = (
    "Laurea Triennale in Economia aziendale (voto 101/110). Esperienza: 8 mesi "
    "come impiegata amministrativa in uno studio commercialista, 3 mesi di "
    "tirocinio in segreteria organizzativa. Strumenti: Excel avanzato, gestionale "
    "Zucchetti, fatturazione elettronica. Inglese B1. Cerca ruoli in "
    "amministrazione e controllo di gestione, ibrido o in sede."
)
SAMPLE_OFFER = {
    "titolo": "AI Quality Analyst (LLM Evaluation)",
    "azienda": "Nordic Data Labs",
    "descrizione": (
        "Cerchiamo un AI Quality Analyst per valutare le risposte dei nostri "
        "modelli linguistici. Responsabilità: definire rubriche di valutazione, "
        "annotare output secondo una tassonomia di errori, misurare le "
        "allucinazioni e documentare regressioni tra release. Requisiti: laurea "
        "triennale in ambito tecnico o linguistico, ottima conoscenza dell'italiano "
        "scritto, inglese almeno B2, familiarità con Python per script di supporto "
        "e attenzione al dettaglio. Gradita esperienza con annotazione dati o "
        "prompt engineering. Offriamo contratto a tempo indeterminato, lavoro "
        "ibrido con due giorni in sede a Milano, formazione continua e RAL "
        "indicativa 28.000€-32.000€ in base all'esperienza."
    ),
}

#: Keys a scoring answer MUST carry to be usable by the app.
_REQUIRED_SCORING_KEYS = ("punteggio", "match_axes", "skills_match", "requisiti")


def scoring_probe_prompt() -> str:
    """The production scoring prompt, on the fixed sample offer."""
    from app.services.scanner_service import _analysis_prompt

    return _analysis_prompt(
        SAMPLE_CV, SAMPLE_OFFER["titolo"], SAMPLE_OFFER["azienda"], SAMPLE_OFFER["descrizione"]
    )


def _schema_ok(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if any(key not in result for key in _REQUIRED_SCORING_KEYS):
        return False
    axes = result.get("match_axes")
    return isinstance(axes, dict) and len(axes) >= 3


def _probe_one(provider: LLMProvider, model: str, *, scoring: bool = False) -> dict[str, Any]:
    from app.services.scanner_service import _scoring_max_tokens

    prompt = scoring_probe_prompt() if scoring else PROBE_PROMPT
    max_tokens = _scoring_max_tokens(1) if scoring else 120
    t0 = time.monotonic()
    try:
        result = provider.complete_json(prompt=prompt, model=model, max_tokens=max_tokens)
        latency_ms = int((time.monotonic() - t0) * 1000)
        json_ok = isinstance(result, dict) and bool(result)
        return {
            "model": model,
            "ok": True,
            "latency_ms": latency_ms,
            "json_ok": json_ok,
            # For the cheap probe the schema question doesn't apply: report the
            # JSON verdict so the ranking key stays meaningful in both modes.
            "schema_ok": _schema_ok(result) if scoring else json_ok,
            "empty": not result,
            "error": None,
        }
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "model": model,
            "ok": False,
            "latency_ms": latency_ms,
            "json_ok": False,
            "schema_ok": False,
            "empty": False,
            "error": str(exc)[:160],
        }


def probe_models(
    provider: LLMProvider,
    model_ids: list[str],
    *,
    timeout: float = 25.0,
    concurrency: int = 6,
    scoring: bool = False,
) -> list[dict[str, Any]]:
    """Probe each model once, concurrently. Returns results ranked best-first
    (usable schema, then valid JSON, then any success, then fastest). Never
    raises — a hung/slow model becomes an ``ok: False`` row with
    ``error: "timeout"``.

    ``scoring=True`` runs the real scoring prompt on a fixed sample offer and
    checks the answer carries the keys the app needs, instead of asking for a
    two-field toy object that every model passes.
    """
    if not model_ids:
        return []
    results: list[dict[str, Any]] = []
    workers = min(max(1, concurrency), len(model_ids))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_probe_one, provider, m, scoring=scoring): m for m in model_ids}
        for fut, model in futures.items():
            try:
                results.append(fut.result(timeout=timeout))
            except FutureTimeout:
                results.append(
                    {
                        "model": model,
                        "ok": False,
                        "latency_ms": int(timeout * 1000),
                        "json_ok": False,
                        "schema_ok": False,
                        "empty": False,
                        "error": "timeout",
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive
                results.append(
                    {
                        "model": model,
                        "ok": False,
                        "latency_ms": 0,
                        "json_ok": False,
                        "schema_ok": False,
                        "empty": False,
                        "error": str(exc)[:160],
                    }
                )
    results.sort(
        key=lambda r: (not r.get("schema_ok"), not r["json_ok"], not r["ok"], r["latency_ms"])
    )
    return results


def penalty_reason(result: dict[str, Any]) -> str | None:
    """Map a probe result to a factory penalty reason, or None if the model is
    healthy (probe succeeded with a usable answer)."""
    if result.get("json_ok") and result.get("schema_ok", True):
        return None
    if result.get("empty"):
        return "empty"
    err = (result.get("error") or "").lower()
    if "403" in err or "forbidden" in err or "key limit" in err:
        return "forbidden"
    if "429" in err or "rate limit" in err or "too many requests" in err:
        return "rate_limit"
    return "json_fail"
