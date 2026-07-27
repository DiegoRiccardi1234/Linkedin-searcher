"""Per-request provider failover.

When the active provider exhausts its retries (rate-limited / down) the manager
must try the other available providers in ``llm_provider_order`` before giving
up — otherwise chat drops straight to the canned fallback even though a working
key is configured. A 401 during a live call must also flag the provider invalid
so the UI stops showing it as healthy.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config import load_settings
from app.providers.base import LLMProvider
from app.providers.factory import ProviderManager


class _StubProvider(LLMProvider):
    def __init__(
        self,
        name: str,
        *,
        answer: str | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.name = name
        self._answer = answer
        self._exc = exc
        self.key_invalid = False
        self.calls = 0

    def is_available(self) -> bool:
        return not self.key_invalid

    def list_models(self) -> list[str]:
        return ["model-x"]

    def select_model(self, preferred_model: str | None = None) -> str:
        return "model-x"

    def complete_text(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> str:
        return self._answer or ""

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, max_tokens: int = 700
    ) -> str:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._answer or ""

    def complete_json(
        self, prompt: str, model: str | None = None, max_tokens: int = 700
    ) -> dict[str, Any]:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return {"answer": self._answer}


class _Http401(Exception):
    def __init__(self) -> None:
        super().__init__("401 wrong api key")
        self.status_code = 401


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers import factory as _mod

    monkeypatch.setattr(_mod._time, "sleep", lambda _: None)
    monkeypatch.setenv("LLM_MAX_RETRIES", "1")


def _mgr(tmp_path: Any, providers: dict[str, _StubProvider], order: list[str], active: str):
    settings = load_settings(tmp_path)
    settings.llm_provider_order = order
    mgr = ProviderManager(settings)
    mgr.providers = providers  # type: ignore[assignment]
    ap = providers[active]
    mgr.active_provider = ap
    mgr.active_provider_name = ap.name
    mgr.active_model = "model-x"
    return mgr


def test_chat_fails_over_when_active_provider_errors(tmp_path: Any) -> None:
    bad = _StubProvider("cerebras", exc=Exception("429 rate limit"))
    good = _StubProvider("openrouter", answer="from openrouter")
    mgr = _mgr(
        tmp_path, {"cerebras": bad, "openrouter": good}, ["cerebras", "openrouter"], "cerebras"
    )

    assert mgr.chat(messages=[{"role": "user", "content": "hi"}]) == "from openrouter"
    assert good.calls == 1


def test_complete_json_fails_over(tmp_path: Any) -> None:
    bad = _StubProvider("cerebras", exc=Exception("503 service unavailable"))
    good = _StubProvider("openrouter", answer="ok")
    mgr = _mgr(
        tmp_path, {"cerebras": bad, "openrouter": good}, ["cerebras", "openrouter"], "cerebras"
    )

    assert mgr.complete_json(prompt="x") == {"answer": "ok"}


def test_chat_flags_key_invalid_on_401_then_fails_over(tmp_path: Any) -> None:
    bad = _StubProvider("cerebras", exc=_Http401())
    good = _StubProvider("openrouter", answer="ok")
    mgr = _mgr(
        tmp_path, {"cerebras": bad, "openrouter": good}, ["cerebras", "openrouter"], "cerebras"
    )

    assert mgr.chat(messages=[{"role": "user", "content": "hi"}]) == "ok"
    assert bad.key_invalid is True


def test_failover_skips_already_invalid_provider(tmp_path: Any) -> None:
    bad = _StubProvider("cerebras", exc=Exception("boom"))
    dead = _StubProvider("groq", answer="should-not-be-called")
    dead.key_invalid = True
    good = _StubProvider("openrouter", answer="ok")
    mgr = _mgr(
        tmp_path,
        {"cerebras": bad, "groq": dead, "openrouter": good},
        ["cerebras", "groq", "openrouter"],
        "cerebras",
    )

    assert mgr.chat(messages=[{"role": "user", "content": "hi"}]) == "ok"
    assert dead.calls == 0
    assert good.calls == 1


def test_single_failing_provider_raises(tmp_path: Any) -> None:
    """No other provider to fail over to → raise so the caller can fall back."""
    bad = _StubProvider("openrouter", exc=Exception("429 rate limit"))
    mgr = _mgr(tmp_path, {"openrouter": bad}, ["openrouter"], "openrouter")

    with pytest.raises(Exception):
        mgr.chat(messages=[{"role": "user", "content": "hi"}])


def test_key_invalid_cooldown_reprobes_after_window(tmp_path: Any, monkeypatch) -> None:
    """A key_invalid provider is excluded within the cooldown, then re-probed
    (flag cleared) once the cooldown window elapses — a transient 401 no longer
    disables it for the whole session."""
    from app.providers import factory as _mod

    bad = _StubProvider("cerebras", exc=Exception("boom"))
    good = _StubProvider("openrouter", answer="ok")
    bad.key_invalid = True
    mgr = _mgr(
        tmp_path, {"cerebras": bad, "openrouter": good}, ["cerebras", "openrouter"], "openrouter"
    )

    names1 = [p.name for p, _m in mgr._failover_candidates(None, None)]
    assert "cerebras" not in names1  # within cooldown → excluded

    monkeypatch.setattr(_mod, "_KEY_INVALID_COOLDOWN_SECONDS", 0.0)
    names2 = [p.name for p, _m in mgr._failover_candidates(None, None)]
    assert "cerebras" in names2  # cooldown elapsed → re-probed
    assert bad.key_invalid is False


def test_429_records_model_for_derank(tmp_path: Any) -> None:
    """A persistent 429 records the model so auto-selection de-ranks it."""
    bad = _StubProvider("openrouter", exc=Exception("429 rate limit"))
    mgr = _mgr(tmp_path, {"openrouter": bad}, ["openrouter"], "openrouter")
    with pytest.raises(Exception):
        mgr.chat(messages=[{"role": "user", "content": "hi"}])
    assert "openrouter::model-x" in mgr._model_penalty
    assert "model-x" in mgr._penalized_model_ids("openrouter")


class _MultiModelProvider(LLMProvider):
    """Provider with several models; a chosen ``bad_model`` always 429s."""

    def __init__(self, name: str, models: list[str], bad_model: str) -> None:
        self.name = name
        self._models = models
        self._bad = bad_model
        self.key_invalid = False
        self.calls: list[str | None] = []

    def is_available(self) -> bool:
        return not self.key_invalid

    def list_models(self) -> list[str]:
        return list(self._models)

    def select_model(self, preferred_model: str | None = None) -> str:
        return self._models[0]

    def complete_text(self, prompt: str, model: str | None = None, max_tokens: int = 700) -> str:
        return ""

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, max_tokens: int = 700
    ) -> str:
        self.calls.append(model)
        if model == self._bad:
            raise Exception("429 rate limit")
        return f"ok:{model}"

    def complete_json(
        self, prompt: str, model: str | None = None, max_tokens: int = 700
    ) -> dict[str, Any]:
        self.calls.append(model)
        if model == self._bad:
            raise Exception("429 rate limit")
        return {"answer": f"ok:{model}"}


def test_intra_provider_model_failover(tmp_path: Any) -> None:
    """The top model 429s → rotate to another model of the SAME provider, even
    when no other provider is configured (the real single-OpenRouter case)."""
    p = _MultiModelProvider(
        "openrouter",
        ["gpt-oss-120b:free", "llama-3.3-70b:free"],
        bad_model="gpt-oss-120b:free",
    )
    mgr = _mgr(tmp_path, {"openrouter": p}, ["openrouter"], "openrouter")

    out = mgr.chat(messages=[{"role": "user", "content": "hi"}])
    assert out == "ok:llama-3.3-70b:free"  # rotated off the 429ing top model
    assert p.calls[0] == "gpt-oss-120b:free" and len(p.calls) >= 2


class _JsonStub(_StubProvider):
    """Stub whose complete_json returns a fixed payload (e.g. ``{}``)."""

    def __init__(self, name: str, payload: dict[str, Any]) -> None:
        super().__init__(name)
        self._payload = payload

    def complete_json(
        self, prompt: str, model: str | None = None, max_tokens: int = 700
    ) -> dict[str, Any]:
        self.calls += 1
        return self._payload


def test_empty_json_fails_over_to_next_provider(tmp_path: Any) -> None:
    """A 200-with-empty-dict reply (reasoning-only free models) is useless:
    it must be penalized AND failed over, not returned to the caller."""
    empty = _JsonStub("cerebras", {})
    good = _JsonStub("openrouter", {"answer": "ok"})
    mgr = _mgr(
        tmp_path, {"cerebras": empty, "openrouter": good}, ["cerebras", "openrouter"], "cerebras"
    )

    assert mgr.complete_json(prompt="x") == {"answer": "ok"}
    assert empty.calls == 1 and good.calls == 1
    assert "cerebras::model-x" in mgr._model_penalty


def test_all_empty_returns_empty_without_raise(tmp_path: Any) -> None:
    """Every candidate empty → return the empty result (contract: empty never
    raises; callers have their own fallbacks), all penalized."""
    e1 = _JsonStub("cerebras", {})
    e2 = _JsonStub("openrouter", {})
    mgr = _mgr(tmp_path, {"cerebras": e1, "openrouter": e2}, ["cerebras", "openrouter"], "cerebras")

    assert mgr.complete_json(prompt="x") == {}
    assert e1.calls == 1 and e2.calls == 1
    assert "cerebras::model-x" in mgr._model_penalty
    assert "openrouter::model-x" in mgr._model_penalty


def test_explicit_pin_empty_returned_without_failover(tmp_path: Any) -> None:
    """An explicit provider pin is a deliberate choice — empty comes back as-is."""
    empty = _JsonStub("cerebras", {})
    good = _JsonStub("openrouter", {"answer": "ok"})
    mgr = _mgr(
        tmp_path, {"cerebras": empty, "openrouter": good}, ["cerebras", "openrouter"], "cerebras"
    )

    assert mgr.complete_json(prompt="x", provider_name="cerebras") == {}
    assert good.calls == 0


def test_explicit_provider_is_not_failed_over(tmp_path: Any) -> None:
    """An explicit provider request is honored — no silent switch to another."""
    bad = _StubProvider("cerebras", exc=Exception("boom"))
    good = _StubProvider("openrouter", answer="ok")
    mgr = _mgr(
        tmp_path, {"cerebras": bad, "openrouter": good}, ["cerebras", "openrouter"], "openrouter"
    )

    with pytest.raises(Exception):
        mgr.chat(messages=[{"role": "user", "content": "hi"}], provider_name="cerebras")
    assert good.calls == 0


# ── no-credit accounts (v1.7.8) ──────────────────────────────────────────────


class _CatalogProvider(_StubProvider):
    """A provider with a real catalog: free models plus paid ones."""

    def __init__(self, name: str, models: list[str], **kwargs: Any) -> None:
        super().__init__(name, **kwargs)
        self._models = models

    def list_models(self) -> list[str]:
        return list(self._models)

    def select_model(self, preferred_model: str | None = None) -> str:
        # The real providers hand back something from their own catalog; the
        # bare stub's "model-x" states no size and so clears every floor.
        return self._models[0] if self._models else "model-x"


class _Http403(Exception):
    def __init__(self) -> None:
        super().__init__("403 Key limit exceeded (total limit)")
        self.status_code = 403


def test_one_403_on_a_paid_model_retires_all_of_them(tmp_path: Any) -> None:
    """A 403 says the ACCOUNT has no credit, not that this model is bad.

    Without this, every paid model in the catalog gets its own turn to discover
    the same thing: 88 of the 191 calls in the 2026-07-27 scan were exactly that.
    """
    catalog = [
        "google/gemma-4-31b-it:free",
        "openai/gpt-4-turbo",
        "qwen/qwen-2.5-72b-instruct",
        "anthropic/claude-3.5-sonnet",
    ]
    provider = _CatalogProvider("openrouter", catalog)
    mgr = _mgr(tmp_path, {"openrouter": provider}, ["openrouter"], "openrouter")

    ranked_before = mgr._ranked_models_for(provider, limit=4)
    assert any(not m.endswith(":free") for m in ranked_before)

    mgr.record_model_penalty("openrouter", "openai/gpt-4-turbo", "forbidden")
    mgr.mark_no_credit("openrouter")

    ranked_after = mgr._ranked_models_for(provider, limit=4)
    assert ranked_after, "the free models must still be offered"
    assert all(m.endswith(":free") for m in ranked_after)


def test_no_credit_does_not_empty_an_all_paid_provider(tmp_path: Any) -> None:
    """Nothing free to fall back to: keep the provider in the chain rather than
    dropping it silently — its 403 is at least an honest error."""
    provider = _CatalogProvider("openrouter", ["openai/gpt-4-turbo", "openai/gpt-4o"])
    mgr = _mgr(tmp_path, {"openrouter": provider}, ["openrouter"], "openrouter")
    mgr.mark_no_credit("openrouter")
    assert mgr._ranked_models_for(provider, limit=3)


def test_no_credit_is_per_provider(tmp_path: Any) -> None:
    """Cerebras naming nothing ":free" must not be mistaken for a paid catalog."""
    cerebras = _CatalogProvider("cerebras", ["gemma-4-31b", "qwen-3-235b-instruct"])
    mgr = _mgr(tmp_path, {"cerebras": cerebras}, ["cerebras"], "cerebras")
    mgr.mark_no_credit("openrouter")  # a different provider's problem
    assert mgr._ranked_models_for(cerebras, limit=2)


def test_local_endpoint_is_exempt_from_the_scoring_floor(tmp_path: Any) -> None:
    """A 12B is what fits on a 12GB card; the 26B floor would leave nothing."""
    from app.services.scanner_service import _SCORING_POLICY

    local = _CatalogProvider("custom", ["gemma-4-12b", "qwen2.5:14b"])
    mgr = _mgr(tmp_path, {"custom": local}, ["custom"], "custom")
    ranked = mgr._ranked_models_for(local, limit=2, policy_override=_SCORING_POLICY)
    assert ranked, "a local model the user installed must not be floored out"

    # The same models on a remote catalog are still held to the floor.
    remote = _CatalogProvider("openrouter", ["gemma-4-12b", "qwen2.5:14b"])
    mgr_remote = _mgr(tmp_path, {"openrouter": remote}, ["openrouter"], "openrouter")
    assert mgr_remote._ranked_models_for(remote, limit=2, policy_override=_SCORING_POLICY) == []
