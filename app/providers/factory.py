import os as _os
import random as _random
import time as _time
from collections.abc import Callable
from typing import Any, TypeVar

from app.config import AppSettings
from app.log import get_logger
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import LLMProvider
from app.providers.cerebras_provider import CerebrasProvider
from app.providers.google_provider import GoogleProvider
from app.providers.groq_provider import GroqProvider
from app.providers.model_selector import choose_best_model
from app.providers.openai_provider import OpenAIProvider
from app.providers.openrouter_provider import OpenRouterProvider

_RetryT = TypeVar("_RetryT")

log = get_logger(__name__)

_MODELS_CACHE_TTL_SECONDS = 300.0


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
        }
        self.active_provider: LLMProvider | None = None
        self.active_provider_name: str = "none"
        self.active_model: str = "none"
        self._models_cache: dict[str, tuple[float, list[str]]] = {}

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
                    )
            except Exception as exc:
                log.info(
                    "Provider %s list_models failed, using select_model result: %s",
                    provider_name,
                    exc,
                )

            self.active_provider = provider
            self.active_provider_name = provider.name
            self.active_model = selected_model
            log.info("LLM provider active: %s (model=%s)", provider.name, selected_model)
            return

        self.active_provider = None
        self.active_provider_name = "none"
        self.active_model = "none"
        log.warning("No LLM provider available; chat/LLM features will fall back.")

    def metadata(self) -> dict[str, Any]:
        providers_metadata: dict[str, Any] = {}
        for name, provider in self.providers.items():
            try:
                available = provider.is_available()
            except Exception as exc:
                log.warning("Provider %s is_available error: %s", name, exc)
                available = False

            models: list[str] = []
            if available:
                try:
                    models = provider.list_models()
                except Exception as exc:
                    log.info("Provider %s list_models error: %s", name, exc)
                    models = []

            providers_metadata[name] = {
                "available": available,
                "models": models,
            }

        return {
            "active_provider": self.active_provider_name,
            "active_model": self.active_model,
            "available": self.active_provider is not None,
            "providers": providers_metadata,
        }

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

    def complete_json(
        self,
        prompt: str,
        max_tokens: int = 700,
        provider_name: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        provider = self.providers.get(provider_name) if provider_name else self.active_provider
        active_model = model_name if model_name else self.active_model
        if not provider:
            raise RuntimeError("No LLM provider available")
        return _with_retry(
            lambda: provider.complete_json(
                prompt=prompt, model=active_model, max_tokens=max_tokens
            ),
            provider_label=provider_name or self.active_provider_name or "unknown",
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 700,
        provider_name: str | None = None,
        model_name: str | None = None,
    ) -> str:
        provider = self.providers.get(provider_name) if provider_name else self.active_provider
        active_model = model_name if model_name else self.active_model
        if not provider:
            raise RuntimeError("No LLM provider available")
        return _with_retry(
            lambda: provider.chat(messages=messages, model=active_model, max_tokens=max_tokens),
            provider_label=provider_name or self.active_provider_name or "unknown",
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


def _with_retry(fn: Callable[[], _RetryT], provider_label: str) -> _RetryT:
    max_attempts = max(1, int(_os.environ.get("LLM_MAX_RETRIES", "3")))
    base = float(_os.environ.get("LLM_RETRY_BASE_SECONDS", "1.0"))
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= max_attempts or not _is_retryable(exc):
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
