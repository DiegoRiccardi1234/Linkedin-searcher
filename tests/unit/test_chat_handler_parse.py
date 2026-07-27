"""Unit tests for `_parse_llm_response` edge cases including `suggested_roles`."""

from __future__ import annotations

from app.services.chat.handler import _parse_llm_response


def test_parse_plain_valid_json() -> None:
    raw = '{"answer": "ok", "action": null}'
    answer, action, roles = _parse_llm_response(raw)
    assert answer == "ok"
    assert action is None
    assert roles == []


def test_parse_strips_json_fence() -> None:
    raw = '```json\n{"answer": "wrapped", "action": null}\n```'
    answer, _, _ = _parse_llm_response(raw)
    assert answer == "wrapped"


def test_parse_unusable_reply_yields_nothing_rather_than_the_payload() -> None:
    """Returning the raw text is how the contract ended up in a chat bubble.

    The caller turns an empty answer into an honest sentence; it must never
    turn a failed parse into "here is your answer".
    """
    answer, action, roles = _parse_llm_response("this is not json")
    assert answer == ""
    assert action is None
    assert roles == []


def test_parse_non_dict_payload() -> None:
    answer, action, roles = _parse_llm_response('["a", "b"]')
    assert answer == ""
    assert action is None
    assert roles == []


def test_parse_salvages_json_wrapped_in_prose() -> None:
    """Models routinely announce the JSON before emitting it."""
    raw = 'Certo! Ecco la risposta:\n{"answer": "ciao", "action": null}\nSpero sia utile.'
    answer, action, roles = _parse_llm_response(raw)
    assert answer == "ciao"
    assert action is None


def test_parse_recovers_the_prose_from_a_truncated_envelope() -> None:
    """The real report: the reply was cut off inside suggested_roles, and the
    user read `"action": null, "suggested_roles": [ {...` in the chat."""
    raw = (
        '{"answer": "Ti consiglio questi ruoli.", "action": null, '
        '"suggested_roles": [{"label": "Consulente HR", "keywords": ["HR Consultant"]}, '
        '{"label": "Psicologo del'
    )
    answer, action, roles = _parse_llm_response(raw)
    assert answer == "Ti consiglio questi ruoli."
    assert "suggested_roles" not in answer
    assert "{" not in answer
    assert roles == []  # half-written extras are dropped, not guessed at
    assert action is None


def test_parse_empty_answer_does_not_fall_back_to_the_envelope() -> None:
    raw = '{"answer": "", "action": null, "suggested_roles": []}'
    answer, _, _ = _parse_llm_response(raw)
    assert answer == ""


def test_parsed_answer_keeps_braces_the_coach_meant_to_write() -> None:
    """A coach explaining JSON must be allowed to type braces."""
    raw = '{"answer": "Usa {\\"ruolo\\": \\"AI QA\\"} come esempio.", "action": null}'
    answer, _, _ = _parse_llm_response(raw)
    assert answer == 'Usa {"ruolo": "AI QA"} come esempio.'


def test_parse_extracts_suggested_roles() -> None:
    raw = (
        '{"answer": "ecco", "action": null, '
        '"suggested_roles": [{"label": "ML Engineer", "keywords": ["ML", "Machine Learning"]}]}'
    )
    _, _, roles = _parse_llm_response(raw)
    assert len(roles) == 1
    assert roles[0]["label"] == "ML Engineer"
    assert "ML" in roles[0]["keywords"]


def test_parse_drops_role_without_label() -> None:
    raw = (
        '{"answer": "x", "action": null, '
        '"suggested_roles": [{"keywords": ["foo"]}, {"label": "  ", "keywords": ["bar"]}, '
        '{"label": "Valid", "keywords": ["v"]}]}'
    )
    _, _, roles = _parse_llm_response(raw)
    assert len(roles) == 1
    assert roles[0]["label"] == "Valid"


def test_parse_role_keywords_fallback_to_label() -> None:
    raw = '{"answer": "x", "action": null, "suggested_roles": [{"label": "Cloud Engineer"}]}'
    _, _, roles = _parse_llm_response(raw)
    assert roles[0]["keywords"] == ["Cloud Engineer"]


def test_parse_role_keywords_non_list_is_replaced() -> None:
    raw = (
        '{"answer": "x", "action": null, '
        '"suggested_roles": [{"label": "DBA", "keywords": "oracle"}]}'
    )
    _, _, roles = _parse_llm_response(raw)
    assert roles[0]["keywords"] == ["DBA"]


def test_parse_ignores_non_dict_role_entries() -> None:
    raw = (
        '{"answer": "x", "action": null, '
        '"suggested_roles": ["not a dict", null, {"label": "OK", "keywords": ["k"]}]}'
    )
    _, _, roles = _parse_llm_response(raw)
    assert len(roles) == 1
    assert roles[0]["label"] == "OK"


def test_parse_action_kept_when_dict() -> None:
    raw = (
        '{"answer": "ricerca pronta", '
        '"action": {"type": "FILL_SCAN_FORM", "keywords": ["Python"], "locations": ["Roma"]}}'
    )
    _, action, _ = _parse_llm_response(raw)
    assert action is not None
    assert action["type"] == "FILL_SCAN_FORM"


def test_parse_action_rejected_when_not_dict() -> None:
    raw = '{"answer": "ok", "action": "FILL_FORM"}'
    _, action, _ = _parse_llm_response(raw)
    assert action is None
