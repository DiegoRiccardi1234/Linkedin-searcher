from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app import rate_limit
from app.models import ScanRequest
from app.services.scanner_service import run_scan

if TYPE_CHECKING:
    from app.container import AppContainer


def build_router(container: AppContainer) -> APIRouter:
    router = APIRouter()

    @router.get("/api/scan/stream")
    def scan_stream(
        request: Request,
        search_terms: str = Query(default=""),
        location: str | None = Query(default=None),
        locations: str = Query(default=""),
        country: str | None = Query(default=None),
        is_remote: bool = Query(default=False),
        sites: str = Query(default="linkedin,indeed"),
        experience_levels: str = Query(default=""),
        job_types: str = Query(default=""),
        work_types: str = Query(default=""),
        min_salary: int = Query(default=0),
    ) -> StreamingResponse:
        # Same guards as POST /api/scan: a direct hit or the pre-banner race
        # must not kick off an unauthenticated, provider-less scrape loop.
        rate_limit.check(request, bucket="scan", limit=5, window_seconds=60)
        container.require_provider()
        if container.scan_control.running:
            raise HTTPException(status_code=409, detail="scan_in_progress")
        term_list = (
            [t.strip() for t in search_terms.split(",") if t.strip()] if search_terms else []
        )
        site_list = [s.strip() for s in sites.split(",") if s.strip()]
        # Locations are pipe-separated (a location like "Torino, Italy" contains commas).
        loc_list = [s.strip() for s in locations.split("|") if s.strip()] if locations else []

        def _split(csv: str) -> list[str]:
            return [s.strip() for s in csv.split(",") if s.strip()] if csv else []

        payload = ScanRequest(
            search_terms=term_list,
            location=location,
            locations=loc_list,
            country=country,
            is_remote=is_remote,
            sites=site_list,
            experience_levels=_split(experience_levels),
            job_types=_split(job_types),
            work_types=_split(work_types),
            min_salary=min_salary or None,
        )

        def event_generator() -> Iterator[str]:
            import json

            if not container.scan_control.try_begin():
                yield f"data: {json.dumps({'error': 'scan_in_progress'})}\n\n"
                return
            try:
                for event in run_scan(
                    db=container.db,
                    settings=container.settings,
                    provider_manager=container.providers,
                    payload=payload,
                    cancel_check=container.scan_control.is_cancelled,
                ):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            finally:
                container.scan_control.end()

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # There was a blocking POST /api/scan here that ran the same scan and
    # returned only the final event. Nothing called it: the UI has always used
    # the SSE stream above, because a scan takes ten minutes and a request that
    # holds a connection open that long is a timeout waiting to happen. It had
    # no test either, so a second, unexercised way to spend LLM quota sat behind
    # a rate limiter nobody checked. Removed rather than tested: the stream is
    # the interface.

    @router.post("/api/scan/cancel")
    def scan_cancel() -> dict[str, Any]:
        """Signal the in-flight scan to stop promptly (user hit stop / tab closed)
        so it stops spending LLM quota. No-op when nothing is running."""
        running = container.scan_control.running
        if running:
            container.scan_control.cancel()
        return {"ok": True, "cancelling": running}

    @router.get("/api/countries")
    def list_countries() -> dict[str, Any]:
        """Countries jobspy supports for Indeed/Glassdoor (the country selector).
        Value = the canonical jobspy alias; label = a display name."""
        try:
            from jobspy.model import Country
        except Exception:  # pragma: no cover - jobspy optional
            return {"countries": [], "default": container.settings.country_default}
        seen: set[str] = set()
        out: list[dict[str, str]] = []
        for c in Country:
            raw = c.value[0] if isinstance(c.value, tuple) else c.value
            value = str(raw).split(",")[0].strip()
            if not value or value in seen:
                continue
            seen.add(value)
            out.append({"value": value, "label": c.name.replace("_", " ").title()})
        out.sort(key=lambda x: x["label"])
        return {"countries": out, "default": container.settings.country_default}

    return router
