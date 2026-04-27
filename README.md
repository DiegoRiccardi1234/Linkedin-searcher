# Job Finder — AI-Powered Job Search Assistant

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![SQLite](https://img.shields.io/badge/SQLite-WAL-003B57?logo=sqlite&logoColor=white)](https://sqlite.org)
[![CI](https://github.com/DiegoRiccardi1234/Linkedin-searcher/actions/workflows/tests.yml/badge.svg)](https://github.com/DiegoRiccardi1234/Linkedin-searcher/actions/workflows/tests.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/DiegoRiccardi1234/Linkedin-searcher/main/coverage.json)](https://github.com/DiegoRiccardi1234/Linkedin-searcher/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![AI Powered](https://img.shields.io/badge/AI-6%20LLM%20providers-blueviolet)](#supported-llm-providers)
[![Mypy: strict](https://img.shields.io/badge/mypy-strict-2A6DB2)](https://mypy.readthedocs.io/)
[![Ruff](https://img.shields.io/badge/ruff-checked-261230)](https://github.com/astral-sh/ruff)
[![Changelog](https://img.shields.io/badge/changelog-Keep%20a%20Changelog-orange)](CHANGELOG.md)

> A localhost-first AI-powered job search assistant. Scrape LinkedIn & Indeed, score offers against your CV with the LLM of your choice, and plan applications from a single dashboard.

---

## Why this project

I built Job Finder while preparing my own transition into IT. Existing job boards push generic listings and waste hours on roles that don't fit. I wanted a tool that:

- knows my CV and preferences,
- scrapes real listings,
- ranks them with an LLM I control,
- and keeps everything **on my machine** — no third-party dashboard owns my data.

The result is a portfolio-grade FastAPI app with a multi-provider LLM backbone, a chat-driven UX, and an honest fallback when the network or the model is down.

---

## Features

- **Smart CV analysis** — Upload PDF / DOCX / TXT; the LLM extracts skills, seniority, and ideal roles.
- **AI Career Coach** — Chat that learns your preferences, suggests search terms, and can autofill the scan form via structured `action` payloads.
- **Multi-source scan** — LinkedIn + Indeed in parallel, streamed via Server-Sent Events.
- **Personalized scoring** — Each job gets a 1-10 AI score with pros/cons and an apply/skip recommendation.
- **Kanban tracking** — Open → Applied → Interviewing → Rejected.
- **Cover-letter generator** — One-click, tailored to the job and your CV.
- **Multilingual UI** — English, Italian, Spanish, French, German (204 keys per locale, 100% parity).
- **Multi-LLM fallback** — Cerebras, Groq, OpenAI, Anthropic, Google, OpenRouter — configurable order.
- **Resilient by default** — Structured logging, no silent `except Exception`, WAL-mode SQLite, file size + MIME validation on uploads.

---

## Demo

**Dashboard** — personalized hero, live analytics, and an always-on AI Career Coach in the right rail.

![Dashboard](screenshots/readme/dashboard-en.png)

**Job Search wizard** — guided 3-step flow: analyze your CV, pick target roles from AI-suggested chips, launch the scan.

![Job Search](screenshots/readme/job-search-en.png)

> 🎯 Step 1 reads your active CV and surfaces matching role suggestions as clickable chips.
> ✨ Dark mode is included — one-click toggle in the top bar.

**AI Career Coach chat** — ask questions in plain language; the coach suggests target roles as clickable pills and can auto-fill the scan form.

![Chat](screenshots/readme/chat-view-en.png)

**Live scan progress** — during a scan you see each portal scraped, every job analyzed, and its score (green/yellow/red) in a live feed. Minimize it to keep browsing.

![Scan progress](screenshots/readme/scan-progress-en.png)

---

## Architecture

```mermaid
flowchart LR
    subgraph Browser
        UI[Vanilla JS UI<br/>i18n · Glassmorphism]
    end

    subgraph Backend["FastAPI backend (localhost:8000)"]
        API[REST + SSE endpoints]
        ChatSvc[chat package<br/>state · context · prompts<br/>intents · fallback · handler]
        ScanSvc[scanner_service<br/>analyze_offer · run_scan]
        CV[cv_ingest<br/>PDF/DOCX → markdown → LLM summary]
    end

    subgraph LLM["ProviderManager"]
        OR[OpenRouter]
        AN[Anthropic]
        OAI[OpenAI]
        GO[Google]
        GR[Groq]
        CB[Cerebras]
    end

    Scraper[python-jobspy<br/>LinkedIn + Indeed]
    DB[(SQLite WAL<br/>data/searcher.db)]

    UI <-->|fetch / SSE| API
    API --> ChatSvc
    API --> ScanSvc
    API --> CV
    ChatSvc --> LLM
    ScanSvc --> LLM
    ScanSvc --> Scraper
    CV --> LLM
    API --> DB
    ScanSvc --> DB
    ChatSvc --> DB
```

The chat service is split into single-responsibility modules:
`state` (chat state machine + preference extraction) ·
`context` (profile / preferences / jobs context blocks) ·
`prompts` (templates loaded from `app/prompts/chat/*.txt`) ·
`intents` (search / role-guidance heuristics) ·
`fallback` (rule-based answer when no LLM is available) ·
`handler` (orchestration + JSON-envelope parsing).

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+, FastAPI, uvicorn |
| Database | SQLite (WAL mode, `threading.Lock` shared connection) |
| Frontend | Vanilla JS (ES2020), CSS3 glassmorphism, no framework |
| AI / LLM | 6-provider factory: Cerebras, Groq, OpenAI, Anthropic, Google, OpenRouter |
| Scraping | [python-jobspy](https://github.com/Bunsly/JobSpy) |
| Streaming | Server-Sent Events |
| Testing | pytest (unit, 99 tests), Playwright (E2E) |
| Logging | stdlib `logging` + RotatingFileHandler → `data/logs/app.log` |

---

## Project structure

```
app/
├── main.py                  FastAPI app, AppContainer wiring
├── config.py                AppSettings + local secrets persistence
├── db.py                    SQLite Database (WAL + lock)
├── log.py                   Centralized logging setup
├── cv_ingest.py             CV → markdown → LLM summary + content validation
├── lifecycle.py             Post-scan retention/archive policy
├── models.py                Pydantic request/response models
├── rate_limit.py            Token-bucket limiter for /api/chat, /api/scan, /api/upload-cv
├── version.py               Version metadata + GitHub release checker
├── migrations/              Numbered SQLite schema migrations (idempotent runner)
├── prompts/chat/            System-prompt templates (.txt)
├── providers/               LLM factory + 6 provider implementations (retry + backoff)
└── services/
    ├── chat/                Chat package (state/context/memory/prompts/intents/fallback/handler)
    ├── chat_service.py      Backwards-compat facade
    ├── roles_shortlist.py   Role suggestion CRUD + dedup
    └── scanner_service.py   Job scraping + scoring orchestration
web/                         Vanilla JS UI + i18n JSON
tests/
├── unit/                    pytest unit tests (config, log, db, chat, factory)
└── e2e/                     Playwright end-to-end tests
scripts/check_i18n.py        i18n coverage audit (fails CI on missing keys)
```

---

## Quick start

### Prerequisites
- Python 3.11+
- At least one LLM API key (any of the 6 supported providers)
- Node.js (optional — only for Playwright E2E)

### Install & run

```bash
git clone https://github.com/DiegoRiccardi1234/Linkedin-searcher.git
cd Linkedin-searcher

python -m venv .venv
# Windows
.\.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
python run_webapp.py
```

Open **http://127.0.0.1:8000**.

### Run with Docker

```bash
cp .env.example .env   # add at least one LLM API key
docker compose up -d
```

The container exposes the app on `${PORT:-8000}` and persists the SQLite DB and logs in `./data/`. A built-in healthcheck pings `/api/health` every 30s.

### First-time setup

1. Open **Settings** and paste at least one LLM API key.
2. Upload your CV (PDF / DOCX / TXT, max 5 MB).
3. Chat with the AI Coach — it will ask about preferences.
4. Run a job scan from Settings (or let the chatbot pre-fill the form).
5. Review the dashboard and move jobs through the Kanban board.

---

## Localhost ≠ offline

The app runs entirely on your machine, but some features need internet:

| Works without internet | Requires internet |
|-------|-----------|
| UI navigation, filters, Kanban | Job scraping (LinkedIn / Indeed) |
| Local SQLite data | LLM chat / coaching |
| Existing scored jobs | AI scoring of newly scraped jobs |
| Manual status changes | Cover-letter generation |
| CSV export | Provider health checks |

When offline, online features fail gracefully and fall back to rule-based answers.

---

## Supported LLM providers

| Provider | Notes |
|----------|-------|
| **OpenRouter** | Single key, hundreds of models (Claude 4.x, GPT-5, Llama 4, Qwen 3, DeepSeek...) |
| **Cerebras** | Llama 4 Scout / Maverick, Qwen 3 235B — sub-second inference, free tier |
| **Groq** | Llama 4, Qwen 3, Kimi K2, DeepSeek — ultra-low latency |
| **OpenAI** | GPT-5, GPT-5 mini, o4-mini, o3 |
| **Anthropic** | Claude Opus 4.7, Sonnet 4.6, Haiku 4.5 |
| **Google** | Gemini 2.5 Pro & Flash, Gemini 2.0 Flash |

The `ProviderManager` picks the first available provider from your configured order, logs the choice, and exposes a `metadata()` endpoint for the UI status badge.

---

## API endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health + provider/key status |
| POST | `/api/upload-cv` | Upload CV (size + MIME validated) |
| GET | `/api/profile` | Active candidate profile |
| GET | `/api/scan/stream` | SSE-streamed job scan |
| GET | `/api/jobs` | List jobs with filters |
| GET | `/api/jobs/{id}` | Job detail + AI analysis |
| POST | `/api/jobs/{id}/cover-letter` | Generate cover letter |
| POST | `/api/jobs/{id}/action` | Set status (apply/skip/archive) |
| POST | `/api/chat` | Chat with AI Career Coach |
| GET | `/api/analytics` | Dashboard stats |
| GET | `/api/recommendations` | Top AI-recommended jobs |

---

## Testing

```bash
# Unit tests (fast, no network, no SDK dependencies)
pip install -r requirements-dev.txt
pytest

# i18n coverage audit
python scripts/check_i18n.py

# E2E (browser)
npm install
npx playwright install chromium
npm run test:e2e
```

The unit suite uses a `FakeProviderManager` fixture so it runs without any LLM API key.

---

## Logging

Logging is configured once in `AppContainer.__init__`. Output goes to stderr **and** to a rotating log file at `data/logs/app.log` (1 MB × 3 backups). Set `LOG_LEVEL=DEBUG` to see provider selection details.

```
2026-04-22 22:08:43 | INFO    | app.main              | AppContainer initializing
2026-04-22 22:08:48 | INFO    | app.providers.factory | LLM provider active: openrouter (model=anthropic/claude-sonnet-4-6)
2026-04-22 22:09:01 | WARNING | app.services.scanner_service | scrape_jobs failed (term='QA Tester'): TimeoutError
```

---

## Local data

Everything lives in `data/`:
- `searcher.db` — SQLite (WAL journal mode)
- `local_secrets.json` — provider API keys (gitignored)
- `settings.json` — user preferences
- `logs/app.log` — rotating application log

Back up the `data/` folder before major updates.

---

## Documentation

- [DB schema reference](DOCS/schema.md) — table-by-table breakdown of `data/searcher.db`.
- [Security notes](DOCS/security.md) — localhost threat model, secret storage, network surface.
- [Contributing guide](CONTRIBUTING.md) — local dev workflow, Makefile targets, quality gates.
- [Security policy](SECURITY.md) — how to report vulnerabilities, scope, threat model.
- [Changelog](CHANGELOG.md) — release history.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full release history.

---

## License

MIT — see [LICENSE](LICENSE).
