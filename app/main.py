import contextlib
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import rate_limit
from app.config import SUPPORTED_PROVIDERS, AppSettings, load_settings, save_local_provider_keys
from app.cv_ingest import (
    InvalidCVContent,
    extract_candidate_name,
    extract_markdown_from_upload,
    summarize_profile,
    summarize_profile_with_llm,
    validate_cv_content,
)
from app.db import Database
from app.log import configure_logging, get_logger
from app.models import (
    ChatRequest,
    ChatResponse,
    ChatSessionCreateRequest,
    ChatSessionRenameRequest,
    FavoriteRequest,
    JobActionRequest,
    ManualJobCreateRequest,
    PinJobRequest,
    PreferenceUpdateRequest,
    ProfileUpdate,
    ProviderKeysRequest,
    RoleShortlistRequest,
    ScanRequest,
)
from app.providers.factory import ProviderManager
from app.services import roles_shortlist as roles_shortlist_svc
from app.services.chat_service import handle_chat_message
from app.services.scanner_service import analyze_offer, run_scan
from app.version import __version__, get_version_info


class AppContainer:
    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir
        self.settings: AppSettings = load_settings(workspace_dir)
        configure_logging(log_dir=self.settings.data_dir / "logs")
        # Propagate the OCR language list to ``cv_ingest`` (which reads the env
        # var lazily) so all extraction paths honor the user's locale config.
        os.environ.setdefault("JOBFINDER_OCR_LANG", self.settings.ocr_languages)
        self.log = get_logger("app.main")
        self.log.info("AppContainer initializing (workspace=%s)", workspace_dir)
        self.db = Database(self.settings.db_path)
        self.providers = ProviderManager(self.settings)
        # Give the manager a DB handle so it can persist token usage per call.
        self.providers._db = self.db
        self.providers.initialize()

        cv_path = workspace_dir / "cv.md"
        if cv_path.exists() and not self.db.get_latest_candidate_profile():
            markdown = cv_path.read_text(encoding="utf-8", errors="replace")
            summary = summarize_profile(markdown)
            created_id = self.db.save_candidate_profile(
                source_name="cv.md",
                markdown=markdown,
                summary=summary,
                name=summary.get("name") or extract_candidate_name(markdown),
            )
            self.db.set_active_profile(created_id)

        if not self.db.get_preference("active_profile_id", ""):
            latest = self.db.get_latest_candidate_profile()
            if latest:
                self.db.set_active_profile(int(latest["id"]))

    def shutdown(self) -> None:
        self.db.close()

    def reload_providers(self) -> None:
        self.settings = load_settings(self.workspace_dir)
        self.providers = ProviderManager(self.settings)
        self.providers._db = self.db
        self.providers.initialize()

    def keys_status(self) -> dict[str, Any]:
        primary = self.settings.llm_provider_order[0] if self.settings.llm_provider_order else ""
        return {
            "cerebras_configured": bool(self.settings.cerebras_api_key),
            "groq_configured": bool(self.settings.groq_api_key),
            "openai_configured": bool(self.settings.openai_api_key),
            "anthropic_configured": bool(self.settings.anthropic_api_key),
            "google_configured": bool(self.settings.google_api_key),
            "openrouter_configured": bool(self.settings.openrouter_api_key),
            "primary_provider": primary,
            "preferred_model": self.settings.preferred_model or "",
        }


def create_app(workspace_dir: Path) -> FastAPI:
    container = AppContainer(workspace_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        container.shutdown()

    fastapi_app = FastAPI(title="Job Finder", version=__version__, lifespan=lifespan)
    web_dir = (
        Path(sys._MEIPASS) / "web"  # type: ignore[attr-defined]
        if getattr(sys, "frozen", False)
        else workspace_dir / "web"
    )
    fastapi_app.mount("/web", StaticFiles(directory=web_dir), name="web")

    @fastapi_app.get("/")
    def home() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @fastapi_app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(web_dir / "favicon.svg", media_type="image/svg+xml")

    @fastapi_app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "provider": container.providers.metadata(),
            "keys": container.keys_status(),
            "preferences": container.db.list_preferences(),
            "db_path": str(container.settings.db_path),
        }

    @fastapi_app.get("/api/providers/keys/status")
    def providers_keys_status() -> dict[str, Any]:
        return {
            "ok": True,
            "keys": container.keys_status(),
            "provider": container.providers.metadata(),
        }

    @fastapi_app.post("/api/providers/keys")
    def save_provider_keys(payload: ProviderKeysRequest) -> dict[str, Any]:
        local_status = save_local_provider_keys(
            data_dir=container.settings.data_dir,
            cerebras_api_key=payload.cerebras_api_key,
            groq_api_key=payload.groq_api_key,
            openai_api_key=payload.openai_api_key,
            anthropic_api_key=payload.anthropic_api_key,
            google_api_key=payload.google_api_key,
            openrouter_api_key=payload.openrouter_api_key,
            primary_provider=payload.primary_provider,
            preferred_model=payload.preferred_model,
        )
        container.reload_providers()
        return {
            "ok": True,
            "keys": {**local_status, **container.keys_status()},
            "provider": container.providers.metadata(),
        }

    @fastapi_app.get("/api/providers/{name}/models")
    def provider_models(name: str, force_refresh: int = 0) -> dict[str, Any]:
        if name not in SUPPORTED_PROVIDERS:
            raise HTTPException(status_code=404, detail="unknown_provider")
        provider = container.providers.providers.get(name)
        if not provider or not provider.is_available():
            raise HTTPException(status_code=400, detail="key_missing")
        result = container.providers.get_models(name, force_refresh=bool(force_refresh))
        return {"ok": True, "provider": name, **result}

    @fastapi_app.post("/api/upload-cv")
    async def upload_cv(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
        rate_limit.check(request, bucket="upload_cv", limit=10, window_seconds=60)
        MAX_CV_BYTES = 5 * 1024 * 1024  # 5 MB
        ALLOWED_EXTS = {
            ".pdf",
            ".docx",
            ".md",
            ".markdown",
            ".txt",
            # Image formats handled via OCR (Tesseract).
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".avif",
            ".tiff",
            ".tif",
            ".bmp",
            ".svg",
        }

        filename = file.filename or "cv"
        ext = Path(filename).suffix.lower()
        if ext and ext not in ALLOWED_EXTS:
            raise HTTPException(status_code=415, detail=f"Unsupported CV type: {ext}")

        if file.size is not None and file.size > MAX_CV_BYTES:
            raise HTTPException(status_code=413, detail="CV file too large (max 5 MB)")

        data = await file.read()
        if len(data) > MAX_CV_BYTES:
            raise HTTPException(status_code=413, detail="CV file too large (max 5 MB)")
        if not data:
            raise HTTPException(status_code=400, detail="Empty CV file")
        markdown = extract_markdown_from_upload(filename, data)
        try:
            validate_cv_content(markdown)
        except InvalidCVContent as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        content_hash = hashlib.sha256(data).hexdigest()
        existing_id = container.db.find_candidate_profile_by_hash(content_hash)
        if existing_id is not None:
            container.db.set_active_profile(existing_id)
            existing = container.db.get_candidate_profile(existing_id)
            existing_summary = (existing or {}).get("summary_json") or {}
            return {
                "profile_id": existing_id,
                "source": file.filename,
                "summary": existing_summary,
                "deduplicated": True,
            }

        summary_method = "heuristic"
        retry_count = 0

        def _track_retry(attempt: int, wait: float, exc: Exception) -> None:
            nonlocal retry_count
            retry_count = attempt

        try:
            summary = summarize_profile_with_llm(
                markdown, container.providers, on_retry=_track_retry
            )
            # If the result is structurally richer than the heuristic, mark as llm.
            if any(k in summary for k in ("strengths", "industries", "summary")):
                summary_method = "llm"
        except Exception as exc:
            container.log.warning("LLM CV summarization failed, using heuristic: %s", exc)
            summary = summarize_profile(markdown)
        candidate_name = summary.get("name") if isinstance(summary, dict) else None
        if not candidate_name:
            candidate_name = extract_candidate_name(markdown)
        profile_id = container.db.save_candidate_profile(
            source_name=file.filename or "cv_upload",
            markdown=markdown,
            summary=summary,
            content_hash=content_hash,
            name=candidate_name,
        )
        container.db.set_active_profile(profile_id)

        preferred_roles = summary.get("preferred_roles")
        if isinstance(preferred_roles, list) and preferred_roles:
            container.db.set_preference(
                "preferred_roles", ",".join(str(r) for r in preferred_roles)
            )

        return {
            "profile_id": profile_id,
            "source": file.filename,
            "summary": summary,
            "summary_method": summary_method,
            "retries": retry_count,
        }

    @fastapi_app.get("/api/profile")
    def get_profile() -> dict[str, Any]:
        profile = container.db.get_active_candidate_profile()
        active = container.db.get_preference("active_profile_id", "")
        return {"profile": profile, "active_profile_id": active}

    @fastapi_app.patch("/api/profile")
    def update_profile(payload: ProfileUpdate) -> dict[str, Any]:
        profile = container.db.get_active_candidate_profile()
        if not profile:
            raise HTTPException(status_code=404, detail="no_profile")
        summary = dict(profile.get("summary_json") or {})
        if payload.preferred_roles is not None:
            cleaned = [r.strip() for r in payload.preferred_roles if r and r.strip()]
            summary["preferred_roles"] = cleaned
            container.db.set_preference("preferred_roles", json.dumps(cleaned, ensure_ascii=False))
        if payload.skills is not None:
            summary["skills"] = [s.strip() for s in payload.skills if s and s.strip()]
        if payload.languages is not None:
            summary["languages"] = [
                lang.strip() for lang in payload.languages if lang and lang.strip()
            ]
        container.db.update_candidate_profile_summary(int(profile["id"]), summary)
        updated = container.db.get_active_candidate_profile()
        return {"ok": True, "profile": updated}

    @fastapi_app.get("/api/profiles")
    def get_profiles() -> dict[str, Any]:
        profiles = container.db.list_candidate_profiles()
        active = container.db.get_preference("active_profile_id", "")
        return {"profiles": profiles, "active_profile_id": active}

    @fastapi_app.post("/api/profiles/{profile_id}/activate")
    def activate_profile(profile_id: int) -> dict[str, Any]:
        profile = container.db.get_candidate_profile(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        container.db.set_active_profile(profile_id)
        return {"ok": True, "active_profile_id": profile_id}

    @fastapi_app.delete("/api/profiles/{profile_id}")
    def delete_profile(profile_id: int) -> dict[str, Any]:
        deleted = container.db.delete_candidate_profile(profile_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Profile not found")
        active_raw = container.db.get_preference("active_profile_id", "")
        return {"ok": True, "deleted_id": profile_id, "active_profile_id": active_raw}

    @fastapi_app.get("/api/scan/stream")
    def scan_stream(
        search_terms: str = Query(default=""),
        location: str | None = Query(default=None),
        is_remote: bool = Query(default=False),
        sites: str = Query(default="linkedin,indeed"),
        experience_levels: str = Query(default=""),
        job_types: str = Query(default=""),
        work_types: str = Query(default=""),
    ) -> StreamingResponse:
        term_list = (
            [t.strip() for t in search_terms.split(",") if t.strip()] if search_terms else []
        )
        site_list = [s.strip() for s in sites.split(",") if s.strip()]

        def _split(csv: str) -> list[str]:
            return [s.strip() for s in csv.split(",") if s.strip()] if csv else []

        payload = ScanRequest(
            search_terms=term_list,
            location=location,
            is_remote=is_remote,
            sites=site_list,
            experience_levels=_split(experience_levels),
            job_types=_split(job_types),
            work_types=_split(work_types),
        )

        def event_generator() -> Iterator[str]:
            import json

            try:
                for event in run_scan(
                    db=container.db,
                    settings=container.settings,
                    provider_manager=container.providers,
                    payload=payload,
                ):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\\n\\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\\n\\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    def _require_provider() -> None:
        """Reject requests when no LLM key is configured. UI banner gates this too,
        but a backend 412 protects against direct API hits and the polling
        race where the user submits before the banner enforces."""
        if not any(
            container.keys_status()[k]
            for k in (
                "cerebras_configured",
                "groq_configured",
                "openai_configured",
                "anthropic_configured",
                "google_configured",
                "openrouter_configured",
            )
        ):
            raise HTTPException(
                status_code=412,
                detail={"code": "no_provider_configured", "message_key": "errors.noProvider"},
            )

    @fastapi_app.get("/api/usage/stats")
    def usage_stats(range: str = "today") -> dict[str, Any]:
        """Token-usage aggregates. ``range`` ∈ {today, week, month, all}."""
        from app.services.usage_tracker import aggregate_stats

        if range not in {"today", "week", "month", "all"}:
            range = "today"
        return aggregate_stats(container.db, range_=range)

    @fastapi_app.get("/api/setup/status")
    def setup_status() -> dict[str, Any]:
        ks = container.keys_status()
        provider_configured = any(
            ks[k]
            for k in (
                "cerebras_configured",
                "groq_configured",
                "openai_configured",
                "anthropic_configured",
                "google_configured",
                "openrouter_configured",
            )
        )
        cv_loaded = container.db.get_active_candidate_profile() is not None
        return {
            "ready": provider_configured,
            "provider_configured": provider_configured,
            "cv_loaded": cv_loaded,
            "first_run": not provider_configured and not cv_loaded,
        }

    @fastapi_app.post("/api/scan")
    def scan(request: Request, payload: ScanRequest) -> dict[str, Any]:
        rate_limit.check(request, bucket="scan", limit=5, window_seconds=60)
        _require_provider()
        result = {}
        for event in run_scan(
            db=container.db,
            settings=container.settings,
            provider_manager=container.providers,
            payload=payload,
        ):
            if "status" in event and event["status"] == "complete":
                result = event
            elif "error" in event:
                raise HTTPException(status_code=500, detail=event["error"])
        return result

    @fastapi_app.get("/api/jobs")
    def list_jobs(
        status: str | None = Query(default=None),
        only_favorites: bool = Query(default=False),
        only_new: bool = Query(default=False),
        search_text: str | None = Query(default=None),
        min_score: int | None = Query(default=None, ge=0, le=10),
        max_age_days: int | None = Query(default=None, ge=1, le=365),
        limit: int = Query(default=200, ge=1, le=2000),
    ) -> dict[str, Any]:
        jobs = container.db.list_jobs(
            status=status,
            only_favorites=only_favorites,
            only_new=only_new,
            search_text=search_text,
            min_score=min_score,
            max_age_days=max_age_days,
            limit=limit,
        )
        return {"jobs": jobs}

    @fastapi_app.get("/api/jobs/{job_id}")
    def get_job_detail(job_id: int) -> dict[str, Any]:
        job = container.db.get_job_with_analysis(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        recruiter = container.db.get_recruiter(job_id)
        return {"job": job, "recruiter": recruiter}

    @fastapi_app.post("/api/jobs/{job_id}/cover-letter")
    def generate_cover_letter(job_id: int) -> dict[str, Any]:
        job = container.db.get_job_with_analysis(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        profile = container.db.get_active_candidate_profile()
        profile_markdown = profile["markdown"] if profile else "CV non disponibile."

        linkedin_url = container.db.get_preference("linkedin_url", "")
        if linkedin_url:
            profile_markdown += f"\n\nProfilo LinkedIn: {linkedin_url}"

        titolo = job.get("titolo", "N/A")
        azienda = job.get("azienda", "N/A")
        descrizione = job.get("descrizione", "")

        recruiter = container.db.get_recruiter(job_id)
        if not recruiter and job.get("link") and "linkedin.com" in job.get("link", ""):
            try:
                from app.services.recruiter_scrape import fetch_recruiter

                fetched = fetch_recruiter(job["link"], timeout=3.0)
                if fetched:
                    container.db.upsert_recruiter(job_id, fetched)
                    recruiter = container.db.get_recruiter(job_id)
            except Exception as exc:
                container.log.debug("on-demand recruiter scrape failed: %s", exc)

        recruiter_block = ""
        if recruiter and (recruiter.get("name") or recruiter.get("headline")):
            parts = []
            if recruiter.get("name"):
                parts.append(f"Nome: {recruiter['name']}")
            if recruiter.get("title"):
                parts.append(f"Ruolo: {recruiter['title']}")
            if recruiter.get("headline"):
                parts.append(f"Headline: {recruiter['headline']}")
            recruiter_block = (
                "\nDESTINATARIO (recruiter / hiring manager visibile nell'annuncio):\n"
                + "\n".join(parts)
                + "\nApri la lettera con un saluto nominale rivolto a questa persona "
                "(es. 'Gentile {nome},') e fai un breve riferimento al suo ruolo."
            )

        prompt = f"""Sei un assistente che aiuta un IT professional a trovare lavoro.
Scrivi una Cover Letter / messaggio InMail (circa 100-150 parole, concisa ma efficace e performante, tono professionale ma non ingessato, focalizzato sui risultati) per questo annuncio.
Usa le informazioni del CV per evidenziare la corrispondenza con l'annuncio.
{recruiter_block}

CV candidato:
{profile_markdown[:3500]}

OFFERTA:
Titolo: {titolo}
Azienda: {azienda}
{("Descrizione: " + descrizione[:1800]) if descrizione else ""}

Non aggiungere testo extra. Devi rispondere SOLO con JSON valido con la chiave "cover_letter":
{{
  "cover_letter": "Il testo completo del messaggio..."
}}
"""

        try:
            result = container.providers.complete_json(prompt=prompt, max_tokens=600)
            if isinstance(result, dict) and "cover_letter" in result:
                cover_letter = result["cover_letter"]
            else:
                cover_letter = str(result)
            container.db.save_cover_letter(job_id, cover_letter)
        except Exception as e:
            cover_letter = f"Error generating cover letter: {e}"

        return {"cover_letter": cover_letter}

    @fastapi_app.get("/api/analytics")
    def get_analytics() -> dict[str, Any]:
        return container.db.get_analytics()

    @fastapi_app.get("/api/recommendations")
    def recommendations(limit: int = Query(default=5, ge=1, le=20)) -> dict[str, Any]:
        jobs = container.db.get_recommended_jobs(limit=limit)
        return {
            "jobs": jobs,
            "message": "Ecco i lavori prioritari da valutare e candidare.",
        }

    @fastapi_app.post("/api/jobs/manual")
    def add_manual_job(payload: ManualJobCreateRequest) -> dict[str, Any]:
        row = {
            "titolo": payload.titolo,
            "azienda": payload.azienda,
            "descrizione": payload.descrizione,
            "sede": payload.sede,
            "fonte": payload.fonte,
            "link": payload.link,
            "ricerca_usata": payload.ricerca_usata,
            "modalita": payload.modalita,
        }
        job_id = container.db.add_manual_job(row)

        profile = container.db.get_active_candidate_profile()
        profile_markdown = profile["markdown"] if profile else "Profile not loaded"

        linkedin_url = container.db.get_preference("linkedin_url", "")
        if linkedin_url:
            profile_markdown += f"\n\nProfilo LinkedIn: {linkedin_url}"

        analysis = analyze_offer(
            provider_manager=container.providers,
            profile_markdown=profile_markdown,
            titolo=payload.titolo,
            azienda=payload.azienda,
            descrizione=payload.descrizione,
        )
        container.db.update_job_analysis(job_id=job_id, analysis=analysis)
        return {"job_id": job_id, "analysis": analysis}

    @fastapi_app.post("/api/jobs/{job_id}/action")
    def set_job_action(job_id: int, payload: JobActionRequest) -> dict[str, Any]:
        job = container.db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        container.db.set_job_action(job_id=job_id, action=payload.action.value, notes=payload.notes)
        return {"ok": True}

    @fastapi_app.post("/api/jobs/{job_id}/favorite")
    def set_favorite(job_id: int, payload: FavoriteRequest) -> dict[str, Any]:
        job = container.db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        container.db.set_favorite(job_id=job_id, is_favorite=payload.is_favorite)
        return {"ok": True}

    @fastapi_app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: int) -> dict[str, Any]:
        deleted = container.db.delete_job(job_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"ok": True, "deleted_id": job_id}

    @fastapi_app.delete("/api/jobs")
    def delete_all_jobs() -> dict[str, Any]:
        count = container.db.delete_all_jobs()
        return {"ok": True, "deleted": count}

    @fastapi_app.post("/api/chat", response_model=ChatResponse)
    def chat(request: Request, payload: ChatRequest) -> ChatResponse:
        rate_limit.check(request, bucket="chat", limit=20, window_seconds=60)
        _require_provider()
        result = handle_chat_message(
            db=container.db,
            provider_manager=container.providers,
            message=payload.message,
            session_id=payload.session_id,
            provider=payload.provider,
            model=payload.model,
        )
        return ChatResponse(**result)

    @fastapi_app.get("/api/chat/history")
    def chat_history(session_id: str = "default", limit: int = 30) -> dict[str, Any]:
        items = container.db.list_chat_messages(session_id=session_id, limit=limit)
        return {"messages": items}

    @fastapi_app.get("/api/chat/sessions")
    def list_chat_sessions() -> dict[str, Any]:
        sessions = container.db.list_chat_sessions()
        if not sessions:
            container.db.create_chat_session("default", "")
            sessions = container.db.list_chat_sessions()
        return {"sessions": sessions}

    @fastapi_app.post("/api/chat/sessions")
    def create_chat_session(payload: ChatSessionCreateRequest) -> dict[str, Any]:
        import secrets

        new_id = "s_" + secrets.token_hex(6)
        session = container.db.create_chat_session(new_id, payload.title or "")
        return {"session": session}

    @fastapi_app.patch("/api/chat/sessions/{session_id}")
    def rename_chat_session(session_id: str, payload: ChatSessionRenameRequest) -> dict[str, Any]:
        ok = container.db.rename_chat_session(session_id, payload.title)
        if not ok:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"ok": True}

    @fastapi_app.delete("/api/chat/sessions/{session_id}")
    def delete_chat_session(session_id: str) -> dict[str, Any]:
        ok = container.db.delete_chat_session(session_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"ok": True}

    @fastapi_app.get("/api/chat/sessions/{session_id}/pinned")
    def list_pinned(session_id: str) -> dict[str, Any]:
        return {"jobs": container.db.list_pinned_jobs(session_id)}

    @fastapi_app.post("/api/chat/sessions/{session_id}/pin")
    def pin_job(session_id: str, payload: PinJobRequest) -> dict[str, Any]:
        if not container.db.get_job(payload.job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        container.db.touch_chat_session(session_id)
        container.db.pin_job(session_id, payload.job_id)
        return {"ok": True}

    @fastapi_app.delete("/api/chat/sessions/{session_id}/pin/{job_id}")
    def unpin_job(session_id: str, job_id: int) -> dict[str, Any]:
        ok = container.db.unpin_job(session_id, job_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Pin not found")
        return {"ok": True}

    @fastapi_app.get("/api/roles/shortlist")
    def get_role_shortlist() -> dict[str, Any]:
        return {"roles": roles_shortlist_svc.load(container.db)}

    @fastapi_app.post("/api/roles/shortlist")
    def add_role_shortlist(payload: RoleShortlistRequest) -> dict[str, Any]:
        return {"roles": roles_shortlist_svc.add(container.db, payload.roles or [])}

    @fastapi_app.delete("/api/roles/shortlist/{role}")
    def remove_role_shortlist(role: str) -> dict[str, Any]:
        return {"roles": roles_shortlist_svc.remove(container.db, role)}

    @fastapi_app.get("/api/chat/prompts")
    def chat_prompts(lang: str | None = None) -> dict[str, Any]:
        from app.services.chat.context import suggest_chat_prompts

        resolved_lang = (lang or container.db.get_preference("ui_language", "en") or "en").lower()
        return {"prompts": suggest_chat_prompts(container.db, lang=resolved_lang)}

    @fastapi_app.get("/api/version")
    def version_info(refresh: bool = False) -> dict[str, Any]:
        return get_version_info(force_refresh=refresh)

    @fastapi_app.post("/api/update")
    def run_update() -> dict[str, Any]:
        from scripts.update import update as run_update_script

        result = run_update_script(repo_root=workspace_dir)
        # Refresh version cache so banner reflects new state on next /api/version call.
        get_version_info(force_refresh=True)
        return result

    @fastapi_app.post("/api/update/start", status_code=202)
    def start_bundle_update() -> dict[str, Any]:
        if not getattr(sys, "frozen", False):
            raise HTTPException(
                status_code=409,
                detail="Bundle update is only available in the standalone Windows build. "
                "Use `git pull && pip install -r requirements.txt` in dev mode.",
            )
        info = get_version_info(force_refresh=True)
        latest = info.get("latest")
        current = info.get("current")
        if not latest or latest == current:
            raise HTTPException(status_code=409, detail="Already on the latest version.")

        install_dir = Path(sys.executable).resolve().parent
        updater_exe = install_dir / "Updater.exe"
        if not updater_exe.exists():
            raise HTTPException(
                status_code=500,
                detail=f"Updater.exe not found next to JobFinder.exe (looked in {install_dir}).",
            )

        # Lockfile guard: refuse if another updater spawn happened recently.
        # The double-click race shipped two parallel Updater.exe processes
        # both racing on JobFinder.exe and producing PermissionError.
        lock_path = container.workspace_dir / "data" / "update.lock"
        lock_ttl_seconds = 60
        if lock_path.exists():
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = lock_ttl_seconds + 1  # treat unreadable lock as stale
            if age < lock_ttl_seconds:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "update_already_in_progress",
                        "lock_age_s": int(age),
                        "lock_ttl_s": lock_ttl_seconds,
                    },
                )
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(f"{os.getpid()}\n{latest}\n", encoding="utf-8")

        # Launch Updater.exe from a temp copy so the install-dir copy is
        # unlocked while the new bundle is staged on top. Without this,
        # sync_install_dir hits PermissionError when shutil.copy2 reaches
        # Updater.exe — Windows holds an exclusive section-object lock on
        # the running EXE and no number of retries will release it.
        try:
            launcher_dir = Path(tempfile.mkdtemp(prefix=f"jobfinder-updater-{os.getpid()}-"))
            launcher_exe = launcher_dir / "Updater.exe"
            shutil.copy2(updater_exe, launcher_exe)
        except OSError as exc:
            with contextlib.suppress(OSError):
                lock_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=500,
                detail=f"Could not stage Updater.exe in temp dir: {exc!r}",
            ) from exc

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            [
                str(launcher_exe),
                "--install-dir",
                str(install_dir),
                "--parent-pid",
                str(os.getpid()),
                "--temp-launcher-dir",
                str(launcher_dir),
            ],
            close_fds=True,
            creationflags=creationflags,
        )

        # Give the response a moment to flush, then hard-exit so the updater
        # can replace our files. Graceful uvicorn shutdown is too slow.
        threading.Timer(0.8, lambda: os._exit(0)).start()
        return {"status": "updating", "next_version": latest, "from_version": current}

    @fastapi_app.post("/api/system/open-logs")
    def open_logs_folder() -> dict[str, Any]:
        """Open the logs directory in the OS file explorer.

        Used by the Settings "Open logs" button and the update modal's
        error-state link. Lets non-developer users grab `updater.log`
        for support without hunting through `data/logs/` by hand.
        """
        log_dir = container.workspace_dir / "data" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(log_dir))
            return {"ok": True, "path": str(log_dir)}
        raise HTTPException(status_code=501, detail="open_logs_unsupported_on_platform")

    @fastapi_app.delete("/api/update/lock")
    def clear_update_lock() -> dict[str, Any]:
        """Force-clear the update lockfile.

        Used by the frontend when the user explicitly closes the update
        modal before completion, so they can retry without waiting for
        the TTL to expire. Safe because at this point either the updater
        completed (lockfile already gone) or it crashed (no live updater
        will fight us for files).
        """
        lock_path = container.workspace_dir / "data" / "update.lock"
        existed = lock_path.exists()
        with contextlib.suppress(OSError):
            lock_path.unlink(missing_ok=True)
        return {"cleared": existed}

    @fastapi_app.get("/api/update/progress")
    def update_progress() -> dict[str, Any]:
        """Return latest updater event so the frontend can render a step indicator.

        Reads the tail of ``data/logs/updater.log``, finds the most recent
        ``EVENT {...}`` JSON line, and maps the event name to a step + percent.
        Falls back to ``{"step": "idle"}`` if the log is missing or empty.
        """
        log_path = container.workspace_dir / "data" / "logs" / "updater.log"
        if not log_path.exists():
            return {"step": "idle", "percent": 0, "event": None}
        try:
            with log_path.open("rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 8192))
                tail = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return {"step": "idle", "percent": 0, "event": None}

        last_event: dict[str, Any] | None = None
        for line in reversed(tail.splitlines()):
            if not line.startswith("EVENT "):
                continue
            try:
                last_event = json.loads(line[6:])
                break
            except (ValueError, json.JSONDecodeError):
                continue
        if last_event is None:
            return {"step": "idle", "percent": 0, "event": None}

        name = str(last_event.get("event", ""))
        step_map = {
            "started": ("download", 5),
            "parent_exited": ("download", 10),
            "download_start": ("download", 15),
            "download_done": ("verify", 50),
            "download_skipped": ("verify", 50),
            "verify_start": ("verify", 55),
            "verify_done": ("replace", 70),
            "replace_start": ("replace", 75),
            "replace_done": ("restart", 90),
            "restart_spawned": ("restart", 95),
            "error": ("error", 0),
        }
        step, percent = step_map.get(name, ("download", 0))
        return {
            "step": step,
            "percent": percent,
            "event": name,
            "details": last_event,
        }

    @fastapi_app.post("/api/preferences")
    def update_preference(payload: PreferenceUpdateRequest) -> dict[str, Any]:
        container.db.set_preference(payload.key, payload.value)
        return {"ok": True, "preferences": container.db.list_preferences()}

    @fastapi_app.get("/api/preferences")
    def get_preferences() -> dict[str, Any]:
        return {"preferences": container.db.list_preferences()}

    @fastapi_app.get("/api/applications/export")
    def export_applications(format: str = "csv") -> StreamingResponse:
        cur = container.db.conn.cursor()
        raw_rows = cur.execute(
            "SELECT titolo, azienda, sede, status, punteggio_ai, consiglio, link, "
            "updated_at, first_seen_at FROM jobs WHERE status IN (?, ?, ?) "
            "ORDER BY updated_at DESC",
            ("applied", "interviewing", "rejected"),
        ).fetchall()

        records = [
            {
                "title": r[0] or "",
                "company": r[1] or "",
                "location": r[2] or "",
                "status": r[3] or "",
                "ai_score": r[4] or 0,
                "advice": r[5] or "",
                "url": r[6] or "",
                "updated_at": r[7] or "",
                "first_seen_at": r[8] or "",
            }
            for r in raw_rows
        ]

        fmt = (format or "csv").lower()
        if fmt == "json":
            import json as _json_export

            body = _json_export.dumps(records, ensure_ascii=False, indent=2)
            filename = f"applications_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
            return StreamingResponse(
                iter([body.encode("utf-8")]),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        # CSV
        from io import StringIO

        buf = StringIO()
        if records:
            writer = csv.DictWriter(buf, fieldnames=list(records[0].keys()), delimiter=";")
            writer.writeheader()
            writer.writerows(records)
        else:
            buf.write("")
        filename = f"applications_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        return StreamingResponse(
            iter([buf.getvalue().encode("utf-8-sig")]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @fastapi_app.post("/api/export/csv")
    def export_csv() -> dict[str, Any]:
        rows = container.db.export_jobs_for_csv()
        if not rows:
            raise HTTPException(status_code=400, detail="No jobs to export")

        output_name = f"lavori_webapp_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        output_path = container.workspace_dir / output_name
        columns = list(rows[0].keys())

        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)

        return {"ok": True, "file": str(output_path)}

    return fastapi_app


_DEFAULT_WORKSPACE = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = (
    Path(os.environ["JOBFINDER_WORKSPACE"]).resolve()
    if os.environ.get("JOBFINDER_WORKSPACE")
    else _DEFAULT_WORKSPACE
)
app = create_app(WORKSPACE_DIR)
