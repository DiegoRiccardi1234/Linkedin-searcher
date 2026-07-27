"""Application container: shared singletons (settings, DB, providers).

Extracted from ``app.main`` so the per-domain routers in ``app.routers`` can
depend on it without importing the FastAPI app factory (avoids a circular
import).
"""

import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.config import SUPPORTED_PROVIDERS, AppSettings, load_settings
from app.cv_ingest import extract_candidate_name, summarize_profile
from app.db import Database
from app.log import configure_logging, get_logger
from app.providers.factory import ProviderManager

_PROVIDER_FLAGS = tuple(f"{name}_configured" for name in SUPPORTED_PROVIDERS)


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

        # Single-scan coordinator (manual scan + auto-scan share it) + cancel flag.
        from app.services.scan_control import ScanControl

        self.scan_control = ScanControl()

        # In-process scheduler for the optional auto-scan feature. Created
        # inert; started by the app lifespan, stopped on shutdown.
        from app.services.autoscan import AutoScanScheduler

        self.autoscan = AutoScanScheduler(self)

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
        self.autoscan.shutdown()
        self.db.close()

    def reload_providers(self) -> None:
        self.settings = load_settings(self.workspace_dir)
        self.providers = ProviderManager(self.settings)
        self.providers._db = self.db
        self.providers.initialize()

    def keys_status(self) -> dict[str, Any]:
        primary = self.settings.llm_provider_order[0] if self.settings.llm_provider_order else ""
        status: dict[str, Any] = {
            f"{name}_configured": bool(getattr(self.settings, f"{name}_api_key", None))
            for name in SUPPORTED_PROVIDERS
        }
        # The custom endpoint is configured by its URL: a local model server has
        # no key to give, so requiring one would make it permanently "missing".
        status["custom_configured"] = bool(self.settings.custom_base_url)
        status["custom_base_url"] = self.settings.custom_base_url or ""
        status["primary_provider"] = primary
        status["preferred_model"] = self.settings.preferred_model or ""
        status["scoring_model"] = self.settings.scoring_model or ""
        status["chat_model"] = self.settings.chat_model or ""
        status["cv_model"] = self.settings.cv_model or ""
        return status

    def has_provider_configured(self) -> bool:
        status = self.keys_status()
        return any(status[flag] for flag in _PROVIDER_FLAGS)

    def feature_enabled(self, name: str, default: bool = True) -> bool:
        """Return whether an optional feature is enabled.

        Toggles live in the ``preferences`` table under ``feature_<name>``
        ("1"/"0"). Missing key falls back to ``default`` so features ship
        with a sensible on/off state.
        """
        raw = self.db.get_preference(f"feature_{name}", "1" if default else "0")
        return raw not in ("0", "false", "off", "")

    def require_feature(self, name: str, default: bool = True) -> None:
        if not self.feature_enabled(name, default):
            raise HTTPException(
                status_code=403,
                detail={"code": "feature_disabled", "feature": name},
            )

    def require_provider(self) -> None:
        """Reject requests when no LLM key is configured. UI banner gates this too,
        but a backend 412 protects against direct API hits and the polling
        race where the user submits before the banner enforces."""
        if not self.has_provider_configured():
            raise HTTPException(
                status_code=412,
                detail={"code": "no_provider_configured", "message_key": "errors.noProvider"},
            )
