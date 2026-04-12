import csv
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import AppSettings, load_settings
from app.cv_ingest import extract_markdown_from_upload, summarize_profile
from app.db import Database
from app.models import (
    ChatRequest,
    ChatResponse,
    FavoriteRequest,
    JobActionRequest,
    ManualJobCreateRequest,
    PreferenceUpdateRequest,
    ScanRequest,
)
from app.providers.factory import ProviderManager
from app.services.chat_service import handle_chat_message
from app.services.scanner_service import analyze_offer, run_scan


class AppContainer:
    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir
        self.settings: AppSettings = load_settings(workspace_dir)
        self.db = Database(self.settings.db_path)
        self.providers = ProviderManager(self.settings)
        self.providers.initialize()

        cv_path = workspace_dir / "cv.md"
        if cv_path.exists() and not self.db.get_latest_candidate_profile():
            markdown = cv_path.read_text(encoding="utf-8", errors="replace")
            summary = summarize_profile(markdown)
            created_id = self.db.save_candidate_profile(source_name="cv.md", markdown=markdown, summary=summary)
            self.db.set_active_profile(created_id)

        if not self.db.get_preference("active_profile_id", ""):
            latest = self.db.get_latest_candidate_profile()
            if latest:
                self.db.set_active_profile(int(latest["id"]))

    def shutdown(self) -> None:
        self.db.close()


def create_app(workspace_dir: Path) -> FastAPI:
    container = AppContainer(workspace_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        container.shutdown()

    fastapi_app = FastAPI(title="Job Finder Universale", version="0.2.0", lifespan=lifespan)
    web_dir = workspace_dir / "web"
    fastapi_app.mount("/web", StaticFiles(directory=web_dir), name="web")

    @fastapi_app.get("/")
    def home() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @fastapi_app.get("/api/health")
    def health() -> dict:
        return {
            "ok": True,
            "provider": container.providers.metadata(),
            "preferences": container.db.list_preferences(),
            "db_path": str(container.settings.db_path),
        }

    @fastapi_app.post("/api/upload-cv")
    async def upload_cv(file: UploadFile = File(...)) -> dict:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="File CV vuoto")
        markdown = extract_markdown_from_upload(file.filename or "cv", data)
        summary = summarize_profile(markdown)
        profile_id = container.db.save_candidate_profile(
            source_name=file.filename or "cv_upload",
            markdown=markdown,
            summary=summary,
        )
        container.db.set_active_profile(profile_id)

        if summary.get("ruoli_preferiti"):
            container.db.set_preference("ruoli_preferiti", ",".join(summary["ruoli_preferiti"]))

        return {
            "profile_id": profile_id,
            "source": file.filename,
            "summary": summary,
        }

    @fastapi_app.get("/api/profile")
    def get_profile() -> dict:
        profile = container.db.get_active_candidate_profile()
        active = container.db.get_preference("active_profile_id", "")
        return {"profile": profile, "active_profile_id": active}

    @fastapi_app.get("/api/profiles")
    def get_profiles() -> dict:
        profiles = container.db.list_candidate_profiles()
        active = container.db.get_preference("active_profile_id", "")
        return {"profiles": profiles, "active_profile_id": active}

    @fastapi_app.post("/api/profiles/{profile_id}/activate")
    def activate_profile(profile_id: int) -> dict:
        profile = container.db.get_candidate_profile(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="Profilo non trovato")
        container.db.set_active_profile(profile_id)
        return {"ok": True, "active_profile_id": profile_id}

    @fastapi_app.post("/api/scan")
    def scan(payload: ScanRequest) -> dict:
        result = run_scan(
            db=container.db,
            settings=container.settings,
            provider_manager=container.providers,
            payload=payload,
        )
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
    ) -> dict:
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
    def get_job_detail(job_id: int) -> dict:
        job = container.db.get_job_with_analysis(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Annuncio non trovato")
        return {"job": job}

    @fastapi_app.post("/api/jobs/manual")
    def add_manual_job(payload: ManualJobCreateRequest) -> dict:
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
        profile_markdown = profile["markdown"] if profile else "Profilo non caricato"
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
    def set_job_action(job_id: int, payload: JobActionRequest) -> dict:
        job = container.db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Annuncio non trovato")
        container.db.set_job_action(job_id=job_id, action=payload.action.value, notes=payload.notes)
        return {"ok": True}

    @fastapi_app.post("/api/jobs/{job_id}/favorite")
    def set_favorite(job_id: int, payload: FavoriteRequest) -> dict:
        job = container.db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Annuncio non trovato")
        container.db.set_favorite(job_id=job_id, is_favorite=payload.is_favorite)
        return {"ok": True}

    @fastapi_app.post("/api/chat", response_model=ChatResponse)
    def chat(payload: ChatRequest) -> ChatResponse:
        result = handle_chat_message(
            db=container.db,
            provider_manager=container.providers,
            message=payload.message,
            session_id=payload.session_id,
        )
        return ChatResponse(**result)

    @fastapi_app.get("/api/chat/history")
    def chat_history(session_id: str = "default", limit: int = 30) -> dict:
        items = container.db.list_chat_messages(session_id=session_id, limit=limit)
        return {"messages": items}

    @fastapi_app.post("/api/preferences")
    def update_preference(payload: PreferenceUpdateRequest) -> dict:
        container.db.set_preference(payload.key, payload.value)
        return {"ok": True, "preferences": container.db.list_preferences()}

    @fastapi_app.get("/api/preferences")
    def get_preferences() -> dict:
        return {"preferences": container.db.list_preferences()}

    @fastapi_app.post("/api/export/csv")
    def export_csv() -> dict:
        rows = container.db.export_jobs_for_csv()
        if not rows:
            raise HTTPException(status_code=400, detail="Nessun annuncio da esportare")

        output_name = f"lavori_webapp_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        output_path = container.workspace_dir / output_name
        columns = list(rows[0].keys())

        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)

        return {"ok": True, "file": str(output_path)}

    return fastapi_app


WORKSPACE_DIR = Path(__file__).resolve().parent.parent
app = create_app(WORKSPACE_DIR)
