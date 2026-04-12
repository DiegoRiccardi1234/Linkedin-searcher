from typing import Any

from app.config import AppSettings
from app.providers.base import LLMProvider
from app.providers.cerebras_provider import CerebrasProvider
from app.providers.groq_provider import GroqProvider


class ProviderManager:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.providers: dict[str, LLMProvider] = {
            "cerebras": CerebrasProvider(api_key=settings.cerebras_api_key),
            "groq": GroqProvider(api_key=settings.groq_api_key),
        }
        self.active_provider: LLMProvider | None = None
        self.active_provider_name: str = "none"
        self.active_model: str = "none"

    def initialize(self) -> None:
        for provider_name in self.settings.llm_provider_order:
            provider = self.providers.get(provider_name)
            if not provider or not provider.is_available():
                continue

            # Provider sceglie il miglior modello disponibile; poi possiamo forzare via preferred_model.
            selected_model = provider.select_model(preferred_model=self.settings.preferred_model)
            # Per i provider che espongono list_models, applichiamo policy centralizzata.
            try:
                from app.providers.model_selector import choose_best_model

                models = provider.list_models()
                if models:
                    selected_model = choose_best_model(
                        models=models,
                        preferred_model=self.settings.preferred_model,
                        policy=self.settings.model_selection_policy,
                    )
            except Exception:
                pass

            self.active_provider = provider
            self.active_provider_name = provider.name
            self.active_model = selected_model
            return

        self.active_provider = None
        self.active_provider_name = "none"
        self.active_model = "none"

    def metadata(self) -> dict[str, Any]:
        return {
            "active_provider": self.active_provider_name,
            "active_model": self.active_model,
            "available": self.active_provider is not None,
            "providers": {
                name: {
                    "available": provider.is_available(),
                    "models": provider.list_models(),
                }
                for name, provider in self.providers.items()
            },
        }

    def complete_json(self, prompt: str, max_tokens: int = 700) -> dict[str, Any]:
        if not self.active_provider:
            raise RuntimeError("Nessun provider LLM disponibile")
        return self.active_provider.complete_json(prompt=prompt, model=self.active_model, max_tokens=max_tokens)

    def chat(self, messages: list[dict[str, str]], max_tokens: int = 700) -> str:
        if not self.active_provider:
            raise RuntimeError("Nessun provider LLM disponibile")
        return self.active_provider.chat(messages=messages, model=self.active_model, max_tokens=max_tokens)
