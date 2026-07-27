from abc import ABC, abstractmethod
from typing import Any


def extract_usage(response: Any) -> dict[str, int] | None:
    """Best-effort token usage extraction from heterogeneous SDK responses.

    OpenAI / Groq / Cerebras / OpenRouter expose ``response.usage`` with
    ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens``. Anthropic
    uses ``input_tokens`` / ``output_tokens``. Google's OpenAI-compat path
    matches OpenAI's shape. Returns None when nothing is recognisable.
    """
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return None

    def _get(key: str) -> Any:
        if isinstance(usage, dict):
            return usage.get(key)
        return getattr(usage, key, None)

    prompt = _get("prompt_tokens") or _get("input_tokens") or 0
    completion = _get("completion_tokens") or _get("output_tokens") or 0
    total = _get("total_tokens") or (prompt + completion) or 0
    if not (prompt or completion or total):
        return None
    return {
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
        "total_tokens": int(total or 0),
    }


def is_unauthorized(exc: Exception) -> bool:
    """Detect HTTP 401 across the heterogeneous provider SDK exception types.

    OpenAI / Anthropic / Groq / Cerebras raise ``AuthenticationError`` with a
    ``status_code`` attribute. ``requests.HTTPError`` exposes ``response.status_code``.
    Google's SDK only puts the code in the message string. We accept all of these.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
    if status == 401:
        return True
    text = str(exc).lower()
    return "401" in text and (
        "unauthor" in text or "wrong api key" in text or "invalid api key" in text
    )


def is_transport_failure(exc: Exception) -> bool:
    """True for an error a second call to the same host cannot fix: auth, quota
    or rate limit (401 / 403 / 429).

    ``complete_json`` degrades to a plain-text call when the JSON parse fails —
    the right move for a chatty model, the wrong one for a throttled host: it
    doubles the request count on exactly the failures that should fail fast and
    rotate. The factory already treats these as failover-worthy; this keeps the
    provider from spending an extra request before it gets there.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
    if status in (401, 403, 429):
        return True
    if is_unauthorized(exc):
        return True
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "quota" in text


def is_truncated(response: Any) -> bool:
    """True when the model stopped at its max_tokens limit (output cut off).

    Normalizes the two response shapes, mirroring how ``extract_usage`` handles
    the object-vs-dict split: OpenAI-compatible SDK objects expose
    ``choices[0].finish_reason == "length"``; Anthropic-style dict payloads use
    ``stop_reason == "max_tokens"``."""
    choices = getattr(response, "choices", None)
    if choices:
        return getattr(choices[0], "finish_reason", None) == "length"
    if isinstance(response, dict):
        return response.get("stop_reason") == "max_tokens"
    return False


class EmptyCompletionError(ValueError):
    """Raised when a response carries no usable choice at all.

    OpenRouter (and any gateway fronting a flaky upstream) can answer 200 with
    ``choices`` set to ``None`` or ``[]`` and the real error buried elsewhere in
    the payload. Reaching for ``response.choices[0]`` then raised a bare
    ``TypeError`` ("'NoneType' object is not subscriptable") which the factory
    did NOT classify — so the broken model kept winning the ranking and was
    re-picked for every offer of a scan. Subclasses ``ValueError`` (like
    ``TruncatedCompletionError``) so existing ``except ValueError`` sites keep
    working, but the factory maps it to its own ``"malformed"`` penalty.
    """

    def __init__(self, model: str = "") -> None:
        self.model = model
        detail = f" ({model})" if model else ""
        super().__init__(f"completion without choices{detail}")


def first_choice(response: Any, model: str = "") -> Any:
    """The first choice of an OpenAI-shaped response, or ``EmptyCompletionError``.

    Single entry point for every ``response.choices[0]`` read, so a malformed
    payload always surfaces as a classified error instead of a ``TypeError``.
    """
    choices = getattr(response, "choices", None)
    if not choices:
        raise EmptyCompletionError(model)
    return choices[0]


class TruncatedCompletionError(ValueError):
    """Raised when an LLM stopped because it hit ``max_tokens`` (``finish_reason
    == "length"``): the output is cut off, so any JSON in it is untrustworthy.

    Subclasses ``ValueError`` so existing ``except ValueError`` sites still catch
    it, but the factory classifies it as its own ``"truncated"`` penalty reason —
    a model that burns the token budget on hidden reasoning before emitting valid
    JSON should be de-ranked, not merely retried. Carries the model id for logging.
    """

    def __init__(self, model: str = "") -> None:
        self.model = model
        detail = f" ({model})" if model else ""
        super().__init__(f"completion truncated: finish_reason=length{detail}")


class LLMProvider(ABC):
    name: str
    # Set to True by ``list_models``/``chat``/``complete_*`` when the provider
    # responds with HTTP 401 (revoked or wrong key). Stops the factory from
    # hammering the API on every health poll. Cleared by
    # ``ProviderManager.invalidate_caches()`` when keys are re-saved.
    key_invalid: bool = False
    # Populated by each provider after a successful chat / complete_* call so
    # the factory can persist token usage to ``usage_log``. Schema:
    # ``{"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}``.
    last_usage: dict[str, Any] | None = None

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
    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, max_tokens: int = 700
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def complete_json(
        self, prompt: str, model: str | None = None, max_tokens: int = 700
    ) -> dict[str, Any]:
        raise NotImplementedError
