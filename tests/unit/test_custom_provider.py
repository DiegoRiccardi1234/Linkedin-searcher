"""The custom OpenAI-compatible endpoint: local models and gateways.

One base URL covers what the app would otherwise need code for each of:
Ollama, LM Studio, vLLM, llama.cpp, or an aggregating gateway. The difference
from every other provider is that the API key is OPTIONAL — a model running on
your own machine has none to give, and requiring one made local setups
impossible.
"""

from __future__ import annotations

from pathlib import Path

from app.config import SUPPORTED_PROVIDERS, load_settings, save_local_provider_keys
from app.providers.openai_compat import CustomOpenAIProvider


def test_available_with_an_endpoint_and_no_key() -> None:
    provider = CustomOpenAIProvider(api_key=None, base_url="http://localhost:11434/v1")
    assert provider.is_available() is True


def test_not_available_without_an_endpoint() -> None:
    """A key alone says nothing about WHERE to send the request."""
    assert CustomOpenAIProvider(api_key="sk-x", base_url=None).is_available() is False
    assert CustomOpenAIProvider(api_key=None, base_url=None).is_available() is False


def test_key_is_used_when_given() -> None:
    provider = CustomOpenAIProvider(api_key="sk-real", base_url="https://gateway.example/v1")
    assert provider.api_key == "sk-real"
    assert provider.is_available() is True


def test_endpoint_round_trips_through_settings(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    status = save_local_provider_keys(
        data_dir=data_dir,
        custom_base_url="http://localhost:1234/v1",
    )
    assert status["custom_configured"] is True

    settings = load_settings(tmp_path)
    assert settings.custom_base_url == "http://localhost:1234/v1"
    assert settings.custom_api_key is None  # optional, and absent here


def test_clearing_the_endpoint_unconfigures_it(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    save_local_provider_keys(data_dir=data_dir, custom_base_url="http://localhost:11434/v1")
    status = save_local_provider_keys(data_dir=data_dir, custom_base_url="")
    assert status["custom_configured"] is False


def test_custom_is_a_supported_provider() -> None:
    assert "custom" in SUPPORTED_PROVIDERS
