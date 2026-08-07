import os
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.container import AppContainer
from app.routers import chat as chat_router
from app.routers import jobs as jobs_router
from app.routers import mail as mail_router
from app.routers import preferences as preferences_router
from app.routers import profile as profile_router
from app.routers import providers as providers_router
from app.routers import saved_searches as saved_searches_router
from app.routers import scan as scan_router
from app.routers import scheduler as scheduler_router
from app.routers import system as system_router
from app.version import __version__

# Re-exported for backwards compatibility (tests / scripts import AppContainer
# from app.main); the implementation now lives in app.container.
__all__ = ["AppContainer", "app", "create_app"]

# Appends the cache-bust ``?v=`` token to relative ES-module import specifiers
# (``from "./x.js"``, ``import "./x.js"``, ``import("./x.js")``). Every JS file —
# the entry app.js AND each module — is served through the versioned route, so a
# shared module like ``i18n.js`` is always referenced as ``i18n.js?v=<ver>`` from
# everywhere. Mismatched query strings would make the browser load duplicate
# module instances and break shared singleton state (e.g. i18n translations).
_JS_IMPORT_RE = re.compile(r'((?:from|import)\s*\(?\s*["\'])(\.\.?/[^"\']+\.js)(["\'])')


def create_app(workspace_dir: Path) -> FastAPI:
    container = AppContainer(workspace_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        container.autoscan.start()
        yield
        container.shutdown()

    fastapi_app = FastAPI(title="Job Finder", version=__version__, lifespan=lifespan)
    # The container was reachable only through the closures of the routers, so a
    # test could exercise an endpoint but never the background work behind it —
    # and background work with no test around its wiring is how the end-of-scan
    # audit stayed broken for weeks.
    fastapi_app.state.container = container
    web_dir = (
        Path(sys._MEIPASS) / "web"  # type: ignore[attr-defined]
        if getattr(sys, "frozen", False)
        else workspace_dir / "web"
    )

    def _render_versioned(rel_path: str, media_type: str) -> Response:
        """Serve an app-owned asset with the release version injected for busting.

        ``{{VERSION}}`` placeholders (index.html, styles.css) are replaced with
        ``__version__``; JS files additionally get ``?v=`` appended to every
        relative module import so the whole ES-module graph busts from one source
        (``app.version.__version__``) — no manual ``?v=`` bumping, and consistent
        query strings keep each module a single shared instance. Read per request:
        fine for a localhost single-user app.
        """
        text = (web_dir / rel_path).read_text(encoding="utf-8")
        text = text.replace("{{VERSION}}", __version__)
        if rel_path.endswith(".js"):
            text = _JS_IMPORT_RE.sub(rf"\g<1>\g<2>?v={__version__}\g<3>", text)
        return Response(content=text, media_type=media_type)

    @fastapi_app.get("/")
    def home() -> Response:
        return _render_versioned("index.html", "text/html; charset=utf-8")

    @fastapi_app.get("/web/app.js", include_in_schema=False)
    def app_js() -> Response:
        return _render_versioned("app.js", "text/javascript; charset=utf-8")

    @fastapi_app.get("/web/modules/{name}.js", include_in_schema=False)
    def module_js(name: str) -> Response:
        module_path = web_dir / "modules" / f"{name}.js"
        if not module_path.is_file():
            raise HTTPException(status_code=404, detail="not_found")
        return _render_versioned(f"modules/{name}.js", "text/javascript; charset=utf-8")

    @fastapi_app.get("/web/styles.css", include_in_schema=False)
    def styles_css() -> Response:
        return _render_versioned("styles.css", "text/css; charset=utf-8")

    @fastapi_app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(web_dir / "favicon.svg", media_type="image/svg+xml")

    # Registered AFTER the explicit asset routes above so they take precedence;
    # the mount serves everything else under /web (modules, chat.css, fonts…).
    fastapi_app.mount("/web", StaticFiles(directory=web_dir), name="web")

    for module in (
        system_router,
        providers_router,
        profile_router,
        scan_router,
        jobs_router,
        chat_router,
        preferences_router,
        scheduler_router,
        saved_searches_router,
        mail_router,
    ):
        fastapi_app.include_router(module.build_router(container))

    return fastapi_app


_DEFAULT_WORKSPACE = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = (
    Path(os.environ["JOBFINDER_WORKSPACE"]).resolve()
    if os.environ.get("JOBFINDER_WORKSPACE")
    else _DEFAULT_WORKSPACE
)
app = create_app(WORKSPACE_DIR)
