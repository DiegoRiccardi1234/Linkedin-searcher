# Contributing

This is a personal portfolio project. The notes below describe the local dev workflow.

## Setup

```bash
python -m venv .venv
# Windows
.\.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt -r requirements-dev.txt
pre-commit install
```

## Daily workflow

| Command | What it does |
|---------|--------------|
| `make test` | pytest unit suite (fast, no network) |
| `make coverage` | pytest with coverage, term + HTML report under `htmlcov/` |
| `make lint` | `ruff check` + `mypy --strict` on `app/` |
| `make fmt` | `ruff check --fix` + `ruff format` on `app/` |
| `make e2e` | install Playwright + run end-to-end suite |
| `make run` | start uvicorn locally on `:8000` |
| `make docker` | `docker compose up -d --build` |
| `make docker-down` | tear down the compose stack |
| `make clean` | remove `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, coverage artifacts, `__pycache__` dirs |

## Quality gates

The CI workflow runs the same checks; matching them locally before pushing avoids surprises.

- Unit tests pass on Python 3.11 and 3.12.
- `ruff check app/` and `ruff format --check app/` are clean.
- `mypy app/` reports zero errors under strict mode.
- `python scripts/check_i18n.py` succeeds (every locale has the same key set as `en.json`).

## Commit style

Conventional Commits — `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`, `ci:`, `perf:`. Keep the subject under 72 characters; use the body to explain *why* a change was made when that is not obvious.

## Repository layout

See [README.md](README.md#project-structure) for the high-level tree. Key invariants:

- `app/` is the FastAPI backend; everything new should pass `mypy --strict`.
- `web/` is vanilla JS / CSS, no framework. ES modules under `web/modules/`.
- `tests/unit/` are LLM-free thanks to the `FakeProviderManager` fixture in `tests/unit/conftest.py`. Tests that need a real provider live in `tests/e2e/` and skip without `RUN_LIVE_LLM=1`.
- Schema changes go in `app/migrations/NNN_name.py` with `VERSION = NNN`, `DESCRIPTION`, `def upgrade(conn)`.
