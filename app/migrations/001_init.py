"""Initial schema: jobs, scan_runs, candidate_profiles, chat_messages, preferences, job_actions."""

from __future__ import annotations

import sqlite3

VERSION = 1
DESCRIPTION = "Initial schema"


_SCHEMA = """
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


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
