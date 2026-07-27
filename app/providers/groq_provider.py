import json
import re
from typing import Any, cast

from app.log import get_logger
from app.providers.base import (
    EmptyCompletionError,
    LLMProvider,
    TruncatedCompletionError,
    extract_usage,
    first_choice,
    is_transport_failure,
    is_truncated,
    is_unauthorized,
)
from app.providers.model_selector import choose_best_model

log = get_logger(__name__)

try:
    from groq import Groq
except Exception:  # pragma: no cover
    Groq = None  # type: ignore[assignment,misc]


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Nessun JSON trovato")
    return cast(dict[str, Any], json.loads(match.group()))


class GroqProvider(LLMProvider):
    name = "groq"

    def __init__(self, api_key: str | None):
        self.api_key = api_key
        self.client = Groq(api_key=api_key) if (api_key and Groq is not None) else None
        self._selected_model: str | None = None

    def is_available(self) -> bool:
        return self.client is not None and not self.key_invalid

    def list_models(self) -> list[str]:
        if not self.client or self.key_invalid:
            return []
        try:
            models = self.client.models.list()
            return [m.id for m in models.data if getattr(m, "id", None)]
        except Exception as exc:
            if is_unauthorized(exc):
                self.key_invalid = True
                log.warning("Groq key marked invalid (401); will skip until reload.")
            else:
                log.warning("Groq list_models failed: %s", exc)
            return []

    def select_model(self, preferred_model: str | None = None) -> str:
        models = self.list_models()
        if not models:
            fallback = preferred_model or "meta-llama/llama-4-maverick-17b-128e-instruct"
            self._selected_model = fallback
            return fallback
        selected = choose_best_model(models, preferred_model=preferred_model)
        self._selected_model = selected
        return selected

    def complete_text(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> str:
        if not self.client:
            raise RuntimeError("Groq not configured")
        resolved_model = model or self._selected_model or self.select_model()
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_completion_tokens=max_tokens,
        )
        self.last_usage = extract_usage(response)
        return (first_choice(response, resolved_model).message.content or "").strip()

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, max_tokens: int = 700
    ) -> str:
        if not self.client:
            raise RuntimeError("Groq not configured")
        resolved_model = model or self._selected_model or self.select_model()
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.2,
            max_completion_tokens=max_tokens,
        )
        self.last_usage = extract_usage(response)
        return (first_choice(response, resolved_model).message.content or "").strip()

    def complete_json(
        self, prompt: str, model: str | None = None, max_tokens: int = 700
    ) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError("Groq not configured")
        resolved_model = model or self._selected_model or self.select_model()
        # Mirror OpenAICompatibleProvider: try a structured parse first, then fall
        # back to prose + regex extraction so a chatty reply degrades gracefully
        # instead of raising a bare ValueError straight into failover.
        try:
            response = self.client.chat.completions.create(
                model=resolved_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_completion_tokens=max_tokens,
            )
            self.last_usage = extract_usage(response)
            # Same structural guards as the other OpenAI-shaped providers: a
            # cut-off reply (finish_reason=length) or a choice-less payload can't
            # be salvaged by a second call, so it must reach the factory as its
            # own penalty reason instead of being retried as prose.
            if is_truncated(response):
                raise TruncatedCompletionError(resolved_model)
            content = (first_choice(response, resolved_model).message.content or "").strip()
            return cast(dict[str, Any], json.loads(content))
        except (TruncatedCompletionError, EmptyCompletionError):
            raise
        except Exception as exc:
            # A throttled or unauthorised host answers the retry the same way:
            # falling back to prose here just spends a second request before the
            # factory fails over. (json.JSONDecodeError was listed alongside
            # Exception in this clause — it is a ValueError, so it never
            # matched separately.)
            if is_transport_failure(exc):
                raise
            log.info("groq complete_json fallback (model=%s): %s", resolved_model, exc)
            text = self.complete_text(prompt=prompt, model=resolved_model, max_tokens=max_tokens)
            return _extract_json(text)
