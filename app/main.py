import csv
import hashlib
import os
import subprocess
import sys
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import rate_limit
from app.config import AppSettings, load_settings, save_local_provider_keys
from app.cv_ingest import (
    InvalidCVContent,
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
    FavoriteRequest,
    JobActionRequest,
    ManualJobCreateRequest,
    PreferenceUpdateRequest,
    ProviderKeysRequest,
    RoleShortlistRequest,
    ScanRequest,
)
from app.providers.factory import ProviderManager
from app.services import roles_shortlist as roles_shortlist_svc
from app.services.chat_service import handle_chat_message
from app.services.scanner_service import analyze_offer, run_scan
from app.version import get_version_info


class AppContainer:
    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir
        self.settings: AppSettings = load_settings(workspace_dir)
        configure_logging(log_dir=self.settings.data_dir / "logs")
        self.log = get_logger("app.main")
        self.log.info("AppContainer initializing (workspace=%s)", workspace_dir)
        self.db = Database(self.settings.db_path)
        self.providers = ProviderManager(self.settings)
        self.providers.initialize()

        cv_path = workspace_dir / "cv.md"
        if cv_path.exists() and not self.db.get_latest_candidate_profile():
            markdown = cv_path.read_text(encoding="utf-8", errors="replace")
            summary = summarize_profile(markdown)
            created_id = self.db.save_candidate_profile(
                source_name="cv.md", markdown=markdown, summary=summary
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

    fastapi_app = FastAPI(title="Job Finder", version="0.3.0", lifespan=lifespan)
    if getattr(sys, "frozen", False):
        web_dir = Path(sys._MEIPASS) / "web"  # type: ignore[attr-defined]
    else:
        web_dir = workspace_dir / "web"
    fastapi_app.mount("/web", StaticFiles(directory=web_dir), name="web")

    @fastapi_app.get("/")
    def home() -> FileResponse:
        return FileResponse(web_dir / "index.html")

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

    @fastapi_app.post("/api/upload-cv")
    async def upload_cv(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
        rate_limit.check(request, bucket="upload_cv", limit=10, window_seconds=60)
        MAX_CV_BYTES = 5 * 1024 * 1024  # 5 MB
        ALLOWED_EXTS = {".pdf", ".docx", ".md", ".markdown", ".txt"}

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

        try:
            summary = summarize_profile_with_llm(markdown, container.providers)
        except Exception as exc:
            container.log.warning("LLM CV summarization failed, using heuristic: %s", exc)
            summary = summarize_profile(markdown)
        profile_id = container.db.save_candidate_profile(
            source_name=file.filename or "cv_upload",
            markdown=markdown,
            summary=summary,
            content_hash=content_hash,
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
        }

    @fastapi_app.get("/api/profile")
    def get_profile() -> dict[str, Any]:
        profile = container.db.get_active_candidate_profile()
        active = container.db.get_preference("active_profile_id", "")
        return {"profile": profile, "active_profile_id": active}

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

    @fastapi_app.get("/api/scan/stream")
    def scan_stream(
        search_terms: str = Query(default=""),
        location: str | None = Query(default=None),
        is_remote: bool = Query(default=False),
        sites: str = Query(default="linkedin,indeed"),
    ) -> StreamingResponse:
        term_list = (
            [t.strip() for t in search_terms.split(",") if t.strip()] if search_terms else []
        )
        site_list = [s.strip() for s in sites.split(",") if s.strip()]

        payload = ScanRequest(
            search_terms=term_list,
            location=location,
            is_remote=is_remote,
            sites=site_list,
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

    @fastapi_app.post("/api/scan")
    def scan(request: Request, payload: ScanRequest) -> dict[str, Any]:
        rate_limit.check(request, bucket="scan", limit=5, window_seconds=60)
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
        return {"job": job}

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

        prompt = f"""Sei un assistente che aiuta un IT professional a trovare lavoro.
Scrivi una Cover Letter / messaggio InMail (circa 100-150 parole, concisa ma efficace e performante, tono professionale ma non ingessato, focalizzato sui risultati) per questo annuncio.
Usa le informazioni del CV per evidenziare la corrispondenza con l'annuncio.

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

    @fastapi_app.post("/api/chat", response_model=ChatResponse)
    def chat(request: Request, payload: ChatRequest) -> ChatResponse:
        rate_limit.check(request, bucket="chat", limit=20, window_seconds=60)
        result = handle_chat_message(
            db=container.db,
            provider_manager=container.providers,
            message=payload.message,
            session_id=payload.session_id,
            provider=payload.provider,
        )
        return ChatResponse(**result)

    @fastapi_app.get("/api/chat/history")
    def chat_history(session_id: str = "default", limit: int = 30) -> dict[str, Any]:
        items = container.db.list_chat_messages(session_id=session_id, limit=limit)
        return {"messages": items}

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

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            [
                str(updater_exe),
                "--install-dir",
                str(install_dir),
                "--parent-pid",
                str(os.getpid()),
            ],
            close_fds=True,
            creationflags=creationflags,
        )

        # Give the response a moment to flush, then hard-exit so the updater
        # can replace our files. Graceful uvicorn shutdown is too slow.
        threading.Timer(0.8, lambda: os._exit(0)).start()
        return {"status": "updating", "next_version": latest, "from_version": current}

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
