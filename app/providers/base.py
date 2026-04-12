from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def is_available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def select_model(self, preferred_model: str | None = None) -> str:
        raise NotImplementedError

    @abstractmethod
    def complete_text(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> str:
        raise NotImplementedError

    @abstractmethod
    def chat(self, messages: list[dict[str, str]], model: str | None = None, max_tokens: int = 700) -> str:
        raise NotImplementedError

    @abstractmethod
    def complete_json(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> dict[str, Any]:
        raise NotImplementedError
