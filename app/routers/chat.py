from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query, Request

from app import rate_limit
from app.models import (
    ChatRequest,
    ChatResponse,
    ChatSessionCreateRequest,
    ChatSessionRenameRequest,
    PinJobRequest,
)
from app.services.chat_service import handle_chat_message

if TYPE_CHECKING:
    from app.container import AppContainer


def build_router(container: AppContainer) -> APIRouter:
    router = APIRouter()

    @router.post("/api/chat", response_model=ChatResponse)
    def chat(request: Request, payload: ChatRequest) -> ChatResponse:
        rate_limit.check(request, bucket="chat", limit=20, window_seconds=60)
        container.require_provider()
        # Per-request popover choice wins; otherwise fall back to the Settings
        # default chat model (pinned on the primary provider), else Auto.
        provider = payload.provider
        model = payload.model
        if not model and container.settings.chat_model:
            model = container.settings.chat_model
            order = container.settings.llm_provider_order
            provider = provider or (order[0] if order else None)
        result = handle_chat_message(
            db=container.db,
            provider_manager=container.providers,
            message=payload.message,
            session_id=payload.session_id,
            provider=provider,
            model=model,
        )
        return ChatResponse(**result)

    @router.get("/api/chat/history")
    def chat_history(
        session_id: str = "default", limit: int = Query(default=30, ge=1, le=500)
    ) -> dict[str, Any]:
        items = container.db.list_chat_messages(session_id=session_id, limit=limit)
        return {"messages": items}

    @router.get("/api/chat/sessions")
    def list_chat_sessions() -> dict[str, Any]:
        sessions = container.db.list_chat_sessions()
        if not sessions:
            container.db.create_chat_session("default", "")
            sessions = container.db.list_chat_sessions()
        return {"sessions": sessions}

    @router.post("/api/chat/sessions")
    def create_chat_session(request: Request, payload: ChatSessionCreateRequest) -> dict[str, Any]:
        rate_limit.check(request, bucket="chat_session", limit=10, window_seconds=60)
        if len(container.db.list_chat_sessions()) >= 100:
            raise HTTPException(status_code=409, detail="too_many_sessions")
        import secrets

        new_id = "s_" + secrets.token_hex(6)
        session = container.db.create_chat_session(new_id, payload.title or "")
        return {"session": session}

    @router.patch("/api/chat/sessions/{session_id}")
    def rename_chat_session(session_id: str, payload: ChatSessionRenameRequest) -> dict[str, Any]:
        ok = container.db.rename_chat_session(session_id, payload.title)
        if not ok:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"ok": True}

    @router.delete("/api/chat/sessions/{session_id}")
    def delete_chat_session(session_id: str) -> dict[str, Any]:
        ok = container.db.delete_chat_session(session_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"ok": True}

    @router.get("/api/chat/sessions/{session_id}/pinned")
    def list_pinned(session_id: str) -> dict[str, Any]:
        return {"jobs": container.db.list_pinned_jobs(session_id)}

    @router.post("/api/chat/sessions/{session_id}/pin")
    def pin_job(session_id: str, payload: PinJobRequest) -> dict[str, Any]:
        if not container.db.get_job(payload.job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        container.db.touch_chat_session(session_id)
        container.db.pin_job(session_id, payload.job_id)
        return {"ok": True}

    @router.delete("/api/chat/sessions/{session_id}/pin/{job_id}")
    def unpin_job(session_id: str, job_id: int) -> dict[str, Any]:
        ok = container.db.unpin_job(session_id, job_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Pin not found")
        return {"ok": True}

    @router.get("/api/chat/prompts")
    def chat_prompts(lang: str | None = None) -> dict[str, Any]:
        from app.services.chat.context import suggest_chat_prompts

        resolved_lang = (lang or container.db.get_preference("ui_language", "en") or "en").lower()
        return {"prompts": suggest_chat_prompts(container.db, lang=resolved_lang)}

    return router
