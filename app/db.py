import functools
import hashlib
import json
import logging
import sqlite3
import threading
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from app.scoring_schema import ANALYSIS_VERSION_KEY, CURRENT_ANALYSIS_VERSION

logger = logging.getLogger(__name__)

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _synchronized(method: Callable[_P, _R]) -> Callable[_P, _R]:
    """Serialize a write method on the owning :class:`Database`'s lock.

    Reads stay lock-free (WAL allows concurrent readers); only methods that
    mutate state acquire the reentrant lock, so nested write calls (e.g.
    ``add_manual_job`` -> ``upsert_job``) do not deadlock.
    """

    @functools.wraps(method)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        lock = args[0].lock  # type: ignore[attr-defined]
        with lock:
            return method(*args, **kwargs)

    return wrapper


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _escape_like(text: str) -> str:
    """Escape LIKE metacharacters so user search text matches literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def make_job_hash(titolo: str, azienda: str, link: str) -> str:
    raw = f"{titolo.strip().lower()}|{azienda.strip().lower()}|{link.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


DEDUP_MODES = ("exact", "city", "title_company")


def _normalize_city(sede: str) -> str:
    """Canonical city token from a free-text location.

    Takes the part before the first comma ("Milano, Lombardia, Italia" ->
    "Milano"), lowercases, strips accents and collapses whitespace, so sources
    that spell the region/country differently still match on the city. Does NOT
    bridge cross-language names (Milano vs Milan stay distinct).
    """
    city = (sede or "").split(",")[0].strip().lower()
    city = "".join(c for c in unicodedata.normalize("NFKD", city) if not unicodedata.combining(c))
    return " ".join(city.split())


def make_dedup_key(titolo: str, azienda: str, sede: str, mode: str = "exact") -> str:
    """Cross-source identity of a role, tunable via ``mode`` (see DEDUP_MODES).

    Same posting on LinkedIn vs Indeed has different URLs (so a different
    ``make_job_hash``) but can share a ``dedup_key`` — ``upsert_job`` uses it to
    merge the second source into the first row instead of duplicating.
    - ``exact``: title+company+full location (most conservative).
    - ``city``: title+company+normalized city (merges cross-source location spellings).
    - ``title_company``: title+company only (most aggressive; ignores location).
    """
    titolo_n = titolo.strip().lower()
    azienda_n = azienda.strip().lower()
    if mode == "title_company":
        raw = f"{titolo_n}|{azienda_n}"
    elif mode == "city":
        raw = f"{titolo_n}|{azienda_n}|{_normalize_city(sede)}"
    else:  # exact
        raw = f"{titolo_n}|{azienda_n}|{sede.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_sources(raw: Any) -> list[dict[str, str]]:
    """Decode ``jobs.sources_json`` into a list; tolerate null/legacy rows."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _merge_source(sources: list[dict[str, str]], fonte: str, link: str) -> list[dict[str, str]]:
    """Append ``{fonte, link}`` unless an entry with the same link already exists."""
    link_norm = (link or "").strip().lower()
    for entry in sources:
        if (entry.get("link") or "").strip().lower() == link_norm:
            return sources
    return [*sources, {"fonte": fonte or "", "link": link or ""}]


class Database:
    """SQLite wrapper shared across FastAPI request threads.

    The connection uses ``check_same_thread=False``; a reentrant
    :class:`threading.RLock` serializes writes (via the ``@_synchronized``
    decorator) so concurrent requests do not race on cursors or trigger
    ``database is locked`` errors. WAL journal mode is enabled to allow
    concurrent readers.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:
            pass
        from app.migrations import apply_migrations

        apply_migrations(self.conn)

    def close(self) -> None:
        self.conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        return self.conn

    @_synchronized
    def begin_scan(self, location: str, is_remote: bool, terms: list[str]) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO scan_runs(started_at, location, is_remote, terms_json)
            VALUES (?, ?, ?, ?)
            """,
            (now_iso(), location, 1 if is_remote else 0, json.dumps(terms, ensure_ascii=False)),
        )
        run_id = int(cur.lastrowid or 0)
        self.conn.commit()
        # NB: previous "new" badges are cleared lazily via clear_new_flags() only
        # once this run actually scrapes rows — a scan whose scrape fails entirely
        # (e.g. an upstream selector regression) must not wipe the badges with
        # nothing to replace them.
        return run_id

    @_synchronized
    def clear_new_flags(self) -> None:
        """Reset the ``is_new`` badge on every job. Called once per scan, after
        the first successful scrape, so genuinely-new jobs upserted afterwards
        keep their badge while a failed scrape leaves the prior run's badges."""
        self.conn.execute("UPDATE jobs SET is_new = 0")
        self.conn.commit()

    @_synchronized
    def finish_scan(
        self,
        run_id: int,
        totale_trovati: int,
        totale_nuovi: int,
        totale_analizzati: int,
        totale_scartati: int,
    ) -> None:
        self.conn.execute(
            """
            UPDATE scan_runs
            SET finished_at = ?,
                totale_trovati = ?,
                totale_nuovi = ?,
                totale_analizzati = ?,
                totale_scartati = ?
            WHERE id = ?
            """,
            (
                now_iso(),
                totale_trovati,
                totale_nuovi,
                totale_analizzati,
                totale_scartati,
                run_id,
            ),
        )
        self.conn.commit()

    @_synchronized
    def upsert_job(self, payload: dict[str, Any]) -> tuple[int, bool, str]:
        titolo = payload.get("titolo", "")
        azienda = payload.get("azienda", "")
        sede = payload.get("sede", "")
        fonte = payload.get("fonte", "")
        link = payload.get("link", "")
        hash_value = make_job_hash(titolo, azienda, link)
        mode = self.get_preference("dedup_mode", "city")
        if mode not in DEDUP_MODES:
            mode = "city"
        dedup_key = make_dedup_key(titolo, azienda, sede, mode)
        cur = self.conn.cursor()
        timestamp = now_iso()

        # (1) Exact same posting (same link) → refresh content in place.
        cur.execute("SELECT id, status, sources_json FROM jobs WHERE job_hash = ?", (hash_value,))
        row = cur.fetchone()
        if row:
            sources = _merge_source(_parse_sources(row["sources_json"]), fonte, link)
            cur.execute(
                """
                UPDATE jobs
                SET descrizione = ?,
                    sede = ?,
                    fonte = ?,
                    ricerca_usata = ?,
                    modalita = ?,
                    dedup_key = ?,
                    sources_json = ?,
                    last_seen_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.get("descrizione", ""),
                    sede,
                    fonte,
                    payload.get("ricerca_usata", ""),
                    payload.get("modalita", ""),
                    dedup_key,
                    json.dumps(sources, ensure_ascii=False),
                    timestamp,
                    timestamp,
                    row["id"],
                ),
            )
            self.conn.commit()
            return int(row["id"]), False, str(row["status"])

        # (2) Same role from a different source (same title+company+location, new
        # link) → record the extra source on the existing row; keep its analysis.
        cur.execute(
            "SELECT id, status, sources_json FROM jobs WHERE dedup_key = ? ORDER BY id ASC LIMIT 1",
            (dedup_key,),
        )
        dup = cur.fetchone()
        if dup:
            sources = _merge_source(_parse_sources(dup["sources_json"]), fonte, link)
            cur.execute(
                "UPDATE jobs SET sources_json = ?, last_seen_at = ?, updated_at = ? WHERE id = ?",
                (json.dumps(sources, ensure_ascii=False), timestamp, timestamp, dup["id"]),
            )
            self.conn.commit()
            return int(dup["id"]), False, str(dup["status"])

        # (3) New role.
        cur.execute(
            """
            INSERT INTO jobs(
                job_hash, dedup_key, titolo, azienda, descrizione, sede, fonte, link,
                ricerca_usata, modalita, sources_json,
                first_seen_at, last_seen_at, updated_at, is_new
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                hash_value,
                dedup_key,
                titolo,
                azienda,
                payload.get("descrizione", ""),
                sede,
                fonte,
                link,
                payload.get("ricerca_usata", ""),
                payload.get("modalita", ""),
                json.dumps([{"fonte": fonte, "link": link}], ensure_ascii=False),
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        job_id = int(cur.lastrowid or 0)
        self.conn.commit()
        return job_id, True, "open"

    @_synchronized
    def update_job_analysis(self, job_id: int, analysis: dict[str, Any]) -> None:
        score = int(analysis.get("punteggio", 0) or 0)
        consiglio = str(analysis.get("consiglio", ""))
        # NULL for a heuristic/fallback analysis (the scorer leaves the version
        # out) so the job is re-scored next time instead of being frozen at a
        # keyword score — see app.scoring_schema.
        raw_version = analysis.get(ANALYSIS_VERSION_KEY)
        try:
            version = int(raw_version) if raw_version is not None else None
        except (TypeError, ValueError):
            version = None
        self.conn.execute(
            """
            UPDATE jobs
            SET analysis_json = ?, punteggio_ai = ?, consiglio = ?, analysis_v = ?,
                analyzed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(analysis, ensure_ascii=False),
                score,
                consiglio,
                version,
                now_iso(),
                now_iso(),
                job_id,
            ),
        )
        self.conn.commit()

    @_synchronized
    def add_manual_job(self, payload: dict[str, Any]) -> int:
        job_id, _, _ = self.upsert_job(payload)
        return job_id

    @_synchronized
    def set_job_action(self, job_id: int, action: str, notes: str = "") -> None:
        # Only status-changing actions touch jobs.status; others (e.g. "note")
        # are recorded on the timeline without altering the job's state.
        status_map = {
            "applied": "applied",
            "interviewing": "interviewing",
            "rejected": "rejected",
            "reopened": "open",
            "archived": "archived",
        }
        if action in status_map:
            self.conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (status_map[action], now_iso(), job_id),
            )
        self.conn.execute(
            "INSERT INTO job_actions(job_id, action, notes, created_at) VALUES (?, ?, ?, ?)",
            (job_id, action, notes, now_iso()),
        )
        self.conn.commit()

    @_synchronized
    def list_job_actions(self, job_id: int) -> list[dict[str, Any]]:
        """Chronological timeline of actions/notes for a job (oldest first)."""
        cur = self.conn.execute(
            "SELECT action, notes, created_at FROM job_actions "
            "WHERE job_id = ? ORDER BY created_at ASC, id ASC",
            (job_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    @_synchronized
    def set_job_reminder(self, job_id: int, reminder_at: str, note: str = "") -> None:
        """Set a manual follow-up date/deadline on a job (F4). Empty clears it."""
        if reminder_at and reminder_at.strip():
            self.conn.execute(
                "UPDATE jobs SET reminder_at = ?, reminder_note = ?, updated_at = ? WHERE id = ?",
                (reminder_at.strip(), (note or "").strip(), now_iso(), job_id),
            )
        else:
            self.conn.execute(
                "UPDATE jobs SET reminder_at = NULL, reminder_note = NULL, updated_at = ? WHERE id = ?",
                (now_iso(), job_id),
            )
        self.conn.commit()

    @_synchronized
    def clear_job_reminder(self, job_id: int) -> None:
        self.conn.execute(
            "UPDATE jobs SET reminder_at = NULL, reminder_note = NULL, updated_at = ? WHERE id = ?",
            (now_iso(), job_id),
        )
        self.conn.commit()

    def list_reminders(self, stale_days: int = 7) -> dict[str, Any]:
        """Reminders due + auto nudges for stale applications (F4).

        ``reminders``: jobs with a manual ``reminder_at`` (any date; ``overdue``
        flags past dates). ``stale``: applied/interviewing jobs whose most recent
        timeline event is older than ``stale_days`` (derived from job_actions,
        falling back to ``updated_at`` for jobs with no recorded action).
        """
        now = now_iso()
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id, titolo, azienda, reminder_at, reminder_note FROM jobs "
            "WHERE reminder_at IS NOT NULL AND TRIM(reminder_at) != '' "
            "ORDER BY reminder_at ASC"
        )
        reminders = [
            {
                "job_id": int(r["id"]),
                "titolo": r["titolo"] or "",
                "azienda": r["azienda"] or "",
                "type": "reminder",
                "due_at": r["reminder_at"],
                "note": r["reminder_note"] or "",
                "overdue": bool(r["reminder_at"] and r["reminder_at"] <= now),
            }
            for r in cur.fetchall()
        ]

        cur.execute(
            "SELECT j.id, j.titolo, j.azienda, j.status, "
            "COALESCE(MAX(a.created_at), j.updated_at) AS last_at "
            "FROM jobs j LEFT JOIN job_actions a ON a.job_id = j.id "
            "WHERE j.status IN ('applied', 'interviewing') "
            "GROUP BY j.id "
            "HAVING julianday('now') - julianday(last_at) >= ? "
            "ORDER BY last_at ASC",
            (stale_days,),
        )
        stale = [
            {
                "job_id": int(r["id"]),
                "titolo": r["titolo"] or "",
                "azienda": r["azienda"] or "",
                "type": "stale",
                "status": r["status"],
                "since": r["last_at"],
            }
            for r in cur.fetchall()
        ]
        return {"reminders": reminders, "stale": stale, "count": len(reminders) + len(stale)}

    @_synchronized
    def create_saved_search(self, name: str, config: dict[str, Any]) -> int:
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO saved_searches(name, config_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (name.strip() or "Untitled", json.dumps(config, ensure_ascii=False), ts, ts),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def list_saved_searches(self) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT id, name, config_json, created_at FROM saved_searches ORDER BY id DESC"
        )
        out: list[dict[str, Any]] = []
        for r in cur.fetchall():
            row = dict(r)
            try:
                row["config"] = json.loads(row.pop("config_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                row["config"] = {}
            out.append(row)
        return out

    @_synchronized
    def delete_saved_search(self, search_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM saved_searches WHERE id = ?", (search_id,))
        self.conn.commit()
        return cur.rowcount > 0

    @_synchronized
    def set_favorite(self, job_id: int, is_favorite: bool) -> None:
        self.conn.execute(
            "UPDATE jobs SET is_favorite = ?, updated_at = ? WHERE id = ?",
            (1 if is_favorite else 0, now_iso(), job_id),
        )
        self.conn.commit()

    # Tables holding per-job child rows. Their FKs are inert (PRAGMA
    # foreign_keys is never enabled, and job_actions has no ON DELETE anyway),
    # so deletes must clear them explicitly or they accumulate as orphans.
    _JOB_CHILD_TABLES = ("job_actions", "recruiters", "pinned_jobs")

    @_synchronized
    def delete_job(self, job_id: int) -> bool:
        for tbl in self._JOB_CHILD_TABLES:
            self.conn.execute(f"DELETE FROM {tbl} WHERE job_id = ?", (job_id,))
        cur = self.conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self.conn.commit()  # single commit: parent+children go atomically
        return cur.rowcount > 0

    @_synchronized
    def delete_all_jobs(self) -> int:
        for tbl in self._JOB_CHILD_TABLES:
            self.conn.execute(f"DELETE FROM {tbl}")
        cur = self.conn.execute("DELETE FROM jobs")
        self.conn.commit()
        return cur.rowcount or 0

    def list_jobs(
        self,
        status: str | None = None,
        only_favorites: bool = False,
        only_new: bool = False,
        remote_only: bool = False,
        search_text: str | None = None,
        min_score: int | None = None,
        max_age_days: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM jobs WHERE 1=1"
        params: list[Any] = []

        if status:
            query += " AND status = ?"
            params.append(status)
        if only_favorites:
            query += " AND is_favorite = 1"
        if only_new:
            query += " AND is_new = 1"
        if remote_only:
            # ``modalita`` is free text ("Remoto" / "Remote" / "Da remoto" …);
            # matching '%remot%' covers the common remote variants across locales.
            query += " AND LOWER(COALESCE(modalita, '')) LIKE '%remot%'"
        if search_text:
            query += (
                " AND (LOWER(titolo) LIKE ? ESCAPE '\\' OR LOWER(azienda) LIKE ? ESCAPE '\\'"
                " OR LOWER(descrizione) LIKE ? ESCAPE '\\')"
            )
            like = f"%{_escape_like(search_text.strip().lower())}%"
            params.extend([like, like, like])
        if min_score is not None:
            query += " AND punteggio_ai >= ?"
            params.append(min_score)
        if max_age_days is not None:
            query += " AND julianday('now') - julianday(last_seen_at) <= ?"
            params.append(max_age_days)

        query += " ORDER BY punteggio_ai DESC, last_seen_at DESC LIMIT ?"
        params.append(limit)
        cur = self.conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()

        output: list[dict[str, Any]] = []
        for row in rows:
            raw = dict(row)
            raw["is_favorite"] = bool(raw.get("is_favorite", 0))
            raw["is_new"] = bool(raw.get("is_new", 0))
            raw["sources"] = _parse_sources(raw.get("sources_json"))
            output.append(raw)
        return output

    def get_top_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        return self.list_jobs(status="open", limit=limit)

    def get_recommended_jobs(self, limit: int = 5) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT *
            FROM jobs
            WHERE status = 'open'
            ORDER BY
                CASE
                    WHEN LOWER(consiglio) LIKE '%candidati subito%' THEN 0
                    WHEN LOWER(consiglio) LIKE '%valutabile%' THEN 1
                    ELSE 2
                END,
                punteggio_ai DESC,
                last_seen_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        for row in rows:
            row["is_favorite"] = bool(row.get("is_favorite", 0))
            row["is_new"] = bool(row.get("is_new", 0))
            row["sources"] = _parse_sources(row.get("sources_json"))
        return rows

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        data["sources"] = _parse_sources(data.get("sources_json"))
        return data

    def job_has_analysis(self, job_id: int) -> bool:
        """Whether a job already carries a CURRENT AI analysis — cheaper than
        get_job() when the scan loop only needs to decide skip-vs-rescore.

        "Current" means: written by a model (not the local heuristic) against
        the schema version the app runs today. Anything older, or anything the
        heuristic produced, counts as absent so the job is re-scored once and
        self-heals — analyses from before the deterministic checks could hide a
        hard blocker (a US-based job scored 8 for a candidate with no visa, a
        posting demanding 102/110 scored 10 for a 95/110 CV).

        Bumping :data:`app.scoring_schema.CURRENT_ANALYSIS_VERSION` is the
        intended way to force a one-off mass re-score after a schema change.
        """
        cur = self.conn.execute(
            "SELECT 1 FROM jobs WHERE id = ? AND analysis_json IS NOT NULL "
            "AND analysis_json != '' AND analysis_v >= ?",
            (int(job_id), CURRENT_ANALYSIS_VERSION),
        )
        return cur.fetchone() is not None

    def recent_ral_estimates(self, limit: int = 30) -> list[str]:
        """Salary ranges the AI read on recently analysed jobs, newest first.

        Feeds the salary-suggestion prompt with what THIS user's market actually
        pays, instead of a generic national average. Rows where nothing was
        estimable are skipped, so the caller may get fewer than ``limit``.
        """
        cur = self.conn.execute(
            "SELECT json_extract(analysis_json, '$.ral_stimata') FROM jobs "
            "WHERE analysis_json IS NOT NULL AND analysis_json != '' "
            "ORDER BY analyzed_at DESC LIMIT ?",
            (int(limit) * 3,),
        )
        seen: list[str] = []
        for (value,) in cur.fetchall():
            text = str(value or "").strip()
            if not text or "stimabile" in text.lower() or text in seen:
                continue
            seen.append(text)
            if len(seen) >= limit:
                break
        return seen

    def get_job_with_analysis(self, job_id: int) -> dict[str, Any] | None:
        data = self.get_job(job_id)
        if not data:
            return None
        raw = data.get("analysis_json") or "{}"
        try:
            data["analysis"] = json.loads(raw)
        except json.JSONDecodeError:
            data["analysis"] = {}
        return data

    @_synchronized
    def save_candidate_profile(
        self,
        source_name: str,
        markdown: str,
        summary: dict[str, Any],
        content_hash: str | None = None,
        name: str | None = None,
    ) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO candidate_profiles(source_name, markdown, summary_json, content_hash, name, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                source_name,
                markdown,
                json.dumps(summary, ensure_ascii=False),
                content_hash,
                name,
                now_iso(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    @_synchronized
    def update_candidate_profile_name(self, profile_id: int, name: str | None) -> None:
        self.conn.execute(
            "UPDATE candidate_profiles SET name = ? WHERE id = ?",
            (name, profile_id),
        )
        self.conn.commit()

    def find_candidate_profile_by_hash(self, content_hash: str) -> int | None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id FROM candidate_profiles WHERE content_hash = ? ORDER BY id DESC LIMIT 1",
            (content_hash,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None

    def get_latest_candidate_profile(self) -> dict[str, Any] | None:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM candidate_profiles ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["summary_json"] = json.loads(data.get("summary_json") or "{}")
        except json.JSONDecodeError:
            data["summary_json"] = {}
        return data

    def list_candidate_profiles(self) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("SELECT id, source_name, created_at FROM candidate_profiles ORDER BY id DESC")
        return [dict(r) for r in cur.fetchall()]

    def get_candidate_profile(self, profile_id: int) -> dict[str, Any] | None:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM candidate_profiles WHERE id = ?", (profile_id,))
        row = cur.fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["summary_json"] = json.loads(data.get("summary_json") or "{}")
        except json.JSONDecodeError:
            data["summary_json"] = {}
        return data

    @_synchronized
    def set_active_profile(self, profile_id: int) -> None:
        self.set_preference("active_profile_id", str(profile_id))

    @_synchronized
    def update_candidate_profile_summary(self, profile_id: int, summary: dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE candidate_profiles SET summary_json = ? WHERE id = ?",
            (json.dumps(summary, ensure_ascii=False), profile_id),
        )
        self.conn.commit()

    @_synchronized
    def update_candidate_profile_fields(
        self, profile_id: int, *, markdown: str | None = None, name: str | None = None
    ) -> None:
        """Update the raw CV markdown and/or the display name of a profile
        (manual in-app editing). Only the provided fields are touched."""
        sets: list[str] = []
        params: list[Any] = []
        if markdown is not None:
            sets.append("markdown = ?")
            params.append(markdown)
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if not sets:
            return
        params.append(profile_id)
        self.conn.execute(f"UPDATE candidate_profiles SET {', '.join(sets)} WHERE id = ?", params)
        self.conn.commit()

    @_synchronized
    def delete_candidate_profile(self, profile_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM candidate_profiles WHERE id = ?", (profile_id,))
        self.conn.commit()
        deleted = cur.rowcount > 0
        if deleted:
            active_raw = self.get_preference("active_profile_id", "")
            if active_raw.isdigit() and int(active_raw) == profile_id:
                latest = self.get_latest_candidate_profile()
                if latest:
                    self.set_preference("active_profile_id", str(int(latest["id"])))
                else:
                    self.set_preference("active_profile_id", "")
        return deleted

    def get_active_candidate_profile(self) -> dict[str, Any] | None:
        active_raw = self.get_preference("active_profile_id", "")
        if active_raw.isdigit():
            profile = self.get_candidate_profile(int(active_raw))
            if profile:
                return profile
        return self.get_latest_candidate_profile()

    # ---- Chat sessions (multi-chat) ----

    def list_chat_sessions(self) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT cs.id, cs.title, cs.created_at, cs.updated_at, "
            "(SELECT COUNT(*) FROM chat_messages cm WHERE cm.session_id = cs.id "
            "AND cm.content_type = 'message') AS message_count "
            "FROM chat_sessions cs ORDER BY cs.updated_at DESC, cs.id DESC"
        )
        return [dict(r) for r in cur.fetchall()]

    @_synchronized
    def create_chat_session(self, session_id: str, title: str = "") -> dict[str, Any]:
        ts = now_iso()
        self.conn.execute(
            "INSERT OR IGNORE INTO chat_sessions(id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, title, ts, ts),
        )
        self.conn.commit()
        return {"id": session_id, "title": title, "created_at": ts, "updated_at": ts}

    @_synchronized
    def rename_chat_session(self, session_id: str, title: str) -> bool:
        cur = self.conn.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, now_iso(), session_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    @_synchronized
    def touch_chat_session(self, session_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO chat_sessions(id, title, created_at, updated_at) "
            "VALUES (?, '', ?, ?)",
            (session_id, now_iso(), now_iso()),
        )
        self.conn.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (now_iso(), session_id),
        )
        self.conn.commit()

    @_synchronized
    def delete_chat_session(self, session_id: str) -> bool:
        if session_id == "default":
            self.conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
            self.conn.execute("DELETE FROM pinned_jobs WHERE session_id = ?", (session_id,))
            self.conn.commit()
            return True
        self.conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        self.conn.execute("DELETE FROM pinned_jobs WHERE session_id = ?", (session_id,))
        cur = self.conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # ---- Pinned jobs ----

    @_synchronized
    def pin_job(self, session_id: str, job_id: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO pinned_jobs(session_id, job_id, pinned_at) VALUES (?, ?, ?)",
            (session_id, int(job_id), now_iso()),
        )
        self.conn.commit()

    @_synchronized
    def unpin_job(self, session_id: str, job_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM pinned_jobs WHERE session_id = ? AND job_id = ?",
            (session_id, int(job_id)),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_pinned_jobs(self, session_id: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT j.* FROM pinned_jobs p JOIN jobs j ON j.id = p.job_id "
            "WHERE p.session_id = ? ORDER BY p.pinned_at DESC",
            (session_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    # ---- Recruiter info per job ----

    @_synchronized
    def upsert_recruiter(self, job_id: int, data: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO recruiters(job_id, name, title, headline, profile_url, raw_text, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET "
            "name=excluded.name, title=excluded.title, headline=excluded.headline, "
            "profile_url=excluded.profile_url, raw_text=excluded.raw_text, "
            "fetched_at=excluded.fetched_at",
            (
                int(job_id),
                data.get("name"),
                data.get("title"),
                data.get("headline"),
                data.get("profile_url"),
                data.get("raw_text"),
                now_iso(),
            ),
        )
        self.conn.commit()

    def get_recruiter(self, job_id: int) -> dict[str, Any] | None:
        cur = self.conn.execute("SELECT * FROM recruiters WHERE job_id = ?", (int(job_id),))
        row = cur.fetchone()
        return dict(row) if row else None

    @_synchronized
    def save_chat_message(
        self,
        session_id: str,
        role: str,
        content: str,
        content_type: str = "message",
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO chat_messages(session_id, role, content, content_type, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, content_type, now_iso()),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def list_chat_messages(
        self,
        session_id: str,
        limit: int = 30,
        include_types: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        if include_types:
            placeholders = ",".join("?" * len(include_types))
            cur.execute(
                f"SELECT * FROM chat_messages WHERE session_id = ? "
                f"AND content_type IN ({placeholders}) "
                "ORDER BY id DESC LIMIT ?",
                (session_id, *include_types, limit),
            )
        else:
            cur.execute(
                "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            )
        rows = [dict(r) for r in cur.fetchall()]
        rows.reverse()
        return rows

    def count_chat_messages(self, session_id: str, content_type: str = "message") -> int:
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE session_id = ? AND content_type = ?",
            (session_id, content_type),
        )
        row = cur.fetchone()
        return int(row[0] if row else 0)

    @_synchronized
    def delete_chat_messages_by_ids(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        self.conn.execute(f"DELETE FROM chat_messages WHERE id IN ({placeholders})", ids)
        self.conn.commit()

    def get_analytics(self) -> dict[str, Any]:
        cursor = self.conn.cursor()
        total = cursor.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] or 0
        applied = (
            cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'applied'").fetchone()[0] or 0
        )
        rejected = (
            cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'rejected'").fetchone()[0] or 0
        )

        jobs_by_status: dict[str, int] = {
            "open": 0,
            "applied": 0,
            "interviewing": 0,
            "rejected": 0,
            "archived": 0,
        }
        for row in cursor.execute(
            "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
        ).fetchall():
            status = str(row[0] or "").strip().lower()
            count = int(row[1] or 0)
            if status in jobs_by_status:
                jobs_by_status[status] = count

        score_distribution: dict[str, int] = {str(i): 0 for i in range(11)}
        for row in cursor.execute(
            "SELECT punteggio_ai, COUNT(*) AS count FROM jobs GROUP BY punteggio_ai"
        ).fetchall():
            try:
                score = int(row[0])
            except (TypeError, ValueError):
                logger.debug("Skipping non-numeric punteggio_ai in analytics: %r", row[0])
                continue
            if 0 <= score <= 10:
                score_distribution[str(score)] = int(row[1] or 0)

        top_companies: list[dict[str, Any]] = []
        for row in cursor.execute(
            "SELECT azienda, COUNT(*) AS c FROM jobs WHERE azienda != '' "
            "GROUP BY azienda ORDER BY c DESC LIMIT 5"
        ).fetchall():
            top_companies.append({"company": str(row[0]), "count": int(row[1] or 0)})

        return {
            "total": total,
            "applied": applied,
            "rejected": rejected,
            "jobs_by_status": jobs_by_status,
            "score_distribution": score_distribution,
            "top_companies": top_companies,
        }

    @_synchronized
    def save_job_analysis_field(self, job_id: int, field: str, value: Any) -> bool:
        """Merge a single key into a job's ``analysis_json`` blob.

        Used to persist generated artifacts (cover letter, interview prep,
        tailored resume) without a dedicated column per artifact. Returns
        ``False`` if the job has no analysis row yet.
        """
        cursor = self.conn.cursor()
        row = cursor.execute("SELECT analysis_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not (row and row[0]):
            return False
        try:
            data = json.loads(row[0])
        except json.JSONDecodeError:
            data = {}
        data[field] = value
        cursor.execute(
            "UPDATE jobs SET analysis_json = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), job_id),
        )
        self.conn.commit()
        return True

    def save_cover_letter(self, job_id: int, letter: str) -> None:
        self.save_job_analysis_field(job_id, "cover_letter", letter)

    @_synchronized
    def set_preference(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO preferences(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now_iso()),
        )
        self.conn.commit()

    def get_preference(self, key: str, default: str = "") -> str:
        cur = self.conn.cursor()
        cur.execute("SELECT value FROM preferences WHERE key = ?", (key,))
        row = cur.fetchone()
        return str(row["value"]) if row else default

    def list_preferences(self) -> dict[str, str]:
        cur = self.conn.cursor()
        cur.execute("SELECT key, value FROM preferences")
        return {str(r["key"]): str(r["value"]) for r in cur.fetchall()}

    @_synchronized
    def cleanup_stale_jobs(self, retention_days: int) -> int:
        # Always keep favorites; archive only non-favorite open jobs past retention.
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE jobs
            SET status = 'archived', updated_at = ?
            WHERE status = 'open'
              AND is_favorite = 0
              AND julianday('now') - julianday(last_seen_at) > ?
            """,
            (now_iso(), retention_days),
        )
        self.conn.commit()
        return cur.rowcount

    def export_jobs_for_csv(self) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM jobs ORDER BY punteggio_ai DESC, id DESC")
        rows = cur.fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            analysis: dict[str, Any] = {}
            raw = data.get("analysis_json") or "{}"
            try:
                analysis = json.loads(raw)
            except json.JSONDecodeError:
                analysis = {}

            output.append(
                {
                    "Modalità": data.get("modalita", ""),
                    "Punteggio AI": data.get("punteggio_ai", 0),
                    "Consiglio": data.get("consiglio", ""),
                    "Titolo": data.get("titolo", ""),
                    "Azienda": data.get("azienda", ""),
                    "Sede": data.get("sede", ""),
                    "Fonte": data.get("fonte", ""),
                    "Programmazione richiesta": analysis.get("programmazione_richiesta", "?"),
                    "Smart Working": analysis.get("smart_working", "?"),
                    "Contratto": analysis.get("contratto", "?"),
                    "Tipo ingaggio": analysis.get("tipo_ingaggio", "?"),
                    "Eleggibilità": analysis.get("eleggibilita_geografica", "?"),
                    "Titolo di studio richiesto": analysis.get("titolo_studio_richiesto", "?"),
                    "Anni esperienza richiesti": analysis.get("anni_esperienza_richiesti", "?"),
                    # The old headers named the developer ("Punti forza per Diego")
                    # in every user's export; the analysis keys were renamed too.
                    "Punti di forza": analysis.get("punti_forza", "?"),
                    "Punti deboli": analysis.get("punti_deboli", "?"),
                    "Riassunto AI": analysis.get("riassunto", "?"),
                    "Stipendio Min (jobspy)": analysis.get("stipendio_min", "N/D"),
                    "Stipendio Max (jobspy)": analysis.get("stipendio_max", "N/D"),
                    "RAL Stimata AI": analysis.get("ral_stimata", "Non stimabile"),
                    "Adatta Neolaureati": analysis.get("adatta_neolaureati", "?"),
                    "Ricerca usata": data.get("ricerca_usata", ""),
                    "Link": data.get("link", ""),
                    "Mandata candidatura?": "Si" if data.get("status") == "applied" else "",
                }
            )
        return output
