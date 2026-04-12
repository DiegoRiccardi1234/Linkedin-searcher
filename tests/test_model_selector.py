from app.providers.model_selector import choose_best_model, score_model_name


def test_choose_preferred_model_if_exists() -> None:
    models = ["llama-3.1-8b-instant", "llama-4-scout-17b-16e-instruct"]
    selected = choose_best_model(models, preferred_model="llama-3.1-8b-instant")
    assert selected == "llama-3.1-8b-instant"


def test_policy_can_penalize_big_models() -> None:
    models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
    policy = {
        "max_cost_tier": "low",
        "weights": {
            "size": 5,
            "speed": 20,
        },
    }
    selected = choose_best_model(models, policy=policy)
    assert selected == "llama-3.1-8b-instant"


def test_score_model_name_returns_int() -> None:
    result = score_model_name("llama-4-scout-17b-16e-instruct")
    assert isinstance(result, int)
