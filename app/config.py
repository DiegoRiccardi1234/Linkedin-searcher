import json
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_SEARCH_TERMS = [
    "Analista Funzionale Junior",
    "Junior QA Tester",
    "Junior Cybersecurity Analyst",
    "Junior Data Analyst",
    "Junior IT Consultant",
    "Junior AI Consultant",
]

LOCAL_SECRETS_FILE = "local_secrets.json"
SUPPORTED_PROVIDERS = ["cerebras", "groq", "openai", "anthropic", "google", "openrouter"]


@dataclass
class AppSettings:
    workspace_dir: Path
    data_dir: Path
    db_path: Path
    groq_key_file: Path
    llm_provider_order: list[str]
    preferred_model: str | None
    retention_days: int
    hours_old: int
    max_annunci: int
    delay_tra_chiamate: float
    delay_tra_ricerche: float
    location_default: str
    location_remote_default: str
    default_search_terms: list[str]
    cerebras_api_key: str | None
    groq_api_key: str | None
    openai_api_key: str | None
    anthropic_api_key: str | None
    google_api_key: str | None
    openrouter_api_key: str | None
    model_selection_policy: dict


def _load_optional_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_local_provider_keys(
    data_dir: Path,
    cerebras_api_key: str | None = None,
    groq_api_key: str | None = None,
    openai_api_key: str | None = None,
    anthropic_api_key: str | None = None,
    google_api_key: str | None = None,
    openrouter_api_key: str | None = None,
    primary_provider: str | None = None,
) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    secrets_path = data_dir / LOCAL_SECRETS_FILE
    current = _load_optional_json(secrets_path)
    if not isinstance(current, dict):
        current = {}

    if cerebras_api_key is not None:
        value = cerebras_api_key.strip()
        if value:
            current["cerebras_api_key"] = value
        else:
            current.pop("cerebras_api_key", None)

    if groq_api_key is not None:
        value = groq_api_key.strip()
        if value:
            current["groq_api_key"] = value
        else:
            current.pop("groq_api_key", None)

    if openai_api_key is not None:
        value = openai_api_key.strip()
        if value:
            current["openai_api_key"] = value
        else:
            current.pop("openai_api_key", None)

    if anthropic_api_key is not None:
        value = anthropic_api_key.strip()
        if value:
            current["anthropic_api_key"] = value
        else:
            current.pop("anthropic_api_key", None)

    if google_api_key is not None:
        value = google_api_key.strip()
        if value:
            current["google_api_key"] = value
        else:
            current.pop("google_api_key", None)

    if openrouter_api_key is not None:
        value = openrouter_api_key.strip()
        if value:
            current["openrouter_api_key"] = value
        else:
            current.pop("openrouter_api_key", None)

    if primary_provider is not None:
        normalized = primary_provider.strip().lower()
        if normalized in SUPPORTED_PROVIDERS:
            current["primary_provider"] = normalized
        else:
            current.pop("primary_provider", None)

    secrets_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "cerebras_configured": bool(current.get("cerebras_api_key")),
        "groq_configured": bool(current.get("groq_api_key")),
        "openai_configured": bool(current.get("openai_api_key")),
        "anthropic_configured": bool(current.get("anthropic_api_key")),
        "google_configured": bool(current.get("google_api_key")),
        "openrouter_configured": bool(current.get("openrouter_api_key")),
        "primary_provider": current.get("primary_provider", ""),
    }


def load_settings(workspace_dir: Path) -> AppSettings:
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

    primary_provider = str(
        local_secrets.get("primary_provider") or os.getenv("LLM_PROVIDER") or ""
    ).strip().lower()
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

    merged_policy = dict(model_policy_defaults)
    for key, value in model_policy.items():
        if key == "weights" and isinstance(value, dict):
            weights = dict(model_policy_defaults["weights"])
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
        preferred_model=cfg.get("preferred_model") or os.getenv("LLM_MODEL"),
        retention_days=int(cfg.get("retention_days", 15)),
        hours_old=int(cfg.get("hours_old", 336)),
        max_annunci=int(cfg.get("max_annunci", 20)),
        delay_tra_chiamate=float(cfg.get("delay_tra_chiamate", 1.5)),
        delay_tra_ricerche=float(cfg.get("delay_tra_ricerche", 4.0)),
        location_default=str(cfg.get("location_default", "Torino, Italy")),
        location_remote_default=str(cfg.get("location_remote_default", "Italy")),
        default_search_terms=[str(x) for x in terms],
        cerebras_api_key=cerebras_api_key,
        groq_api_key=groq_api_key,
        openai_api_key=openai_api_key,
        anthropic_api_key=anthropic_api_key,
        google_api_key=google_api_key,
        openrouter_api_key=openrouter_api_key,
        model_selection_policy=merged_policy,
    )
