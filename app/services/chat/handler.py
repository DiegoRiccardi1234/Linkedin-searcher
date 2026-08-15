"""Main chat handler: orchestrates state → context → prompt → provider → parse."""

from __future__ import annotations

import json
import re
from typing import Any

from app.db import Database
from app.log import get_logger
from app.providers.factory import ProviderManager
from app.services import market_snapshot, market_web
from app.services.chat.context import (
    build_preferences_context,
    build_profile_context,
    jobs_context,
)
from app.services.chat.fallback import fallback_answer
from app.services.chat.memory import load_session_summary, maybe_summarize
from app.services.chat.prompts import system_prompt
from app.services.chat.state import extract_pref_updates, get_chat_state

log = get_logger(__name__)

#: Completion budget for one chat turn. The reply is an envelope carrying prose
#: plus up to five suggested roles with keywords; at 1400 it was routinely cut
#: off inside that list.
_CHAT_MAX_TOKENS = 2200


def _strip_markdown_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json") :].lstrip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:].lstrip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].rstrip()
    return cleaned


def _sanitize_chat_answer(text: str) -> str:
    """Last-ditch tidy of a reply that never parsed as the envelope.

    Only reached when :func:`_parse_llm_response` gave up: a successfully parsed
    ``answer`` is left alone, because a coach explaining JSON or code has every
    right to write braces.
    """
    if not text:
        return text
    cleaned = _strip_markdown_fence(text.strip())
    while cleaned and cleaned[0] in "{}" and cleaned.count("{") != cleaned.count("}"):
        cleaned = cleaned[1:].lstrip()
    while cleaned and cleaned[-1] in "{}" and cleaned.count("{") != cleaned.count("}"):
        cleaned = cleaned[:-1].rstrip()
    return cleaned.strip() or text


def _first_json_object(text: str) -> str | None:
    """The first complete ``{...}`` in ``text``, brace-matched, or None.

    Models routinely wrap the envelope in a sentence ("Sure! Here it is: {…}")
    or add a closing remark after it. ``json.loads`` refuses both, and the chat
    path — unlike ``complete_json`` — had no salvage step, so the whole reply
    was handed to the user as the answer: the JSON contract, rendered in the
    chat bubble.
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


#: Recovers the prose from an envelope that was cut off mid-object — the reply
#: hit the token ceiling somewhere inside ``suggested_roles``. ``chat()`` returns
#: a bare string, so there is no ``finish_reason`` to read here; an unterminated
#: object IS the signal.
_ANSWER_RE = re.compile(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)"', re.DOTALL)


def _recover_answer(text: str) -> str | None:
    match = _ANSWER_RE.search(text)
    if not match:
        return None
    try:
        recovered = json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        return None
    recovered = str(recovered).strip()
    return recovered or None


def _parse_llm_response(raw: str) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    """Extract ``answer``, ``action`` and ``suggested_roles`` from the JSON envelope.

    Returns ``("", None, [])`` when nothing usable can be recovered — the caller
    then shows an honest message. What it must never do is return the raw
    payload as the answer, which is how a user ended up reading
    ``"action": null, "suggested_roles": [ … ]`` in a chat bubble.
    """
    candidate = _strip_markdown_fence(raw)
    parsed: Any = None
    for text in (candidate, _first_json_object(candidate)):
        if not text:
            continue
        try:
            parsed = json.loads(text)
            break
        except json.JSONDecodeError:
            continue

    if not isinstance(parsed, dict):
        recovered = _recover_answer(candidate)
        if recovered:
            # Truncated or malformed envelope, but the prose survived: show it
            # and drop the half-written extras.
            log.info("Chat envelope unparseable; recovered the answer text only")
            return recovered, None, []
        log.warning("Chat response could not be parsed as the envelope (%d chars)", len(raw))
        return "", None, []

    answer = str(parsed.get("answer") or "").strip()
    if not answer:
        # A valid envelope with an empty answer used to fall back to the whole
        # raw payload as the message body.
        answer = _recover_answer(candidate) or ""
    action = _clean_action(parsed.get("action"))

    roles_raw = parsed.get("suggested_roles")
    roles: list[dict[str, Any]] = []
    if isinstance(roles_raw, list):
        for entry in roles_raw:
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("label") or "").strip()
            if not label:
                continue
            kws_raw = entry.get("keywords")
            if isinstance(kws_raw, list):
                kws = [str(k).strip() for k in kws_raw if str(k).strip()]
            else:
                kws = []
            roles.append({"label": label, "keywords": kws or [label]})
    return str(answer), action, roles


#: What the model is allowed to propose, and which profile fields it may
#: propose a value for. The user still has to press the button — nothing here
#: is applied on arrival — but an unknown action type or an unlisted field is
#: dropped before it reaches the page, so a confused model cannot invent a
#: control the app does not have.
_ACTION_TYPES = {"FILL_SCAN_FORM", "ADD_ROLES", "SET_PROFILE_FIELD", "OPEN_JOB"}
_SETTABLE_FIELDS = {
    "base_cities",
    "work_modes",
    "years_experience",
    "education_level",
    "grade",
    "driving_licence",
    "protected_category",
    "min_ral",
    "goal",
    "remote_mode",
    "prefer_role_qa",
    "prefer_role_cyber",
    "prefer_role_data",
}


def _clean_action(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("type") or "").strip().upper()
    if kind not in _ACTION_TYPES:
        if kind:
            log.info("Chat proposed an unknown action %r; dropped", kind)
        return None
    if kind == "SET_PROFILE_FIELD" and str(raw.get("field") or "") not in _SETTABLE_FIELDS:
        log.info("Chat proposed writing an unlisted field %r; dropped", raw.get("field"))
        return None
    return {**raw, "type": kind}


#: Shown when the model's reply cannot be turned into an answer at all. Better
#: an honest sentence in the user's language than the raw contract on screen.
_UNPARSEABLE_MESSAGE = {
    "en": "I couldn't put that answer together — please ask me again.",
    "it": "Non sono riuscito a formulare la risposta — riprova a chiedermelo.",
    "es": "No he podido formular la respuesta: vuelve a preguntármelo.",
    "de": "Ich konnte die Antwort nicht formulieren — frag mich bitte noch einmal.",
    "fr": "Je n'ai pas réussi à formuler la réponse — repose-moi la question.",
}


def _unparseable_message(db: Database) -> str:
    lang = str(db.get_preference("ui_language", "en") or "en").lower()[:2]
    return _UNPARSEABLE_MESSAGE.get(lang, _UNPARSEABLE_MESSAGE["en"])


#: How a preference detected in passing is worded when offered back.
_PREF_LABELS = {
    "remote_mode": {"full_remote": "full remote", "hybrid": "ibrido", "onsite": "in sede"},
}


def _preference_proposal(key: str, value: str) -> dict[str, Any]:
    """One detected preference, as an action the user can accept or ignore."""
    label = _PREF_LABELS.get(key, {}).get(value, value)
    return {"type": "SET_PROFILE_FIELD", "field": key, "value": value, "label": label}


def handle_chat_message(
    db: Database,
    provider_manager: ProviderManager,
    message: str,
    session_id: str,
    provider: str | None = None,
    model: str | None = None,
    view: str | None = None,
) -> dict[str, Any]:
    """Handle one chat turn.

    Flow:
    1. Persist the user message.
    2. Notice preferences stated in passing and propose them (never write).
    3. Compute the chat state (``no_cv`` / ``onboarding`` / ``ready_to_search`` / ``advising``)
       and, separately, which page the user is looking at.
    4. Build profile, preferences, and jobs context blocks.
    5. Call the active LLM provider with the state-specific system prompt.
    6. Parse the JSON envelope (``answer`` + optional ``action``). Fall back
       to a rule-based answer if the provider fails.
    7. Persist the assistant reply and return a summary dict.
    """
    is_first_message = db.count_chat_messages(session_id, "message") == 0
    db.touch_chat_session(session_id)
    db.save_chat_message(session_id=session_id, role="user", content=message)

    if is_first_message and session_id != "default":
        auto_title = message.strip().splitlines()[0][:40] if message.strip() else ""
        if auto_title:
            db.rename_chat_session(session_id, auto_title)

    # From here on the user message is already persisted: any unexpected failure
    # must still leave a coherent assistant reply, never an orphaned turn.
    try:
        # Detected, not applied. This used to write straight to the database:
        # say "full remote, minimo 25k" in passing and two preferences changed
        # with no confirmation and no notice — the one silent write left after
        # every chat action was turned into a card with a button.
        updates = extract_pref_updates(message)
        proposals = [_preference_proposal(key, value) for key, value in updates.items()]

        # Keep long conversations coherent without blowing up the prompt.
        try:
            maybe_summarize(db=db, session_id=session_id, provider_manager=provider_manager)
        except Exception as exc:  # never block the turn on summarizer failure
            log.warning("maybe_summarize failed: %s", exc)

        state = get_chat_state(db)
        ui_lang = db.get_preference("ui_language", "en")
        sys_prompt = system_prompt(state=state, ui_language=ui_lang, view=view)

        summary = load_session_summary(db, session_id)
        summary_block = f"\n\n=== Conversation summary so far ===\n{summary}" if summary else ""

        # What the market looked like in the postings this app actually read.
        # The coach was being asked market questions ("which roles fit me?", "is
        # this pay normal?") with no market data at all — only the CV and a few
        # job rows. Free: it is the user's own scan results, read back.
        market = market_snapshot.as_prompt_block(db)
        market_block = f"\n\n=== Mercato osservato dai tuoi scan ===\n{market}" if market else ""

        # For a question that is actually about the market, the model's training
        # cutoff is the problem: it answers confidently from memory. If the user
        # has a Google key, Gemini's Search grounding is free (1500/day) and
        # gives a current, sourced answer. No key, no network, no match on the
        # question -> nothing happens and the local snapshot still applies.
        if market_web.looks_like_market_question(message):
            grounded = market_web.ask(
                db,
                getattr(provider_manager.settings, "google_api_key", None),
                message,
                language=str(ui_lang or "it"),
            )
            if grounded:
                market_block += f"\n\n=== Dal web, verificato ora ===\n{grounded}"

        # Recent turns as their own messages so the model has real conversation
        # context (the rolling summary only kicks in for very long chats). The
        # just-saved current user message is dropped to avoid duplicating it.
        history_rows = db.list_chat_messages(session_id, limit=9, include_types=("message",))
        if history_rows and history_rows[-1].get("role") == "user":
            history_rows = history_rows[:-1]
        history_msgs = [
            {"role": str(r["role"]), "content": str(r["content"])}
            for r in history_rows[-6:]
            if r.get("role") in ("user", "assistant") and r.get("content")
        ]

        prompt_messages = [
            {"role": "system", "content": sys_prompt},
            *history_msgs,
            {
                "role": "user",
                "content": (
                    f"=== Candidate Profile ===\n{build_profile_context(db)}\n\n"
                    f"=== Preferences ===\n{build_preferences_context(db)}\n\n"
                    f"=== Top Job Listings ===\n{jobs_context(db, session_id=session_id)}"
                    f"{market_block}"
                    f"{summary_block}\n\n"
                    f"=== User Message ===\n{message}"
                ),
            },
        ]

        suggested_roles: list[dict[str, Any]] = []
        degraded = False
        try:
            raw_answer = provider_manager.chat(
                # The envelope carries prose AND up to five suggested roles with
                # their keywords; 1400 tokens cut that off mid-object often
                # enough that the truncated JSON reached the user as the answer.
                prompt_messages,
                max_tokens=_CHAT_MAX_TOKENS,
                provider_name=provider,
                model_name=model,
            )
            answer, action_payload, suggested_roles = _parse_llm_response(raw_answer)
            if not answer:
                answer = _unparseable_message(db)
                degraded = True
        except Exception as exc:
            log.error("Provider chat call failed, using fallback: %s", exc, exc_info=True)
            answer, action_payload = fallback_answer(db=db, message=message)
            degraded = True  # canned fallback, not a real LLM answer
            answer = _sanitize_chat_answer(answer)
        db.save_chat_message(
            session_id=session_id,
            role="assistant",
            content=answer,
            # Stored with the message so the pills survive a reload or a session
            # switch, instead of living only in this response.
            meta={"suggested_roles": suggested_roles} if suggested_roles else None,
        )
        return {
            "session_id": session_id,
            "answer": answer,
            "updated_preferences": {},
            # What the message seemed to say about the user's preferences, as
            # proposals they can accept. ``updated_preferences`` stays in the
            # response and stays empty: nothing was updated.
            "proposals": proposals,
            "chat_state": state,
            "action": action_payload,
            "suggested_roles": suggested_roles,
            "degraded": degraded,
        }
    except Exception:
        log.error("Chat turn failed after persisting user message", exc_info=True)
        error_answer = "Si è verificato un errore durante l'elaborazione del messaggio. Riprova."
        try:
            db.save_chat_message(session_id=session_id, role="assistant", content=error_answer)
        except Exception:  # best-effort: do not mask the original failure
            log.error("Could not persist assistant error message", exc_info=True)
        raise
