from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.config import SUPPORTED_PROVIDERS, save_local_provider_keys
from app.models import LocalPullRequest, LocalUseRequest, ProviderKeysRequest
from app.providers.model_selector import SCORING_MIN_SIZE_B, infer_size_b, rank_models
from app.services import local_models, model_stats
from app.services.model_probe import penalty_reason, probe_models
from app.services.model_scoreboard import scoreboard

if TYPE_CHECKING:
    from app.container import AppContainer


def _clears_quality_floor(model: str) -> bool:
    """A model clears the floor when it advertises no size (unknown -> not
    punished, same as the scorer) or a size at/above ``SCORING_MIN_SIZE_B``.
    Uses the SAME shared floor as scan-scoring (``_SCORING_POLICY``) so the
    report never headlines a model too weak for nuanced job<->CV scoring even
    when it is the healthiest/fastest."""
    size = infer_size_b(model)
    return size == 0 or size >= SCORING_MIN_SIZE_B


def _health_sort_key(model: str, health: dict[str, dict[str, Any]]) -> tuple[int, float, float]:
    """Sort key: healthy (status 0) first, then higher 5-min uptime, then lower
    latency. Models with no stats sort last (treated as unknown, not down)."""
    h = health.get(model)
    if not h:
        return (2, 0.0, float("inf"))
    down = 0 if h.get("status") == 0 else 1
    up5m = h.get("up5m")
    lat = h.get("lat_ms")
    return (
        down,
        -(float(up5m) if isinstance(up5m, (int, float)) else 0.0),
        float(lat) if isinstance(lat, (int, float)) else float("inf"),
    )


def _stats_row(model: str, health: dict[str, dict[str, Any]]) -> dict[str, Any]:
    h = health.get(model) or {}
    return {
        "model": model,
        "status": h.get("status"),
        "up5m": h.get("up5m"),
        "up30m": h.get("up30m"),
        "lat_ms": h.get("lat_ms"),
        "tput": h.get("tput"),
        "ctx": h.get("ctx"),
        "maxc": h.get("maxc"),
    }


#: Where the user's answer to "may I look at this machine?" is kept. A
#: preference, not a secret, and per-install like the machine it describes.
_HARDWARE_PROBE_PREFERENCE = "local_hardware_probe"
#: The last probe result. Hardware does not change between page loads, so the
#: answer is read from here instead of shelling out again (see ``local_status``).
_HARDWARE_SNAPSHOT_PREFERENCE = "local_hardware_snapshot"


def build_router(container: AppContainer) -> APIRouter:
    router = APIRouter()

    def _hardware_probe_allowed() -> bool:
        return container.db.get_preference(_HARDWARE_PROBE_PREFERENCE, "") in ("1", "true", "on")

    @router.get("/api/providers/keys/status")
    def providers_keys_status() -> dict[str, Any]:
        return {
            "ok": True,
            "keys": container.keys_status(),
            "provider": container.providers.metadata(),
        }

    @router.post("/api/providers/keys")
    def save_provider_keys(payload: ProviderKeysRequest) -> dict[str, Any]:
        local_status = save_local_provider_keys(
            data_dir=container.settings.data_dir,
            cerebras_api_key=payload.cerebras_api_key,
            groq_api_key=payload.groq_api_key,
            openai_api_key=payload.openai_api_key,
            anthropic_api_key=payload.anthropic_api_key,
            google_api_key=payload.google_api_key,
            openrouter_api_key=payload.openrouter_api_key,
            deepseek_api_key=payload.deepseek_api_key,
            xai_api_key=payload.xai_api_key,
            glm_api_key=payload.glm_api_key,
            mistral_api_key=payload.mistral_api_key,
            custom_api_key=payload.custom_api_key,
            custom_base_url=payload.custom_base_url,
            primary_provider=payload.primary_provider,
            preferred_model=payload.preferred_model,
            scoring_model=payload.scoring_model,
            chat_model=payload.chat_model,
            cv_model=payload.cv_model,
        )
        container.reload_providers()
        return {
            "ok": True,
            "keys": {**local_status, **container.keys_status()},
            "provider": container.providers.metadata(),
        }

    @router.get("/api/providers/{name}/models")
    def provider_models(name: str, force_refresh: int = 0) -> dict[str, Any]:
        if name not in SUPPORTED_PROVIDERS:
            raise HTTPException(status_code=404, detail="unknown_provider")
        provider = container.providers.providers.get(name)
        if provider is None:
            raise HTTPException(status_code=400, detail="key_missing")
        # Distinguish a revoked/wrong key (present but 401'd) from a missing one
        # so the UI can say "check your key" instead of "add a key".
        if getattr(provider, "key_invalid", False):
            raise HTTPException(status_code=400, detail="key_invalid")
        if not provider.is_available():
            raise HTTPException(status_code=400, detail="key_missing")
        result = container.providers.get_models(name, force_refresh=bool(force_refresh))
        return {"ok": True, "provider": name, **result}

    @router.post("/api/providers/{name}/probe")
    def provider_probe(
        name: str, confirm: bool = False, limit: int = 12, top: int = 3
    ) -> dict[str, Any]:
        """Report how a provider's models are doing.

        Default (``confirm=False``): a FREE report built from OpenRouter's
        published live health (uptime/latency/throughput) — zero inference, so it
        doesn't spend the shared free daily request quota. ``confirm=True``:
        micro-probe only the top ``top`` healthiest models with a tiny JSON call
        (a few inference requests) to confirm they actually return valid JSON for
        our schema, and seed the factory penalty map from the result.
        """
        if name not in SUPPORTED_PROVIDERS:
            raise HTTPException(status_code=404, detail="unknown_provider")
        provider = container.providers.providers.get(name)
        if provider is None:
            raise HTTPException(status_code=400, detail="key_missing")
        if getattr(provider, "key_invalid", False):
            raise HTTPException(status_code=400, detail="key_invalid")
        if not provider.is_available():
            raise HTTPException(status_code=400, detail="key_missing")

        models = container.providers.get_models(name).get("models") or []
        if not models:
            raise HTTPException(status_code=400, detail="no_models")
        # Most promising candidates first (free + fast bias), bounded.
        ranked = rank_models(models, policy={"prefer_free": True, "prefer_fast": True})
        candidates = ranked[: max(1, min(limit, 20))]
        # Live health (OpenRouter only; {} elsewhere) — free, no inference.
        health = model_stats.get_model_health(provider, candidates)
        # Order by health so both the report and the confirm-probe surface the
        # best-looking models first.
        candidates.sort(key=lambda m: _health_sort_key(m, health))

        if not confirm:
            results: list[dict[str, Any]] = [_stats_row(m, health) for m in candidates]
            # Recommend the healthiest model that ALSO clears the quality floor,
            # so the report never headlines a model too small for real work (the
            # scan-scoring path de-ranks sub-floor sizes). ``candidates`` is
            # already health-sorted, so this keeps "healthiest" semantics and
            # merely skips the too-small ones; the displayed list still shows
            # every size. Fall back to the plain healthiest, then to anything.
            healthy = [m for m in candidates if health.get(m, {}).get("status") == 0]
            best = (
                next((m for m in healthy if _clears_quality_floor(m)), None)
                or (healthy[0] if healthy else None)
                or (candidates[0] if candidates else None)
            )
            mode = "stats"
        else:
            top_models = candidates[: max(1, min(top, 5))]
            # The real scoring prompt on a fixed sample offer, not {"ok": true}:
            # passing a two-field toy object says nothing about emitting the
            # app's schema over a full posting, which is the load that truncates.
            probe_results = probe_models(provider, top_models, scoring=True, timeout=60.0)
            # Feed empirical signals back into auto-selection immediately.
            for res in probe_results:
                reason = penalty_reason(res)
                if reason:
                    container.providers.record_model_penalty(name, res["model"], reason)
            results = probe_results
            best = next((r["model"] for r in probe_results if r.get("schema_ok")), None)
            mode = "probe"

        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "mode": mode,
            "results": results,
            "best": best,
            # What each model actually did on real calls, from usage_log: free,
            # and unlike the in-memory penalties it survives a restart.
            "scoreboard": [r.as_dict() for r in scoreboard(container.db)[:12]],
        }
        with contextlib.suppress(Exception):  # persistence is best-effort
            container.db.set_preference(f"model_probe_{name}", json.dumps(payload))
        return {"ok": True, "provider": name, **payload}

    @router.get("/api/providers/health")
    def providers_health(days: int = 14) -> dict[str, Any]:
        """What each provider has actually done lately, read from ``usage_log``.

        Free and instant: no inference, no network — the same records the model
        ranking already uses to de-rank a model that truncates or answers
        garbage. It existed only inside the "Test models" report, so a provider
        silently failing every scoring call looked exactly like a working one.
        """
        records = scoreboard(container.db, endpoint=None, days=max(1, min(days, 90)))
        by_provider: dict[str, dict[str, Any]] = {}
        for record in records:
            entry = by_provider.setdefault(
                record.provider,
                {"provider": record.provider, "calls": 0, "successes": 0, "models": []},
            )
            entry["calls"] += record.calls
            entry["successes"] += record.successes
            entry["models"].append(record.as_dict())
        for entry in by_provider.values():
            entry["models"] = entry["models"][:5]
            entry["success_rate"] = (
                round(entry["successes"] / entry["calls"], 3) if entry["calls"] else 0.0
            )
        return {
            "days": days,
            # Busiest first: that is where a failure costs the most.
            "providers": sorted(by_provider.values(), key=lambda e: e["calls"], reverse=True),
        }

    # ── Local models: what this machine can run, and what it already has ─────

    def _fresh_snapshot() -> dict[str, Any]:
        """Probe the machine and remember the answer."""
        snap = local_models.snapshot()
        with contextlib.suppress(Exception):  # a cache miss is not worth an error
            container.db.set_preference(
                _HARDWARE_SNAPSHOT_PREFERENCE,
                json.dumps(
                    {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "snapshot": snap},
                    ensure_ascii=False,
                ),
            )
        return snap

    def _cached_snapshot() -> dict[str, Any] | None:
        raw = container.db.get_preference(_HARDWARE_SNAPSHOT_PREFERENCE, "")
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        snap = data.get("snapshot") if isinstance(data, dict) else None
        return snap if isinstance(snap, dict) else None

    @router.get("/api/local/status")
    def local_status(refresh: bool = False) -> dict[str, Any]:
        """Hardware, the model sizes it can sustain, and what Ollama already has.

        Nothing is read until the user has said yes. Answering this used to mean
        running ``nvidia-smi``, a PowerShell query for the RAM, a lookup for the
        Ollama binary and — with a GPU present — a request to huggingface.co, all
        from ``bootstrap()`` on every single launch, before anyone had opened
        Settings. Inspecting someone's machine is a thing to ask for, not a side
        effect of starting an app.

        Consent alone was not enough: once granted, the full probe ran again on
        every page load, flashing console windows over the app each time. The
        answer is now remembered, so a reload reads the DB and starts no
        processes. ``refresh=true`` (the Refresh button, and after a download
        changes what is installed) is the only thing that probes again.
        """
        if not _hardware_probe_allowed():
            return {"consent": False}
        if not refresh:
            cached = _cached_snapshot()
            if cached is not None:
                return {"consent": True, "cached": True, **cached}
        return {"consent": True, "cached": False, **_fresh_snapshot()}

    @router.post("/api/local/probe")
    def local_probe() -> dict[str, Any]:
        """Grant the hardware probe, and answer with what it found.

        Consent and result in one round trip: the user clicked a button that
        says "look at this PC", so making them wait for a second request to see
        the answer would be theatre. Remembered from here on.
        """
        container.db.set_preference(_HARDWARE_PROBE_PREFERENCE, "1")
        return {"consent": True, "cached": False, **_fresh_snapshot()}

    @router.post("/api/local/pull")
    def local_pull(payload: LocalPullRequest) -> StreamingResponse:
        """Download a model through Ollama, streaming ITS progress lines.

        Ollama owns quantisation, resume and disk layout; re-implementing that
        against Hugging Face would mean shipping a download manager to save a
        dependency the user already has.
        """
        model = payload.model.strip()
        if not model:
            raise HTTPException(status_code=400, detail="model_required")

        def _events() -> Any:
            try:
                for event in local_models.pull_events(model):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as exc:  # a dead Ollama must not 500 mid-stream
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"

        return StreamingResponse(_events(), media_type="text/event-stream")

    @router.post("/api/local/use")
    def local_use(payload: LocalUseRequest) -> dict[str, Any]:
        """Point the app at a local model for scoring.

        Writes the same settings the provider panel would: the custom endpoint's
        base URL and the model to pin for scan scoring. The key stays empty —
        Ollama does not want one.
        """
        model = payload.model.strip()
        if not model:
            raise HTTPException(status_code=400, detail="model_required")
        base_url = payload.base_url.strip() or local_models.OLLAMA_OPENAI_BASE
        # Ollama serves every model with a 4096-token window unless told
        # otherwise, and a scoring prompt alone is ~2500: without a wider variant
        # every local answer comes back truncated. Free and instant — the copy
        # layers parameters over the same weights.
        pinned = local_models.ensure_scoring_variant(model) if payload.for_scoring else model
        save_local_provider_keys(
            container.settings.data_dir,
            custom_base_url=base_url,
            scoring_model=pinned if payload.for_scoring else None,
            # Pin the provider too: chat and the CV tools stay wherever they are,
            # and an Ollama tag asked of the primary cloud provider is a 404.
            scoring_provider="custom" if payload.for_scoring else None,
        )
        container.reload_providers()
        return {
            "ok": True,
            "scoring_model": pinned if payload.for_scoring else "",
            "scoring_provider": "custom" if payload.for_scoring else "",
            "base_url": base_url,
        }

    return router
