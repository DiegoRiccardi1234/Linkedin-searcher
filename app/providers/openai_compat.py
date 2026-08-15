"""Reusable base for OpenAI-API-compatible providers + the concrete new ones.

Every provider that speaks the OpenAI Chat Completions API (DeepSeek, xAI/Grok,
Zhipu GLM, Mistral, OpenRouter, …) differs only by ``base_url`` and a fallback
``default_model``. ``OpenAICompatibleProvider`` captures the shared logic once;
adding a provider is now a ~4-line subclass. The client is built with
``max_retries=0`` so retries are owned solely by ``factory._with_retry`` (no
duplicated SDK-level 429 retry spam).
"""

import json
import re
import urllib.parse
import urllib.request
from typing import Any, cast

from app.log import get_logger
from app.providers.base import (
    LLMProvider,
    TruncatedCompletionError,
    extract_usage,
    first_choice,
    is_truncated,
    is_unauthorized,
)
from app.providers.model_selector import choose_best_model

log = get_logger(__name__)

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment,misc]


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Nessun JSON trovato")
    return cast(dict[str, Any], json.loads(match.group()))


class OpenAICompatibleProvider(LLMProvider):
    """Concrete provider for any OpenAI-compatible Chat Completions endpoint.

    Subclasses set ``name`` (class attr), ``base_url`` and ``default_model``.
    An empty ``base_url`` means the OpenAI default endpoint.
    """

    base_url: str = ""
    default_model: str = ""
    #: Seconds the HTTP client waits for a reply. The SDK's own default (~10
    #: minutes of connect+read, but 60s for a non-streaming call in practice) is
    #: tuned for hosted models; a subclass serving a local one overrides it.
    request_timeout: float | None = None

    def __init__(self, api_key: str | None, base_url: str | None = None):
        self.api_key = api_key
        # An explicit override shadows the class default (e.g. GLM China console).
        self.base_url = base_url or type(self).base_url
        client_kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": 0}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        if type(self).request_timeout:
            client_kwargs["timeout"] = type(self).request_timeout
        client_kwargs.update(self.client_kwargs(api_key))
        self.client = OpenAI(**client_kwargs) if (api_key and OpenAI is not None) else None
        self._selected_model: str | None = None

    def client_kwargs(self, api_key: str | None) -> dict[str, Any]:
        """Extra SDK arguments for this provider. Empty for almost everyone —
        the exception is a free tier that answers only without credentials."""
        return {}

    def is_available(self) -> bool:
        return self.client is not None and not self.key_invalid

    def list_models(self) -> list[str]:
        if not self.client or self.key_invalid:
            return []
        try:
            models = self.client.models.list()
            output: list[str] = []
            for model in models.data:
                model_id = getattr(model, "id", "")
                if model_id:
                    output.append(str(model_id))
            return output
        except Exception as exc:
            if is_unauthorized(exc):
                self.key_invalid = True
                log.warning("%s key marked invalid (401); will skip until reload.", self.name)
            else:
                log.warning("%s list_models failed: %s", self.name, exc)
            return []

    def select_model(self, preferred_model: str | None = None) -> str:
        models = self.list_models()
        if not models:
            fallback = preferred_model or self.default_model
            self._selected_model = fallback
            return fallback
        selected = choose_best_model(models, preferred_model=preferred_model)
        self._selected_model = selected
        return selected

    def complete_text(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> str:
        if not self.client:
            raise RuntimeError(f"{self.name} not configured")
        resolved_model = model or self._selected_model or self.select_model()
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=max_tokens,
        )
        self.last_usage = extract_usage(response)
        return (first_choice(response, resolved_model).message.content or "").strip()

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, max_tokens: int = 700
    ) -> str:
        if not self.client:
            raise RuntimeError(f"{self.name} not configured")
        resolved_model = model or self._selected_model or self.select_model()
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.2,
            max_tokens=max_tokens,
        )
        self.last_usage = extract_usage(response)
        return (first_choice(response, resolved_model).message.content or "").strip()

    def complete_json(
        self, prompt: str, model: str | None = None, max_tokens: int = 700
    ) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError(f"{self.name} not configured")
        resolved_model = model or self._selected_model or self.select_model()
        # Deliberately OUTSIDE any try: transport/HTTP errors (429/401/timeout)
        # must propagate to the factory, which owns retry/penalty/failover. The
        # old catch-all swallowed them and fired a SECOND network call via
        # complete_text — a second 429 on an already rate-limited host.
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=max_tokens,
            # Not all compatible models support json_object; rely on prompt + fallback.
        )
        self.last_usage = extract_usage(response)
        # Truncated (finish_reason=length) means the model hit max_tokens and
        # the JSON is cut off (reasoning models burn the budget on hidden
        # thinking before emitting valid JSON). Don't degrade to complete_text
        # — it truncates the same way — raise so the factory penalises this
        # model ("truncated") and fails over to a leaner one.
        if is_truncated(response):
            raise TruncatedCompletionError(resolved_model)
        # No choices at all (gateway answered 200 with choices=None): raise a
        # classified error instead of the bare TypeError that used to escape here.
        content = (first_choice(response, resolved_model).message.content or "").strip()
        try:
            return cast(dict[str, Any], json.loads(content))
        except json.JSONDecodeError:
            pass
        try:
            # Local salvage: JSON wrapped in prose/markdown fences — no network.
            return _extract_json(content)
        except ValueError as exc:
            # No JSON at all in the reply: one complete_text retry is the last
            # resort (some models ignore the JSON instruction on first pass).
            log.info("%s complete_json fallback (model=%s): %s", self.name, resolved_model, exc)
            text = self.complete_text(prompt=prompt, model=resolved_model, max_tokens=max_tokens)
            return _extract_json(text)


class DeepSeekProvider(OpenAICompatibleProvider):
    name = "deepseek"
    base_url = "https://api.deepseek.com"
    default_model = "deepseek-chat"


class XAIProvider(OpenAICompatibleProvider):
    name = "xai"
    base_url = "https://api.x.ai/v1"
    default_model = "grok-3-mini"


class GLMProvider(OpenAICompatibleProvider):
    name = "glm"
    base_url = "https://api.z.ai/api/paas/v4"
    default_model = "glm-4.6"


class MistralProvider(OpenAICompatibleProvider):
    name = "mistral"
    base_url = "https://api.mistral.ai/v1"
    default_model = "mistral-large-latest"


class CustomOpenAIProvider(OpenAICompatibleProvider):
    """Any OpenAI-compatible endpoint the user points at: a gateway, a
    self-hosted server, or a model running on their own machine.

    One base URL covers cases the app would otherwise each need code for:
    Ollama (``http://localhost:11434/v1``), LM Studio
    (``http://localhost:1234/v1``), vLLM, llama.cpp, or an aggregating gateway
    such as OmniRoute. Rather than reimplementing multi-provider routing, the
    app can simply talk to one.

    The key is OPTIONAL — that is the whole difference from its siblings. A
    local server has no key to give, and requiring one made every local setup
    unusable; the SDK still wants a non-empty string, so a placeholder is sent.
    """

    name = "custom"
    base_url = ""
    default_model = ""
    # A local model writes a full scoring JSON in 45-60s on a mid-range GPU
    # (measured: 12B Q4 on an RTX 5070, ~1200 tokens at ~22 tok/s), and the first
    # call of a session also loads several GB into VRAM. The default client
    # timeout cut every one of those off as a connection error.
    request_timeout = 300.0

    def __init__(self, api_key: str | None, base_url: str | None = None):
        # A local endpoint authenticates nobody: without this the provider would
        # report itself unavailable and never appear in the failover chain.
        super().__init__(api_key or ("local" if base_url else None), base_url)

    def is_available(self) -> bool:
        return bool(self.base_url) and self.client is not None and not self.key_invalid


#: What a user stores as their OVH "key" to accept the anonymous tier. It is a
#: choice, not a default: without it OVH stays unconfigured, because sending a
#: CV to a shared endpoint nobody asked for is not the app's decision to make.
OVH_ANONYMOUS = "anonymous"


class OVHProvider(OpenAICompatibleProvider):
    """OVHcloud AI Endpoints — the one catalog here served from the EU.

    The key is optional. Measured 2026-08-15 on the anonymous tier: the models
    list and a JSON completion both answer, `Mistral-Small-3.2-24B` replies in
    1.1s with clean JSON, while `Llama-3.3-70B` returned 429 three times in a
    row with sixty-five seconds between calls — the shared pool is busy, not the
    caller. With a key it is 400 requests a minute, billed per token.
    """

    name = "ovh"
    base_url = "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1"
    default_model = "Meta-Llama-3_3-70B-Instruct"

    def client_kwargs(self, api_key: str | None) -> dict[str, Any]:
        if (api_key or "").strip().lower() != OVH_ANONYMOUS:
            return {}
        # The anonymous tier answers only when there is no credentials header at
        # all: measured, ANY bearer token — including the string "anonymous" —
        # comes back 403, while an empty header is served like no header.
        return {"default_headers": {"Authorization": ""}}


class CloudflareProvider(OpenAICompatibleProvider):
    """Workers AI. Ten thousand Neurons a day for free, on one API token.

    Measured 2026-08-15: `llama-3.3-70b-instruct-fp8-fast` answers a scoring
    prompt in 1.2s and `gpt-oss-120b` in 2.8s, both `finish_reason: stop` with
    valid JSON — the same gpt-oss that truncates on OpenRouter's free tier. At
    roughly 196 Neurons per real offer, the free allowance is about fifty offers
    a day on the 70B.

    The endpoint embeds the account id, so ``base_url`` is empty until it is
    discovered from the token (see ``cloudflare_base_url``).
    """

    name = "cloudflare"
    base_url = ""
    default_model = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"

    def __init__(self, api_key: str | None, base_url: str | None = None):
        # Never build a client without the account URL: the base class would
        # fall back to OpenAI's endpoint and point a Cloudflare token at it.
        super().__init__(api_key if base_url else None, base_url)

    def is_available(self) -> bool:
        return bool(self.base_url) and self.client is not None and not self.key_invalid

    def list_models(self) -> list[str]:
        """The OpenAI-compatible surface has no catalog: ``GET …/ai/v1/models``
        answers 405, "GET not supported for requested URI". Cloudflare keeps its
        own, so ask that one — and only for models that generate text."""
        if not self.base_url or not self.api_key:
            return []
        catalog = self.base_url.replace("/ai/v1", "/ai/models/search")
        query = urllib.parse.urlencode({"task": "Text Generation", "per_page": 100})
        request = urllib.request.Request(
            f"{catalog}?{query}", headers={"Authorization": f"Bearer {self.api_key}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except Exception as exc:
            if is_unauthorized(exc):
                self.key_invalid = True
            log.warning("Cloudflare model catalog unavailable (%s)", exc)
            return []
        entries = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return []
        return [
            str(entry.get("name"))
            for entry in entries
            if isinstance(entry, dict) and entry.get("name")
        ]


def cloudflare_base_url(api_key: str, timeout: float = 15.0) -> str | None:
    """The account-scoped endpoint for this token, or ``None``.

    Cloudflare's OpenAI-compatible URL carries the account id. Asking the user
    to find and paste it is a second field and a second thing to get wrong, when
    the token itself already answers the question in one call.
    """
    request = urllib.request.Request(
        "https://api.cloudflare.com/client/v4/accounts",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:  # network, auth, or anything else: not fatal
        log.info("Cloudflare account lookup failed (%s)", exc)
        return None
    accounts = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(accounts, list) or not accounts:
        return None
    account_id = str((accounts[0] or {}).get("id") or "")
    if not account_id:
        return None
    return f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
