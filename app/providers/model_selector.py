import re
from typing import Any

# Quality floor for job<->CV scoring model selection: below this a free model is
# too small to score matches with nuance. Shared by scanner_service._SCORING_POLICY
# and the probe report's ``best`` pick so both agree on "too small". 26 keeps clean
# mid-size models (e.g. gemma-4-26b, ~2% JSON truncation) eligible while excluding
# sub-24B toys that scored job<->CV matches badly.
SCORING_MIN_SIZE_B = 26

# Name markers of models that are structurally unfit for high-volume JSON work,
# whatever their size: an explicit reasoning build burns the completion budget on
# hidden thinking before the JSON (finish_reason=length), a vision/VL build is a
# small multimodal model wearing a big name, and a safety-classifier build isn't a
# general completer at all. Measured on a real scan: with these in the pool the
# top of the ranking got written by a 12B VL model and by a reasoning model that
# returned choices=None 11 times. Used only when a policy sets ``hard_floor``.
SCORING_UNFIT_MARKERS = (
    "reasoning",
    "-vl",
    "vision",
    "content-safety",
    "guard",
    # Not a completer at all. These reached the scoring ranking because the soft
    # penalty in score_model_name only sinks them, and under a 429 storm every
    # decent model is penalized so a text-to-speech build wins by default.
    "tts",
    "speech",
    "voice",
    "orpheus",
    "whisper",
    "audio",
    "embed",
    "rerank",
)


def is_scoring_fit(model_name: str, min_size_b: int = SCORING_MIN_SIZE_B) -> bool:
    """False when a model must be EXCLUDED (not merely de-ranked) from scoring.

    The soft ``min_size_b`` weighting inside :func:`score_model_name` only sinks
    such models; under a 429 storm everything better is penalized and the toy
    model wins anyway. This is the hard gate: better an honest heuristic analysis
    than a confident score from a model that can't do the job.
    """
    name = model_name.lower()
    if any(marker in name for marker in SCORING_UNFIT_MARKERS):
        return False
    size_b = infer_size_b(name)
    # size_b == 0 means the id doesn't state a size — not a reason to exclude.
    return not (0 < size_b < min_size_b)


def infer_size_b(model_name: str) -> int:
    """Largest ``<N>b`` size advertised in a model id (parameters in billions),
    or 0 when none is stated. Shared by the scorer's quality weighting and the
    probe report's ``best`` pick so both agree on what counts as 'too small'."""
    sizes = [int(x) for x in re.findall(r"(\d{1,4})b", model_name.lower())]
    return max(sizes) if sizes else 0


def _weight(policy: dict[str, Any] | None, key: str, default: int) -> int:
    if not policy:
        return default
    weights = policy.get("weights", {})
    if not isinstance(weights, dict):
        return default
    value = weights.get(key, default)
    try:
        return int(value)
    except Exception:
        return default


def score_model_name(model_name: str, policy: dict[str, Any] | None = None) -> int:
    name = model_name.lower()
    score = 0

    # "-it" is Google's instruction-tuned suffix (gemma-3-27b-it) and "-instruct"
    # is everyone else's. Recognising only the literal word "instruct" cost the
    # instruction-tuned Gemmas ~30 points — the exact models that survive a
    # high-volume JSON workload best.
    if "instruct" in name or name.endswith("-it") or "-it:" in name or "-it-" in name:
        score += _weight(policy, "instruct", 30)
    if "chat" in name:
        score += _weight(policy, "chat", 15)

    if "llama-4" in name or "llama4" in name:
        score += _weight(policy, "family", 40)
    elif "llama-3.3" in name or "llama3.3" in name:
        score += _weight(policy, "family", 40) - 5
    elif "llama-3" in name or "llama3" in name:
        score += _weight(policy, "family", 40) - 15
    elif "gemma" in name:
        # Absent from this ladder until v1.7.7, so it scored as an unknown family
        # (0 points) while every rival got 34-46 — and gemma is the mid-size
        # family that emits clean JSON without a hidden reasoning phase.
        score += _weight(policy, "family", 40)
    elif "qwen" in name:
        score += _weight(policy, "family", 40) - 2
    elif "gpt-4.1" in name or "gpt-4o" in name:
        score += _weight(policy, "family", 40) + 6
    elif "gpt" in name or "deepseek" in name or "grok" in name:
        score += _weight(policy, "family", 40)
    elif "claude" in name:
        score += _weight(policy, "family", 40) + 4
    elif "gemini" in name:
        score += _weight(policy, "family", 40) + 2
    elif (
        "mistral" in name
        or "mixtral" in name
        or "codestral" in name
        or "magistral" in name
        or "glm" in name
        or "chatglm" in name
    ):
        score += _weight(policy, "family", 40) - 2
    elif "kimi" in name:
        score += _weight(policy, "family", 40) - 4
    elif "phi" in name or "command-r" in name or "command" in name:
        # Small-but-decent instruct families: known, ranked below the leaders.
        score += _weight(policy, "family", 40) - 6
    elif "nemotron" in name or "hunyuan" in name or "minimax" in name:
        # Known families, but the builds that reach a free tier are reasoning
        # ones that truncate JSON. Ranked below the mid-size instruct models
        # rather than treated as unknown.
        score += _weight(policy, "family", 40) - 20

    size_b = infer_size_b(name)
    base_size_weight = _weight(policy, "size", 20)
    if size_b >= 200:
        score += base_size_weight + 8
    elif size_b >= 70:
        score += base_size_weight
    elif size_b >= 30:
        score += base_size_weight - 6
    elif size_b >= 8:
        score += base_size_weight - 12

    # Quality floor: models that advertise a small size in their name are too
    # weak for nuanced work (job↔CV scoring, CV review). ``min_size_b`` de-ranks
    # them so selection stays "fastest among CAPABLE models". Gated on size_b > 0
    # so models that simply don't state a size aren't punished. Kept above -500
    # (de-rank, not exclude) so choose_best_model still returns something.
    min_size_b = int((policy or {}).get("min_size_b", 0) or 0)
    if min_size_b and 0 < size_b < min_size_b:
        score += _weight(policy, "small_penalty", -150)

    if "reason" in name:
        score += _weight(policy, "reasoning", 6)
    if "sonnet" in name:
        score += 4
    if "opus" in name:
        score += 5
    if "pro" in name:
        score += 2
    if "json" in name or "tool" in name:
        score += _weight(policy, "json", 12)
    if "turbo" in name or "instant" in name or "flash" in name:
        score += _weight(policy, "speed", 8)

    if "vision" in name:
        score += _weight(policy, "vision_penalty", -8)

    # Hard-avoid patterns: models that are not general-purpose chat completers.
    # The list is matched against the id, so it only catches what the id says:
    # Groq's "canopylabs/orpheus-v1-english" is a speech model whose id contains
    # none of the obvious words, and it was picked seven times to write JSON on
    # the 2026-07-27 scan. Names of known non-text families belong here too.
    avoid_patterns = (
        "embed",
        "embedding",
        "whisper",
        "tts",
        "dall-e",
        "moderation",
        "audio",
        "image-",
        "-image",
        "speech",
        "voice",
        "orpheus",
        "playai",
        "sora",
        "veo",
        "imagen",
        "rerank",
        "guard",
    )
    if any(p in name for p in avoid_patterns):
        score += _weight(policy, "non_chat_penalty", -1000)

    # Soft-avoid patterns: experimental/preview/deprecated builds.
    soft_avoid = ("preview", "deprecated", "experimental", "alpha")
    if any(p in name for p in soft_avoid):
        score += _weight(policy, "preview_penalty", -50)

    # OpenRouter free tier suffix: prefer ":free" so users can start without
    # paying, but only as a tie-breaker among equivalent models — a +25 bonus
    # used to hoist a rate-limited free model over a clearly better different
    # one, feeding the 429 storms. +5 still wins genuine ties (same base model,
    # free vs paid) without overriding a meaningfully stronger option; true
    # de-ranking of a model after repeated 429s is handled by request failover.
    if name.endswith(":free"):
        score += _weight(policy, "free_bonus", 5)

    max_cost_tier = str((policy or {}).get("max_cost_tier", "high")).lower()
    if max_cost_tier in {"low", "medium"} and size_b >= 70:
        score -= 15
    if max_cost_tier == "low" and size_b >= 30:
        score -= 8

    prefer_fast = bool((policy or {}).get("prefer_fast", True))
    prefer_quality = bool((policy or {}).get("prefer_quality", True))
    if prefer_fast and ("instant" in name or "turbo" in name):
        score += 6
    if prefer_quality and (size_b >= 70 or "reason" in name):
        score += 6
    if prefer_fast and size_b >= 200:
        score -= 5

    # prefer_free: hard-bias toward ":free" models. On a credit-less OpenRouter
    # account every paid model returns 403, so scan scoring (which sets this) must
    # never pick one — a big penalty on non-free models keeps selection on the
    # free tier without inflating free_bonus (which would hoist rate-limited free
    # models over genuinely better ones and feed 429 storms).
    #
    # But ":free" is an OpenRouter naming convention, not a fact about cost:
    # everything on Cerebras, Groq and Google AI Studio is free-tier and none of
    # it carries the suffix. Applying the penalty there punished the models that
    # actually work — measured on the 2026-07-27 scan, Cerebras' gemma-4-31b (8
    # successes out of 10, the best model of the day) scored -270 while a paid
    # gpt-4-turbo sat at -262 and was tried first. ``paid_by_name`` is set by the
    # caller, which knows which provider the catalog came from.
    if (
        bool((policy or {}).get("prefer_free", False))
        and bool((policy or {}).get("paid_by_name", True))
        and not name.endswith(":free")
    ):
        # Big enough to always lose to any ":free" model, small enough to stay
        # above the -500 hard-avoid cutoff so an all-paid catalog still ranks.
        score += _weight(policy, "paid_penalty", -300)

    return score


def _penalized_key(x: str, policy: dict[str, Any] | None, penalized: set[str] | None) -> int:
    penalty = 10_000 if penalized and x in penalized else 0
    return score_model_name(x, policy=policy) - penalty


def rank_models(
    models: list[str],
    preferred_model: str | None = None,
    policy: dict[str, Any] | None = None,
    *,
    penalized: set[str] | None = None,
    limit: int | None = None,
) -> list[str]:
    """Return chat-capable models ordered best-first.

    Hard-avoid models (embeddings/TTS/… score <= -500) are excluded. ``penalized``
    (e.g. models seen 429ing recently) are pushed to the bottom via a large
    penalty but kept in the list. An explicit ``preferred_model`` (a user choice)
    is hoisted to the front — everywhere the pin is the user's call, EXCEPT under
    ``hard_floor``: see below. ``limit`` truncates the result. Used both for the
    "recommended" default and for per-request intra-provider failover.

    A policy with ``hard_floor: True`` (scan scoring) additionally EXCLUDES models
    that :func:`is_scoring_fit` rejects, so the list can legitimately come back
    empty — the caller then fails over instead of scoring with a toy model. The
    hoist does NOT re-admit a model the floor just rejected: the pin is a
    preference expressed once, in settings, for every use of the app, while the
    floor is a per-task statement that this model cannot do THIS job. Measured:
    a ``preferred_model`` of ``gpt-oss-120b:free`` (a reasoning build) sat on top
    of every scoring ranking, truncated its JSON, and sent the whole scan down
    the failover path onto the local heuristic. Chat and the CV tools set no
    floor, so there the pin still wins.
    """
    viable = [m for m in models if score_model_name(m, policy=policy) > -500]
    floored = bool((policy or {}).get("hard_floor"))
    floor = int((policy or {}).get("min_size_b", SCORING_MIN_SIZE_B) or SCORING_MIN_SIZE_B)
    if floored:
        viable = [m for m in viable if is_scoring_fit(m, floor)]
    ranked = sorted(viable, key=lambda x: _penalized_key(x, policy, penalized), reverse=True)
    if preferred_model:
        pref = next((m for m in models if m.lower() == preferred_model.lower()), None)
        if pref is not None and not (floored and not is_scoring_fit(pref, floor)):
            ranked = [pref] + [m for m in ranked if m != pref]
    if limit is not None:
        ranked = ranked[: max(0, limit)]
    return ranked


def choose_best_model(
    models: list[str],
    preferred_model: str | None = None,
    policy: dict[str, Any] | None = None,
    *,
    penalized: set[str] | None = None,
) -> str:
    """Pick the highest-scoring model. ``penalized`` (e.g. models seen 429ing
    recently) get a large score penalty so auto-selection avoids them, but they
    are never excluded — if every model is penalized, one is still returned. An
    explicit ``preferred_model`` always wins (a user choice overrides de-ranking)."""
    if preferred_model:
        for model in models:
            if model.lower() == preferred_model.lower():
                return model

    if not models:
        raise RuntimeError("Nessun modello disponibile")

    ranked = rank_models(models, policy=policy, penalized=penalized)
    if ranked:
        return ranked[0]
    # Every model is hard-avoid: never exclude everything — return the best of them.
    return sorted(models, key=lambda x: _penalized_key(x, policy, penalized), reverse=True)[0]


def pick_default_model(
    provider_name: str, models: list[str], policy: dict[str, Any] | None = None
) -> str | None:
    """Return the best default model for a provider, or None if none viable.

    Used by the factory when the user selects "Auto (provider default)".
    Filters out models with hard-avoid patterns (embeddings, TTS, etc.) before
    ranking, so an embedding-only key never matches as a chat default.
    """
    if not models:
        return None
    viable = [
        m
        for m in models
        if score_model_name(m, policy=policy) > -500  # excludes hard-avoid
    ]
    if not viable:
        return None
    return choose_best_model(viable, policy=policy)
