"""Tests for model_selector heuristic, especially v1.2.0 new patterns."""

from __future__ import annotations

from app.providers.model_selector import (
    SCORING_MIN_SIZE_B,
    choose_best_model,
    is_scoring_fit,
    pick_default_model,
    rank_models,
    score_model_name,
)


def test_scoring_policy_quality_floor_is_26() -> None:
    """The scan-scoring floor is 26B (not 40): a 26B model clears it with no
    small_penalty, so clean mid-size models (gemma-4-26b) stay eligible, while a
    24B model is still de-ranked. Guards the floor lowering in _SCORING_POLICY."""
    from app.services.scanner_service import _SCORING_POLICY

    assert SCORING_MIN_SIZE_B == 26
    s26 = score_model_name("foo-26b-instruct", policy=_SCORING_POLICY)
    s24 = score_model_name("foo-24b-instruct", policy=_SCORING_POLICY)
    # identical except the -150 small_penalty that hits only the sub-floor 24B
    assert s26 - s24 == 150


def test_instruction_tuned_suffix_counts_as_instruct() -> None:
    """Google writes "-it" where everyone else writes "-instruct"; matching only
    the literal word cost the Gemmas the whole instruct bonus."""
    assert score_model_name("google/gemma-3-27b-it:free") > score_model_name("google/gemma-3-27b")
    assert score_model_name("google/gemma-3-27b-it") == score_model_name(
        "google/gemma-3-27b-instruct"
    )


def test_gemma_is_a_known_family() -> None:
    """Gemma was missing from the family ladder entirely: it scored as an
    unknown family (0) while every rival got 34-46, so the mid-size model that
    emits the cleanest JSON kept losing to giants that truncate it."""
    from app.services.scanner_service import _SCORING_POLICY

    gemma = score_model_name("google/gemma-3-27b-it:free", policy=_SCORING_POLICY)
    qwen = score_model_name("qwen/qwen-2.5-32b-instruct:free", policy=_SCORING_POLICY)
    assert gemma > 0
    assert abs(gemma - qwen) <= 15  # comparable, not a write-off


def test_reasoning_families_rank_below_clean_instruct_models() -> None:
    from app.services.scanner_service import _SCORING_POLICY

    gemma = score_model_name("google/gemma-3-27b-it:free", policy=_SCORING_POLICY)
    for slug in (
        "nvidia/llama-3.3-nemotron-super-49b:free",
        "tencent/hunyuan-a13b-instruct:free",
        "minimax/minimax-m1:free",
    ):
        assert score_model_name(slug, policy=_SCORING_POLICY) < gemma, slug


def test_pick_default_returns_none_for_empty_list() -> None:
    assert pick_default_model("openai", []) is None


def test_pick_default_excludes_embedding_only() -> None:
    result = pick_default_model("openai", ["text-embedding-3-large", "gpt-4o-2024-11"])
    assert result == "gpt-4o-2024-11"


def test_pick_default_excludes_tts_and_whisper() -> None:
    result = pick_default_model("openai", ["whisper-1", "tts-1", "gpt-4o-mini"])
    assert result == "gpt-4o-mini"


def test_pick_default_returns_none_when_all_avoided() -> None:
    assert pick_default_model("any", ["dall-e-3", "tts-1", "whisper-1"]) is None


def test_free_tier_wins_among_equivalents() -> None:
    free = "openai/gpt-oss-120b:free"
    paid = "openai/gpt-oss-120b"
    result = pick_default_model("openrouter", [paid, free])
    assert result == free


def test_preview_models_lose_to_stable() -> None:
    result = pick_default_model("openai", ["gpt-5-preview", "gpt-5-chat"])
    assert result == "gpt-5-chat"


def test_score_function_penalizes_embedding() -> None:
    embed = score_model_name("text-embedding-3-large")
    chat = score_model_name("gpt-4o-mini")
    assert chat > embed
    assert embed < -500


def test_choose_best_model_prefers_recent_llama() -> None:
    models = ["llama-3-8b-instruct", "llama-3.3-70b-instruct"]
    assert choose_best_model(models) == "llama-3.3-70b-instruct"


def test_choose_best_model_de_ranks_penalized_model() -> None:
    """A model recently 429'd is penalized in auto-selection (not excluded)."""
    models = ["llama-3.3-70b-instruct", "llama-3.1-70b-instruct"]
    winner = choose_best_model(models)
    loser = next(m for m in models if m != winner)
    assert choose_best_model(models, penalized={winner}) == loser
    assert choose_best_model(models, penalized=None) == winner
    # even if ALL are penalized, one is still returned (penalty, not exclusion)
    assert choose_best_model(models, penalized=set(models)) in models


def test_new_provider_families_get_family_bonus() -> None:
    """DeepSeek/xAI/GLM/Mistral models must score above an unknown model, so the
    ⭐ recommended pick is sensible for the newly-added providers."""
    baseline = score_model_name("mystery-model-xyz")
    for m in ("deepseek-chat", "grok-3-mini", "glm-4.6", "mistral-large-latest", "kimi-k2"):
        assert score_model_name(m) > baseline, m


def test_choose_best_prefers_known_new_family_over_unknown() -> None:
    assert choose_best_model(["mystery-xyz", "deepseek-chat"]) == "deepseek-chat"
    assert choose_best_model(["some-random-id", "mistral-large-latest"]) == "mistral-large-latest"


def test_new_family_models_are_default_pickable() -> None:
    assert pick_default_model("deepseek", ["deepseek-chat", "deepseek-coder"]) is not None
    assert pick_default_model("xai", ["grok-3", "grok-3-mini"]) is not None


def test_rank_models_excludes_hard_avoid_and_limits() -> None:
    models = ["gpt-4o", "text-embedding-3-large", "llama-3-8b-instruct"]
    ranked = rank_models(models, limit=2)
    assert "text-embedding-3-large" not in ranked  # hard-avoid dropped
    assert len(ranked) == 2
    # best-first, ordered by the shared scorer
    expected = sorted(
        [m for m in models if m != "text-embedding-3-large"],
        key=score_model_name,
        reverse=True,
    )
    assert ranked == expected


def test_rank_models_penalized_sinks_but_stays() -> None:
    models = ["llama-3.3-70b-instruct", "llama-3.1-70b-instruct"]
    top = rank_models(models)[0]
    ranked = rank_models(models, penalized={top})
    assert ranked[-1] == top  # de-ranked to the bottom
    assert set(ranked) == set(models)  # but still present


def test_rank_models_preferred_hoisted_to_front() -> None:
    models = ["gpt-4o", "llama-3-8b-instruct"]
    assert rank_models(models, preferred_model="llama-3-8b-instruct")[0] == "llama-3-8b-instruct"


# ── hard floor for scan scoring (v1.7.6) ─────────────────────────────────────


def test_hard_floor_excludes_unfit_scoring_models() -> None:
    """With ``hard_floor`` the unfit models are dropped, not just de-ranked.

    The soft floor only sinks them, so under a 429 storm (everything else
    penalized) the toy model won anyway: on a real scan a 12B VL model wrote two
    of the top scores and an explicit reasoning build returned choices=None 11
    times."""
    from app.services.scanner_service import _SCORING_POLICY

    models = [
        "google/gemma-4-31b-it:free",
        "nvidia/nemotron-nano-12b-v2-vl:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "nvidia/nemotron-3.5-content-safety:free",
    ]
    ranked = rank_models(models, policy=_SCORING_POLICY)
    assert ranked == ["google/gemma-4-31b-it:free"]


def test_hard_floor_can_return_empty() -> None:
    """No fit model → empty list, so the caller fails over instead of scoring
    with something it just declared unfit."""
    from app.services.scanner_service import _SCORING_POLICY

    assert rank_models(["some/toy-3b:free"], policy=_SCORING_POLICY) == []


def test_hard_floor_beats_an_unfit_preferred_model() -> None:
    """A pinned model the floor rejects is not hoisted back to the top.

    The pin is a preference stated once in settings, for the whole app; the floor
    is a per-task statement that this model cannot do THIS job. Real config:
    ``preferred_model = openai/gpt-oss-120b:free`` (a reasoning build) would sit
    on top of every scoring ranking and truncate its JSON."""
    from app.services.scanner_service import _SCORING_POLICY

    models = ["google/gemma-4-31b-it:free", "openai/gpt-oss-120b-reasoning:free"]
    ranked = rank_models(
        models, preferred_model="openai/gpt-oss-120b-reasoning:free", policy=_SCORING_POLICY
    )
    assert ranked == ["google/gemma-4-31b-it:free"]


def test_unfit_preferred_model_still_wins_without_a_floor() -> None:
    """Chat and the CV tools set no floor: there the pin stays the user's call."""
    models = ["google/gemma-4-31b-it:free", "openai/gpt-oss-120b-reasoning:free"]
    ranked = rank_models(models, preferred_model="openai/gpt-oss-120b-reasoning:free")
    assert ranked[0] == "openai/gpt-oss-120b-reasoning:free"


def test_default_policy_keeps_soft_behaviour() -> None:
    # Chat/CV paths don't set hard_floor: nothing is excluded.
    models = ["nvidia/nemotron-nano-12b-v2-vl:free", "google/gemma-4-31b-it:free"]
    assert set(rank_models(models)) == set(models)


def test_is_scoring_fit_ignores_models_without_a_stated_size() -> None:
    assert is_scoring_fit("some-provider/mystery-model:free") is True
    assert is_scoring_fit("some-provider/mystery-8b:free") is False


# ── ":free" is a naming convention, not a price (v1.7.8) ─────────────────────


def test_free_penalty_only_applies_where_paid_models_are_named() -> None:
    """Cerebras/Groq/Google are entirely free tier and name nothing ":free".

    Measured on the 2026-07-27 scan: Cerebras' gemma-4-31b (8 successes out of
    10, the best model of the day) scored -270 under the blanket penalty while a
    paid gpt-4-turbo sat at -262 and was therefore tried first — where it 403s.
    """
    from app.services.scanner_service import _SCORING_POLICY

    local_catalog = dict(_SCORING_POLICY, paid_by_name=False)
    openrouter_catalog = dict(_SCORING_POLICY, paid_by_name=True)

    assert score_model_name("gemma-4-31b", local_catalog) > 0
    # Same id on OpenRouter, where a bare name really does mean "paid".
    assert score_model_name("gemma-4-31b", openrouter_catalog) < 0
    assert score_model_name("google/gemma-4-31b-it:free", openrouter_catalog) > 0


def test_non_text_models_are_excluded_from_scoring() -> None:
    """A text-to-speech build was picked seven times to write JSON: its id says
    nothing about speech beyond the model's own name."""
    for model in (
        "canopylabs/orpheus-v1-english",
        "playai-tts",
        "nomic-embed-text",
        "whisper-large-v3",
    ):
        assert is_scoring_fit(model) is False, model
        assert score_model_name(model) <= -500, model

    # A normal instruct model is untouched by the widened list.
    assert is_scoring_fit("google/gemma-4-31b-it:free") is True
