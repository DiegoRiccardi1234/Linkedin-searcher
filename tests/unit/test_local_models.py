"""Deciding whether this machine can score jobs by itself."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.services import local_models as lm


def _hw(
    vram: float, gpu: str = "NVIDIA GeForce RTX 5070", source: str = "nvidia-smi"
) -> lm.Hardware:
    return lm.Hardware(gpu_name=gpu, vram_gb=vram, ram_gb=16.0, cpu="x86", source=source)


def test_recommendation_scales_with_vram() -> None:
    """A 12GB card runs a 12B; a 24GB one runs the 27B; 6GB neither."""
    assert [m["tag"] for m in lm.recommend(_hw(12.0)).models][-1] == "qwen3:14b"
    assert [m["tag"] for m in lm.recommend(_hw(24.0)).models][-1] == "gemma3:27b"
    # 6 GB minus the context overhead leaves room for ~7B, so the 8B is out:
    # the ceiling is what FITS, not what nearly fits and then spills to the CPU.
    small = lm.recommend(_hw(6.0))
    assert [m["tag"] for m in small.models] == ["gemma3:4b"]
    assert small.verdict == "workable"
    assert [m["tag"] for m in lm.recommend(_hw(8.0)).models] == ["gemma3:4b", "qwen3:8b"]


def test_no_gpu_is_told_plainly() -> None:
    """A CPU-only machine must be told, not handed a model that takes minutes
    per offer."""
    reco = lm.recommend(_hw(0.0, gpu="", source="none"))
    assert reco.verdict == "cpu_only"
    assert reco.models == []


def test_a_tiny_card_gets_no_suggestions() -> None:
    reco = lm.recommend(_hw(2.0))
    assert reco.verdict == "cpu_only"
    assert reco.models == []


def test_already_usable_reads_the_size_from_the_tag_and_drops_what_spills() -> None:
    models = [
        {"name": "hf.co/unsloth/gemma-4-12b-it-GGUF:Q4_K_M", "size_gb": 6.8},
        {"name": "qwen3:32b", "size_gb": 20.0},  # will not fit a 12GB card
        {"name": "some-unlabelled-model", "size_gb": 3.0},
    ]
    usable = lm.already_usable(models, ceiling_b=16)
    names = [m["name"] for m in usable]
    assert "qwen3:32b" not in names
    assert names[0].endswith("Q4_K_M")  # biggest that fits, first
    assert usable[0]["params_b"] == 12
    assert usable[0]["family"] == "gemma"
    # A tag that states no size is kept (unknown ≠ too big), just ranked last.
    assert "some-unlabelled-model" in names


def test_snapshot_prefers_what_is_already_downloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suggesting a 12B download to someone who already has one is seven
    gigabytes for nothing."""
    monkeypatch.setattr(lm, "detect_hardware", lambda: _hw(12.0))
    monkeypatch.setattr(
        lm,
        "ollama_status",
        lambda *a, **k: {
            "running": True,
            "installed": True,
            "models": [{"name": "gemma-4-12b-it:Q4_K_M", "size_gb": 6.8}],
            "host": lm.OLLAMA_HOST,
        },
    )
    snap = lm.snapshot()
    by_tag = {m["tag"]: m for m in snap["recommendation"]["models"]}
    assert by_tag["gemma3:12b"]["installed"] == "1"  # same family, same size
    assert by_tag["qwen3:14b"]["installed"] == ""  # different family: still offered
    assert "puoi usarlo subito" in snap["recommendation"]["reason"]
    assert snap["ready"][0]["params_b"] == 12


def test_ollama_absent_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing here may raise: a machine without Ollama is the normal case."""

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(lm.urllib.request, "urlopen", _boom)
    status = lm.ollama_status()
    assert status["running"] is False
    assert status["models"] == []


def test_hardware_probe_survives_a_missing_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lm, "_run", lambda *a, **k: "")
    hw = lm.detect_hardware()
    assert hw.vram_gb == 0.0
    assert lm.recommend(hw).verdict in {"cpu_only", "unknown"}


def test_scoring_variant_widens_the_context_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama serves 4096 tokens whatever max_tokens says, and a scoring prompt
    is ~2500 of them: measured, every reply stopped at total_tokens=4096."""
    sent: dict[str, Any] = {}

    class _Resp:
        status = 200

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *_a: Any) -> None:
            return None

    def _fake_urlopen(request: Any, timeout: float = 0) -> Any:
        sent["url"] = request.full_url
        sent["body"] = json.loads(request.data.decode())
        return _Resp()

    monkeypatch.setattr(lm.urllib.request, "urlopen", _fake_urlopen)
    name = lm.ensure_scoring_variant("gemma-4-12b:Q4")
    assert name == lm.SCORING_VARIANT
    assert sent["url"].endswith("/api/create")
    assert sent["body"]["from"] == "gemma-4-12b:Q4"
    assert sent["body"]["parameters"]["num_ctx"] == lm.SCORING_NUM_CTX


def test_scoring_variant_falls_back_to_the_base_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """A narrower window is worse, not fatal: the truncation guard still catches
    it downstream, so refusing to configure anything would be the worse outcome."""

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(lm.urllib.request, "urlopen", _boom)
    assert lm.ensure_scoring_variant("gemma-4-12b:Q4") == "gemma-4-12b:Q4"


def test_scan_falls_back_to_the_cloud_when_the_local_server_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinning has no failover by design — but a local endpoint that is simply
    not running would send EVERY offer to the keyword estimate, one dead call at
    a time. Cheap to detect, so detect it."""
    from app.services import scanner_service as ss

    class _Settings:
        scoring_model = "jobfinder-scorer"
        scoring_provider = "custom"
        custom_base_url = "http://localhost:11434/v1"
        llm_provider_order = ["openrouter"]

    class _PM:
        settings = _Settings()

    monkeypatch.setattr(ss, "_local_server_reachable", lambda *_a, **_k: False)
    assert ss._scoring_call_kwargs(_PM()) == {"policy_override": ss._SCORING_POLICY}

    monkeypatch.setattr(ss, "_local_server_reachable", lambda *_a, **_k: True)
    assert ss._scoring_call_kwargs(_PM()) == {
        "provider_name": "custom",
        "model_name": "jobfinder-scorer",
    }


def test_a_pin_on_the_base_tag_is_widened_before_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """A config written by hand names the tag the user downloaded, not the
    variant — and Ollama serves that with a 4096-token window, truncating every
    scoring reply. Deriving the variant costs one instant call and no disk."""
    from app.services import scanner_service as ss

    class _Settings:
        scoring_model = "hf.co/unsloth/gemma-4-12B-it-qat-GGUF:UD-Q4_K_XL"
        scoring_provider = "custom"
        custom_base_url = "http://localhost:11434/v1"
        llm_provider_order = ["custom"]

    class _PM:
        settings = _Settings()

    asked: list[str] = []
    monkeypatch.setattr(ss, "_local_server_reachable", lambda *_a, **_k: True)
    monkeypatch.setattr(
        lm, "ensure_scoring_variant", lambda base: asked.append(base) or lm.SCORING_VARIANT
    )

    ss._widened_local_model.cache_clear()
    assert ss._scoring_call_kwargs(_PM())["model_name"] == lm.SCORING_VARIANT
    assert asked == [_Settings.scoring_model]


def test_a_pin_already_on_the_variant_asks_ollama_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ollama lists the variant as ``<name>:latest``; both spellings are it."""
    from app.services import scanner_service as ss

    class _Settings:
        scoring_model = f"{lm.SCORING_VARIANT}:latest"
        scoring_provider = "custom"
        custom_base_url = "http://localhost:11434/v1"
        llm_provider_order = ["custom"]

    class _PM:
        settings = _Settings()

    def _unexpected(_base: str) -> str:
        raise AssertionError("the variant must not be re-derived from itself")

    monkeypatch.setattr(ss, "_local_server_reachable", lambda *_a, **_k: True)
    monkeypatch.setattr(lm, "ensure_scoring_variant", _unexpected)

    ss._widened_local_model.cache_clear()
    assert ss._scoring_call_kwargs(_PM())["model_name"] == f"{lm.SCORING_VARIANT}:latest"
