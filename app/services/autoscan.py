"""In-process scheduled auto-scan.

A lightweight daemon thread (no external scheduler dependency) that, while the
app is running and the feature is enabled, re-runs the user's last search every
N hours and records new high-scoring jobs as a pending in-app highlight. Runs
only while the app is open by design.

Configuration lives in the ``preferences`` table:
- ``autoscan_enabled``       "1"/"0" (default off)
- ``autoscan_interval_hours``  int, default 12
- ``autoscan_score_threshold`` int 0-10, default 7
- ``autoscan_last_run_ts``     float epoch seconds (managed)
- ``autoscan_pending``         JSON highlight payload (managed)

The reused search parameters come from the ``last_scan_*`` preferences that
``scanner_service.run_scan`` already persists on every manual scan.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from app.db import Database
from app.log import get_logger
from app.models import ScanRequest
from app.services.scanner_service import run_scan as _default_run_scan

if TYPE_CHECKING:
    from app.container import AppContainer

log = get_logger(__name__)

DEFAULT_INTERVAL_HOURS = 12
DEFAULT_THRESHOLD = 7
_TICK_SECONDS = 60.0


def _to_int(value: str, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


class AutoScanScheduler:
    def __init__(
        self,
        container: AppContainer,
        *,
        run_scan_fn: Callable[..., Any] = _default_run_scan,
        tick_seconds: float = _TICK_SECONDS,
        clock: Callable[[], float] = time.time,
        extra_tasks: Sequence[Callable[[], None]] = (),
    ) -> None:
        self._container = container
        self._run_scan = run_scan_fn
        self._tick = tick_seconds
        self._clock = clock
        # Other things that want to happen on a timer, run on this same thread.
        # A second daemon thread would buy nothing: the tick is already a minute,
        # and everything hanging off it decides its own interval. The cost is
        # that a long scan delays these — delays, not drops, since each one
        # compares its own last-run stamp on the next tick.
        self._extra_tasks = tuple(extra_tasks)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- config helpers ----

    @property
    def _db(self) -> Database:
        db: Database = self._container.db
        return db

    def enabled(self) -> bool:
        return self._db.get_preference("autoscan_enabled", "0") == "1"

    def interval_hours(self) -> int:
        return max(
            1,
            _to_int(self._db.get_preference("autoscan_interval_hours", ""), DEFAULT_INTERVAL_HOURS),
        )

    def threshold(self) -> int:
        return max(
            0,
            min(
                10,
                _to_int(self._db.get_preference("autoscan_score_threshold", ""), DEFAULT_THRESHOLD),
            ),
        )

    # ---- lifecycle ----

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="autoscan", daemon=True)
        self._thread.start()
        log.info("AutoScanScheduler started")

    def shutdown(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def _loop(self) -> None:
        # ``Event.wait`` returns True once stop is set, so the loop exits
        # promptly on shutdown instead of sleeping out the full tick.
        while not self._stop.wait(self._tick):
            try:
                self._maybe_run()
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("autoscan tick failed: %s", exc)
            for task in self._extra_tasks:
                # Each in its own guard: one failing task must not stop the
                # others, nor the scan check above.
                try:
                    task()
                except Exception as exc:  # pragma: no cover - defensive
                    log.warning("scheduled task failed: %s", exc)

    def _maybe_run(self) -> None:
        if not self.enabled():
            return
        last = 0.0
        try:
            last = float(self._db.get_preference("autoscan_last_run_ts", "0") or 0)
        except (TypeError, ValueError):
            last = 0.0
        if self._clock() - last >= self.interval_hours() * 3600:
            self.run_once()

    # ---- scan ----

    def _build_payload(self) -> ScanRequest:
        terms_raw = self._db.get_preference("last_scan_terms", "")
        try:
            terms = json.loads(terms_raw) if terms_raw else []
        except (ValueError, TypeError):
            terms = []
        filters_raw = self._db.get_preference("last_scan_filters", "")
        try:
            filters = json.loads(filters_raw) if filters_raw else {}
        except (ValueError, TypeError):
            filters = {}
        # run_scan persists BOTH the primary location and the full list; reading
        # only the singular key silently reduced a multi-location saved search to
        # its first location on every scheduled run.
        locations_raw = self._db.get_preference("last_scan_locations", "")
        try:
            locations = [str(x) for x in json.loads(locations_raw)] if locations_raw else []
        except (ValueError, TypeError):
            locations = []
        locations = [loc for loc in locations if loc.strip()]
        location = self._db.get_preference("last_scan_location", "") or None
        country = self._db.get_preference("last_scan_country", "") or None
        is_remote = self._db.get_preference("last_scan_is_remote", "0") == "1"
        return ScanRequest(
            search_terms=[str(t) for t in terms if str(t).strip()],
            location=location,
            locations=locations,
            country=country,
            is_remote=is_remote,
            sites=["linkedin", "indeed"],
            experience_levels=list(filters.get("experience_levels") or []),
            job_types=list(filters.get("job_types") or []),
            work_types=list(filters.get("work_types") or []),
        )

    def run_once(self) -> dict[str, Any]:
        """Run one scan synchronously and record new high-score highlights."""
        # Shared single-scan slot: never overlap a manual scan (they'd both call
        # begin_scan and double-spend LLM quota).
        if not self._container.scan_control.try_begin():
            return {"status": "already_running"}
        try:
            try:
                if not self._container.has_provider_configured():
                    return {"status": "skipped", "reason": "no_provider"}
                payload = self._build_payload()
                # A scheduled scan replays what the user ran by hand. If they
                # never ran one, there is nothing to replay — and the fallback
                # that used to cover this case is exactly the one that made
                # every install search for the same six terms in Turin.
                if not payload.search_terms:
                    log.info("autoscan skipped: no search of your own to repeat yet")
                    return {"status": "skipped", "reason": "no_search_intent"}
                for _event in self._run_scan(
                    db=self._container.db,
                    settings=self._container.settings,
                    provider_manager=self._container.providers,
                    payload=payload,
                    cancel_check=self._container.scan_control.is_cancelled,
                ):
                    pass  # drain the generator; persistence happens inside run_scan

                threshold = self.threshold()
                highlights = self._db.list_jobs(only_new=True, min_score=threshold, limit=20)
                pending = {
                    "count": len(highlights),
                    "threshold": threshold,
                    "generated_at": self._clock(),
                    "jobs": [
                        {
                            "id": j.get("id"),
                            "titolo": j.get("titolo"),
                            "azienda": j.get("azienda"),
                            "score": j.get("punteggio_ai"),
                        }
                        for j in highlights[:8]
                    ],
                }
                self._db.set_preference("autoscan_last_run_ts", str(self._clock()))
                self._db.set_preference("autoscan_pending", json.dumps(pending, ensure_ascii=False))
                self._maybe_native_notify(pending)
                log.info("autoscan completed: %d new jobs >= %d", pending["count"], threshold)
                return {"status": "complete", **pending}
            except Exception as exc:
                # Never let an exception escape — run_once is invoked from a bare
                # daemon thread (manual run-now) where it would be lost silently.
                log.warning("autoscan run failed: %s", exc)
                # Stamp the attempt even on failure. Without this the "time since
                # last run" test in _maybe_run stays satisfied and the scheduler
                # re-launches a full scan (LLM scoring included) on the very next
                # 60s tick, forever.
                self._db.set_preference("autoscan_last_run_ts", str(self._clock()))
                return {"status": "error", "error": str(exc)}
        finally:
            self._container.scan_control.end()

    def _maybe_native_notify(self, pending: dict[str, Any]) -> None:
        """Fire a native (tray) desktop notification for new high-scoring jobs.

        Opt-in via the ``autoscan_notify`` preference. No-op unless a notifier is
        registered (frozen build with a live tray) — dev/source runs skip it.
        """
        if pending.get("count", 0) <= 0:
            return
        if self._db.get_preference("autoscan_notify", "0") != "1":
            return
        texts = {
            "en": ("New matching jobs", "{count} new jobs at or above score {threshold}."),
            "it": ("Nuovi lavori adatti", "{count} nuovi lavori con punteggio >= {threshold}."),
            "es": (
                "Nuevos empleos compatibles",
                "{count} nuevos empleos con puntuación >= {threshold}.",
            ),
            "de": ("Neue passende Jobs", "{count} neue Jobs mit Score >= {threshold}."),
            "fr": ("Nouvelles offres", "{count} nouvelles offres avec un score >= {threshold}."),
        }
        try:
            from app.notify import notify
            from app.services.chat.state import get_ui_language

            title, body_tpl = texts.get(get_ui_language(self._db), texts["en"])
            notify(title, body_tpl.format(count=pending["count"], threshold=pending["threshold"]))
        except Exception as exc:
            log.debug("native notify skipped: %s", exc)

    # ---- status / pending ----

    def pending(self) -> dict[str, Any] | None:
        raw = self._db.get_preference("autoscan_pending", "")
        if not raw:
            return None
        try:
            data: dict[str, Any] = json.loads(raw)
            return data
        except (ValueError, TypeError):
            return None

    def clear_pending(self) -> None:
        self._db.set_preference("autoscan_pending", "")

    def status(self) -> dict[str, Any]:
        last = 0.0
        try:
            last = float(self._db.get_preference("autoscan_last_run_ts", "0") or 0)
        except (TypeError, ValueError):
            last = 0.0
        interval = self.interval_hours()
        return {
            "enabled": self.enabled(),
            "interval_hours": interval,
            "threshold": self.threshold(),
            "running": self._container.scan_control.running,
            "last_run_ts": last or None,
            "next_run_ts": (last + interval * 3600) if last else None,
            "pending": self.pending(),
        }
