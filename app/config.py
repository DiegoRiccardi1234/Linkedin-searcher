import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

# Two domain tokens each, never a bare role word. Measured on a real scan:
# "AI Specialist" alone returned 27 of 47 postings and nearly all the noise —
# job boards match "Specialist" against payroll, sales and partnership roles —
# while "AI QA" returned 15 with one strong hit and "LLM Evaluation" 5 with two.
# A narrow term fishes less and better, and it costs nothing to fix here.
DEFAULT_SEARCH_TERMS = [
    "AI QA",
    "LLM Evaluation",
    "AI Automation",
    "Prompt Engineering",
    "Data Annotation",
    "QA Engineer",
]

LOCAL_SECRETS_FILE = "local_secrets.json"
SUPPORTED_PROVIDERS = [
    "cerebras",
    "groq",
    "openai",
    "anthropic",
    "google",
    "openrouter",
    "deepseek",
    "xai",
    "glm",
    "mistral",
    # Any OpenAI-compatible endpoint the user points at: a model running on
    # their own machine (Ollama, LM Studio, vLLM, llama.cpp) or a gateway.
    # Configured by base URL; the key is optional because a local server has
    # none to give.
    "custom",
]


@dataclass
class AppSettings:
    workspace_dir: Path
    data_dir: Path
    db_path: Path
    groq_key_file: Path
    llm_provider_order: list[str]
    preferred_model: str | None
    # Per-context model overrides (empty/None = Auto). Pin a specific model for
    # scan scoring / chat / CV tools; the provider is the primary…
    scoring_model: str | None
    chat_model: str | None
    cv_model: str | None
    # …except when the pinned scoring model does not live on the primary. Running
    # the scoring on a local endpoint while chat and the CV tools stay on a cloud
    # provider is the whole point of the local-model panel, and asking OpenRouter
    # for "gemma-4-12b" (an Ollama tag) simply 404s.
    scoring_provider: str | None
    retention_days: int
    hours_old: int
    max_annunci: int
    delay_tra_ricerche: float
    location_default: str
    location_remote_default: str
    # Default Indeed/Glassdoor country (a jobspy Country name/alias, e.g. "italy",
    # "usa"). Overridable per-scan by the country selector.
    country_default: str
    default_search_terms: list[str]
    cerebras_api_key: str | None
    groq_api_key: str | None
    openai_api_key: str | None
    anthropic_api_key: str | None
    google_api_key: str | None
    openrouter_api_key: str | None
    deepseek_api_key: str | None
    xai_api_key: str | None
    glm_api_key: str | None
    mistral_api_key: str | None
    # Optional GLM/Zhipu endpoint override (env GLM_BASE_URL). Default is the
    # international host; the China console uses open.bigmodel.cn.
    glm_base_url: str | None
    # "custom" provider: the endpoint IS the configuration. Empty base URL =
    # not configured. The key is optional (local servers don't have one).
    custom_api_key: str | None
    custom_base_url: str | None
    model_selection_policy: dict[str, Any]
    # Tesseract language list passed to ``image_to_string(lang=...)`` (``+`` joined).
    # Default covers the 5 UI locales; the bundle ships ``eng+ita+spa+fra+deu+osd``.
    ocr_languages: str = "eng+ita+spa+fra+deu"
    # Max concurrent LLM scoring calls during a scan. Bounded so a burst doesn't
    # trip provider rate limits; DB writes stay serialized by the connection RLock.
    scan_concurrency: int = 4
    # Jobs scored per LLM call during a scan. >=2 sends N offers in one request
    # (fewer calls = less free-tier 429 exposure, ~4x faster in A/B testing); a
    # short/invalid batch falls back to per-offer scoring. 1 = one job per call.
    scan_batch_size: int = 3


def _load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        # A corrupt secrets/config file silently unconfigures every provider —
        # at least say why, so the user knows what to repair.
        logging.getLogger(__name__).warning("Corrupt JSON config ignored: %s (%s)", path, exc)
        return {}


def _as_int(cfg: dict[str, Any], key: str, default: int) -> int:
    """Read a numeric setting without letting a typo take the app down.

    ``settings.json`` is a user-editable file and the whole container is built at
    import time, so ``{"max_annunci": "venti"}`` used to raise a ValueError out
    of a module import — an unrecoverable traceback with no UI to report it. A
    bad value now falls back to the default and says so in the log.
    """
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        logging.getLogger(__name__).warning(
            "Setting %r is not a number (%r); using %s", key, cfg.get(key), default
        )
        return default


def _as_float(cfg: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        logging.getLogger(__name__).warning(
            "Setting %r is not a number (%r); using %s", key, cfg.get(key), default
        )
        return default


def save_local_provider_keys(
    data_dir: Path,
    cerebras_api_key: str | None = None,
    groq_api_key: str | None = None,
    openai_api_key: str | None = None,
    anthropic_api_key: str | None = None,
    google_api_key: str | None = None,
    openrouter_api_key: str | None = None,
    deepseek_api_key: str | None = None,
    xai_api_key: str | None = None,
    glm_api_key: str | None = None,
    mistral_api_key: str | None = None,
    custom_api_key: str | None = None,
    custom_base_url: str | None = None,
    primary_provider: str | None = None,
    preferred_model: str | None = None,
    scoring_model: str | None = None,
    chat_model: str | None = None,
    cv_model: str | None = None,
    scoring_provider: str | None = None,
) -> dict[str, Any]:
    data_dir.mkdir(parents=True, exist_ok=True)
    secrets_path = data_dir / LOCAL_SECRETS_FILE
    current = _load_optional_json(secrets_path)
    if not isinstance(current, dict):
        current = {}

    # A non-None value is a write: a non-empty string stores the key, an empty
    # string clears it (the "Remove key" UI path). ``None`` leaves it untouched.
    provider_keys = {
        "cerebras_api_key": cerebras_api_key,
        "groq_api_key": groq_api_key,
        "openai_api_key": openai_api_key,
        "anthropic_api_key": anthropic_api_key,
        "google_api_key": google_api_key,
        "openrouter_api_key": openrouter_api_key,
        "deepseek_api_key": deepseek_api_key,
        "xai_api_key": xai_api_key,
        "glm_api_key": glm_api_key,
        "mistral_api_key": mistral_api_key,
        "custom_api_key": custom_api_key,
        # The endpoint is what configures the custom provider, so it is stored
        # the same way a key is (and cleared the same way).
        "custom_base_url": custom_base_url,
    }
    for field_name, raw in provider_keys.items():
        if raw is None:
            continue
        value = raw.strip()
        if value:
            current[field_name] = value
        else:
            current.pop(field_name, None)

    if primary_provider is not None:
        normalized = primary_provider.strip().lower()
        if normalized in SUPPORTED_PROVIDERS:
            current["primary_provider"] = normalized
        else:
            current.pop("primary_provider", None)

    # Global preferred model + per-context overrides (scan scoring / chat / CV
    # tools). Non-empty string pins; empty string clears (back to Auto); None
    # leaves untouched — same convention as the keys above.
    for model_field, model_value in (
        ("preferred_model", preferred_model),
        ("scoring_model", scoring_model),
        ("chat_model", chat_model),
        ("cv_model", cv_model),
        ("scoring_provider", scoring_provider),
    ):
        if model_value is None:
            continue
        value = model_value.strip()
        if value:
            current[model_field] = value
        else:
            current.pop(model_field, None)

    secrets_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    status = {f"{p}_configured": bool(current.get(f"{p}_api_key")) for p in SUPPORTED_PROVIDERS}
    # The custom provider is configured by its endpoint, not by a key.
    status["custom_configured"] = bool(current.get("custom_base_url"))
    status["primary_provider"] = current.get("primary_provider", "")
    status["preferred_model"] = current.get("preferred_model", "")
    status["scoring_model"] = current.get("scoring_model", "")
    status["scoring_provider"] = current.get("scoring_provider", "")
    status["chat_model"] = current.get("chat_model", "")
    status["cv_model"] = current.get("cv_model", "")
    return status


def _load_dotenv(workspace_dir: Path) -> None:
    """Populate ``os.environ`` from ``.env`` in the workspace if present.

    Existing environment variables take precedence (a real env wins over the
    file). Lines that do not contain ``=`` and lines starting with ``#`` are
    ignored. Surrounding single/double quotes around values are stripped.
    """
    env_path = workspace_dir / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_settings(workspace_dir: Path) -> AppSettings:
    _load_dotenv(workspace_dir)
    data_dir = workspace_dir / "data"
    config_path = data_dir / "settings.json"
    cfg = _load_optional_json(config_path)

    data_dir.mkdir(parents=True, exist_ok=True)
    db_override = os.getenv("SEARCHER_DB_PATH")
    if db_override:
        db_path = Path(db_override).expanduser().resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        db_path = data_dir / "searcher.db"
    groq_key_file = workspace_dir / "groq key.txt"
    local_secrets = _load_optional_json(data_dir / LOCAL_SECRETS_FILE)
    if not isinstance(local_secrets, dict):
        local_secrets = {}

    groq_api_key = local_secrets.get("groq_api_key") or os.getenv("GROQ_API_KEY")
    if not groq_api_key and groq_key_file.exists():
        groq_api_key = groq_key_file.read_text(encoding="utf-8").strip()

    cerebras_api_key = local_secrets.get("cerebras_api_key") or os.getenv("CEREBRAS_API_KEY")
    openai_api_key = local_secrets.get("openai_api_key") or os.getenv("OPENAI_API_KEY")
    anthropic_api_key = local_secrets.get("anthropic_api_key") or os.getenv("ANTHROPIC_API_KEY")
    google_api_key = (
        local_secrets.get("google_api_key")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GEMINI_API_KEY")
    )
    openrouter_api_key = local_secrets.get("openrouter_api_key") or os.getenv("OPENROUTER_API_KEY")
    deepseek_api_key = local_secrets.get("deepseek_api_key") or os.getenv("DEEPSEEK_API_KEY")
    xai_api_key = local_secrets.get("xai_api_key") or os.getenv("XAI_API_KEY")
    glm_api_key = local_secrets.get("glm_api_key") or os.getenv("GLM_API_KEY")
    glm_base_url = local_secrets.get("glm_base_url") or os.getenv("GLM_BASE_URL")
    mistral_api_key = local_secrets.get("mistral_api_key") or os.getenv("MISTRAL_API_KEY")
    custom_api_key = local_secrets.get("custom_api_key") or os.getenv("CUSTOM_API_KEY")
    custom_base_url = local_secrets.get("custom_base_url") or os.getenv("CUSTOM_BASE_URL")

    provider_order = cfg.get("llm_provider_order", SUPPORTED_PROVIDERS)
    if not isinstance(provider_order, list) or not provider_order:
        provider_order = list(SUPPORTED_PROVIDERS)

    sanitized_order: list[str] = []
    seen: set[str] = set()
    for provider in provider_order:
        name = str(provider).strip().lower()
        if name in SUPPORTED_PROVIDERS and name not in seen:
            sanitized_order.append(name)
            seen.add(name)

    for provider in SUPPORTED_PROVIDERS:
        if provider not in seen:
            sanitized_order.append(provider)

    primary_provider = (
        str(local_secrets.get("primary_provider") or os.getenv("LLM_PROVIDER") or "")
        .strip()
        .lower()
    )
    if primary_provider in SUPPORTED_PROVIDERS:
        sanitized_order = [primary_provider] + [p for p in sanitized_order if p != primary_provider]

    terms = cfg.get("default_search_terms", DEFAULT_SEARCH_TERMS)
    if not isinstance(terms, list) or not terms:
        terms = DEFAULT_SEARCH_TERMS

    model_policy = cfg.get("model_selection_policy", {})
    if not isinstance(model_policy, dict):
        model_policy = {}

    model_policy_defaults = {
        "prefer_fast": True,
        "prefer_quality": True,
        "prefer_json_reliability": True,
        "max_cost_tier": "high",
        "weights": {
            "instruct": 30,
            "chat": 15,
            "family": 40,
            "size": 20,
            "reasoning": 6,
            "json": 12,
            "speed": 8,
            "vision_penalty": -8,
        },
    }

    merged_policy: dict[str, Any] = dict(model_policy_defaults)
    for key, value in model_policy.items():
        if key == "weights" and isinstance(value, dict):
            weights = dict(cast(dict[str, Any], model_policy_defaults["weights"]))
            weights.update(value)
            merged_policy["weights"] = weights
        else:
            merged_policy[key] = value

    return AppSettings(
        workspace_dir=workspace_dir,
        data_dir=data_dir,
        db_path=db_path,
        groq_key_file=groq_key_file,
        llm_provider_order=sanitized_order,
        preferred_model=(
            local_secrets.get("preferred_model")
            or cfg.get("preferred_model")
            or os.getenv("LLM_MODEL")
        ),
        scoring_model=local_secrets.get("scoring_model") or None,
        scoring_provider=local_secrets.get("scoring_provider") or None,
        chat_model=local_secrets.get("chat_model") or None,
        cv_model=local_secrets.get("cv_model") or None,
        retention_days=_as_int(cfg, "retention_days", 15),
        hours_old=_as_int(cfg, "hours_old", 336),
        max_annunci=_as_int(cfg, "max_annunci", 20),
        delay_tra_ricerche=_as_float(cfg, "delay_tra_ricerche", 4.0),
        location_default=str(cfg.get("location_default", "Torino, Italy")),
        location_remote_default=str(cfg.get("location_remote_default", "Italy")),
        country_default=str(cfg.get("country_default", "italy")),
        default_search_terms=[str(x) for x in terms],
        cerebras_api_key=cerebras_api_key,
        groq_api_key=groq_api_key,
        openai_api_key=openai_api_key,
        anthropic_api_key=anthropic_api_key,
        google_api_key=google_api_key,
        openrouter_api_key=openrouter_api_key,
        deepseek_api_key=deepseek_api_key,
        xai_api_key=xai_api_key,
        glm_api_key=glm_api_key,
        mistral_api_key=mistral_api_key,
        glm_base_url=glm_base_url,
        custom_api_key=custom_api_key,
        custom_base_url=custom_base_url,
        model_selection_policy=merged_policy,
        scan_concurrency=max(1, _as_int(cfg, "scan_concurrency", 4)),
        scan_batch_size=max(1, _as_int(cfg, "scan_batch_size", 3)),
    )
