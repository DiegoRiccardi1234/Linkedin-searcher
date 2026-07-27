# Security Notes

Job Finder is designed as a **localhost-only** app. Do not expose it on a public network without adding real auth.

## Secret storage

- LLM API keys are stored in `data/local_secrets.json` — **plaintext**. This is acceptable for single-user localhost use on a trusted machine; it is **not** safe on shared systems or cloud VMs. (Keys are *not* written to the SQLite `preferences` table, as an earlier version of this document claimed: `save_local_provider_keys` writes the JSON file only.)
- `POST /api/preferences` accepts only known preference keys (feature toggles, onboarding answers, scan defaults, UI state). It used to accept any key/value pair, which meant one unauthenticated local request could switch off Privacy Mode before a CV was sent to a model.
- To move to an encrypted store, integrate OS keyring (e.g. `keyring` PyPI) and migrate `save_local_provider_keys` accordingly.

## Network surface

- Uvicorn default binds `127.0.0.1:8000`. Never bind `0.0.0.0` without adding authentication.
- Scraping targets (LinkedIn, Indeed) are called directly from your IP. Excessive scans can trigger a ban. Fase 9 adds request pacing (0.8–2.4s between terms) and a canary that detects unexpected empty results.

## Request-side protections

- **Rate limiting** (`app/rate_limit.py`): sliding window per IP+bucket. Defaults:
  - `/api/chat` — 20 req/min
  - `/api/scan` — 5 req/min
  - `/api/upload-cv` — 10 req/min
  Disable for tests with `ENABLE_RATE_LIMIT=0`.
- **CV upload** validates extension (`.pdf/.docx/.md/.txt`) and size (5 MB cap).
- **Chat markdown renderer** always `escapeHtml`s content before applying minimal markdown transforms; no untrusted HTML reaches the DOM.

## Known risks

- Plaintext key storage (see above).
- No CSRF protection — localhost usage assumption.
- Scraper output is LLM-consumed; malicious listing descriptions could attempt prompt injection. Current system prompt asks the model to strictly output JSON; further hardening (input sanitization, prompt injection detection) is a future enhancement.

## Reporting

Local project, no coordinated disclosure process. Open an issue on the GitHub repo.
