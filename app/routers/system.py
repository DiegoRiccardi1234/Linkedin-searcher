from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

from app.version import get_version_info

if TYPE_CHECKING:
    from app.container import AppContainer


def build_router(container: AppContainer) -> APIRouter:
    router = APIRouter()

    @router.get("/api/health")
    def health() -> dict[str, Any]:
        # Note: the absolute DB path was intentionally dropped — the UI never
        # used it and exposing a filesystem path in every page load is noise a
        # reviewer flags. Preferences stay: several UI surfaces read them.
        return {
            "ok": True,
            "provider": container.providers.metadata(),
            "keys": container.keys_status(),
            "preferences": container.db.list_preferences(),
        }

    @router.get("/api/usage/stats")
    def usage_stats(range: str = "today") -> dict[str, Any]:
        """Token-usage aggregates. ``range`` ∈ {today, week, month, all}."""
        from app.services.quota import status as quota_status
        from app.services.usage_tracker import aggregate_stats

        if range not in {"today", "week", "month", "all"}:
            range = "today"
        # Today's request count against the daily ceiling: the free tier has one
        # (1000/day per OpenRouter account) and nothing in the UI ever said so.
        return {**aggregate_stats(container.db, range_=range), "quota": quota_status(container.db)}

    @router.get("/api/setup/status")
    def setup_status() -> dict[str, Any]:
        provider_configured = container.has_provider_configured()
        cv_loaded = container.db.get_active_candidate_profile() is not None
        return {
            "ready": provider_configured,
            "provider_configured": provider_configured,
            "cv_loaded": cv_loaded,
            "first_run": not provider_configured and not cv_loaded,
        }

    @router.get("/api/version")
    def version_info(refresh: bool = False) -> dict[str, Any]:
        return get_version_info(force_refresh=refresh)

    @router.post("/api/update")
    def run_update() -> dict[str, Any]:
        from scripts.update import update as run_update_script

        result = run_update_script(repo_root=container.workspace_dir)
        # Refresh version cache so banner reflects new state on next /api/version call.
        get_version_info(force_refresh=True)
        return result

    @router.post("/api/update/start", status_code=202)
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
        # Require latest to be strictly NEWER, not merely different: with a local
        # dev/pre-release version ahead of the newest GitHub release, `latest !=
        # current` would happily downgrade the install to the older bundle.
        if not latest or not info.get("update_available"):
            raise HTTPException(
                status_code=409,
                detail=f"No newer version available (current {current}, latest {latest}).",
            )

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
        # Must exceed the updater's worst-case runtime so the lock never goes
        # "stale" mid-update and lets a second Updater.exe spawn: download
        # (urlopen timeout 120s) + 3s grace + copy-retry backoff (~31s) ≈ 150s+.
        lock_ttl_seconds = 180
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
            # PyInstaller onedir bootloader loads python311.dll from
            # ``<exe parent>/_internal`` *before* Python starts. Copying only
            # Updater.exe to %TEMP% (as v1.2.8 did) crashes with
            # "Failed to load Python DLL". Mirror the _internal folder next
            # to the staged exe so the bootloader can resolve runtime DLLs.
            internal_src = updater_exe.parent / "_internal"
            if internal_src.is_dir():
                shutil.copytree(internal_src, launcher_dir / "_internal")
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

    @router.post("/api/system/open-logs")
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

    @router.post("/api/system/shutdown", status_code=202)
    def shutdown_app() -> dict[str, Any]:
        """Stop the app cleanly — the windowless build has no terminal to close.

        Returns 202, then hard-exits on a short Timer (so the HTTP response
        flushes first), stopping autoscan + closing the DB via
        ``container.shutdown()`` beforehand — ``os._exit`` bypasses the FastAPI
        lifespan teardown otherwise.
        """

        def _stop() -> None:
            with contextlib.suppress(Exception):
                container.shutdown()
            os._exit(0)

        threading.Timer(0.5, _stop).start()
        return {"status": "shutting_down"}

    @router.delete("/api/update/lock")
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

    @router.get("/api/update/progress")
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

    return router
