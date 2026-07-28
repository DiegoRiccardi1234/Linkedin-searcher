"""What a local model costs depends on how its weights are stored.

The panel assumed Q4 for everything, so an FP16 build was reported as fitting a
card three times too small. And the catalogue was five hand-written tags, which
could never know that the best build for a 12 GB card is a quantisation-aware
12B published on Hugging Face — the one this machine ended up running, pulled by
hand.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import hf_catalog, local_models as lm


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("hf.co/unsloth/gemma-4-12B-it-qat-GGUF:UD-Q4_K_XL", "QAT-INT4"),
        ("gemma3:12b", "Q4_K_M"),  # Ollama's default when the tag is silent
        ("qwen3:14b-q8_0", "Q8_0"),
        ("llama3:8b-instruct-fp16", "F16"),  # "FP16" does not contain "F16"
        ("model-BF16", "BF16"),
        ("something-Q2_K", "Q2_K"),
    ],
)
def test_quant_is_read_from_the_tag(tag: str, expected: str) -> None:
    assert lm.quant_of(tag) == expected


def test_vram_depends_on_quantisation() -> None:
    """Same model, three storage formats, three very different cards."""
    assert lm.vram_needed_gb(12, "Q4_K_M") == pytest.approx(8.5, abs=0.1)
    assert lm.vram_needed_gb(12, "QAT-INT4") == pytest.approx(7.5, abs=0.1)
    assert lm.vram_needed_gb(12, "F16") == pytest.approx(25.5, abs=0.1)


def test_qat_costs_less_quality_than_a_plain_q4() -> None:
    """Quantisation-aware training recovers most of the int4 loss, which is why
    the model already on this machine is the right one to keep."""
    assert lm.quality_penalty("QAT-INT4") > lm.quality_penalty("Q4_K_M")
    assert lm.quality_penalty("Q8_0") == 0


def test_a_half_precision_model_is_not_offered_as_fitting() -> None:
    models = [
        {"name": "gemma-4-12b-it-fp16", "size_gb": 24.0},
        {"name": "gemma-4-12b-it-qat-GGUF:UD-Q4_K_XL", "size_gb": 7.0},
    ]
    usable = lm.already_usable(models, ceiling_b=16, vram_ceiling_gb=12.0)
    assert [m["name"] for m in usable] == ["gemma-4-12b-it-qat-GGUF:UD-Q4_K_XL"]
    assert usable[0]["quant"] == "QAT-INT4"
    assert usable[0]["vram_gb"] < 12.0


# --- the Hugging Face catalogue ----------------------------------------------

_LISTING = [
    {"id": "mixedbread-ai/mxbai-embed-large-v1", "downloads": 5_000_000},  # embeddings
    {"id": "ggml-org/embeddinggemma-300M-GGUF", "downloads": 4_000_000},  # embeddings
    {"id": "some/tts-voice-3B-GGUF", "downloads": 3_000_000},  # speech
    {"id": "tiny/toy-1B-GGUF", "downloads": 2_000_000},  # below the floor
    {"id": "unsloth/gemma-4-12B-it-qat-GGUF", "downloads": 1_000_000},
    {"id": "big/whale-70B-GGUF", "downloads": 900_000},  # will not fit 12 GB
]

_FILES = {
    "unsloth/gemma-4-12B-it-qat-GGUF": {
        "siblings": [
            {"rfilename": "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf"},
            {"rfilename": "MTP/mtp-gemma-4-12B-it-Q8_0.gguf"},
            {"rfilename": "mmproj-BF16.gguf"},  # vision projector, not a build
            {"rfilename": "README.md"},
        ]
    },
    "big/whale-70B-GGUF": {"siblings": [{"rfilename": "whale-70B-Q4_K_M.gguf"}]},
}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    hf_catalog._cache["data"] = None
    hf_catalog._cache["fetched_at"] = 0.0

    def fake_get(url: str, timeout: float) -> Any:
        if url.startswith(hf_catalog.HF_MODELS_URL) and "?" in url:
            return _LISTING
        repo = url.rsplit("/models/", 1)[-1].replace("%2F", "/")
        return _FILES.get(repo, {"siblings": []})

    monkeypatch.setattr(hf_catalog, "_get_json", fake_get)


def test_catalog_drops_models_that_cannot_write_json() -> None:
    """The download ranking puts embeddings and speech models at the very top."""
    repos = [entry["repo"] for entry in hf_catalog.fetch_catalog()]
    assert "mixedbread-ai/mxbai-embed-large-v1" not in repos
    assert "ggml-org/embeddinggemma-300M-GGUF" not in repos
    assert "some/tts-voice-3B-GGUF" not in repos
    assert "tiny/toy-1B-GGUF" not in repos, "below the usable floor"
    assert "unsloth/gemma-4-12B-it-qat-GGUF" in repos


def test_suggestions_fit_the_card_and_carry_a_real_pull_tag() -> None:
    picks = hf_catalog.fits_this_machine(hf_catalog.fetch_catalog(), vram_gb=12.0)
    assert [p["repo"] for p in picks] == ["unsloth/gemma-4-12B-it-qat-GGUF"]
    pick = picks[0]
    # Best quality that still fits: the Q8 build needs 14 GB, the QAT one 7.5.
    assert pick["quant"] == "QAT-INT4"
    assert pick["vram_gb"] < 12.0
    # The tag is the file's own suffix. Hand Ollama our normalised label
    # ("QAT-INT4") and the pull fails with "manifest unknown".
    assert pick["pull"] == "hf.co/unsloth/gemma-4-12B-it-qat-GGUF:UD-Q4_K_XL"


def test_a_card_too_small_gets_no_suggestions() -> None:
    assert hf_catalog.fits_this_machine(hf_catalog.fetch_catalog(), vram_gb=4.0) == []


def test_network_failure_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A suggestions panel must never be the thing that breaks the settings page."""

    def boom(url: str, timeout: float) -> Any:
        raise OSError("no network")

    monkeypatch.setattr(hf_catalog, "_get_json", boom)
    assert hf_catalog.fetch_catalog() == []
