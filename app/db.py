import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_job_hash(titolo: str, azienda: str, link: str) -> str:
    raw = f"{titolo.strip().lower()}|{azienda.strip().lower()}|{link.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_hash TEXT NOT NULL UNIQUE,
                titolo TEXT NOT NULL,
                azienda TEXT NOT NULL,
                descrizione TEXT DEFAULT '',
                sede TEXT DEFAULT '',
                fonte TEXT DEFAULT '',
                link TEXT DEFAULT '',
                ricerca_usata TEXT DEFAULT '',
                modalita TEXT DEFAULT '',
                analysis_json TEXT,
                punteggio_ai INTEGER DEFAULT 0,
                consiglio TEXT DEFAULT '',
                status TEXT DEFAULT 'open',
                is_favorite INTEGER DEFAULT 0,
                is_new INTEGER DEFAULT 1,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                analyzed_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                location TEXT DEFAULT '',
                is_remote INTEGER DEFAULT 0,
                terms_json TEXT DEFAULT '[]',
                totale_trovati INTEGER DEFAULT 0,
                totale_nuovi INTEGER DEFAULT 0,
                totale_analizzati INTEGER DEFAULT 0,
                totale_scartati INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS candidate_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                markdown TEXT NOT NULL,
                summary_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS job_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def begin_scan(self, location: str, is_remote: bool, terms: list[str]) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO scan_runs(started_at, location, is_remote, terms_json)
            VALUES (?, ?, ?, ?)
            """,
            (now_iso(), location, 1 if is_remote else 0, json.dumps(terms, ensure_ascii=False)),
        )
        run_id = int(cur.lastrowid)
        self.conn.commit()

        # Le nuove card appartengono solo all'ultima scansione.
        self.conn.execute("UPDATE jobs SET is_new = 0")
        self.conn.commit()
        return run_id

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

    def upsert_job(self, payload: dict[str, Any]) -> tuple[int, bool, str]:
        hash_value = make_job_hash(
            payload.get("titolo", ""),
            payload.get("azienda", ""),
            payload.get("link", ""),
        )
        cur = self.conn.cursor()
        cur.execute("SELECT id, status FROM jobs WHERE job_hash = ?", (hash_value,))
        row = cur.fetchone()
        timestamp = now_iso()
        if row:
            cur.execute(
                """
                UPDATE jobs
                SET descrizione = ?,
                    sede = ?,
                    fonte = ?,
                    ricerca_usata = ?,
                    modalita = ?,
                    last_seen_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.get("descrizione", ""),
                    payload.get("sede", ""),
                    payload.get("fonte", ""),
                    payload.get("ricerca_usata", ""),
                    payload.get("modalita", ""),
                    timestamp,
                    timestamp,
                    row["id"],
                ),
            )
            self.conn.commit()
            return int(row["id"]), False, str(row["status"])

        cur.execute(
            """
            INSERT INTO jobs(
                job_hash, titolo, azienda, descrizione, sede, fonte, link,
                ricerca_usata, modalita, first_seen_at, last_seen_at, updated_at, is_new
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                hash_value,
                payload.get("titolo", ""),
                payload.get("azienda", ""),
                payload.get("descrizione", ""),
                payload.get("sede", ""),
                payload.get("fonte", ""),
                payload.get("link", ""),
                payload.get("ricerca_usata", ""),
                payload.get("modalita", ""),
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        job_id = int(cur.lastrowid)
        self.conn.commit()
        return job_id, True, "open"

    def update_job_analysis(self, job_id: int, analysis: dict[str, Any]) -> None:
        score = int(analysis.get("punteggio", 0) or 0)
        consiglio = str(analysis.get("consiglio", ""))
        self.conn.execute(
            """
            UPDATE jobs
            SET analysis_json = ?, punteggio_ai = ?, consiglio = ?, analyzed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(analysis, ensure_ascii=False),
                score,
                consiglio,
                now_iso(),
                now_iso(),
                job_id,
            ),
        )
        self.conn.commit()

    def add_manual_job(self, payload: dict[str, Any]) -> int:
        job_id, _, _ = self.upsert_job(payload)
        return job_id

    def set_job_action(self, job_id: int, action: str, notes: str = "") -> None:
        new_status = "open"
        if action == "applied":
            new_status = "applied"
        elif action == "rejected":
            new_status = "rejected"
        elif action == "reopened":
            new_status = "open"

        self.conn.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now_iso(), job_id),
        )
        self.conn.execute(
            "INSERT INTO job_actions(job_id, action, notes, created_at) VALUES (?, ?, ?, ?)",
            (job_id, action, notes, now_iso()),
        )
        self.conn.commit()

    def set_favorite(self, job_id: int, is_favorite: bool) -> None:
        self.conn.execute(
            "UPDATE jobs SET is_favorite = ?, updated_at = ? WHERE id = ?",
            (1 if is_favorite else 0, now_iso(), job_id),
        )
        self.conn.commit()

    def list_jobs(
        self,
        status: str | None = None,
        only_favorites: bool = False,
        only_new: bool = False,
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
        if search_text:
            query += " AND (LOWER(titolo) LIKE ? OR LOWER(azienda) LIKE ? OR LOWER(descrizione) LIKE ?)"
            like = f"%{search_text.strip().lower()}%"
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
        return rows

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        return dict(row) if row else None

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

    def save_candidate_profile(self, source_name: str, markdown: str, summary: dict[str, Any]) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO candidate_profiles(source_name, markdown, summary_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                source_name,
                markdown,
                json.dumps(summary, ensure_ascii=False),
                now_iso(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

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

    def set_active_profile(self, profile_id: int) -> None:
        self.set_preference("active_profile_id", str(profile_id))

    def get_active_candidate_profile(self) -> dict[str, Any] | None:
        active_raw = self.get_preference("active_profile_id", "")
        if active_raw.isdigit():
            profile = self.get_candidate_profile(int(active_raw))
            if profile:
                return profile
        return self.get_latest_candidate_profile()

    def save_chat_message(self, session_id: str, role: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO chat_messages(session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, now_iso()),
        )
        self.conn.commit()

    def list_chat_messages(self, session_id: str, limit: int = 30) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT * FROM chat_messages
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
        rows = [dict(r) for r in cur.fetchall()]
        rows.reverse()
        return rows

    def get_analytics(self) -> dict:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            total = cursor.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] or 0
            applied = cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'applied'").fetchone()[0] or 0
            rejected = cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'rejected'").fetchone()[0] or 0
            return {"total": total, "applied": applied, "rejected": rejected}

    def save_cover_letter(self, job_id: int, letter: str) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT analysis_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row and row[0]:
                import json
                data = json.loads(row[0])
                data["cover_letter"] = letter
                cursor.execute("UPDATE jobs SET analysis_json = ? WHERE id = ?", (json.dumps(data, ensure_ascii=False), job_id))
            conn.commit()

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

    def cleanup_stale_jobs(self, retention_days: int) -> int:
        # Mantieni sempre i preferiti. Archivia solo open non preferiti oltre retention.
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
                    "Junior Friendly": analysis.get("junior_friendly", "?"),
                    "Anni esperienza richiesti": analysis.get("anni_esperienza_richiesti", "?"),
                    "Punti forza per Diego": analysis.get("punti_forza_per_diego", "?"),
                    "Punti deboli per Diego": analysis.get("punti_deboli_per_diego", "?"),
                    "Riassunto AI": analysis.get("riassunto", "?"),
                    "Stipendio Min (jobspy)": analysis.get("stipendio_min", "N/D"),
                    "Stipendio Max (jobspy)": analysis.get("stipendio_max", "N/D"),
                    "RAL Stimata AI": analysis.get("ral_stimata", "Non stimabile"),
                    "Reputazione Azienda": analysis.get("reputazione_azienda", "?"),
                    "Adatta Neolaureati": analysis.get("adatta_neolaureati", "?"),
                    "Note Azienda": analysis.get("note_azienda", "?"),
                    "Ricerca usata": data.get("ricerca_usata", ""),
                    "Link": data.get("link", ""),
                    "Mandata candidatura?": "Si" if data.get("status") == "applied" else "",
                }
            )
        return output
