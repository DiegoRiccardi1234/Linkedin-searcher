"""New OpenAI-compatible providers (DeepSeek, xAI, GLM, Mistral, Cloudflare, OVH).

Pins provider identity, offline model fallback, SDK-retry suppression, and the
config/factory wiring so a valid key round-trips end to end.
"""

from __future__ import annotations

import pytest

from app.config import SUPPORTED_PROVIDERS, load_settings, save_local_provider_keys
from app.providers.factory import ProviderManager
from app.providers.openai_compat import (
    CloudflareProvider,
    DeepSeekProvider,
    GLMProvider,
    MistralProvider,
    OpenAICompatibleProvider,
    OVHProvider,
    XAIProvider,
)

_NEW = ("deepseek", "xai", "glm", "mistral", "cloudflare", "ovh")


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "CEREBRAS_API_KEY",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
        "GLM_API_KEY",
        "MISTRAL_API_KEY",
        "CLOUDFLARE_API_KEY",
        "CLOUDFLARE_BASE_URL",
        "OVH_API_KEY",
        "LLM_PROVIDER",
        "LLM_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_supported_providers_includes_new() -> None:
    for name in _NEW:
        assert name in SUPPORTED_PROVIDERS


def test_new_providers_are_openai_compatible_subclasses() -> None:
    for cls in (DeepSeekProvider, XAIProvider, GLMProvider, MistralProvider, OVHProvider, CloudflareProvider):
        assert issubclass(cls, OpenAICompatibleProvider)


def test_provider_identity_and_offline_default_model() -> None:
    """With no key the client is None; select_model falls back to default_model
    (offline, no network) — so a provider still works even if /models 404s."""
    cases = {
        DeepSeekProvider: ("deepseek", "deepseek-chat", "https://api.deepseek.com"),
        XAIProvider: ("xai", "grok-3-mini", "https://api.x.ai/v1"),
        GLMProvider: ("glm", "glm-4.6", "https://api.z.ai/api/paas/v4"),
        MistralProvider: ("mistral", "mistral-large-latest", "https://api.mistral.ai/v1"),
        OVHProvider: (
            "ovh",
            "Meta-Llama-3_3-70B-Instruct",
            "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        ),
        # Cloudflare's base URL stays empty until the account id is read from
        # the token: the endpoint is account-scoped, there is no shared host.
        CloudflareProvider: ("cloudflare", "@cf/meta/llama-3.3-70b-instruct-fp8-fast", ""),
    }
    for cls, (name, default_model, base_url) in cases.items():
        p = cls(api_key=None)
        assert p.name == name
        assert p.base_url == base_url
        assert p.is_available() is False
        assert p.select_model() == default_model


def test_client_disables_sdk_retries() -> None:
    """SDK-level retries are off; our factory _with_retry owns retries (kills the
    duplicated 429 'Retrying request' log spam)."""
    p = DeepSeekProvider(api_key="sk-test")
    assert p.client is not None
    assert p.client.max_retries == 0


def test_factory_instantiates_new_providers(tmp_path) -> None:
    settings = load_settings(tmp_path)
    mgr = ProviderManager(settings)
    for name in _NEW:
        assert name in mgr.providers
        assert mgr.providers[name].name == name


def test_new_provider_key_round_trips(tmp_path) -> None:
    save_local_provider_keys(tmp_path / "data", deepseek_api_key="sk-deep")
    settings = load_settings(tmp_path)
    assert settings.deepseek_api_key == "sk-deep"


def test_clearing_new_provider_key_removes_it(tmp_path) -> None:
    save_local_provider_keys(tmp_path / "data", mistral_api_key="sk-m")
    save_local_provider_keys(tmp_path / "data", mistral_api_key="")
    settings = load_settings(tmp_path)
    assert settings.mistral_api_key is None


# --- the two that are not plain four-line subclasses --------------------------


def test_cloudflare_refuses_to_build_a_client_without_its_account_url() -> None:
    """The base class falls back to OpenAI's own endpoint when there is no base
    URL. For Cloudflare that would point an account token at the wrong host."""
    p = CloudflareProvider(api_key="cfat_whatever")
    assert p.client is None
    assert p.is_available() is False

    configured = CloudflareProvider(
        api_key="cfat_whatever",
        base_url="https://api.cloudflare.com/client/v4/accounts/abc123/ai/v1",
    )
    assert configured.is_available() is True
    assert str(configured.client.base_url).startswith(
        "https://api.cloudflare.com/client/v4/accounts/abc123/ai/v1"
    )


def test_the_account_url_is_read_from_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """One call, at save time, instead of a second field for the user to find."""
    import io
    import json as _json

    from app.providers import openai_compat as mod

    class _Ctx(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        mod.urllib.request,
        "urlopen",
        lambda request, timeout=0: _Ctx(
            _json.dumps({"result": [{"id": "acc-42"}]}).encode()
        ),
    )
    assert (
        mod.cloudflare_base_url("cfat_token")
        == "https://api.cloudflare.com/client/v4/accounts/acc-42/ai/v1"
    )


def test_a_token_that_answers_nothing_leaves_cloudflare_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers import openai_compat as mod

    def boom(request, timeout=0):
        raise OSError("no network")

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    assert mod.cloudflare_base_url("cfat_token") is None


def test_ovh_anonymous_sends_no_credentials() -> None:
    """Measured: the free tier serves a request with no Authorization header and
    a request with an empty one, and answers 403 to ANY bearer token — including
    the word this app stores to mean 'anonymous'."""
    anon = OVHProvider(api_key="anonymous")
    assert anon.client_kwargs("anonymous") == {"default_headers": {"Authorization": ""}}
    assert anon.is_available() is True

    keyed = OVHProvider(api_key="a-real-key")
    assert keyed.client_kwargs("a-real-key") == {}


def test_ovh_stays_out_until_the_user_opts_in() -> None:
    """A free shared endpoint is still an endpoint the CV is sent to: it must be
    a choice, not a default nobody made."""
    assert OVHProvider(api_key=None).is_available() is False
