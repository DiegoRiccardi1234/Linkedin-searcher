# Database Schema

SQLite single-file DB at `data/searcher.db`. WAL journal mode. Schema evolves through migrations in `app/migrations/`.

## Tables

### `jobs`
One row per scraped offer. De-duplicated by `job_hash`.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | autoincrement |
| job_hash | TEXT UNIQUE | hash of url/title+company |
| titolo, azienda, descrizione, sede | TEXT | listing fields |
| fonte, link, ricerca_usata, modalita | TEXT | metadata |
| analysis_json | TEXT | LLM analysis (JSON string) |
| punteggio_ai | INTEGER | 0–10 |
| consiglio | TEXT | apply/evaluate/skip |
| status | TEXT | open / applied / interviewing / rejected / archived |
| is_favorite, is_new | INTEGER | bool flags |
| first_seen_at, last_seen_at, analyzed_at, updated_at | TEXT | ISO timestamps |
| analysis_v | INTEGER | scoring schema version; a stale one is re-scored on the next scan |
| analysis_model | TEXT | `provider/model` that produced the verdict (v2.0.0+, NULL for older rows) |
| dedup_key, sources_json | TEXT | cross-source grouping ("also on Indeed") |
| link_opened_at, link_open_count | — | outbound clicks, used to spot an application you never marked |
| apply_confirmed_by, apply_confirm_message_id | TEXT | `email` when a confirmation marked it, and which message did — so it stays reversible |
| reminder_at, reminder_note | TEXT | follow-up nudges |

### `scan_runs`
Scan lifecycle tracking.

### `candidate_profiles`
Uploaded CVs. `summary_json` holds `skills`, `preferred_roles`, `experience_level`, etc.

### `chat_messages`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| session_id | TEXT | |
| role | TEXT | user / assistant / system |
| content | TEXT | |
| content_type | TEXT | `message` or `summary` (see Fase 6) |
| created_at | TEXT | ISO timestamp |

### `preferences`
Key-value store (TEXT/TEXT). Keys used:
- `active_profile_id`, `ui_language`, `linkedin_url`, `remote_mode`, `min_ral`, `prefer_role_qa|cyber|data`, `last_scan_location`
- `role_shortlist` (JSON string of strings)
- API key slots: `cerebras_api_key`, `groq_api_key`, …

### `job_actions`
Audit trail of state transitions per job.

### `schema_version`
Tracker written by `app/migrations/__init__.py`.

### `usage_log`
One row per LLM call — the app's memory of what actually worked.

| Column | Type | Notes |
|---|---|---|
| ts | TEXT | ISO timestamp |
| provider, model, endpoint | TEXT | `endpoint` is `complete_json`, `chat`, … |
| prompt_tokens, completion_tokens, total_tokens | INTEGER | as reported by the provider |
| success | INTEGER | bool |
| error_type | TEXT | `<classified cause>:<ExceptionClass>` on failure, e.g. `rate_limit:HTTPStatusError`. Both halves are read: the scoreboard matches the class name, the rate-limit learner matches the cause |
| duration_ms | INTEGER | round trip |

Read by `model_scoreboard` (which model to trust), `rate_limits` (what a free tier really allows this key), `provider_advice` (which provider to recommend) and the AI Usage card.

### `mail_seen`
Messages the mailbox watcher has already judged, so a re-sweep does not ask twice. `verdict` records what it decided; `job_id` is set when the message was matched to an offer. **No subject and no body is ever stored.**

### `mail_review`
The proposals waiting for a human decision — persisted, so closing the app does not empty the queue.

| Column | Type | Notes |
|---|---|---|
| kind | TEXT | `attach` (could belong to an offer in the archive) or `import` (a company the archive never knew) |
| company, sender, role | TEXT | the employer, the address that wrote, and the job title read from the body |
| rule | TEXT | which recognition rule fired |
| candidates_json | TEXT | job ids it could be about; resolved at read time, so a deleted offer disappears from the choices |

### `score_feedback`
When you disagree with a score: the verdict, what you would have given, and which model wrote the original.

### `watchlist_companies`
Companies to follow. A scan keeps their postings even when the search terms would not have found them.

### `saved_searches`
Named scan presets (`config_json` holds the whole filter set).

### `chat_sessions` / `pinned_jobs`
Multiple coach conversations, and the offers pinned into one of them.

### `recruiters`
Best-effort recruiter details scraped from a posting, cached per job.

## Migrations

29 numbered modules today; `ls app/migrations/` is the authoritative list.

- `001_init.py` — all initial tables.
- `002_chat_message_type.py` — adds `chat_messages.content_type`.
- `023`/`025`/`026` — the mailbox tables above.
- `027`–`028` — degree subject, salary floor, driving licence: requirements that were declared and never checked.
- `029_analysis_model.py` — `jobs.analysis_model`, the model that wrote a verdict. Only filled from v2.0.0 on: attributing older rows by looking at what `usage_log` was doing nearby would be a guess dressed as a fact.

Add new migrations as `NNN_name.py` exposing `VERSION`, `DESCRIPTION`, `upgrade(conn)`. They run in order; baseline detection skips them on pre-existing production DBs.
