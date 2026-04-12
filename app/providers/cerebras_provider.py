import json
import re
from typing import Any

from app.providers.base import LLMProvider
from app.providers.model_selector import choose_best_model

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Nessun JSON trovato")
    return json.loads(match.group())


class CerebrasProvider(LLMProvider):
    name = "cerebras"

    def __init__(self, api_key: str | None):
        self.api_key = api_key
        self.client = (
            OpenAI(api_key=api_key, base_url="https://api.cerebras.ai/v1")
            if (api_key and OpenAI is not None)
            else None
        )
        self._selected_model: str | None = None

    def _extract_model_ids(self, models_obj: Any) -> list[str]:
        ids: list[str] = []

        data_attr = getattr(models_obj, "data", None)
        if data_attr is not None:
            for item in data_attr:
                model_id = getattr(item, "id", None)
                if not model_id and isinstance(item, dict):
                    model_id = item.get("id")
                if model_id:
                    ids.append(str(model_id))

        try:
            for item in models_obj:
                model_id = getattr(item, "id", None)
                if not model_id and isinstance(item, dict):
                    model_id = item.get("id")
                if model_id:
                    ids.append(str(model_id))
        except Exception:
            pass

        # Dedup preservando ordine.
        unique: list[str] = []
        seen: set[str] = set()
        for model_id in ids:
            if model_id not in seen:
                unique.append(model_id)
                seen.add(model_id)
        return unique

    def _list_models_via_http(self) -> list[str]:
        if not self.api_key or requests is None:
            return []
        try:
            response = requests.get(
                "https://api.cerebras.ai/v1/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data", []) if isinstance(payload, dict) else []
            ids = [str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")]
            return ids
        except Exception:
            return []

    def _probe_model(self, model_name: str) -> bool:
        if not self.client:
            return False
        try:
            self.client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": "ok"}],
                temperature=0.0,
                max_tokens=1,
            )
            return True
        except Exception:
            return False

    def is_available(self) -> bool:
        return self.client is not None

    def list_models(self) -> list[str]:
        if not self.client:
            return []
        try:
            models = self.client.models.list()
            model_ids = self._extract_model_ids(models)
            if model_ids:
                return model_ids
            return self._list_models_via_http()
        except Exception:
            return self._list_models_via_http()

    def select_model(self, preferred_model: str | None = None) -> str:
        models = self.list_models()
        if not models:
            fallback_candidates = [
                preferred_model or "",
                "qwen-3-235b-a22b-instruct-2507",
                "llama3.1-8b",
                "llama-3.1-8b",
                "llama-4-scout-17b-16e-instruct",
            ]
            for candidate in fallback_candidates:
                if not candidate:
                    continue
                if self._probe_model(candidate):
                    self._selected_model = candidate
                    return candidate

            fallback = preferred_model or "qwen-3-235b-a22b-instruct-2507"
            self._selected_model = fallback
            return fallback
        selected = choose_best_model(models, preferred_model=preferred_model)
        self._selected_model = selected
        return selected

    def complete_text(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> str:
        if not self.client:
            raise RuntimeError("Cerebras non configurato")
        resolved_model = model or self._selected_model or self.select_model()
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=max_tokens,
        )
        return (response.choices[0].message.content or "").strip()

    def chat(self, messages: list[dict[str, str]], model: str | None = None, max_tokens: int = 700) -> str:
        if not self.client:
            raise RuntimeError("Cerebras non configurato")
        resolved_model = model or self._selected_model or self.select_model()
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=messages,
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return (response.choices[0].message.content or "").strip()

    def complete_json(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> dict[str, Any]:
        text = self.complete_text(prompt=prompt, model=model, max_tokens=max_tokens)
        return _extract_json(text)
