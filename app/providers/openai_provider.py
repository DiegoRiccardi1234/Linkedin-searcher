import json
import re
from typing import Any

from app.providers.base import LLMProvider
from app.providers.model_selector import choose_best_model

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Nessun JSON trovato")
    return json.loads(match.group())


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str | None):
        self.api_key = api_key
        self.client = OpenAI(api_key=api_key) if (api_key and OpenAI is not None) else None
        self._selected_model: str | None = None

    def is_available(self) -> bool:
        return self.client is not None

    def list_models(self) -> list[str]:
        if not self.client:
            return []
        try:
            models = self.client.models.list()
            output: list[str] = []
            for model in models.data:
                model_id = getattr(model, "id", "")
                if not model_id:
                    continue
                lowered = model_id.lower()
                # Skip non-chat models (embeddings, audio, moderation, etc.).
                if any(
                    token in lowered
                    for token in ["embedding", "audio", "tts", "moderation", "whisper"]
                ):
                    continue
                output.append(str(model_id))
            return output
        except Exception:
            return []

    def select_model(self, preferred_model: str | None = None) -> str:
        models = self.list_models()
        if not models:
            fallback = preferred_model or "gpt-4.1-mini"
            self._selected_model = fallback
            return fallback
        selected = choose_best_model(models, preferred_model=preferred_model)
        self._selected_model = selected
        return selected

    def complete_text(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> str:
        if not self.client:
            raise RuntimeError("OpenAI not configured")
        resolved_model = model or self._selected_model or self.select_model()
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=max_tokens,
        )
        return (response.choices[0].message.content or "").strip()

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, max_tokens: int = 700
    ) -> str:
        if not self.client:
            raise RuntimeError("OpenAI not configured")
        resolved_model = model or self._selected_model or self.select_model()
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=messages,
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return (response.choices[0].message.content or "").strip()

    def complete_json(
        self, prompt: str, model: str | None = None, max_tokens: int = 700
    ) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError("OpenAI not configured")

        resolved_model = model or self._selected_model or self.select_model()
        try:
            response = self.client.chat.completions.create(
                model=resolved_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            content = (response.choices[0].message.content or "").strip()
            return json.loads(content)
        except Exception:
            text = self.complete_text(prompt=prompt, model=resolved_model, max_tokens=max_tokens)
            return _extract_json(text)
