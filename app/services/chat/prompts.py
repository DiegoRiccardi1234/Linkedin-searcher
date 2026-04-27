"""Load system prompt templates from ``app/prompts/chat/*.txt``.

Templates are read lazily on first access and cached. Supported states:
``no_cv``, ``onboarding``, ``ready_to_search``, ``advising``. An extra
``json_envelope`` template is appended to every state to enforce the
JSON action protocol.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts" / "chat"
_STATES = {"no_cv", "onboarding", "ready_to_search", "advising"}
_FALLBACK_STATE = "advising"

LANG_MAP = {
    "en": "English",
    "it": "Italian",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
}


@cache
def _load(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8").strip()


def system_prompt(state: str, ui_language: str) -> str:
    """Build the full system prompt for the given chat state and UI language."""
    state_key = state if state in _STATES else _FALLBACK_STATE
    base = _load(state_key)
    envelope = _load("json_envelope")
    lang_name = LANG_MAP.get(ui_language, "English")
    return f"{base}\n\nIMPORTANT: Always respond in {lang_name}.\n\n{envelope}"
