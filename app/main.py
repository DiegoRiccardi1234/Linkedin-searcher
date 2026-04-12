import csv
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import AppSettings, load_settings, save_local_provider_keys
from app.cv_ingest import extract_markdown_from_upload, summarize_profile
from app.db import Database
from app.models import (
    ChatRequest,
    ChatResponse,
    FavoriteRequest,
    JobActionRequest,
    ManualJobCreateRequest,
    PreferenceUpdateRequest,
    ProviderKeysRequest,
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

    def reload_providers(self) -> None:
        self.settings = load_settings(self.workspace_dir)
        self.providers = ProviderManager(self.settings)
        self.providers.initialize()

    def keys_status(self) -> dict:
        primary = self.settings.llm_provider_order[0] if self.settings.llm_provider_order else ""
        return {
            "cerebras_configured": bool(self.settings.cerebras_api_key),
            "groq_configured": bool(self.settings.groq_api_key),
            "openai_configured": bool(self.settings.openai_api_key),
            "anthropic_configured": bool(self.settings.anthropic_api_key),
            "google_configured": bool(self.settings.google_api_key),
            "primary_provider": primary,
        }


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
            "keys": container.keys_status(),
            "preferences": container.db.list_preferences(),
            "db_path": str(container.settings.db_path),
        }

    @fastapi_app.get("/api/providers/keys/status")
    def providers_keys_status() -> dict:
        return {
            "ok": True,
            "keys": container.keys_status(),
            "provider": container.providers.metadata(),
        }

    @fastapi_app.post("/api/providers/keys")
    def save_provider_keys(payload: ProviderKeysRequest) -> dict:
        local_status = save_local_provider_keys(
            data_dir=container.settings.data_dir,
            cerebras_api_key=payload.cerebras_api_key,
            groq_api_key=payload.groq_api_key,
            openai_api_key=payload.openai_api_key,
            anthropic_api_key=payload.anthropic_api_key,
            google_api_key=payload.google_api_key,
            primary_provider=payload.primary_provider,
        )
        container.reload_providers()
        return {
            "ok": True,
            "keys": {**local_status, **container.keys_status()},
            "provider": container.providers.metadata(),
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

    @fastapi_app.get("/api/scan/stream")
    def scan_stream(
        search_terms: str = Query(default=""),
        location: str | None = Query(default=None),
        is_remote: bool = Query(default=False),
        sites: str = Query(default="linkedin,indeed")
    ):
        term_list = [t.strip() for t in search_terms.split(",") if t.strip()] if search_terms else []
        site_list = [s.strip() for s in sites.split(",") if s.strip()]
        
        payload = ScanRequest(
            search_terms=term_list if term_list else None,
            location=location,
            is_remote=is_remote,
            sites=site_list
        )
        def event_generator():
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
    def scan(payload: ScanRequest) -> dict:
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

    @fastapi_app.post("/api/jobs/{job_id}/cover-letter")
    def generate_cover_letter(job_id: int) -> dict:
        job = container.db.get_job_with_analysis(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Annuncio non trovato")
            
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
            cover_letter = f"Errore durante la generazione: {e}"
            
        return {"cover_letter": cover_letter}

    @fastapi_app.get("/api/analytics")
    def get_analytics() -> dict:
        return container.db.get_analytics()

    @fastapi_app.get("/api/recommendations")
    def recommendations(limit: int = Query(default=5, ge=1, le=20)) -> dict:
        jobs = container.db.get_recommended_jobs(limit=limit)
        return {
            "jobs": jobs,
            "message": "Ecco i lavori prioritari da valutare e candidare.",
        }

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

    @fastapi_app.get("/api/chat/prompts")
    def chat_prompts() -> dict:
        return {
            "prompts": [
                "Consigliami i 5 lavori migliori da candidare oggi",
                "Spiegami perche il primo lavoro ha rating alto",
                "Dammi un piano candidature per questa settimana",
                "Suggeriscimi quali lavori evitare e perche",
            ]
        }

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
