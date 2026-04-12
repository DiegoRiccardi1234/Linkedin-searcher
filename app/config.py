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

    secrets_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "cerebras_configured": bool(current.get("cerebras_api_key")),
        "groq_configured": bool(current.get("groq_api_key")),
    }


def load_settings(workspace_dir: Path) -> AppSettings:
    data_dir = workspace_dir / "data"
    config_path = data_dir / "settings.json"
    cfg = _load_optional_json(config_path)

    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "searcher.db"
    groq_key_file = workspace_dir / "groq key.txt"
    local_secrets = _load_optional_json(data_dir / LOCAL_SECRETS_FILE)
    if not isinstance(local_secrets, dict):
        local_secrets = {}

    groq_api_key = local_secrets.get("groq_api_key") or os.getenv("GROQ_API_KEY")
    if not groq_api_key and groq_key_file.exists():
        groq_api_key = groq_key_file.read_text(encoding="utf-8").strip()

    cerebras_api_key = local_secrets.get("cerebras_api_key") or os.getenv("CEREBRAS_API_KEY")

    provider_order = cfg.get("llm_provider_order", ["cerebras", "groq"])
    if not isinstance(provider_order, list) or not provider_order:
        provider_order = ["cerebras", "groq"]

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
        llm_provider_order=[str(x).lower() for x in provider_order],
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
        model_selection_policy=merged_policy,
    )
