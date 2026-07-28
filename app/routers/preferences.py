from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

from app.models import PreferenceUpdateRequest

if TYPE_CHECKING:
    from app.container import AppContainer

# The preferences table is the app's control plane, not a scratchpad: it holds
# the feature toggles (including feature_privacy_mode, which decides whether the
# CV is redacted before it is sent to a third-party model), the active profile
# id and the scan defaults. The endpoint accepted any key/value pair, so one
# unauthenticated POST could silently turn PII redaction off. Writable keys are
# the ones the UI actually sets.
_WRITABLE_PREFIX = (
    "feature_",
    "onboarding_",
    "autoscan_",
    "last_scan_",
    "cv_",
    "kanban_",
    "watchlist_",
)
_WRITABLE_KEYS = frozenset(
    {
        "active_profile_id",
        # The daily request ceiling. It could stop a scan outright and there was
        # no way to change it: not in this list, and no field in the UI — the
        # only remedy was editing the DB by hand.
        "daily_request_limit",
        "dedup_mode",
        "density",
        "linkedin_url",
        "preferred_roles",
        "theme",
        "tutorial_seen",
        "ui_language",
    }
)
_MAX_PREFERENCE_LEN = 20_000


def _is_writable(key: str) -> bool:
    return key in _WRITABLE_KEYS or key.startswith(_WRITABLE_PREFIX)


def build_router(container: AppContainer) -> APIRouter:
    router = APIRouter()

    @router.post("/api/preferences")
    def update_preference(payload: PreferenceUpdateRequest) -> dict[str, Any]:
        if not _is_writable(payload.key):
            raise HTTPException(status_code=400, detail="unknown_preference")
        if len(payload.value) > _MAX_PREFERENCE_LEN:
            raise HTTPException(status_code=413, detail="preference_too_large")
        container.db.set_preference(payload.key, payload.value)
        return {"ok": True, "preferences": container.db.list_preferences()}

    @router.get("/api/preferences")
    def get_preferences() -> dict[str, Any]:
        return {"preferences": container.db.list_preferences()}

    return router
