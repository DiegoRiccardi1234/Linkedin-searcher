import json
import re
from typing import Any

from app.providers.base import LLMProvider
from app.providers.model_selector import choose_best_model

try:
    from groq import Groq
except Exception:  # pragma: no cover
    Groq = None  # type: ignore[assignment]


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Nessun JSON trovato")
    return json.loads(match.group())


class GroqProvider(LLMProvider):
    name = "groq"

    def __init__(self, api_key: str | None):
        self.api_key = api_key
        self.client = Groq(api_key=api_key) if (api_key and Groq is not None) else None
        self._selected_model: str | None = None

    def is_available(self) -> bool:
        return self.client is not None

    def list_models(self) -> list[str]:
        if not self.client:
            return []
        try:
            models = self.client.models.list()
            return [m.id for m in models.data if getattr(m, "id", None)]
        except Exception:
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
            raise RuntimeError("Groq non configurato")
        resolved_model = model or self._selected_model or self.select_model()
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_completion_tokens=max_tokens,
        )
        return (response.choices[0].message.content or "").strip()

    def chat(self, messages: list[dict[str, str]], model: str | None = None, max_tokens: int = 700) -> str:
        if not self.client:
            raise RuntimeError("Groq non configurato")
        resolved_model = model or self._selected_model or self.select_model()
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=messages,
            temperature=0.2,
            max_completion_tokens=max_tokens,
        )
        return (response.choices[0].message.content or "").strip()

    def complete_json(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> dict[str, Any]:
        text = self.complete_text(prompt=prompt, model=model, max_tokens=max_tokens)
        return _extract_json(text)
