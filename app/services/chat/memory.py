"""Conversation summarization to keep long chat sessions coherent.

When a session exceeds ``threshold`` regular messages we ask the LLM to
compress the oldest ``threshold - keep_last`` of them into a short summary
stored as a ``content_type='summary'`` row, then delete the originals.
"""

from __future__ import annotations

from typing import Any

from app.db import Database
from app.log import get_logger
from app.services.chat.prompts import LANG_MAP

log = get_logger(__name__)


def _build_summary_prompt(ui_language: str, transcript: str) -> list[dict[str, str]]:
    lang_name = LANG_MAP.get(ui_language, "English")
    return [
        {
            "role": "system",
            "content": (
                "You compress a career-coach conversation into a concise memory. "
                f"Respond ONLY with a plain paragraph of at most 150 words in {lang_name}. "
                "Preserve: user's preferences (role targets, remote/salary), "
                "roles discussed, decisions made, open questions. "
                "Do not output JSON, markdown, or code fences."
            ),
        },
        {
            "role": "user",
            "content": f"=== Conversation transcript ===\n{transcript}\n=== End ===",
        },
    ]


def maybe_summarize(
    db: Database,
    session_id: str,
    provider_manager: Any,
    threshold: int = 20,
    keep_last: int = 10,
) -> bool:
    """Summarize older messages when count exceeds threshold.

    Returns True if summarization happened. Safe to call on every turn.
    """
    total = db.count_chat_messages(session_id, content_type="message")
    if total <= threshold:
        return False

    # Load all regular messages oldest-first.
    messages = db.list_chat_messages(
        session_id=session_id, limit=10_000, include_types=("message",)
    )
    # messages already ordered ASC by id after list_chat_messages' reverse.
    to_compress = messages[:-keep_last] if keep_last > 0 else messages
    if not to_compress:
        return False

    transcript_lines: list[str] = []
    ids_to_delete: list[int] = []
    for row in to_compress:
        role = row.get("role", "user")
        content = str(row.get("content") or "")
        transcript_lines.append(f"{role.upper()}: {content}")
        ids_to_delete.append(int(row["id"]))
    transcript = "\n".join(transcript_lines)

    ui_lang = db.get_preference("ui_language", "en")
    prompt_messages = _build_summary_prompt(ui_lang, transcript)

    try:
        summary = provider_manager.chat(prompt_messages, max_tokens=250)
    except Exception as exc:
        log.warning("Chat summarization failed: %s", exc)
        return False

    summary_text = str(summary or "").strip()
    if not summary_text:
        return False

    db.save_chat_message(
        session_id=session_id,
        role="system",
        content=summary_text,
        content_type="summary",
    )
    db.delete_chat_messages_by_ids(ids_to_delete)
    log.info(
        "Chat session %s summarized: compressed %d messages into one summary",
        session_id,
        len(ids_to_delete),
    )
    return True


def load_session_summary(db: Database, session_id: str) -> str:
    rows = db.list_chat_messages(session_id=session_id, limit=1, include_types=("summary",))
    if not rows:
        return ""
    return str(rows[-1].get("content") or "").strip()
