import functools
import os as _os
import random as _random
import threading
import time as _time
from collections.abc import Callable
from typing import Any, TypeVar

from app.config import AppSettings
from app.log import get_logger
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import (
    EmptyCompletionError,
    LLMProvider,
    TruncatedCompletionError,
    is_unauthorized,
)
from app.providers.cerebras_provider import CerebrasProvider
from app.providers.google_provider import GoogleProvider
from app.providers.groq_provider import GroqProvider
from app.providers.model_selector import (
    SCORING_MIN_SIZE_B,
    choose_best_model,
    is_scoring_fit,
    rank_models,
)
from app.providers.openai_compat import (
    CustomOpenAIProvider,
    DeepSeekProvider,
    GLMProvider,
    MistralProvider,
    XAIProvider,
)
from app.providers.openai_provider import OpenAIProvider
from app.providers.openrouter_provider import OpenRouterProvider
from app.services import local_models, model_stats

_RetryT = TypeVar("_RetryT")

log = get_logger(__name__)

_MODELS_CACHE_TTL_SECONDS = 300.0
# Health endpoint hits this every poll; keep responses fast and avoid hammering
# providers that have invalid keys. Shorter than _MODELS_CACHE because settings
# changes invalidate this cache anyway.
_METADATA_CACHE_TTL_SECONDS = 60.0
# After a provider is flagged key_invalid (401), re-probe it once this many
# seconds have passed — a transient 401 shouldn't disable it for the whole
# session. Env-overridable like the retry knobs.
_KEY_INVALID_COOLDOWN_SECONDS = float(_os.environ.get("LLM_KEY_INVALID_COOLDOWN_SECONDS", "600"))
# After a model returns a persistent 429 (rate limit), avoid auto-picking it for
# this many seconds so selection rotates to another model.
_MODEL_429_COOLDOWN_SECONDS = float(_os.environ.get("LLM_MODEL_429_COOLDOWN_SECONDS", "300"))
# Per-reason cooldowns (seconds) for the empirical model-penalty map: after a
# model fails a given way, auto-selection de-ranks it for this long so the next
# request rotates to a healthier one. 429/403 are persistent (throttled / no
# credits); empty-content and json_fail are softer/transient.
_MODEL_PENALTY_COOLDOWNS = {
    "rate_limit": _MODEL_429_COOLDOWN_SECONDS,
    "forbidden": _MODEL_429_COOLDOWN_SECONDS,
    "empty": 180.0,
    "json_fail": 180.0,
    # Truncation (finish_reason=length) is a structural mismatch — the model
    # burns the token budget on hidden reasoning before finishing the JSON — not
    # a transient blip, so keep it de-ranked for the whole scan. run_scan clears
    # "truncated" penalties at the start of each scan, so it's re-evaluated fresh
    # next run (sticky within a scan, reset between).
    "truncated": 3600.0,
    # The reply had no usable shape at all (choices=None from a gateway fronting a
    # broken upstream, or an SDK object we can't read). Structural like truncation
    # — retrying the same model just burns the quota — so same long cooldown.
    "malformed": 3600.0,
    # A model that hangs costs the wall-clock timeout on every attempt (measured:
    # 183s per call, 14 of them in one scan). De-rank for a while, but shorter
    # than the structural reasons: it can be the host being busy, not the model.
    "timeout": 900.0,
}

# How long the usage_log-derived "unfit for scoring" set is reused before being
# recomputed. Long enough that a scan doesn't re-query per failover attempt,
# short enough that a model recovering shows up within the same session.
_UNFIT_CACHE_SECONDS = 600.0


def _scoring_variant_in(models: list[str]) -> str | None:
    """The wide-context scoring variant among a local server's models, if there.

    Ollama lists a model under its full tag, so the variant the app created
    appears as ``jobfinder-scorer:latest`` while the app pins it by bare name.
    Both spellings are the same model and both must be recognised.
    """
    variant = local_models.SCORING_VARIANT
    return next(
        (m for m in models if m == variant or m.startswith(f"{variant}:")),
        None,
    )


class ProviderManager:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.providers: dict[str, LLMProvider] = {
            "cerebras": CerebrasProvider(api_key=settings.cerebras_api_key),
            "groq": GroqProvider(api_key=settings.groq_api_key),
            "openai": OpenAIProvider(api_key=settings.openai_api_key),
            "anthropic": AnthropicProvider(api_key=settings.anthropic_api_key),
            "google": GoogleProvider(api_key=settings.google_api_key),
            "openrouter": OpenRouterProvider(api_key=settings.openrouter_api_key),
            "deepseek": DeepSeekProvider(api_key=settings.deepseek_api_key),
            "xai": XAIProvider(api_key=settings.xai_api_key),
            "glm": GLMProvider(api_key=settings.glm_api_key, base_url=settings.glm_base_url),
            "mistral": MistralProvider(api_key=settings.mistral_api_key),
            "custom": CustomOpenAIProvider(
                api_key=settings.custom_api_key, base_url=settings.custom_base_url
            ),
        }
        self.active_provider: LLMProvider | None = None
        self.active_provider_name: str = "none"
        self.active_model: str = "none"
        self._models_cache: dict[str, tuple[float, list[str]]] = {}
        self._metadata_cache: tuple[float, dict[str, Any]] | None = None
        # When each provider was first observed key_invalid (for the re-probe
        # cooldown). Reset for free on reload_providers (new manager instance).
        self._key_invalid_since: dict[str, float] = {}
        # Empirical per-(provider, model) penalty for auto-selection de-ranking.
        # Key = "provider::model", value = (timestamp, reason). Populated from
        # real call outcomes (429/403/json_fail/empty) and by the model probe;
        # in-memory with per-reason TTL so it self-heals across the session.
        self._model_penalty: dict[str, tuple[float, str]] = {}
        # Guards _model_penalty: scan workers record penalties concurrently
        # while others prune/reassign the map in _penalized_model_ids -- an
        # unlocked interleave loses updates (or raises RuntimeError mid-iter).
        self._penalty_lock = threading.Lock()
        # Set by AppContainer after the DB is open; ``_record_call`` uses it
        # to persist token usage. None = no-op (unit tests, isolated usage).
        self._db: Any = None
        # provider -> (timestamp, model ids with a bad recorded scoring record).
        # Read from usage_log, so it survives restarts unlike _model_penalty.
        self._unfit_cache: dict[str, tuple[float, set[str]]] = {}
        # provider -> when a PAID model there returned 403 "key limit exceeded".
        # One such answer says something about the ACCOUNT, not the model: every
        # other paid model on that provider will answer the same way. Measured on
        # the 2026-07-27 scan: 88 of 191 calls were 403s, each one a different
        # paid OpenRouter model discovering the same missing credit.
        self._no_credit_since: dict[str, float] = {}

    def initialize(self) -> None:
        """Pick first available provider from configured order and select a model."""
        for provider_name in self.settings.llm_provider_order:
            provider = self.providers.get(provider_name)
            if not provider:
                log.debug("Unknown provider in order: %s", provider_name)
                continue

            try:
                available = provider.is_available()
            except Exception as exc:
                log.warning("Provider %s is_available() raised: %s", provider_name, exc)
                continue

            if not available:
                log.debug("Provider %s not available (no key)", provider_name)
                continue

            try:
                selected_model = provider.select_model(
                    preferred_model=self.settings.preferred_model
                )
            except Exception as exc:
                log.warning("Provider %s select_model failed: %s", provider_name, exc)
                continue

            try:
                models = provider.list_models()
                if models:
                    selected_model = choose_best_model(
                        models=models,
                        preferred_model=self.settings.preferred_model,
                        policy=self.settings.model_selection_policy,
                        penalized=self._penalized_model_ids(provider_name),
                    )
            except Exception as exc:
                log.info(
                    "Provider %s list_models failed, using select_model result: %s",
                    provider_name,
                    exc,
                )

            # ``select_model``/``list_models`` return a fallback string without
            # raising even when the key is revoked (they flip ``key_invalid`` on
            # a 401). Committing here would keep a dead provider "active" and
            # brick every LLM call until the user changed the primary by hand.
            # Skip it so the next configured provider gets a chance.
            if getattr(provider, "key_invalid", False):
                log.info(
                    "Provider %s key invalid (401); skipping to next provider.",
                    provider_name,
                )
                continue

            self.active_provider = provider
            self.active_provider_name = provider.name
            self.active_model = selected_model
            log.info("LLM provider active: %s (model=%s)", provider.name, selected_model)
            return

        self.active_provider = None
        self.active_provider_name = "none"
        self.active_model = "none"
        log.warning("No LLM provider available; chat/LLM features will fall back.")

    def metadata(self, force_refresh: bool = False) -> dict[str, Any]:
        """Aggregate provider availability + model lists. Cached for 60s.

        Skips ``list_models()`` on providers flagged ``key_invalid`` so a stale
        / revoked key (e.g. expired Cerebras free tier) doesn't trigger an HTTP
        401 on every health poll.
        """
        now = _time.time()
        if (
            not force_refresh
            and self._metadata_cache is not None
            and now - self._metadata_cache[0] < _METADATA_CACHE_TTL_SECONDS
        ):
            return self._metadata_cache[1]

        providers_metadata: dict[str, Any] = {}
        for name, provider in self.providers.items():
            try:
                available = provider.is_available()
            except Exception as exc:
                log.warning("Provider %s is_available error: %s", name, exc)
                available = False

            key_invalid = self._key_invalid_active(name, provider)
            models: list[str] = []
            # Skip the network call when we already know the key is bad — it
            # will only produce another 401 in the log.
            if available and not key_invalid:
                try:
                    models = provider.list_models()
                except Exception as exc:
                    log.info("Provider %s list_models error: %s", name, exc)
                    models = []
                # The provider may have flipped key_invalid during list_models.
                key_invalid = self._key_invalid_active(name, provider)

            providers_metadata[name] = {
                "available": available and not key_invalid,
                "models": models,
                "key_invalid": key_invalid,
            }

        result = {
            "active_provider": self.active_provider_name,
            "active_model": self.active_model,
            "available": self.active_provider is not None,
            "providers": providers_metadata,
        }
        self._metadata_cache = (now, result)
        return result

    def invalidate_caches(self) -> None:
        """Clear metadata + models caches and reset key_invalid flags.

        Call after the user saves provider keys so the next ``metadata()`` reflects
        the new configuration without waiting for the 60s TTL.
        """
        self._metadata_cache = None
        self._models_cache = {}
        self._key_invalid_since = {}
        for provider in self.providers.values():
            if hasattr(provider, "key_invalid"):
                provider.key_invalid = False

    def _key_invalid_active(self, name: str, provider: LLMProvider) -> bool:
        """True while a provider's key_invalid flag should still exclude it.

        Records when the flag was first seen; after
        ``_KEY_INVALID_COOLDOWN_SECONDS`` it clears the flag (one re-probe) so
        the next call/metadata tries the provider again. If the key is still
        bad, the live call 401s and it gets re-flagged — bounded, not permanent.
        """
        if not getattr(provider, "key_invalid", False):
            self._key_invalid_since.pop(name, None)
            return False
        now = _time.time()
        since = self._key_invalid_since.get(name)
        if since is None:
            self._key_invalid_since[name] = now
            return True
        if now - since >= _KEY_INVALID_COOLDOWN_SECONDS:
            provider.key_invalid = False
            self._key_invalid_since.pop(name, None)
            return False
        return True

    def record_model_penalty(self, provider_name: str, model: str, reason: str) -> None:
        """De-rank a (provider, model) after an empirical failure. Also called by
        the model probe to seed penalties for dead/empty models."""
        with self._penalty_lock:
            self._model_penalty[f"{provider_name}::{model}"] = (_time.time(), reason)

    def clear_model_penalties(self, reason: str | None = None) -> None:
        """Drop empirical model penalties — all of them, or only those with the
        given ``reason``. Called at scan start for ``"truncated"`` so each scan
        re-evaluates models fresh (truncation is sticky WITHIN a scan via its long
        cooldown, but reset between scans)."""
        with self._penalty_lock:
            if reason is None:
                self._model_penalty = {}
            else:
                self._model_penalty = {
                    key: val for key, val in self._model_penalty.items() if val[1] != reason
                }

    #: How long a "this account has no credit" observation is trusted. Credit
    #: does not appear in the middle of a scan, and re-discovering it costs one
    #: wasted call per paid model in the catalog.
    _NO_CREDIT_TTL_SECONDS = 1800.0

    def mark_no_credit(self, provider_name: str) -> None:
        """Remember that a PAID model on this provider answered 403."""
        with self._penalty_lock:
            self._no_credit_since[provider_name] = _time.time()

    def _has_no_credit(self, provider_name: str) -> bool:
        with self._penalty_lock:
            since = self._no_credit_since.get(provider_name)
        return bool(since and _time.time() - since < self._NO_CREDIT_TTL_SECONDS)

    def _policy_for(
        self, provider_name: str, policy_override: dict[str, Any] | None
    ) -> dict[str, Any]:
        """The caller's model policy, adjusted for the provider it will run on.

        Two things a policy cannot know on its own:

        - ``:free`` is an OpenRouter naming convention, not a fact about price.
          Everything on Cerebras, Groq and Google AI Studio is free tier and none
          of it carries the suffix, so the "not free" penalty was de-ranking
          exactly the models that work (measured 2026-07-27: Cerebras' gemma-4-31b,
          8 successes out of 10, ranked below a paid gpt-4-turbo that 403s).
        - A local endpoint holds the one or two models the user deliberately
          downloaded and can actually run — not a catalog of three hundred to be
          protected from. The 26B floor would rule out the only model available
          (a 12B is what fits on a 12GB card).
        """
        effective = dict(policy_override or self.settings.model_selection_policy or {})
        effective["paid_by_name"] = provider_name == "openrouter"
        if provider_name == "custom":
            effective["hard_floor"] = False
            effective["min_size_b"] = 0
        return effective

    def _penalized_model_ids(self, provider_name: str) -> set[str]:
        """Model ids currently penalized for ``provider_name`` (stale entries
        pruned per-reason). Fed to rank_models as ``penalized=`` to sink them
        below healthy models without excluding them."""
        now = _time.time()
        fresh: dict[str, tuple[float, str]] = {}
        penalized: set[str] = set()
        prefix = f"{provider_name}::"
        with self._penalty_lock:
            for key, (ts, reason) in self._model_penalty.items():
                cooldown = _MODEL_PENALTY_COOLDOWNS.get(reason, _MODEL_429_COOLDOWN_SECONDS)
                if now - ts < cooldown:
                    fresh[key] = (ts, reason)
                    if key.startswith(prefix):
                        penalized.add(key[len(prefix) :])
            self._model_penalty = fresh
        return penalized

    def get_models(self, provider_name: str, force_refresh: bool = False) -> dict[str, Any]:
        """Return models + recommended for a single provider, cached for 5 min."""
        provider = self.providers.get(provider_name)
        if not provider:
            return {"models": [], "recommended": None, "cached": False, "fetched_at": 0.0}
        if not provider.is_available():
            return {"models": [], "recommended": None, "cached": False, "fetched_at": 0.0}

        now = _time.time()
        cached_entry = self._models_cache.get(provider_name)
        if (
            not force_refresh
            and cached_entry is not None
            and now - cached_entry[0] < _MODELS_CACHE_TTL_SECONDS
        ):
            models = cached_entry[1]
            cached = True
            fetched_at = cached_entry[0]
        else:
            try:
                models = provider.list_models()
            except Exception as exc:
                log.warning("Provider %s list_models() raised: %s", provider_name, exc)
                models = []
            self._models_cache[provider_name] = (now, models)
            cached = False
            fetched_at = now

        recommended = (
            choose_best_model(
                models=models,
                preferred_model=self.settings.preferred_model,
                policy=self.settings.model_selection_policy,
            )
            if models
            else None
        )
        return {
            "models": models,
            "recommended": recommended,
            "cached": cached,
            "fetched_at": fetched_at,
        }

    def _record_call(
        self,
        provider: LLMProvider,
        model: str,
        endpoint: str,
        success: bool,
        error_type: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Persist token usage to ``usage_log`` after a call. Best-effort.

        ``self._db`` is set externally by the AppContainer once the DB is open;
        when missing (unit tests), recording silently no-ops.
        """
        db = getattr(self, "_db", None)
        if db is None:
            return
        try:
            from app.services.usage_tracker import record_usage

            record_usage(
                db,
                provider=provider.name,
                model=model,
                endpoint=endpoint,
                last_usage=getattr(provider, "last_usage", None),
                success=success,
                error_type=error_type,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            log.debug("usage record skipped: %s", exc)

    def _empirically_unfit(self, provider_name: str) -> set[str]:
        """Models with a bad recorded track record for JSON scoring on this
        provider. Cached for the lifetime of a scan-ish window so the query
        doesn't run per failover attempt. Never raises."""
        db = getattr(self, "_db", None)
        if db is None:
            return set()
        now = _time.time()
        cached = self._unfit_cache.get(provider_name)
        if cached and now - cached[0] < _UNFIT_CACHE_SECONDS:
            return cached[1]
        try:
            from app.services.model_scoreboard import unfit_ids

            unfit = unfit_ids(db, provider_name)
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("scoreboard lookup skipped: %s", exc)
            return set()
        self._unfit_cache[provider_name] = (now, unfit)
        if unfit:
            log.info(
                "%s: de-ranking %d model(s) on their recorded record", provider_name, len(unfit)
            )
        return unfit

    def _ranked_models_for(
        self,
        provider: LLMProvider,
        limit: int,
        policy_override: dict[str, Any] | None = None,
        *,
        ignore_penalties: bool = False,
    ) -> list[str]:
        """Up to ``limit`` models to try on ``provider``, best-first.

        Recent-429 models are de-ranked (sunk to the bottom) so a single request
        rotates onto a fresh model of the SAME provider before failing over to
        another provider. Uses the 5-min cached catalog (``get_models``) so this
        adds no per-request network call on the happy path. Falls back to a
        single best-effort model when no live catalog is available — but NOT a
        just-penalized one (return ``[]`` so failover skips this provider),
        unless ``ignore_penalties`` (the anti-brick last resort) is set.
        """
        penalized = set() if ignore_penalties else self._penalized_model_ids(provider.name)
        # The caller's policy, adjusted for WHICH provider this is. Copied, never
        # mutated: _SCORING_POLICY is a module-level dict shared by every call.
        effective_policy = self._policy_for(provider.name, policy_override)
        models = self.get_models(provider.name).get("models") or []
        if models:

            def _rank(pool: list[str], pen: set[str], lim: int) -> list[str]:
                return rank_models(
                    pool,
                    preferred_model=None if policy_override else self.settings.preferred_model,
                    policy=effective_policy,
                    penalized=pen,
                    limit=lim,
                )

            pool = models
            # Known to have no credit: paid models here answer 403 without being
            # asked. Only narrow when free models remain — a provider whose whole
            # catalog is paid still gets its normal (failing) chance rather than
            # being silently dropped from the failover chain.
            if effective_policy["paid_by_name"] and self._has_no_credit(provider.name):
                free_only = [m for m in pool if m.endswith(":free")]
                if free_only:
                    pool = free_only
            # What this model actually DID here, read back from usage_log: the
            # in-memory penalty map forgets everything on restart, so a model
            # that truncates every JSON call was re-elected on every boot. Free
            # (no inference, no network) and persistent. Scoring calls only.
            if not ignore_penalties and (policy_override or {}).get("hard_floor"):
                penalized = penalized | self._empirically_unfit(provider.name)
            # OpenRouter exposes free live health stats (uptime/latency, no
            # inference). Fold models that are down RIGHT NOW into the penalized
            # set so scoring rotates off them before hitting a 429. Bounded to the
            # name-ranked shortlist so we never fetch stats for the whole catalog;
            # empty health (non-OR / network down) leaves behaviour unchanged.
            if provider.name == "openrouter":
                # From ``pool``, not from the full catalog: the no-credit filter
                # above narrowed it, and re-ranking ``models`` here would undo it.
                shortlist = _rank(pool, penalized, max(limit * 4, limit))
                health = model_stats.get_model_health(provider, shortlist)
                if health:
                    penalized = penalized | model_stats.unhealthy_ids(health)
                    pool = shortlist
            ranked = _rank(pool, penalized, limit)
            # A local endpoint is not a catalog to be chosen from: it holds the
            # one or two models the user downloaded, plus the wide-context
            # variant this app derived for scoring. That variant cannot win on
            # name — "jobfinder-scorer" states neither family nor size, so the
            # ranking prefers the raw gemma tag it was built FROM, whose 4096
            # token default context truncates every scoring reply (measured
            # 2026-07-28: 0 usable answers out of 30, and the scan fell through
            # to whatever cloud model the failover reached). Put it first.
            if provider.name == "custom" and (policy_override or {}).get("hard_floor"):
                variant = _scoring_variant_in(models)
                # Not when it is penalized: a variant that keeps failing here has
                # earned its place at the bottom, and the point of this promotion
                # is the right model, not a favoured one.
                if variant and variant not in penalized:
                    ranked = [variant, *(m for m in ranked if m != variant)][:limit]
            if ranked:
                return ranked
        policy = effective_policy
        try:
            fallback = provider.select_model(preferred_model=self.settings.preferred_model)
        except Exception:
            fallback = (
                self.settings.preferred_model
                or self.active_model
                or str(getattr(provider, "default_model", ""))
            )
        # rank_models only de-ranks penalized models (the catalog path always
        # has alternatives); this single-model fallback has none, so proposing
        # a penalized model would re-run the exact failure we just recorded.
        if fallback in penalized:
            return []
        # select_model() knows nothing about the caller's policy, so under a hard
        # floor (scan scoring) it can hand back exactly the toy/reasoning model the
        # floor exists to keep out. Skip the provider instead.
        if policy.get("hard_floor") and not is_scoring_fit(
            fallback, int(policy.get("min_size_b", SCORING_MIN_SIZE_B) or SCORING_MIN_SIZE_B)
        ):
            log.info("%s: no scoring-fit model available (fallback=%s)", provider.name, fallback)
            return []
        return [fallback]

    # Max models to try on a single provider within one request before failing
    # over to the next provider. Bounds total attempts (K on the active/chosen
    # provider, 1 on each other) so a persistent 429 rotates quickly.
    _INTRA_PROVIDER_MODELS = 3

    def _failover_candidates(
        self,
        explicit_provider: str | None,
        explicit_model: str | None,
        policy_override: dict[str, Any] | None = None,
    ) -> list[tuple[LLMProvider, str]]:
        """Ordered ``[(provider, model)]`` to attempt for one request.

        Within a provider we now try up to ``_INTRA_PROVIDER_MODELS`` models
        (best-first, recent-429 de-ranked) BEFORE failing over to the next
        provider — so a single OpenRouter :free model going 429 rotates onto
        another OpenRouter model instead of dropping to the canned fallback when
        no other provider key is configured. An explicit provider+model is
        honored as-is (the user chose it). Cross-provider failover (active first,
        then the rest of ``llm_provider_order``) is preserved.
        """
        K = self._INTRA_PROVIDER_MODELS
        if explicit_provider:
            provider = self.providers.get(explicit_provider)
            if provider is None:
                return []
            if explicit_model:
                return [(provider, explicit_model)]
            ranked = self._ranked_models_for(provider, K, policy_override)
            if not ranked:
                # Anti-brick: an explicitly chosen provider whose only fallback
                # model is penalized still beats returning nothing.
                ranked = self._ranked_models_for(
                    provider, K, policy_override, ignore_penalties=True
                )
            return [(provider, m) for m in ranked]

        def _build(ignore_penalties: bool) -> list[tuple[LLMProvider, str]]:
            candidates: list[tuple[LLMProvider, str]] = []
            seen_pairs: set[tuple[str, str]] = set()
            seen_providers: set[str] = set()

            def add(provider: LLMProvider, model: str) -> None:
                pair = (provider.name, model)
                if model and pair not in seen_pairs:
                    candidates.append((provider, model))
                    seen_pairs.add(pair)

            if self.active_provider is not None:
                for model in self._ranked_models_for(
                    self.active_provider, K, policy_override, ignore_penalties=ignore_penalties
                ):
                    add(self.active_provider, model)
                seen_providers.add(self.active_provider.name)

            for name in self.settings.llm_provider_order:
                if name in seen_providers:
                    continue
                provider = self.providers.get(name)
                if provider is None:
                    continue
                try:
                    # Cooldown check first so it always runs (it may clear the flag
                    # after the window); is_available then reflects the fresh state.
                    usable = (
                        not self._key_invalid_active(name, provider) and provider.is_available()
                    )
                except Exception:
                    usable = False
                if not usable:
                    continue
                for model in self._ranked_models_for(
                    provider, 1, policy_override, ignore_penalties=ignore_penalties
                ):
                    add(provider, model)
                seen_providers.add(name)
            return candidates

        candidates = _build(False)
        if not candidates:
            # Anti-brick: every no-catalog fallback model is penalized — a
            # penalized model still beats "No LLM provider available".
            candidates = _build(True)
        return candidates

    def _maybe_flag_key_invalid(self, provider: LLMProvider, exc: Exception) -> None:
        """Flag a provider whose key just 401'd during a live call.

        Previously only ``list_models`` set this, so a key that went bad after
        startup kept the provider "healthy" in the UI while every chat silently
        degraded. Flagging here makes the invalid-key warning surface and lets
        failover skip it next time.
        """
        if is_unauthorized(exc) and not getattr(provider, "key_invalid", False):
            provider.key_invalid = True
            self._key_invalid_since[provider.name] = _time.time()
            log.warning("Provider %s key marked invalid (401) during live call.", provider.name)

    def _run_with_failover(
        self,
        *,
        endpoint: str,
        explicit_provider: str | None,
        explicit_model: str | None,
        call: Callable[[LLMProvider, str], _RetryT],
        policy_override: dict[str, Any] | None = None,
    ) -> _RetryT:
        candidates = self._failover_candidates(explicit_provider, explicit_model, policy_override)
        if not candidates:
            raise RuntimeError("No LLM provider available")
        last_exc: Exception | None = None
        last_empty: _RetryT | None = None
        for idx, (provider, model) in enumerate(candidates):
            _t0 = _time.time()
            try:
                result = _with_retry(
                    functools.partial(call, provider, model),
                    provider_label=provider.name,
                )
            except Exception as exc:
                elapsed_ms = int((_time.time() - _t0) * 1000)
                last_exc = exc
                reason = _classify_failure(exc)
                if reason:
                    self.record_model_penalty(provider.name, model, reason)
                # A 403 on a PAID model is a statement about the account, not
                # about that model: stop offering every other paid model on this
                # provider the same chance to discover it.
                if reason == "forbidden" and not str(model).endswith(":free"):
                    self.mark_no_credit(provider.name)
                self._maybe_flag_key_invalid(provider, exc)
                self._record_call(provider, model, endpoint, False, type(exc).__name__, elapsed_ms)
                if idx < len(candidates) - 1:
                    log.warning(
                        "Provider %s failed (%s); failing over to next provider.",
                        provider.name,
                        exc.__class__.__name__,
                    )
                continue
            elapsed_ms = int((_time.time() - _t0) * 1000)
            if _is_empty_result(result):
                # Some free models 200 with empty content (reasoning-only) — a
                # successful-but-useless reply. De-rank AND try the next
                # candidate; returning it would poison callers (e.g. a scored
                # job persisted as {} is never re-scored).
                self.record_model_penalty(provider.name, model, "empty")
                self._record_call(provider, model, endpoint, True, "empty_result", elapsed_ms)
                last_empty = result
                if idx < len(candidates) - 1:
                    log.warning(
                        "Provider %s returned an empty result; failing over.",
                        provider.name,
                    )
                continue
            self._record_call(provider, model, endpoint, True, None, elapsed_ms)
            return result
        if last_empty is not None:
            # Every candidate came back empty: keep the "empty never raises"
            # contract — callers (chat/scan/generation) have their own fallbacks.
            return last_empty
        assert last_exc is not None
        raise last_exc

    def pin_kwargs(
        self, model_id: str | None, policy_override: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Kwargs for complete_json/chat honoring a per-context model override.

        If the user pinned a specific model (``model_id``), return an explicit
        (primary provider, model) pin — a deliberate choice, so NO failover.
        Otherwise fall back to the auto-selection ``policy_override``.
        """
        if model_id:
            primary = (
                self.settings.llm_provider_order[0] if self.settings.llm_provider_order else None
            )
            return {"provider_name": primary, "model_name": model_id}
        return {"policy_override": policy_override}

    def complete_json(
        self,
        prompt: str,
        max_tokens: int = 700,
        provider_name: str | None = None,
        model_name: str | None = None,
        policy_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._run_with_failover(
            endpoint="complete_json",
            explicit_provider=provider_name,
            explicit_model=model_name,
            call=lambda p, m: p.complete_json(prompt=prompt, model=m, max_tokens=max_tokens),
            policy_override=policy_override,
        )

    def preview_scoring_model(self, policy_override: dict[str, Any] | None = None) -> str:
        """Best (provider, model) the next scoring call would try — for logging.

        Returns just the model id of the first failover candidate under
        ``policy_override``, or ``"none"`` when no provider is available.
        """
        candidates = self._failover_candidates(None, None, policy_override)
        return candidates[0][1] if candidates else "none"

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 700,
        provider_name: str | None = None,
        model_name: str | None = None,
        policy_override: dict[str, Any] | None = None,
    ) -> str:
        return self._run_with_failover(
            endpoint="chat",
            explicit_provider=provider_name,
            explicit_model=model_name,
            call=lambda p, m: p.chat(messages=messages, model=m, max_tokens=max_tokens),
            policy_override=policy_override,
        )


# ─── Retry helper ──────────────────────────────────────────────

_RETRYABLE_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "timeout",
    "connection reset",
    "rate limit",
    "too many requests",
    "queue_exceeded",
    "service unavailable",
    "temporarily unavailable",
)


def _is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int) and status in (408, 409, 425, 429, 500, 502, 503, 504):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _RETRYABLE_MARKERS)


def _is_rate_limited(exc: Exception) -> bool:
    """True for a 429 specifically (not 5xx) — used to de-rank a model."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 429:
        return True
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


def _is_forbidden(exc: Exception) -> bool:
    """True for a 403 — on a credit-less account paid models 403 ("key limit
    exceeded"); such a model should be de-ranked, not retried."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 403:
        return True
    text = str(exc).lower()
    return "403" in text or "forbidden" in text or "key limit exceeded" in text


def _classify_failure(exc: Exception) -> str | None:
    """Map a failed LLM call to an empirical-penalty reason, or None when the
    failure shouldn't de-rank the model (transient network/5xx — retry handles
    those; 401 is handled separately at the provider level)."""
    if isinstance(exc, TruncatedCompletionError):
        # Checked before ValueError (its base): a max_tokens cutoff is a
        # structural mismatch, not a generic "won't emit JSON".
        return "truncated"
    if isinstance(exc, EmptyCompletionError):
        # Also a ValueError subclass — must precede the json_fail branch.
        return "malformed"
    if _is_rate_limited(exc):
        return "rate_limit"
    if _is_forbidden(exc):
        return "forbidden"
    if isinstance(exc, ValueError):  # "Nessun JSON trovato" — model won't emit JSON
        return "json_fail"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, TypeError | AttributeError | KeyError):
        # A reply we couldn't even walk (unexpected payload shape). Returning
        # None here is what let a model that raised TypeError on every call stay
        # top-ranked and be re-picked for a whole scan (measured: 28 attempts,
        # 11 of them TypeError). Treat "we can't read your answer" as the
        # model's problem and rotate off it.
        return "malformed"
    return None


def _is_empty_result(result: Any) -> bool:
    """A successful-but-useless reply: empty/whitespace string or empty dict."""
    if result is None:
        return True
    if isinstance(result, str):
        return not result.strip()
    if isinstance(result, dict):
        return not result
    return False


def _call_with_timeout(fn: Callable[[], _RetryT], timeout: float) -> _RetryT:
    """Run ``fn`` with a wall-clock timeout on a dedicated daemon thread.

    A per-call thread (rather than a bounded pool) means a hung provider call
    can't exhaust a fixed worker set and block other calls — the abandoned
    thread just lingers until the underlying call returns. On timeout, raises
    ``TimeoutError`` (which ``_is_retryable`` treats as retryable), so the
    caller — and any SSE stream — regains control immediately. ``timeout <= 0``
    disables the guard. Signal-based alarms aren't used (they don't work on
    Windows or in worker threads).
    """
    if timeout <= 0:
        return fn()
    box: list[_RetryT] = []
    err: list[Exception] = []
    done = threading.Event()

    def _runner() -> None:
        try:
            box.append(fn())
        except Exception as exc:
            err.append(exc)
        finally:
            done.set()

    threading.Thread(target=_runner, name="llm-call", daemon=True).start()
    if not done.wait(timeout):
        raise TimeoutError(f"LLM request exceeded timeout of {timeout:.0f}s")
    if err:
        raise err[0]
    return box[0]


#: A model on the user's own GPU answers in tens of seconds, not in one or two:
#: measured on an RTX 5070, a 12B Q4 takes 45-58s to write a full scoring JSON
#: (~1200 tokens at ~22 tok/s), and the first call of the session also loads 6 GB
#: into VRAM. The 60s ceiling that keeps a hung cloud provider from burning a
#: scan would cut off every single local answer.
_LOCAL_TIMEOUT_SECONDS = 300.0
_LOCAL_PROVIDERS = ("custom",)


def _with_retry(fn: Callable[[], _RetryT], provider_label: str) -> _RetryT:
    max_attempts = max(1, int(_os.environ.get("LLM_MAX_RETRIES", "3")))
    base = float(_os.environ.get("LLM_RETRY_BASE_SECONDS", "1.0"))
    timeout = float(_os.environ.get("LLM_REQUEST_TIMEOUT_SECONDS", "60"))
    if provider_label in _LOCAL_PROVIDERS:
        timeout = max(timeout, _LOCAL_TIMEOUT_SECONDS)
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _call_with_timeout(fn, timeout)
        except Exception as exc:
            last_exc = exc
            # Fail fast on 429 AND on a wall-clock timeout: don't hammer the same
            # model — let _run_with_failover rotate to the next model/provider
            # immediately. A timeout costs the FULL timeout window per attempt
            # (measured: 3 x 60s = 183s burned on one dead model, 14 times in a
            # single scan), and a model that hung once almost always hangs again.
            # (5xx / connection resets still retry with backoff.)
            if (
                attempt >= max_attempts
                or not _is_retryable(exc)
                or _is_rate_limited(exc)
                or isinstance(exc, TimeoutError)
            ):
                raise
            delay = base * (2 ** (attempt - 1))
            jitter = delay * 0.3 * (2 * _random.random() - 1)
            wait = max(0.1, delay + jitter)
            log.warning(
                "Provider %s attempt %d/%d failed (%s); retrying in %.2fs",
                provider_label,
                attempt,
                max_attempts,
                exc.__class__.__name__,
                wait,
            )
            _time.sleep(wait)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("retry loop exited unexpectedly")
