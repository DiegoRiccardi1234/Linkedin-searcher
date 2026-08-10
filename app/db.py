import functools
import hashlib
import json
import logging
import sqlite3
import threading
import unicodedata
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from app.scoring_schema import ANALYSIS_VERSION_KEY, CURRENT_ANALYSIS_VERSION
from app.services.scan.companies import canonical_company

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


def _analysis_flags(raw: Any) -> list[str]:
    """The stored analysis's flag codes (``blocchi``), or an empty list.

    Lifted out of the JSON blob so the job list can badge and filter on "you
    can't legally take this" without every caller parsing the analysis itself.
    See ``app.services.scanner_service`` for the codes.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    flags = data.get("blocchi") if isinstance(data, dict) else None
    return [str(f) for f in flags] if isinstance(flags, list) else []


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
                first_seen_at, last_seen_at, updated_at, is_new, punteggio_ai
            )
            -- punteggio_ai NULL, not the column's DEFAULT 0: between being
            -- scraped and being scored a job has no verdict, and a 0 is one —
            -- the worst one. It showed as "0/10" in the list for the minutes a
            -- scan takes to reach it.
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL)
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
        # NULL, not 0, when nobody produced a score: a 0 is a verdict ("worst
        # possible match") and would be indistinguishable from a real one. NULL
        # also does the right thing on its own in SQL — it satisfies no
        # ``min_score`` filter and sorts last under ORDER BY … DESC.
        raw_score = analysis.get("punteggio")
        score: int | None
        try:
            score = int(raw_score) if raw_score is not None else None
        except (TypeError, ValueError):
            score = None
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
            # The answer is in: whatever the funnel now says, there is nothing
            # left to watch the inbox for.
            if status_map[action] != "open":
                self.conn.execute("UPDATE jobs SET link_opened_at = NULL WHERE id = ?", (job_id,))
        # Applying is the one event worth denormalising out of the timeline: the
        # date and the CV that went with it are what the user asks about months
        # later, and the active profile is only knowable NOW (it changes).
        # COALESCE keeps the FIRST application date — re-applying to the same
        # posting does not rewrite history.
        if action == "applied":
            self.conn.execute(
                "UPDATE jobs SET applied_at = COALESCE(applied_at, ?), "
                "applied_profile_id = COALESCE(applied_profile_id, ?) WHERE id = ?",
                (now_iso(), self._active_profile_id(), job_id),
            )
        elif action == "rejected":
            self.conn.execute(
                "UPDATE jobs SET outcome = COALESCE(outcome, 'rejected'), "
                "outcome_at = COALESCE(outcome_at, ?) WHERE id = ?",
                (now_iso(), job_id),
            )
        self.conn.execute(
            "INSERT INTO job_actions(job_id, action, notes, created_at) VALUES (?, ?, ?, ?)",
            (job_id, action, notes, now_iso()),
        )
        self.conn.commit()

    @_synchronized
    def mark_link_opened(self, job_id: int) -> str | None:
        """Record that the posting was opened, and return when the wait started.

        COALESCE keeps the FIRST unresolved open: re-reading a posting a week
        later must not push the window past a confirmation that already arrived.
        The counter still moves, because "opened three times, never applied" and
        "opened once by mistake" are different things.

        Does nothing once the offer has left ``open``: the user has already said
        what happened, and there is nothing left to wait for.
        """
        self.conn.execute(
            "UPDATE jobs SET link_opened_at = COALESCE(link_opened_at, ?), "
            "link_open_count = COALESCE(link_open_count, 0) + 1 "
            "WHERE id = ? AND status = 'open'",
            (now_iso(), job_id),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT link_opened_at FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return str(row[0]) if row and row[0] else None

    @_synchronized
    def clear_link_opened(self, job_id: int) -> None:
        """Stop waiting for a confirmation about this offer."""
        self.conn.execute("UPDATE jobs SET link_opened_at = NULL WHERE id = ?", (job_id,))
        self.conn.commit()

    def list_pending_applications(self, ttl_days: int = 14) -> list[dict[str, Any]]:
        """Offers opened recently and still unanswered, newest first.

        The TTL is what keeps the question answerable: a confirmation arrives in
        minutes, so an open from three weeks ago is not evidence of anything and
        would only widen the window a match is drawn from.
        """
        rows = self.conn.execute(
            "SELECT id, titolo, azienda, link, link_opened_at FROM jobs "
            "WHERE link_opened_at IS NOT NULL AND status = 'open' "
            "AND link_opened_at > datetime('now', ?) ORDER BY link_opened_at DESC",
            (f"-{max(1, int(ttl_days))} days",),
        ).fetchall()
        return [dict(row) for row in rows]

    def filter_unseen_mail(self, account: str, keys: Sequence[str]) -> list[str]:
        """The message keys not examined yet, in the order they were given."""
        if not keys:
            return []
        seen: set[str] = set()
        chunk = 400  # SQLite's variable limit is 999; leave room for the account
        for start in range(0, len(keys), chunk):
            window = list(keys[start : start + chunk])
            placeholders = ",".join("?" for _ in window)
            rows = self.conn.execute(
                f"SELECT mail_key FROM mail_seen WHERE account = ? AND mail_key IN ({placeholders})",
                (account, *window),
            ).fetchall()
            seen.update(str(row[0]) for row in rows)
        return [key for key in keys if key not in seen]

    @_synchronized
    def record_mail_seen(
        self,
        *,
        account: str,
        mail_key: str,
        verdict: str,
        message_id: str = "",
        received_at: str = "",
        job_id: int | None = None,
        matched_rule: str = "",
    ) -> None:
        """Remember the verdict for a message. Never the message itself."""
        self.conn.execute(
            "INSERT OR IGNORE INTO mail_seen"
            "(account, mail_key, message_id, received_at, verdict, job_id, matched_rule, seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account,
                mail_key,
                message_id,
                received_at,
                verdict,
                job_id,
                matched_rule,
                now_iso(),
            ),
        )
        self.conn.commit()

    @_synchronized
    def purge_mail_seen(self, account: str | None = None) -> int:
        """Forget everything read from a mailbox: the log AND the pending queue.

        Disconnecting has to take both. Leaving the queue behind would keep
        proposals about a mailbox the user just detached, and they would be
        unanswerable — there is no longer an account to check them against.
        """
        deleted = 0
        for table in ("mail_seen", "mail_review"):
            cur = (
                self.conn.execute(f"DELETE FROM {table} WHERE account = ?", (account,))
                if account
                else self.conn.execute(f"DELETE FROM {table}")
            )
            deleted += int(cur.rowcount or 0)
        self.conn.commit()
        return deleted

    # ── the queue of messages waiting for a human ───────────────────────────

    @_synchronized
    def add_mail_review(
        self,
        *,
        account: str,
        mail_key: str,
        kind: str,
        message_id: str = "",
        received_at: str = "",
        company: str = "",
        sender: str = "",
        rule: str = "",
        role: str = "",
        candidates: Sequence[int] = (),
    ) -> None:
        """Queue a proposal. Idempotent per message, so a re-sweep does not double it."""
        self.conn.execute(
            "INSERT OR IGNORE INTO mail_review"
            "(account, mail_key, message_id, received_at, kind, company, sender, rule, role, "
            "candidates_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account,
                mail_key,
                message_id,
                received_at,
                kind,
                company,
                sender,
                rule,
                role,
                json.dumps(list(candidates)),
                now_iso(),
            ),
        )
        self.conn.commit()

    def list_mail_review(self, account: str | None = None) -> list[dict[str, Any]]:
        """The pending proposals, with their candidate offers resolved.

        Candidates are looked up now rather than stored denormalised: this user
        has deleted 159 offers by hand, and a proposal pointing at one of them
        must lose that option, not show a row that leads nowhere.
        """
        sql = "SELECT * FROM mail_review"
        params: list[Any] = []
        if account:
            sql += " WHERE account = ?"
            params.append(account)
        sql += " ORDER BY received_at DESC, id DESC"
        out: list[dict[str, Any]] = []
        for row in self.conn.execute(sql, params).fetchall():
            item = dict(row)
            try:
                ids = [int(i) for i in json.loads(item.pop("candidates_json") or "[]")]
            except (TypeError, ValueError):
                ids = []
            item["candidates"] = self._resolve_candidates(ids)
            out.append(item)
        return out

    def _resolve_candidates(self, ids: Sequence[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"SELECT id, titolo, azienda, first_seen_at, status FROM jobs "
            f"WHERE id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
        found = {int(r["id"]): dict(r) for r in rows}
        return [found[i] for i in ids if i in found]

    @_synchronized
    def set_mail_review_role(self, review_id: int, role: str) -> None:
        """Remember the title read from a body, so the list does not reconnect to show it."""
        self.conn.execute(
            "UPDATE mail_review SET role = ? WHERE id = ?", (role.strip()[:120], review_id)
        )
        self.conn.commit()

    @_synchronized
    def close_mail_review(self, review_ids: Sequence[int], verdict: str) -> int:
        """Take proposals off the queue and record that they were answered.

        The row moves from ``mail_review`` to ``mail_seen``: the question is
        closed, and ``filter_unseen_mail`` will not offer the message again. That
        is the part that was missing — a dismissed proposal used to come back on
        the next sweep, because nothing was written down about it either way.
        """
        if not review_ids:
            return 0
        placeholders = ",".join("?" for _ in review_ids)
        rows = self.conn.execute(
            f"SELECT * FROM mail_review WHERE id IN ({placeholders})",
            tuple(review_ids),
        ).fetchall()
        for row in rows:
            self.conn.execute(
                "INSERT OR IGNORE INTO mail_seen"
                "(account, mail_key, message_id, received_at, verdict, job_id, matched_rule, "
                "seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["account"],
                    row["mail_key"],
                    row["message_id"],
                    row["received_at"],
                    verdict,
                    None,
                    row["rule"],
                    now_iso(),
                ),
            )
        self.conn.execute(
            f"DELETE FROM mail_review WHERE id IN ({placeholders})",
            tuple(review_ids),
        )
        self.conn.commit()
        return len(rows)

    @_synchronized
    def add_application_from_mail(
        self, *, company: str, applied_at: str, message_id: str, rule: str, role: str = ""
    ) -> int | None:
        """Record an application to a company the archive has never seen.

        Deliberately NOT ``upsert_job``. Two reasons, both verified:

        * ``add_manual_job`` calls the model on every insert, and these rows have
          no description to read — sixty of them would spend quota producing
          nothing;
        * ``job_hash`` is ``sha256(titolo|azienda|link)`` and UNIQUE, so two
          applications to the same company with an empty title and an empty link
          collide, and the second silently overwrites the first.

        So the hash is built from what actually identifies this thing: the mail
        source, the canonical company, and the DAY. The day is the right grain —
        measured over a year of a real mailbox, no (company, day) pair carries
        more than one confirmation, so keying on it collapses the "LinkedIn says
        sent / the ATS says received" pair for one application without merging
        two genuine ones.

        Returns the new job id, or ``None`` when the row already existed.

        What is deliberately left empty: the TITLE, because a LinkedIn
        confirmation names the employer and never the role, and inventing one is
        the same sin as inventing a score; ``applied_profile_id``, because which
        CV was used three months ago is not knowable and today's active profile
        is a guess wearing a fact's clothes; and every scoring column, which is
        the shape the archive already uses for "collected, not judged".
        """
        canonical = canonical_company(company) or company.strip().lower()
        day = (applied_at or "")[:10]
        job_hash = hashlib.sha256(f"mail|{canonical}|{day}".encode()).hexdigest()
        if self.conn.execute("SELECT 1 FROM jobs WHERE job_hash = ?", (job_hash,)).fetchone():
            return None
        stamp = now_iso()
        cur = self.conn.execute(
            # punteggio_ai is written as an explicit NULL: the column carries
            # DEFAULT 0, so leaving it out would store a zero — a score nobody
            # gave, on a row nobody could score, in the one app that refuses
            # fallback scores everywhere else.
            "INSERT INTO jobs (job_hash, titolo, azienda, descrizione, sede, fonte, link, "
            "ricerca_usata, modalita, dedup_key, status, applied_at, apply_confirmed_by, "
            "apply_confirm_message_id, first_seen_at, last_seen_at, updated_at, is_new, "
            "punteggio_ai) "
            "VALUES (?, ?, ?, '', '', 'mail', '', 'mail_import', '', ?, 'applied', ?, "
            "'email', ?, ?, ?, ?, 0, NULL)",
            (
                job_hash,
                # Empty unless the body was read and gave one up: the subject
                # names the employer and never the role.
                role.strip(),
                company.strip(),
                job_hash,
                applied_at,
                message_id,
                stamp,
                stamp,
                stamp,
            ),
        )
        job_id = int(cur.lastrowid or 0)
        self.conn.execute(
            "INSERT INTO job_actions (job_id, action, notes, created_at) VALUES (?, ?, ?, ?)",
            (job_id, "applied", f"auto:mail:{rule}", stamp),
        )
        self.conn.commit()
        return job_id

    def applications_near(self, company: str, day: str, window_days: int = 7) -> list[int]:
        """Offers from this company already marked applied around that date.

        The third and last dedup level: the application may well be in the
        archive already, entered by hand or matched by an earlier sweep, and a
        second stub for it would be a duplicate nobody asked for.
        """
        canonical = canonical_company(company)
        if not canonical or not day:
            return []
        rows = self.conn.execute(
            "SELECT id, azienda FROM jobs WHERE applied_at IS NOT NULL AND applied_at <> '' "
            "AND date(applied_at) BETWEEN date(?, ?) AND date(?, ?)",
            (day, f"-{window_days} days", day, f"+{window_days} days"),
        ).fetchall()
        from app.services.scan.companies import company_matches

        return [int(r["id"]) for r in rows if company_matches(str(r["azienda"] or ""), company)]

    def open_offers_from(self, company: str) -> list[int]:
        """Open, unapplied offers from this company — the ones worth asking about."""
        if not canonical_company(company):
            return []
        rows = self.conn.execute(
            "SELECT id, azienda FROM jobs WHERE status = 'open' "
            "AND (applied_at IS NULL OR applied_at = '')"
        ).fetchall()
        from app.services.scan.companies import company_matches

        return [int(r["id"]) for r in rows if company_matches(str(r["azienda"] or ""), company)]

    @_synchronized
    def confirm_application_from_mail(self, job_id: int, message_id: str, rule: str) -> bool:
        """Mark an offer as applied because a confirmation message said so.

        The note is a fixed, non-sensitive string on purpose: notes are shown on
        the timeline and travel into the CSV export, so putting a mail subject
        there would put someone's mailbox in a spreadsheet they might share.

        ``apply_confirmed_by`` is what makes every automatic marking listable and
        reversible, which is the price of being allowed to write here at all.
        """
        if not self.conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone():
            return False
        self.set_job_action(job_id=job_id, action="applied", notes=f"auto:mail:{rule}")
        self.conn.execute(
            "UPDATE jobs SET apply_confirmed_by = 'email', apply_confirm_message_id = ?, "
            "link_opened_at = NULL WHERE id = ?",
            (message_id, job_id),
        )
        self.conn.commit()
        return True

    @_synchronized
    def undo_mail_confirmation(self, job_id: int) -> bool:
        """Take back an automatic marking, leaving a manual one untouched."""
        row = self.conn.execute(
            "SELECT apply_confirmed_by FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not row or row[0] != "email":
            return False
        self.conn.execute(
            "UPDATE jobs SET status = 'open', applied_at = NULL, applied_profile_id = NULL, "
            "apply_confirmed_by = NULL, apply_confirm_message_id = NULL, updated_at = ? "
            "WHERE id = ?",
            (now_iso(), job_id),
        )
        self.conn.execute(
            "DELETE FROM job_actions WHERE job_id = ? AND action = 'applied' "
            "AND notes LIKE 'auto:mail:%'",
            (job_id,),
        )
        self.conn.commit()
        return True

    def _active_profile_id(self) -> int | None:
        """Id of the CV profile in use right now, for stamping an application."""
        try:
            raw = self.conn.execute(
                "SELECT value FROM preferences WHERE key = 'active_profile_id'"
            ).fetchone()
            if raw and str(raw[0]).strip():
                return int(str(raw[0]).strip())
            row = self.conn.execute(
                "SELECT id FROM candidate_profiles ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return int(row[0]) if row else None
        except (ValueError, sqlite3.Error):
            return None

    #: Endings an application can have, beyond the funnel state. "no_response" is
    #: the common one the funnel could not express: a job left in "applied" looks
    #: the same whether it is two days or eight months old.
    OUTCOMES = ("pending", "no_response", "rejected", "offer", "accepted", "withdrawn")

    @_synchronized
    def set_job_outcome(self, job_id: int, outcome: str) -> bool:
        """Record how an application ended. Empty/"pending" clears it."""
        value = (outcome or "").strip().lower()
        if value and value not in self.OUTCOMES:
            return False
        if not value or value == "pending":
            self.conn.execute(
                "UPDATE jobs SET outcome = NULL, outcome_at = NULL, updated_at = ? WHERE id = ?",
                (now_iso(), job_id),
            )
        else:
            self.conn.execute(
                "UPDATE jobs SET outcome = ?, outcome_at = ?, updated_at = ? WHERE id = ?",
                (value, now_iso(), now_iso(), job_id),
            )
        self.conn.commit()
        return True

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

    # ── Score feedback: is the AI's verdict any good? (migration 017) ────────

    VERDICTS = ("up", "down")

    @_synchronized
    def add_score_feedback(
        self,
        job_id: int,
        verdict: str,
        expected_score: int | None = None,
        reason: str = "",
    ) -> int:
        """Record what the user thinks of this job's AI score.

        The job's own score, title and company are copied into the row: the
        judgement has to stay readable after a re-score (which overwrites the
        score) or a wipe (which deletes the job).
        """
        value = (verdict or "").strip().lower()
        if value not in self.VERDICTS:
            return 0
        row = self.conn.execute(
            "SELECT titolo, azienda, punteggio_ai, analysis_v, analysis_json FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            return 0
        model = ""
        try:
            model = str((json.loads(row["analysis_json"] or "{}") or {}).get("modello") or "")
        except (json.JSONDecodeError, TypeError):
            model = ""
        cur = self.conn.execute(
            "INSERT INTO score_feedback(job_id, titolo, azienda, verdict, ai_score, "
            "expected_score, reason, analysis_v, model, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                row["titolo"],
                row["azienda"],
                value,
                row["punteggio_ai"],
                expected_score,
                (reason or "").strip(),
                row["analysis_v"],
                model,
                now_iso(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def latest_score_feedback(self, job_id: int) -> dict[str, Any] | None:
        """The most recent judgement on this job, or None. The UI shows which
        way the user voted; the history stays in the table."""
        row = self.conn.execute(
            "SELECT * FROM score_feedback WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return dict(row) if row else None

    @_synchronized
    def delete_score_feedback(self, job_id: int) -> int:
        """Withdraw every judgement on a job (the user changed their mind)."""
        cur = self.conn.execute("DELETE FROM score_feedback WHERE job_id = ?", (job_id,))
        self.conn.commit()
        return cur.rowcount or 0

    def list_score_feedback(self, limit: int = 500) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM score_feedback ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        ]

    def score_feedback_summary(self) -> dict[str, Any]:
        """How often the user agrees with the AI, and by how much when not.

        Only the LATEST judgement per job counts: a mind changed after a re-score
        is an update, not a second data point.
        """
        rows = self.conn.execute(
            """
            SELECT verdict, ai_score, expected_score FROM score_feedback
            WHERE id IN (SELECT MAX(id) FROM score_feedback GROUP BY job_id)
            """
        ).fetchall()
        up = sum(1 for r in rows if r["verdict"] == "up")
        total = len(rows)
        gaps = [
            abs(int(r["ai_score"] or 0) - int(r["expected_score"]))
            for r in rows
            if r["expected_score"] is not None
        ]
        return {
            "total": total,
            "up": up,
            "down": total - up,
            "agreement": round(up * 100 / total) if total else None,
            "avg_gap": round(sum(gaps) / len(gaps), 1) if gaps else None,
            "scored_cases": len(gaps),
        }

    # ── Watchlist: employers followed by name (migration 015) ────────────────

    @_synchronized
    def add_watchlist_company(self, name: str, note: str = "") -> int:
        """Follow ``name``. Re-adding a company already followed reactivates it
        (and keeps its note) rather than failing on the unique canonical."""
        clean = str(name or "").strip()
        canonical = canonical_company(clean)
        if not canonical:
            return 0
        cur = self.conn.execute(
            "SELECT id FROM watchlist_companies WHERE canonical = ?", (canonical,)
        )
        row = cur.fetchone()
        if row:
            self.conn.execute(
                "UPDATE watchlist_companies SET active = 1, name = ?, "
                "note = COALESCE(NULLIF(?, ''), note) WHERE id = ?",
                (clean, str(note or "").strip(), row["id"]),
            )
            self.conn.commit()
            return int(row["id"])
        cur = self.conn.execute(
            "INSERT INTO watchlist_companies(name, canonical, note, active, added_at) "
            "VALUES (?, ?, ?, 1, ?)",
            (clean, canonical, str(note or "").strip(), now_iso()),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def list_watchlist_companies(self, active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM watchlist_companies"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY name COLLATE NOCASE"
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    @_synchronized
    def set_watchlist_active(self, company_id: int, active: bool) -> bool:
        cur = self.conn.execute(
            "UPDATE watchlist_companies SET active = ? WHERE id = ?",
            (1 if active else 0, company_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    @_synchronized
    def delete_watchlist_company(self, company_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM watchlist_companies WHERE id = ?", (company_id,))
        self.conn.commit()
        return cur.rowcount > 0

    @_synchronized
    def touch_watchlist_seen(self, canonical: str) -> None:
        """Record that a scan just matched a posting from this company, so a
        channel that never delivers can be told apart from one that is quiet."""
        self.conn.execute(
            "UPDATE watchlist_companies SET last_seen_at = ? WHERE canonical = ?",
            (now_iso(), canonical),
        )
        self.conn.commit()

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
            # IS NOT NULL is redundant in SQL (NULL >= n is never true) but says
            # the intent out loud: a quality threshold filters judgements, and an
            # offer nobody judged has none — not even a zero.
            query += " AND punteggio_ai IS NOT NULL AND punteggio_ai >= ?"
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
            raw["flags"] = _analysis_flags(raw.get("analysis_json"))
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
            WHERE status = 'open' AND punteggio_ai IS NOT NULL
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
        # The CV that was sent is stored as an id; the panel needs its name, and
        # a profile deleted since then must not blank the whole application block.
        if data.get("applied_profile_id"):
            row = self.conn.execute(
                "SELECT source_name FROM candidate_profiles WHERE id = ?",
                (data["applied_profile_id"],),
            ).fetchone()
            data["applied_profile_name"] = str(row[0]) if row else ""
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
        meta: dict[str, Any] | None = None,
    ) -> int:
        """Persist one message. ``meta`` carries the extras the UI renders with
        it (today: the suggested roles shown as clickable pills), which used to
        live only in the HTTP response and vanished on reload."""
        cur = self.conn.execute(
            "INSERT INTO chat_messages(session_id, role, content, content_type, meta_json, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                session_id,
                role,
                content,
                content_type,
                json.dumps(meta, ensure_ascii=False) if meta else None,
                now_iso(),
            ),
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
        for row in rows:
            raw_meta = row.pop("meta_json", None)
            try:
                meta = json.loads(raw_meta) if raw_meta else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}
            row["meta"] = meta if isinstance(meta, dict) else {}
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
        """Charts of what the scan found — which is not the same as the archive.

        Applications rebuilt from confirmation emails (``fonte = 'mail'``) are
        left out of the score charts on purpose. They are real applications and
        they belong in the archive, but they carry no posting: counting sixty
        rows that nobody could score into "unscored" would say the scoring is
        failing, when in fact there was never anything to read. They stay in the
        funnel counts, because that is precisely what they are evidence of.
        """
        cursor = self.conn.cursor()
        scored_pool = "fonte IS NULL OR fonte <> 'mail'"
        total = cursor.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] or 0
        applied = (
            cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'applied'").fetchone()[0] or 0
        )
        rejected = (
            cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'rejected'").fetchone()[0] or 0
        )
        from_mail = (
            cursor.execute("SELECT COUNT(*) FROM jobs WHERE fonte = 'mail'").fetchone()[0] or 0
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
            f"SELECT punteggio_ai, COUNT(*) AS count FROM jobs WHERE {scored_pool} "
            "GROUP BY punteggio_ai"
        ).fetchall():
            try:
                score = int(row[0])
            except (TypeError, ValueError):
                logger.debug("Skipping non-numeric punteggio_ai in analytics: %r", row[0])
                continue
            if 0 <= score <= 10:
                score_distribution[str(score)] = int(row[1] or 0)

        # Counted apart, not folded into "0": these offers have no score at all,
        # and the loop above drops them silently — which would make them vanish
        # from a chart whose bars are supposed to add up to the archive.
        unscored = (
            cursor.execute(
                f"SELECT COUNT(*) FROM jobs WHERE punteggio_ai IS NULL AND ({scored_pool})"
            ).fetchone()[0]
            or 0
        )

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
            "unscored": int(unscored),
            "from_mail": int(from_mail),
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
