"""Unit tests for the provider-level retry/backoff wrapper."""

from __future__ import annotations

import pytest

from app.providers.factory import _is_retryable, _with_retry


class _HttpError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def test_is_retryable_on_429_message() -> None:
    assert _is_retryable(Exception("Error code: 429 - rate limit")) is True


def test_is_retryable_on_503_status_attr() -> None:
    assert _is_retryable(_HttpError("server down", status_code=503)) is True


def test_is_retryable_on_500_message() -> None:
    assert _is_retryable(Exception("upstream returned 500 internal server error")) is True


def test_is_retryable_on_timeout() -> None:
    assert _is_retryable(TimeoutError("socket timeout")) is True


def test_not_retryable_on_401() -> None:
    assert _is_retryable(_HttpError("unauthorized", status_code=401)) is False


def test_not_retryable_on_400_validation() -> None:
    assert _is_retryable(Exception("400 bad request — invalid model")) is False


def test_retry_succeeds_after_transient_failures(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    monkeypatch.setenv("LLM_RETRY_BASE_SECONDS", "0")

    from app.providers import factory as _mod

    monkeypatch.setattr(_mod._time, "sleep", lambda _: None)

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _HttpError("server overloaded", status_code=503)
        return "ok"

    result = _with_retry(flaky, provider_label="test")
    assert result == "ok"
    assert calls["n"] == 3


def test_retry_gives_up_on_non_retryable(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MAX_RETRIES", "5")
    monkeypatch.setenv("LLM_RETRY_BASE_SECONDS", "0")

    from app.providers import factory as _mod

    monkeypatch.setattr(_mod._time, "sleep", lambda _: None)

    calls = {"n": 0}

    def fail_fast():
        calls["n"] += 1
        raise _HttpError("unauthorized", status_code=401)

    with pytest.raises(_HttpError):
        _with_retry(fail_fast, provider_label="test")

    assert calls["n"] == 1


def test_retry_respects_max_attempts(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")
    monkeypatch.setenv("LLM_RETRY_BASE_SECONDS", "0")

    from app.providers import factory as _mod

    monkeypatch.setattr(_mod._time, "sleep", lambda _: None)

    calls = {"n": 0}

    def always_503():
        calls["n"] += 1
        raise _HttpError("service unavailable", status_code=503)

    with pytest.raises(Exception):
        _with_retry(always_503, provider_label="test")

    assert calls["n"] == 2


def test_a_busy_model_is_asked_again_before_rotating(monkeypatch) -> None:
    """A 429 is a full model, not a broken one.

    This used to fail fast, on the reasoning that hammering a rate-limited
    model is rude. On a shared free pool it produced the opposite of what was
    wanted: the whole candidate list was spent in a couple of seconds and the
    offer came out unevaluated, while the model that was busy answered fine a
    few seconds later.
    """
    monkeypatch.setenv("LLM_MAX_RETRIES", "5")
    monkeypatch.setenv("LLM_RETRY_BASE_SECONDS", "0")

    from app.providers import factory as _mod

    waited: list[float] = []
    monkeypatch.setattr(_mod._time, "sleep", waited.append)
    monkeypatch.setattr(_mod, "_RATE_LIMIT_ATTEMPTS", 3)
    monkeypatch.setattr(_mod, "_RATE_LIMIT_WAIT_SECONDS", 10.0)

    calls = {"n": 0}

    def always_429():
        calls["n"] += 1
        raise _HttpError("too many requests", status_code=429)

    with pytest.raises(Exception):
        _with_retry(always_429, provider_label="test")

    # Three goes at the same model, ten seconds apart, and only then give up on
    # it so the caller can rotate.
    assert calls["n"] == 3
    assert waited == [10.0, 10.0]


def test_the_server_decides_how_long_to_wait_when_it_says_so(monkeypatch) -> None:
    monkeypatch.setenv("LLM_RETRY_BASE_SECONDS", "0")
    from app.providers import factory as _mod

    waited: list[float] = []
    monkeypatch.setattr(_mod._time, "sleep", waited.append)
    monkeypatch.setattr(_mod, "_RATE_LIMIT_ATTEMPTS", 2)

    def busy():
        raise _HttpError("rate-limited upstream, retry_after_seconds: 4", status_code=429)

    with pytest.raises(Exception):
        _with_retry(busy, provider_label="test")
    assert waited == [4.0]


def test_a_timeout_still_fails_fast(monkeypatch) -> None:
    """Unchanged, and for the original reason: a hung model hangs again, and
    each attempt costs the whole timeout window."""
    monkeypatch.setenv("LLM_MAX_RETRIES", "5")
    from app.providers import factory as _mod

    monkeypatch.setattr(_mod._time, "sleep", lambda _: None)
    calls = {"n": 0}

    def always_timeout():
        calls["n"] += 1
        raise TimeoutError("took too long")

    with pytest.raises(Exception):
        _with_retry(always_timeout, provider_label="test")
    assert calls["n"] == 1
