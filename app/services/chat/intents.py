"""Detect user intent from free-form messages (search, role guidance)."""

from __future__ import annotations


def has_search_intent(message: str) -> bool:
    lower = message.lower()
    return any(
        token in lower
        for token in [
            "search", "scan", "find",
            "cerca", "ricerca", "trova",
            "buscar", "encontrar",
            "trouver", "chercher",
            "suchen", "finden",
        ]
    )


def has_role_guidance_intent(message: str) -> bool:
    lower = message.lower()
    has_cv_hint = any(token in lower for token in ["cv", "resume", "profilo", "profile"])
    has_role_hint = any(
        token in lower
        for token in [
            "figura lavorativa",
            "figure lavorative",
            "figura",
            "figure",
            "ruolo",
            "ruoli",
            "posizione",
            "posizioni",
            "job role",
            "job roles",
        ]
    )
    has_guidance_hint = any(
        token in lower
        for token in [
            "quali",
            "adatte",
            "adatto",
            "adatta",
            "consigli",
            "consiglia",
            "suggest",
            "best",
            "fit",
            "target",
            "dovrei",
            "devo",
        ]
    )
    return has_cv_hint and has_role_hint and has_guidance_hint
