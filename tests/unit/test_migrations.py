"""Unit tests for lightweight schema migrations."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.db import Database
from app.migrations import _discover, apply_migrations


def _schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()
    return int(row[0] if row else 0)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def test_migrations_discovered_and_ordered() -> None:
    items = _discover()
    assert len(items) >= 1
    versions = [m.version for m in items]
    assert versions == sorted(versions)
    assert versions == sorted(set(versions)), "duplicate versions detected"


def test_fresh_db_gets_all_migrations_applied(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    db = Database(db_path)
    try:
        conn = db.conn
        latest = _discover()[-1].version
        assert _schema_version(conn) == latest
        # Core tables present
        for tbl in (
            "jobs",
            "scan_runs",
            "candidate_profiles",
            "chat_messages",
            "preferences",
            "job_actions",
        ):
            assert _table_exists(conn, tbl), f"missing table {tbl}"
    finally:
        db.close()


def test_baseline_detection_for_preexisting_db(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    # Simulate a pre-migration DB: jobs table exists, no schema_version table.
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, job_hash TEXT UNIQUE,
            titolo TEXT, azienda TEXT, first_seen_at TEXT, last_seen_at TEXT, updated_at TEXT);
        """
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    try:
        latest = _discover()[-1].version
        assert _schema_version(db.conn) == latest
    finally:
        db.close()


def test_apply_migrations_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "idem.db"
    db = Database(db_path)
    try:
        first = _schema_version(db.conn)
        apply_migrations(db.conn)
        apply_migrations(db.conn)
        second = _schema_version(db.conn)
        assert first == second
    finally:
        db.close()


def test_migration_003_adds_content_hash_column_and_index(tmp_path: Path) -> None:
    db_path = tmp_path / "hash.db"
    db = Database(db_path)
    try:
        cols = {
            row[1] for row in db.conn.execute("PRAGMA table_info(candidate_profiles)").fetchall()
        }
        assert "content_hash" in cols
        idx_rows = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='candidate_profiles'"
        ).fetchall()
        idx_names = {r[0] for r in idx_rows}
        assert "idx_candidate_profiles_hash" in idx_names
    finally:
        db.close()


def test_migration_011_cleans_orphaned_child_rows(tmp_path: Path) -> None:
    """Historic deletes left rows in job_actions/recruiters/pinned_jobs whose
    job no longer exists (FK cascade was never enforced). Migration 011 must
    remove exactly the orphans and keep rows attached to live jobs."""
    db_path = tmp_path / "orphans.db"
    db = Database(db_path)
    try:
        job_id, _new, _hash = db.upsert_job(
            {"titolo": "QA", "azienda": "Acme", "link": "https://example.com/live"}
        )
        db.set_job_action(job_id, "applied", "keep me")
        db.upsert_recruiter(job_id, {"name": "R"})
        db.pin_job("default", job_id)
        # Synthesize orphans as an old delete_job would have left them.
        ghost = job_id + 999
        db.conn.execute(
            "INSERT INTO job_actions(job_id, action, notes, created_at) VALUES (?, 'applied', '', '2024-01-01')",
            (ghost,),
        )
        db.conn.execute(
            "INSERT INTO recruiters(job_id, name, title, headline, profile_url, raw_text, fetched_at) "
            "VALUES (?, 'ghost', '', '', '', '', '2024-01-01')",
            (ghost,),
        )
        db.conn.execute(
            "INSERT INTO pinned_jobs(session_id, job_id, pinned_at) VALUES ('default', ?, '2024-01-01')",
            (ghost,),
        )
        # Rewind the tracker to before 011 so the cleanup migration re-runs
        # against this DB (fresh DBs are already at the latest version).
        db.conn.execute("DELETE FROM schema_version WHERE version >= 11")
        db.conn.commit()

        apply_migrations(db.conn)

        for tbl in ("job_actions", "recruiters", "pinned_jobs"):
            ghosts = db.conn.execute(
                f"SELECT COUNT(*) FROM {tbl} WHERE job_id = ?", (ghost,)
            ).fetchone()[0]
            kept = db.conn.execute(
                f"SELECT COUNT(*) FROM {tbl} WHERE job_id = ?", (job_id,)
            ).fetchone()[0]
            assert ghosts == 0, f"orphans left in {tbl}"
            assert kept == 1, f"live rows lost from {tbl}"
    finally:
        db.close()


def _rewind_to_before_018(db: Database) -> None:
    """Fresh DBs are already at the latest version: rewind so 018 re-runs here."""
    db.conn.execute("DELETE FROM schema_version WHERE version >= 18")
    db.conn.commit()


def test_migration_018_strips_invented_heuristic_scores(tmp_path: Path) -> None:
    """A keyword score already in the archive must not survive the update.

    The scan only re-scores a job when the same posting shows up again, and
    expired ads never do — so without this the user keeps seeing "PAYROLL
    SPECIALIST 6/10" at the top forever.
    """
    db = Database(tmp_path / "u.db")
    try:
        invented, _, _ = db.upsert_job({"titolo": "PAYROLL", "azienda": "A", "link": "l1"})
        db.conn.execute(
            "UPDATE jobs SET analysis_json = ?, punteggio_ai = 6, consiglio = 'Valutabile', "
            "analysis_v = NULL WHERE id = ?",
            (
                json.dumps(
                    {
                        "punteggio": 6,
                        "consiglio": "Valutabile",
                        "riassunto": "Analisi euristica usata. Match stimato 6/10.",
                        "match_axes": {"skills_match": 8, "salary_match": 5},
                        "fonte_analisi": "euristica",
                        "blocchi": ["analisi_locale"],
                    }
                ),
                invented,
            ),
        )
        judged, _, _ = db.upsert_job({"titolo": "AI QA", "azienda": "A", "link": "l2"})
        db.conn.execute(
            "UPDATE jobs SET analysis_json = ?, punteggio_ai = 8, analysis_v = 2 WHERE id = ?",
            (json.dumps({"punteggio": 8, "scoring_v": 2}), judged),
        )
        _rewind_to_before_018(db)

        apply_migrations(db.conn)

        row = db.conn.execute(
            "SELECT punteggio_ai, consiglio, analysis_json FROM jobs WHERE id = ?", (invented,)
        ).fetchone()
        assert row[0] is None
        assert row[1] == ""
        blob = json.loads(row[2])
        assert blob["punteggio"] is None
        assert blob["riassunto"] == ""
        assert all(v is None for v in blob["match_axes"].values())
        assert "non_valutato" in blob["blocchi"]
        assert "analisi_locale" not in blob["blocchi"]
        # A model-written analysis is untouched.
        assert db.conn.execute(
            "SELECT punteggio_ai FROM jobs WHERE id = ?", (judged,)
        ).fetchone()[0] == 8
    finally:
        db.close()


def test_migration_018_keeps_deterministic_caps(tmp_path: Path) -> None:
    """A 3/10 from a hard blocker WAS computed — by this app, from the ad."""
    db = Database(tmp_path / "c.db")
    try:
        blocked, _, _ = db.upsert_job({"titolo": "US role", "azienda": "A", "link": "l3"})
        db.conn.execute(
            "UPDATE jobs SET analysis_json = ?, punteggio_ai = 3, consiglio = 'Salta', "
            "analysis_v = NULL WHERE id = ?",
            (
                json.dumps(
                    {"punteggio": 3, "consiglio": "Salta", "blocchi": ["geo_non_ue"]},
                ),
                blocked,
            ),
        )
        _rewind_to_before_018(db)

        apply_migrations(db.conn)

        row = db.conn.execute(
            "SELECT punteggio_ai, consiglio FROM jobs WHERE id = ?", (blocked,)
        ).fetchone()
        assert row[0] == 3
        assert row[1] == "Salta"
    finally:
        db.close()


def test_migration_018_nulls_never_analysed_rows(tmp_path: Path) -> None:
    """``punteggio_ai INTEGER DEFAULT 0`` claimed a verdict on every fresh row."""
    db = Database(tmp_path / "n.db")
    try:
        fresh, _, _ = db.upsert_job({"titolo": "New", "azienda": "A", "link": "l4"})
        _rewind_to_before_018(db)

        apply_migrations(db.conn)

        assert (
            db.conn.execute("SELECT punteggio_ai FROM jobs WHERE id = ?", (fresh,)).fetchone()[0]
            is None
        )
    finally:
        db.close()


def test_find_candidate_profile_by_hash_dedup(tmp_path: Path) -> None:
    db_path = tmp_path / "dedup.db"
    db = Database(db_path)
    try:
        first_id = db.save_candidate_profile(
            source_name="cv.txt",
            markdown="dummy",
            summary={"skills": ["python"]},
            content_hash="abc123",
        )
        assert db.find_candidate_profile_by_hash("abc123") == first_id
        assert db.find_candidate_profile_by_hash("not-found") is None
    finally:
        db.close()


def _rewind_to_before_019(db: Database) -> None:
    db.conn.execute("DELETE FROM schema_version WHERE version >= 19")
    db.conn.commit()


def _seed_019(db: Database, titolo: str, sede: str, modalita: str, descrizione: str) -> int:
    job_id, _, _ = db.upsert_job(
        {
            "titolo": titolo,
            "azienda": "A",
            "link": f"l-{titolo}",
            "sede": sede,
            "modalita": modalita,
            "descrizione": descrizione,
        }
    )
    db.conn.execute(
        "UPDATE jobs SET analysis_json = ?, punteggio_ai = 9, consiglio = 'Candidati subito', "
        "analysis_v = 2 WHERE id = ?",
        (json.dumps({"punteggio": 9, "consiglio": "Candidati subito", "scoring_v": 2}), job_id),
    )
    return int(job_id)


def test_migration_019_hides_what_the_user_cannot_apply_to(tmp_path: Path) -> None:
    """The three real cases from the 04/08/2026 archive.

    All three had been recommended: an on-site role in another city, one asking
    for years the profile does not have, one asking for a degree it does not
    have. None of them could ever be applied to.
    """
    db = Database(tmp_path / "d.db")
    try:
        db.set_preference("onboarding_work_mode", "Remoto, Torino in sede oppure ibrido su Torino")
        db.set_preference("last_scan_locations", json.dumps(["Torino"]))
        db.save_candidate_profile(
            source_name="cv.pdf",
            markdown="Laurea Triennale in Informatica, votazione 95/110.",
            summary={"years_experience": 0},
        )
        body = "Analisi funzionale e raccolta requisiti in team di prodotto. " * 8
        elsewhere = _seed_019(db, "Analista Roma", "Rome, Latium, Italy", "In sede", body)
        senior = _seed_019(
            db, "Analista 3 anni", "Turin, Piedmont, Italy", "In sede",
            body + " Richiesti almeno 3 anni di esperienza maturata nel ruolo.",
        )
        master = _seed_019(
            db, "Analista magistrale", "Turin, Piedmont, Italy", "In sede",
            body + " Richiesta laurea magistrale in informatica.",
        )
        ok = _seed_019(db, "Analista Torino", "Turin, Piedmont, Italy", "Ibrido", body)
        # Stored "Full Remote" but the text says two days out of five: hybrid.
        mislabelled = _seed_019(
            db, "Consulente ERP", "Alba, Piedmont, Italy", "Full Remote",
            body + " Possibilità di lavorare da remoto (fino a 2 giornate su 5 settimanali).",
        )
        _rewind_to_before_019(db)

        apply_migrations(db.conn)

        def _row(job_id: int) -> tuple:
            return db.conn.execute(
                "SELECT punteggio_ai, consiglio, analysis_json, modalita FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()

        for job_id, flag in (
            (elsewhere, "sede_non_raggiungibile"),
            (senior, "esperienza_richiesta"),
            (master, "titolo_superiore"),
        ):
            score, consiglio, blob, _mode = _row(job_id)
            assert score <= 3, flag
            assert consiglio == "Salta", flag
            assert flag in json.loads(blob)["blocchi"], flag

        # The applicable one keeps its score: no false negatives.
        assert _row(ok)[0] == 9
        assert json.loads(_row(ok)[2]).get("blocchi", []) == []

        # The false "Full Remote" is corrected, and that makes it out-of-area.
        assert _row(mislabelled)[3] == "Ibrido"
        assert "sede_non_raggiungibile" in json.loads(_row(mislabelled)[2])["blocchi"]
    finally:
        db.close()


def test_migration_020_frees_offers_the_corrected_rules_no_longer_block(tmp_path: Path) -> None:
    """The three shapes that were hiding applicable jobs, as stored flags.

    019 only ever adds flags, so fixing the detectors leaves the archive exactly
    as wrong as it was. 020 is the pass that lets go.
    """
    db = Database(tmp_path / "f.db")
    try:
        db.set_preference("onboarding_work_mode", "Remoto, Torino in sede oppure ibrido su Torino")
        db.set_preference("last_scan_locations", json.dumps(["Torino"]))
        db.save_candidate_profile(
            source_name="cv.pdf",
            markdown="Laurea Triennale in Informatica, votazione 95/110.",
            summary={"years_experience": 0},
        )
        body = "Analisi funzionale e raccolta requisiti in team di prodotto. " * 8
        # An apprenticeship blocked by a time window, stored as capped.
        apprendistato = _seed_019(
            db, "Security adviser apprendistato", "Turin, Piedmont, Italy", "In sede",
            body + " Esperienza pregressa di almeno 6 mesi, maturata nel corso degli ultimi 2 anni.",
        )
        # Genuinely out of reach: must stay blocked.
        senior = _seed_019(
            db, "Analista senior", "Turin, Piedmont, Italy", "In sede",
            body + " Richiesti almeno 5 anni di esperienza maturata nel ruolo.",
        )
        for job_id in (apprendistato, senior):
            db.conn.execute(
                "UPDATE jobs SET analysis_json = ?, punteggio_ai = 3, consiglio = 'Salta' "
                "WHERE id = ?",
                (
                    json.dumps(
                        {
                            "punteggio": 3,
                            "consiglio": "Salta",
                            "blocchi": ["esperienza_richiesta"],
                            "blocchi_dettaglio": {
                                "esperienza_richiesta": "Richiede 2 anni di esperienza"
                            },
                            "skills_match": {
                                "hai": [],
                                "mancano": ["Richiede 2 anni di esperienza"],
                            },
                            "scoring_v": 2,
                        }
                    ),
                    job_id,
                ),
            )
        db.conn.commit()
        db.conn.execute("DELETE FROM schema_version WHERE version >= 20")
        db.conn.commit()

        apply_migrations(db.conn)

        freed = db.conn.execute(
            "SELECT punteggio_ai, consiglio, analysis_json, analysis_v FROM jobs WHERE id = ?",
            (apprendistato,),
        ).fetchone()
        blob = json.loads(freed[2])
        assert "esperienza_richiesta" not in blob["blocchi"]
        # No score is handed back: the number under the cap was never recorded.
        assert freed[0] is None and freed[1] == "" and freed[3] is None
        assert "non_valutato" in blob["blocchi"]
        assert blob["skills_match"]["mancano"] == [], "the stale gap must stop being shown"

        still = json.loads(
            db.conn.execute(
                "SELECT analysis_json FROM jobs WHERE id = ?", (senior,)
            ).fetchone()[0]
        )
        assert "esperienza_richiesta" in still["blocchi"], "five years is a real gate"
    finally:
        db.close()


def test_migration_020_is_idempotent(tmp_path: Path) -> None:
    """A second run must find nothing stale and change nothing."""
    db = Database(tmp_path / "g.db")
    try:
        db.set_preference("onboarding_work_mode", "Remoto, Torino in sede oppure ibrido su Torino")
        db.save_candidate_profile(
            source_name="cv.pdf",
            markdown="Laurea Triennale in Informatica.",
            summary={"years_experience": 0},
        )
        job_id = _seed_019(
            db, "Analista", "Turin, Piedmont, Italy", "Ibrido",
            "Analisi funzionale. " * 20 + " Esperienza maturata negli ultimi 2 anni.",
        )
        db.conn.execute(
            "UPDATE jobs SET analysis_json = ?, punteggio_ai = 3 WHERE id = ?",
            (json.dumps({"punteggio": 3, "blocchi": ["esperienza_richiesta"]}), job_id),
        )
        db.conn.commit()
        db.conn.execute("DELETE FROM schema_version WHERE version >= 20")
        db.conn.commit()
        apply_migrations(db.conn)
        first = db.conn.execute(
            "SELECT analysis_json FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()[0]

        db.conn.execute("DELETE FROM schema_version WHERE version >= 20")
        db.conn.commit()
        apply_migrations(db.conn)
        second = db.conn.execute(
            "SELECT analysis_json FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()[0]
        assert json.loads(first) == json.loads(second)
    finally:
        db.close()


def test_migration_019_blocks_nothing_without_a_readable_cv(tmp_path: Path) -> None:
    """No profile, no preferences: every offer must survive untouched."""
    db = Database(tmp_path / "e.db")
    try:
        body = "Ruolo di analisi. " * 20
        job_id = _seed_019(
            db, "Analista Napoli", "Naples, Campania, Italy", "In sede",
            body + " Richiesti almeno 5 anni di esperienza maturata.",
        )
        _rewind_to_before_019(db)

        apply_migrations(db.conn)

        row = db.conn.execute(
            "SELECT punteggio_ai, analysis_json FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        assert row[0] == 9
        assert json.loads(row[1]).get("blocchi", []) == []
    finally:
        db.close()
