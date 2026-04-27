# Security Policy

## Supported versions

Latest `main` branch only. No backports.

## Reporting a vulnerability

Email: **superdiego135@gmail.com**
Subject prefix: `[SECURITY] Job Finder`
Expected initial response: within 72 hours.

Please do not open public GitHub issues for suspected security bugs.

## In scope

- Code in this repository
- Default configuration shipped with the project
- Documented setup paths (Docker, venv)

## Out of scope

- Third-party LLM providers — report directly to them
- [`python-jobspy`](https://github.com/Bunsly/JobSpy) upstream
- User-misconfigured deployments (custom reverse proxies, exposed ports, weak host firewalls)

## Threat model

Job Finder is designed for **localhost** use. It assumes a single trusted user on the host and stores LLM API keys in plaintext under `data/local_secrets.json` (gitignored). Exposing the app on a public network without an authenticating reverse proxy is unsupported.

Concretely, the codebase actively defends against:

- Unbounded uploads — `/api/upload-cv` rejects files over 5 MB, validates the extension and the extracted text.
- Per-IP abuse — `app/rate_limit.py` token bucket guards `/api/chat`, `/api/scan`, `/api/upload-cv`.
- Dependency-injection style attacks via JSON envelopes — chat replies are parsed with strict structure validation.

It does *not* defend against an attacker who already has shell access to the host.
