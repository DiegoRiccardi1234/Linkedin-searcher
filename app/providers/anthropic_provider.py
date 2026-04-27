import json
import re
from typing import Any
from urllib import error, request

from app.providers.base import LLMProvider
from app.providers.model_selector import choose_best_model


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Nessun JSON trovato")
    return json.loads(match.group())


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str | None):
        self.api_key = api_key
        self.base_url = "https://api.anthropic.com/v1"
        self._selected_model: str | None = None

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def list_models(self) -> list[str]:
        if not self.api_key:
            return []
        try:
            req = request.Request(
                f"{self.base_url}/models",
                headers=self._headers(),
                method="GET",
            )
            with request.urlopen(req, timeout=12) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            data = payload.get("data", []) if isinstance(payload, dict) else []
            return [
                str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")
            ]
        except Exception:
            return []

    def select_model(self, preferred_model: str | None = None) -> str:
        models = self.list_models()
        if not models:
            fallback = preferred_model or "claude-3-5-sonnet-latest"
            self._selected_model = fallback
            return fallback
        selected = choose_best_model(models, preferred_model=preferred_model)
        self._selected_model = selected
        return selected

    def _chat_request(self, messages: list[dict[str, str]], model: str, max_tokens: int) -> str:
        if not self.api_key:
            raise RuntimeError("Anthropic not configured")

        system_parts: list[str] = []
        transformed_messages: list[dict[str, Any]] = []
        for msg in messages:
            role = str(msg.get("role", "user")).lower()
            content = str(msg.get("content", ""))
            if role == "system":
                system_parts.append(content)
                continue
            if role not in {"user", "assistant"}:
                role = "user"
            transformed_messages.append(
                {
                    "role": role,
                    "content": [{"type": "text", "text": content}],
                }
            )

        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": transformed_messages
            or [{"role": "user", "content": [{"type": "text", "text": "ok"}]}],
        }
        if system_parts:
            body["system"] = "\n".join(system_parts)

        req = request.Request(
            f"{self.base_url}/messages",
            data=json.dumps(body).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Anthropic HTTP {exc.code}: {details}") from exc

        content = payload.get("content", []) if isinstance(payload, dict) else []
        chunks = [
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join([c for c in chunks if c]).strip()

    def complete_text(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> str:
        resolved_model = model or self._selected_model or self.select_model()
        return self._chat_request(
            messages=[{"role": "user", "content": prompt}],
            model=resolved_model,
            max_tokens=max_tokens,
        )

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, max_tokens: int = 700
    ) -> str:
        resolved_model = model or self._selected_model or self.select_model()
        return self._chat_request(messages=messages, model=resolved_model, max_tokens=max_tokens)

    def complete_json(
        self, prompt: str, model: str | None = None, max_tokens: int = 700
    ) -> dict[str, Any]:
        instruction = f"Rispondi esclusivamente con JSON valido, senza testo extra.\n\n{prompt}"
        text = self.complete_text(prompt=instruction, model=model, max_tokens=max_tokens)
        return _extract_json(text)
