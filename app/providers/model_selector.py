def _weight(policy: dict | None, key: str, default: int) -> int:
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


def score_model_name(model_name: str, policy: dict | None = None) -> int:
    name = model_name.lower()
    score = 0

    if "instruct" in name:
        score += _weight(policy, "instruct", 30)
    if "chat" in name:
        score += _weight(policy, "chat", 15)

    if "llama-4" in name or "llama4" in name:
        score += _weight(policy, "family", 40)
    elif "llama-3.3" in name or "llama3.3" in name:
        score += _weight(policy, "family", 40) - 5
    elif "llama-3" in name or "llama3" in name:
        score += _weight(policy, "family", 40) - 15

    if "70b" in name:
        score += _weight(policy, "size", 20)
    elif "34b" in name:
        score += _weight(policy, "size", 20) - 8
    elif "8b" in name:
        score += _weight(policy, "size", 20) - 12

    if "reason" in name:
        score += _weight(policy, "reasoning", 6)
    if "json" in name or "tool" in name:
        score += _weight(policy, "json", 12)
    if "turbo" in name or "instant" in name or "flash" in name:
        score += _weight(policy, "speed", 8)

    if "vision" in name:
        score += _weight(policy, "vision_penalty", -8)

    max_cost_tier = str((policy or {}).get("max_cost_tier", "high")).lower()
    if max_cost_tier in {"low", "medium"} and "70b" in name:
        score -= 15
    if max_cost_tier == "low" and ("34b" in name or "32b" in name):
        score -= 8

    prefer_fast = bool((policy or {}).get("prefer_fast", True))
    prefer_quality = bool((policy or {}).get("prefer_quality", True))
    if prefer_fast and ("instant" in name or "turbo" in name):
        score += 6
    if prefer_quality and ("70b" in name or "reason" in name):
        score += 6

    return score


def choose_best_model(models: list[str], preferred_model: str | None = None, policy: dict | None = None) -> str:
    if preferred_model:
        for model in models:
            if model.lower() == preferred_model.lower():
                return model

    if not models:
        raise RuntimeError("Nessun modello disponibile")

    ranked = sorted(models, key=lambda x: score_model_name(x, policy=policy), reverse=True)
    return ranked[0]
