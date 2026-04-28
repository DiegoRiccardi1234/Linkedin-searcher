import json
import re
from typing import Any, cast
from urllib import parse, request

from app.log import get_logger
from app.providers.base import LLMProvider
from app.providers.model_selector import choose_best_model

log = get_logger(__name__)

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment,misc]


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Nessun JSON trovato")
    return cast(dict[str, Any], json.loads(match.group()))


class GoogleProvider(LLMProvider):
    name = "google"

    def __init__(self, api_key: str | None):
        self.api_key = api_key
        self.client = (
            OpenAI(
                api_key=api_key, base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
            )
            if (api_key and OpenAI is not None)
            else None
        )
        self._selected_model: str | None = None

    def is_available(self) -> bool:
        return self.client is not None

    def list_models(self) -> list[str]:
        if not self.api_key:
            return []
        try:
            query = parse.urlencode({"key": self.api_key})
            with request.urlopen(
                f"https://generativelanguage.googleapis.com/v1beta/models?{query}",
                timeout=12,
            ) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            models = payload.get("models", []) if isinstance(payload, dict) else []
            result: list[str] = []
            for model in models:
                if not isinstance(model, dict):
                    continue
                name = str(model.get("name", ""))
                if not name:
                    continue
                short_name = name.replace("models/", "")
                if short_name.startswith("gemini"):
                    result.append(short_name)
            return result
        except Exception as exc:
            log.warning("Google list_models failed: %s", exc)
            return []

    def select_model(self, preferred_model: str | None = None) -> str:
        models = self.list_models()
        if not models:
            fallback = preferred_model or "gemini-2.0-flash"
            self._selected_model = fallback
            return fallback
        selected = choose_best_model(models, preferred_model=preferred_model)
        self._selected_model = selected
        return selected

    def complete_text(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> str:
        if not self.client:
            raise RuntimeError("Google not configured")
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
            raise RuntimeError("Google not configured")
        resolved_model = model or self._selected_model or self.select_model()
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return (response.choices[0].message.content or "").strip()

    def complete_json(
        self, prompt: str, model: str | None = None, max_tokens: int = 700
    ) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError("Google not configured")

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
            return cast(dict[str, Any], json.loads(content))
        except Exception:
            text = self.complete_text(prompt=prompt, model=resolved_model, max_tokens=max_tokens)
            return _extract_json(text)
