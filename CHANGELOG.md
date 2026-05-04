# Changelog

## [Unreleased]

## [1.2.6] — 2026-05-05

Visible app version, manual update check, log access for support.

### Added
- **Topbar version chip** — `<span class="version-chip">vX.Y.Z</span>` next to the "Job Finder" brand. Populated at boot from `/api/version`. Users always know which version they're running without opening Settings.
- **Settings → "System" card** — current version, last-check timestamp + result, "Check for updates" button, "Open logs folder" button. The check button calls `checkForUpdate({forceRefresh: true})` which forwards `?refresh=true` to `/api/version` and bypasses the 1 h `_cache` in `app/version.py` so the user gets a real GitHub round-trip on demand.
- **`POST /api/system/open-logs` endpoint** (`app/main.py`) — opens `data/logs/` in Windows Explorer via `os.startfile`. Returns 501 on non-Windows. Backed by 2 unit tests in `tests/unit/test_open_logs_endpoint.py` (157 → 159 total).
- **Update modal error state now shows "Open logs folder"** — when any step transitions to `error`, the verbose log block becomes clickable and a `→ Open logs folder for details` line is appended. One click opens `data/logs/` so the user can grab `updater.log` for support without hunting through `data\logs\` by hand. The handler is reset on each new `runUpdate()` call so the link is re-arm-able after a retry.

### Changed
- **`checkForUpdate()` is now a Promise that resolves to the version info** — was previously fire-and-forget. The Settings check button awaits it to render the result inline.

## [1.2.5] — 2026-05-05

Updater resilience against Windows Defender file scans.

### Fixed
- **`PermissionError(13)` persisted past the v1.2.1 retry budget** — three real-world update attempts each failed exactly 7 s after `replace_start` (= sum of the v1.2.1 backoff `1 s + 2 s + 4 s`). Likely cause: Windows Defender pre-scanning the freshly-extracted bundle (175 MB → ~10–20 s scan). Extended `_COPY_RETRY_DELAYS` in `app/update_sync.py` from `(1, 2, 4)` to `(1, 2, 4, 8, 16)` — five attempts spread over ~31 s, comfortably outlasting a typical AV scan window.
- **The retry-exhausted error now names the file that stayed locked** — replaced the bare `PermissionError` re-raise with one that carries `… (locked after 5 retries): D:\…\JobFinder.exe`. When updates fail again in the wild, the log identifies which file (almost always `JobFinder.exe` itself, or an OCR child) was the holdout. Previously the user only saw `Permission denied` with no file context.

### Added
- **3 s grace period after parent exit before sync starts** (`scripts/updater.py`) — `_wait_for_pid()` returns the moment the PID dies, but Windows can take a few more seconds to flush all inherited handles (uvicorn workers, Tesseract subprocess, AV pre-scan handles). The first file copy now waits 3 s after `parent_exited` instead of racing in immediately. Combined with the extended retry, the worst-case wait against AV is `3 s + 31 s = ~34 s` before the updater gives up — long enough for Defender to release locks on consumer hardware.

## [1.2.4] — 2026-05-05

Critical updater fix: restart now actually persists after Updater exits.

### Fixed
- **JobFinder.exe died seconds after restart, leaving the modal stuck at "Riavvio 95%"** — `scripts/updater.py` spawned the new JobFinder.exe with `subprocess.Popen([str(exe)])` and no `creationflags`. On Windows, `JobFinder.exe` is built with `console=True`, so the new process inherited Updater's console. When Updater returned and its cmd window closed, the JobFinder console closed with it and the just-spawned process died — port 8000 never came back up, the frontend health-poll loop spun until the 600 s timeout, and the user had no app. Fix: detach the restart with `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` (mirrors the flags `app.main.start_bundle_update` already uses to spawn `Updater.exe` from `JobFinder.exe`).

## [1.2.3] — 2026-05-05

Update banner readability + recover from stuck retry state.

### Fixed
- **Inline `<code>` segments inside the in-app release notes are now legible** — the v1.2.0 banner rendered backtick-fenced strings (`── Free ──`, `<option>`, locale keys, etc.) with a `<code>` element whose default background nearly matched the purple banner gradient, so users saw blank patches instead of text. Added explicit `background: rgba(0,0,0,0.35)`, white foreground, and a thin border so code reads on every banner state.
- **"Update now" button gets stuck after a closed modal or failed update** — when the user closed the update modal mid-run (X button) or the update errored, `localStorage["updateInProgress"]` and the backend lockfile both stayed set, so the next click was a no-op or returned HTTP 409. Modal close now clears both: it removes the localStorage flag, re-enables the button, and fires `DELETE /api/update/lock` to force-clear the backend lockfile.

### Changed
- **Backend lockfile TTL 300s → 60s** — 5 minutes was overkill for the legitimate "prevent double-spawn" case (parallel updaters fight within seconds, not minutes) and made it impossible to retry a failed update without sitting on your hands. 60s is enough to dampen accidental rapid double-clicks while keeping retry latency low.

### Added
- **`DELETE /api/update/lock` endpoint** — explicit force-clear of `data/update.lock`, called by the frontend on modal close. Safe at this point: either the updater succeeded (lockfile already gone) or it crashed (no live updater process to fight us for files).

## [1.2.2] — 2026-05-04

Settings model picker readability.

### Changed
- **OpenRouter shows all 371 models, sorted by tier then alphabetical** — replaced the v1.2.0 "Free only" toggle with a visible grouping. Models render as `── Free ──` then alphabetical free entries, then `── Paid ──` then alphabetical paid entries. Disabled `<option>` elements act as section headers. The search input still narrows by substring across both groups.
- **Other providers now sort alphabetically** — Cerebras, Groq, OpenAI, Anthropic, Google all render their model dropdowns in alpha order. The recommended ⭐ model still floats to the top regardless of name. Previously the order was whatever the provider API returned (insertion order, often arbitrary).

### Added
- Locale keys `settings.providers.freeGroup` / `paidGroup` for the OpenRouter section headers.

## [1.2.1] — 2026-05-04

Update flow reliability and UX polish. Driven by a real-world failure where v1.1.1 → v1.2.0 produced two parallel `Updater.exe` processes both racing on `JobFinder.exe` file locks (`PermissionError(13)`) and a 180 s timeout that wasn't enough for slow GitHub downloads of the 175 MB bundle.

### Added
- **Percent on the active step** — the v1.2.0 step indicator now shows the live percentage (5/10/15/50/55/70/75/90/95/100) returned by `/api/update/progress` next to the active label, e.g. *"Downloading new version · 35%"*. Step shows nothing once `done`.
- **Elapsed counter during health-poll wait** — replaced the dot-spam (`....................`) that grew while waiting for the new process to come back, with a single rewritten line `Elapsed: Xs`. No more wall of dots; users can see the wait advancing.
- **Update lockfile guard** — `POST /api/update/start` now writes `data/update.lock` (PID + target version, mtime as TTL marker) and refuses with HTTP 409 + `{code: "update_already_in_progress"}` if a second start arrives within 5 minutes of an existing one. Updater clears the lockfile on success or on any caught exception. Prevents the double-spawn race.
- **Frontend double-click guard** — the "Update now" button disables itself on click and writes `localStorage["updateInProgress"]` keyed by target version. A second click on the same version is a no-op until the page reloads or the flag is cleared. The flag is cleared on success, on timeout, and on any thrown error from `runUpdate`.
- **Retry-on-PermissionError in `sync_install_dir`** — `_copy_with_retry()` wraps `shutil.copy2` with a 3-step backoff (1 s / 2 s / 4 s) so a transient antivirus scan or a still-draining process handle no longer aborts the whole update. After the final retry, the error is propagated as before.

### Changed
- **Frontend update timeout 180 s → 600 s** — covers slow networks where a 175 MB bundle takes > 3 min to download. Elapsed counter makes the long wait observable.

### Fixed
- **Double-spawn updater race that produced `PermissionError` on `JobFinder.exe`** — root cause of the v1.1.1 → v1.2.0 update failures Diego observed (two updater PIDs spawned 35 s apart, both failed at the copy step).
- **Restart step now correctly transitions to "done" with 100%** before the page reload kicks in, instead of staying at the `active` pulsing state.

### Tooling
- 2 new tests in `tests/unit/test_update_sync.py` covering retry-on-PermissionError success and exhausted-retries propagation. Test count 155 → 157.

## [1.2.0] — 2026-05-04

UX release focused on visible release notes, a clear update flow, and a saner model picker.

### Added
- **In-app release notes** — the "Update available" banner now includes a `<details>` element rendering the release notes pulled from `/api/version` (`release_notes` field). Users see *what's new* without leaving the app. Markdown rendered via the existing `renderCoachMarkdown()` helper.
- **GitHub Release pages now show Added/Changed/Fixed bullets directly** — `release.yml` extracts the matching `[$version]` section from `CHANGELOG.md` via PowerShell regex and passes it as `body_path` to `softprops/action-gh-release@v2`. No more empty release pages with only a "Full Changelog" compare link.
- **Update progress modal with 4 step indicators** — when the user clicks "Update now", a new dialog (`#updateModal`) shows live progress through Download → Verify → Replace → Restart, driven by the new `GET /api/update/progress` endpoint that parses structured `EVENT {...}` JSON lines from `data/logs/updater.log`. Includes a hint text "The app will restart automatically and this page will reload" so users know what to expect.
- **OpenRouter search + free-only filter** — for providers exposing more than 30 models (only OpenRouter today, with 371 entries), the Settings card now shows a search input and a "Free only" checkbox above the model dropdown. The filter is applied client-side over the cached model list. Default state is "Free only" enabled, so first-time users immediately see the cheapest options.
- **Smarter Auto model picker** — `app/providers/model_selector.score_model_name()` now penalizes hard-avoid patterns (`embed`, `whisper`, `tts`, `dall-e`, `moderation`, `audio`) with `-1000`, soft-avoid (`preview`, `deprecated`, `experimental`, `alpha`) with `-50`, and rewards OpenRouter `:free` suffix with `+25`. New helper `pick_default_model()` filters out non-chat models entirely before ranking, so an embedding-only key never resolves to a chat default. 8 new unit tests in `tests/unit/test_model_selector.py`.

### Changed
- **`app/version.py`**: `release_notes` truncation raised from 500 to 2000 characters so a typical release section fits without being cut mid-sentence.
- **`scripts/updater.py`**: emits structured `EVENT {...}` JSON lines alongside the existing human-readable log, covering `started`, `parent_exited`, `download_start/done/skipped`, `verify_start/done`, `replace_start/done`, `restart_spawned`, `error`. Backwards-compatible: the human log lines remain unchanged.
- **`.github/workflows/release.yml`**: removed `generate_release_notes: true` (which only produced a "Full Changelog" auto-link) in favor of `body_path: release-notes.md` produced by the new extraction step.

### Fixed
- **Release notes invisible on GitHub Releases** — pages for v1.0.0/v1.1.0/v1.1.1 only showed a "Full Changelog: …" compare link with no content. From v1.2.0 onwards, the body is the actual `CHANGELOG.md` section.
- **Update flow appearing to hang** — previously the modal showed a single text blob ("Downloading update...") for the entire process, leaving users uncertain whether anything was happening. The new step indicator shows live state.

### Tooling
- 8 new tests covering the model picker (147 → 155 total).
- Ruff, mypy strict, format clean.

## [1.1.1] — 2026-05-04

Hotfix release focused on the bundled `Updater.exe` UX.

### Fixed
- **`Updater.exe` no longer flashes a console window** — `JobFinder.spec` now builds the updater with `console=False`. When the updater is invoked correctly by JobFinder (via `POST /api/update/start`), the user sees no transient cmd window.
- **Friendly dialog when `Updater.exe` is double-clicked** — `scripts/updater.py:main()` now detects the no-args case and shows a Windows MessageBox: *"Updater.exe is launched automatically by JobFinder. Open JobFinder.exe and click 'Update now' from the update banner."* Replaces the previous silent argparse crash that left users wondering why the cmd window vanished.

### Notes
- Update detection in v1.0.0 / v1.1.0 requires the GitHub repository to be **public** so the unauthenticated `_fetch_latest_release()` call can read `/releases/latest`. Private repos return 404 and `update_available` stays `false`.

## [1.1.0] — 2026-05-04

Quality release focused on log-spam fix, true internationalization, soft onboarding, and a token-usage tracker.

### Added
- **Token usage tracker** — every `chat` / `complete_text` / `complete_json` call now records `prompt_tokens / completion_tokens / total_tokens` per `(provider, model, endpoint)` into the new `usage_log` table. New endpoint `GET /api/usage/stats?range=today|week|month|all` returns aggregates with per-provider and per-day breakdowns. No pricing/cost — just raw counts (deferred to v1.2.0). Migration `004_usage_log.py`. Unit tests in `tests/unit/test_usage_tracker.py`.
- **OCR multi-lingua** — `app/cv_ingest._ocr_image_bytes` and `_extract_text_pdf_via_ocr` now read the language list from `JOBFINDER_OCR_LANG` env var (set by `AppContainer` from `settings.ocr_languages`). Default `eng+ita+spa+fra+deu`. Bundle ships 5 traineddata files (`scripts/build_exe.py:_REQUIRED_LANGS` extended).
- **Browser locale auto-detect** — `web/modules/i18n.js` falls back to `navigator.languages` instead of always defaulting to English. First-run users with a Spanish/French/German/Italian browser see the UI in their language immediately. Stored preference still wins over auto-detect.
- **Soft onboarding gate** — `GET /api/setup/status` returns `{ready, provider_configured, cv_loaded, first_run}`. Frontend tracks `_setupReady`; `activateView()` redirects non-Settings tabs to Settings while no provider key is configured. Banner is now non-dismissable (close button removed). Tabs get a `tab-locked` CSS class with a 🔒 badge while gated.
- **Backend 412 guard** — `/api/chat` and `/api/scan` return HTTP 412 with `{code: "no_provider_configured"}` when no provider key is configured, protecting against direct API hits even if the UI gate is bypassed.
- **Provider invalid-key flag** — new `LLMProvider.key_invalid` attribute (set on HTTP 401, cleared on key reload via `ProviderManager.invalidate_caches()`). Stops the factory from re-attempting list_models on every health poll.
- **`extract_usage()` helper** in `app/providers/base.py` — best-effort token-usage extraction across heterogeneous SDK shapes (OpenAI/Groq/Cerebras/OpenRouter `usage`, Anthropic `input_tokens`/`output_tokens`, Google).
- **Expanded CV keyword dictionary** — added 9 Spanish, 9 French, 8 German keywords (`habilidades`, `competénce`, `kenntnisse`, etc.) so the validation gate is balanced across the 5 supported locales (was Italian-heavy in v1.0).
- **Spanish/French/German CV fixtures** in `tests/unit/test_cv_ingest.py` — `validate_cv_content_accepts_spanish_cv` + French + German tests confirm cross-locale acceptance.

### Changed
- **`metadata()` is now cached for 60 seconds** (`app/providers/factory.py:_metadata_cache`). Each `/api/health` poll used to call `provider.list_models()` 6× (one per provider with a key). Now a single cached payload is returned until the TTL expires or `invalidate_caches()` is called after a key save.
- **Bundle Tesseract from 3 to 6 traineddata files** (`scripts/build_exe.py:_REQUIRED_LANGS = ("eng", "ita", "spa", "fra", "deu", "osd")`). +10-12 MB zip size (~200 MB total).
- **No-API-key banner is now non-dismissable** — close button removed; banner clears itself once `loadHealth()` sees a configured provider.
- **Version aligned to 1.1.0** across `app/version.py` + `pyproject.toml`.

### Fixed
- **Cerebras 401 spam in logs** — when a stale Cerebras key was loaded from `data/local_secrets.json`, the app emitted "Cerebras SDK list_models failed (401)" + "Cerebras HTTP list_models failed (401)" on every health poll (≈1× per second). Root cause was triple: (1) `metadata()` lacked TTL caching, (2) `is_available()` returned True regardless of key validity, (3) `list_models()` retried both SDK and HTTP paths without remembering the failure. All three are now mitigated. Single 401 line is logged on first attempt, then the provider is marked `key_invalid` and silenced until the user re-saves keys.
- **Hardcoded `lang="ita+eng"`** in `cv_ingest._ocr_image_bytes` and `_extract_text_pdf_via_ocr` — the app no longer assumes Italian for OCR.

### Tooling & Quality
- Test count: **134 → 147 passing** (13 new tests for metadata cache, key_invalid flag, usage tracker, and 4 i18n CV fixtures).
- `ruff check app/ tests/` ✅, `ruff format` ✅, `mypy --strict` ✅ on 39 source files.

### Known limits
- Pricing/cost estimation deliberately excluded from v1.1.0. Pricing tables drift fast across providers; v1.2.0 will add an opt-in cost layer.
- Welcome modal (3-step locale + key + CV picker) not shipped — the soft gate alone covers the gap. May land in v1.1.1.
- Poppler still not bundled, so scanned PDFs (vs. image CVs) remain a lossy path.

## [1.0.0] — 2026-05-04

First stable public release. Adds OCR for image CVs and scanned PDFs, ships a refreshed Profile/Job Search UX, and consolidates the standalone Windows bundle.

### Added
- **OCR pipeline for CV ingest** (`app/cv_ingest.py`): images (`.jpg/.jpeg/.png/.webp/.avif/.tiff/.bmp/.svg`) are routed through Tesseract via `pytesseract`. Scanned PDFs fall back to `pdf2image` rasterization + OCR when `pypdf` returns < 50 chars. AVIF supported via `pillow-avif-plugin`. SVG with inline `<text>` tags parsed directly; full-graphic SVG returns empty (documented limit).
- **Tesseract bundling**: `scripts/build_exe.py:_bundle_tesseract()` copies the system Tesseract install (binary + `tessdata/` ita+eng) into `dist/JobFinder/vendor/tesseract/`. `cv_ingest._resolve_tesseract_cmd()` searches override env, bundle path, system PATH, and Windows default install dirs in that order.
- **CI Tesseract install**: `.github/workflows/release.yml` now `choco install tesseract` before `python scripts/build_exe.py` so the release zip ships with OCR ready.
- **Italian/EN/ES/FR/DE years phrase parser** (`_estimate_years_from_phrases`): captures explicit `Opero da N anni`, `Lavoro da N anni`, `Over N years of experience`, `experiencia de N años`, etc. Combined with the date-range parser via `max()` so explicit phrases never lose precedence to short overlap intervals.
- **Expanded CV keyword dictionary** for content validation: now includes `abilitazion`, `qualifica`, `carriera`, `studi`, `diploma`, `laurea` plus ES/FR/DE keywords, so OCR-noisy CVs (academic, vocational) pass the keyword gate.
- **Image-format hint in CV upload**: `web/index.html` `cvFile` input `accept=` lists every supported format; `cv-dropzone-hint` reads `PDF · DOCX · MD · TXT · IMG (JPG/PNG/AVIF)`.
- **Job deletion UI** (`53e286c`): per-row trash button in the jobs list with confirmation. Cascades through `Database.delete_job()`.
- **CV deletion / Multi-CV history controls** (`0413aaa`, `f3b1c7b`): delete CVs from the Profile tab history.
- **Language extraction from CV** (`45eed86`): `_extract_languages()` parses the dedicated Languages section (5-locale headers) into chips like `Italiano (Madrelingua)`, deduped case-insensitively. Surfaces in Profile chip-list and the LLM summary.
- **Role quick prompts in chat** (`d0097de`): CV-derived prompt suggestions appear as clickable pills above the chat input.
- **Auto-save chips on Profile** (`522e012`, `e2df912`): `preferred_roles` / `skills` / `languages` chip edits PATCH the active profile inline; chat suggestions stay in sync via the same store.
- **Job Search auto-detect experience** (`36b00d9`): flat layout, no wizard stepper. Profile-derived role chips populate `wizardRoleSuggestions` directly; clicking a chip adds it as a keyword tag.
- **6 new regression tests** (`tests/unit/test_cv_ingest.py`): word-boundary skill matching, no hardcoded fallback role, Italian years phrase, English `Over N years` phrase, max(date_intervals, phrase_years), data-analyst trigger expansion. Plus 3 OCR routing tests with mocked `pytesseract`. Suite **134/134**.

### Changed
- **Heuristic skill matching now requires word boundaries** (`_keyword_present()` with `(?<![a-z0-9])kw(?![a-z0-9])`). Previously `soc` matched inside `associato`, `git` inside `logistica`, `api` inside `capi` — non-tech CVs received fake tech skills. Same boundary rule applied to `role_map` triggers, so non-tech CVs no longer default to `Junior SOC Analyst`.
- **`data analy` trigger** split into 3 explicit triggers (`data analyst`, `data analysis`, `data analytics`) so the new word-boundary rule still maps Data-related CVs to the Data Analyst role.
- **Job Search wizard removed** in favor of a flat single-card layout (`web/index.html`). Removed selectors: `#wizardAnalyzeBtn`, `.wizard-steps`. Kept selectors: `#wizardProfileSummary`, `#wizardRoleSuggestions` (now populated automatically on view-enter).
- **README**: Demo section rewritten for the flat layout (6 beats, not 7); Features lists OCR + every new behavior; Tech stack and Project structure updated; Prerequisites mention Tesseract install per OS.
- **Version aligned** across `app/version.py` (was `0.1.0`) and `pyproject.toml` (was an out-of-sync `0.3.0`) → both now `1.0.0`.

### Fixed
- **`years_experience` ignored Italian phrases** (`d2c4db4` + this release): `Opero da 7 anni` now returns `7`, not `0`. Date-range scoping to the work section (commit `d2c4db4`) avoids false positives from graduation years; explicit-phrase parser added to fill the remaining gap.
- **Heuristic CV summary false skills** on non-tech CVs (see "word boundaries" above).
- **Hardcoded `Junior SOC Analyst` fallback** that surfaced on empty templates and non-tech CVs.
- **Generic 415 error message** for unsupported uploads now lists image formats so users know they can retry.
- **Playwright specs** (`tests/e2e/readme-demo-gif.spec.js`, `tests/e2e/readme-demo-screenshots.spec.js`): removed `#wizardAnalyzeBtn` clicks; the flat Job Search now scrolls into view directly.

### Tooling & Quality
- `requirements.txt`: + `pytesseract>=0.3.10`, `pdf2image>=1.17.0`, `Pillow>=10.0.0`, `pillow-avif-plugin>=1.4.0`.
- Test count: **122 → 134 passing** (~10% growth, all new tests cover regressions or new OCR routing).
- LLM retry callback (`d2c4db4`): up to 5 attempts, progressive 3/5/7/9 s waits, optional `on_retry(attempt, wait, exc)` for UI streaming.

### Known limits
- SVG CVs without inline `<text>` (pure vector artwork) fall through OCR with empty result. Workaround: convert to PNG/JPG before upload. Adding `cairosvg` rasterization is tracked for a later release because of the GTK runtime dependency on Windows.
- OCR quality on low-DPI scans can lose keyword matches; the expanded keyword dictionary mitigates this but doesn't eliminate it. Best results: 200+ DPI scans, well-lit photos.
- `pdf2image` requires Poppler. The standalone bundle does not yet ship Poppler, so scanned PDFs (vs. image CVs) will only OCR if the user has Poppler on PATH. Tracked for v1.0.x.

## [0.1.0] — 2026-04-28

First public release. Standalone Windows bundle, self-update, multi-LLM career-coach chat, scan, kanban, analytics, AI Provider cards, Profile tab.

### Added
- **AI Provider cards** (Settings): six per-provider cards (Cerebras, Groq, OpenAI, Anthropic, Google, OpenRouter) replace the flat keys form. Each card has its own state machine (empty / configured / fetching / error / active), per-provider Save & fetch, password-visibility toggle, primary radio, ⭐-recommended model dropdown, and a refresh button. Driven by `GET /api/providers/{name}/models` with a 5-minute TTL cache.
- **Chat per-model selector**: `#chatModelSelectorModel` next to the provider override, populated live from cached provider models. Provider override list filters to providers with a key (others shown as "(no key)" disabled). `/api/chat` accepts an optional `model` field that flows through `handle_chat_message` → `provider_manager.chat(model_name=…)`.
- **"Use as default?" toast**: shown once per session after the first chat override; persists `primary_provider` + `preferred_model` via `POST /api/providers/keys` on confirm.
- **Profile tab** (`#view-profile`, new module `web/modules/profile.js`): read-only view of the AI-summarized CV (preferred_roles, skills, languages, experience, original markdown), inline chip-list edit for the three list fields, CV history accordion with **Set active** per uploaded CV.
- **`PATCH /api/profile`**, `GET /api/profiles`, `POST /api/profiles/{id}/activate` + `Database.update_candidate_profile_summary`: updates the active profile's summary; `preferred_roles` changes also sync to the `preferred_roles` preference used by the role shortlist.
- **Unit test suite** (`tests/unit/`): coverage for chat context, handler parsing, fallback, CV ingest, scanner helpers, role shortlist, migrations, memory summarizer, provider retry, rate limiter, scraper canary.
- **Schema migrations** (`app/migrations/`): lightweight `schema_version`-tracked migrations, baseline detection for pre-existing DBs. 001 init schema, 002 `chat_messages.content_type`.
- **Role shortlist service** (`app/services/roles_shortlist.py`): dedicated module + `/api/roles/shortlist` GET/POST/DELETE. Dedup case-insensitive.
- **Career Coach UX**:
  - CV-derived + localized quick prompts (`/api/chat/prompts?lang=`).
  - Markdown rendering in coach bubbles (**bold** role names, *italic* hints, `code`, `-` bullet lists).
  - Role pills: clickable suggestions that add keywords to Step 2 without launching the search.
  - `suggested_roles` field in chat JSON envelope.
  - Conversation summarizer: condenses sessions >20 messages into a summary memory row.
- **No-API-key banner**: sticky warning when zero providers are configured.
- **Radar chart** (Chart.js) in job detail: skills / seniority / remote / salary / contract axes. Backend `match_axes` in `analyze_offer`.
- **Analytics**: `top_companies` widget with horizontal bar chart.
- **Export applications**: `GET /api/applications/export?format=csv|json` with tracking-relevant columns.
- **Onboarding wizard**: 3-step welcome overlay, localized in 5 languages, surfaces only when no CV loaded.
- **Fluid layout**: clamp-based typography + grid columns; design now scales with viewport without fixed breakpoints below 960px.
- **i18n**: `coach.expand/collapse/savedToShortlist`, `onboarding.*`, `banner.*`, `analytics.topCompanies`, `offcanvas.breakdown` + axis labels, `settings.providers.*` (19 keys), `chat.modelOverride/providerOverride/modelAuto/saveAsDefault/saveAsDefaultBody`, `common.yes/no`, `profile.*` (24 keys), and `topbar.profile` across en/it/es/fr/de — 259 keys per locale, 100% parity.
- **Unit tests** for the new endpoints: `tests/unit/test_providers_models_endpoint.py` (8 tests, including TTL cache hit + `force_refresh` bypass) and `tests/unit/test_profile_endpoint.py` (9 tests, including PATCH preference sync and CV-switch via `POST /api/profiles/{id}/activate`).

### Changed
- **Provider calls** retry on 429/5xx/timeout with exponential backoff + jitter (`LLM_MAX_RETRIES`, `LLM_RETRY_BASE_SECONDS` env).
- **Rate limiter** (`app/rate_limit.py`): in-process sliding window on `/api/chat` (20/min), `/api/scan` (5/min), `/api/upload-cv` (10/min). Toggle with `ENABLE_RATE_LIMIT`.
- **Scraper pacing**: random 0.8–2.4s sleep between terms; `canary_warning` SSE event when a common keyword returns zero results.
- **Frontend**: `app.js` entry is now an ES module; shared helpers extracted to `web/modules/helpers.js`, `shortlist.js`, `theme.js`. Chat styles moved to `web/styles/chat.css`.
- **Chat JSON envelope**: clarified formatting rules (markdown markers) and documented `suggested_roles` shape.
- **Prompts**: `advising.txt` / `onboarding.txt` include a "Role exploration" section guiding CV-aware pivots.
- **E2E**: `chat-role-guidance` and `live-cv-chat-search` now skip by default; opt in with `RUN_LIVE_LLM=1`. New `chat-live-smoke.spec.js` + `live-smoke.yml` manual workflow.

### Removed
- Chat expand/collapse toggle (layout is now fully fluid).

### Tooling & Quality
- **Toolchain**: `pyproject.toml` consolidates ruff, mypy strict, pytest, and coverage config. `.pre-commit-config.yaml` adds whitespace, ruff (lint + format), and mypy hooks; `pytest.ini` removed.
- **Mypy strict**: full pass on `app/`. New `Callable[[], _RetryT] -> _RetryT` generic on `_with_retry`, `cast()` wrapping for SDK and `json.loads` Any leakage, typed lifespan/SSE generators in `main.py`.
- **Coverage**: CI runs `pytest --cov=app --cov-report=xml`, `scripts/coverage_badge.py` generates `coverage.json` for a self-hosted shields.io endpoint badge (no Codecov account required).
- **Docker**: multi-stage `Dockerfile` (deps → runtime), `docker-compose.yml` with healthcheck and persistent `./data` volume, `.dockerignore`, `.env.example` documenting every env var. `app/config.py` learned to read `.env` without adding a dependency.
- **Repo hygiene**: `.gitattributes` enforces LF line endings; extended `.gitignore` for `.env`, `.mypy_cache/`, `.ruff_cache/`, `coverage.xml`, `dist/`, `build/`.

### Bug fixes
- **CV upload preference key mismatch** (`/api/upload-cv`): the handler checked `summary.get("ruoli_preferiti")` but both the LLM prompt and the heuristic returned `preferred_roles`, so the per-user roles preference was never persisted on upload. Now reads and writes `preferred_roles`.
- **CV content validation**: `validate_cv_content()` rejects uploads under 200 chars or missing common CV keywords (HTTP 422), preventing junk PDFs from polluting the profile store.
- **CV upload deduplication**: migration `003_candidate_profile_hash.py` adds `content_hash` + index; re-uploading the same file now returns the existing `profile_id` instead of creating a duplicate row.
- **Bundle: missing `tls_client` DLL**: `JobFinder.spec` now `collect_data_files("tls_client")` so jobspy's TLS native lib (`tls-client-64.dll`) ships with the executable. Without this fix the EXE crashed at first scrape import with `FileNotFoundError`.
- **Bundle: migrations not discovered**: `app/migrations/*.py` added to spec `datas`; `pkgutil.iter_modules(__path__)` requires real files on disk (the PYZ-only inclusion via `collect_submodules` is not enough), so previous bundles raised `sqlite3.OperationalError: no such table: preferences` on first launch.
- **Bundle: `web/` static dir not found**: `create_app` now resolves `web_dir` from `sys._MEIPASS` when frozen (PyInstaller). Workspace dir holds only user-writable state (`data/`, `.env`, `cv.md`); read-only assets live inside the bundle.

### Refactor
- `web/app.js`: extracted i18n into `web/modules/i18n.js` with a `onLanguageChange` callback registry, dropping ~80 LOC from the main entry. Chat / scan / kanban / recommendations splits remain on the follow-up list.

### Docs
- README slimmed to 4 demo screenshots, accurate test count (122) and i18n key count (259), refreshed Project structure tree, new Rate limiting and Database migrations sections, expanded Mermaid architecture diagram (rate_limit, migrations, roles_shortlist, chat memory).
- `CONTRIBUTING.md`, `SECURITY.md`, `Makefile` added; `DOCS/schema.md` and `DOCS/security.md` tracked and linked from README.
- New `tests/e2e/readme-demo-gif.spec.js` records an animated hero GIF via Playwright + ffmpeg (run via `npm run record-demo`).
- Empty `tests/e2e/screenshots.spec.js` deleted; `readme-cv-showcase.spec.js` renamed to `manual-cv-flow.spec.js` and restricted to the Italian CV.

### Standalone Windows bundle
- `scripts/launch_exe.py`: PyInstaller entry point. Resolves a writable workspace next to the executable, sets `JOBFINDER_WORKSPACE` before importing `app.main`, opens the default browser when uvicorn is ready, runs without `reload`.
- `JobFinder.spec`: PyInstaller config with two analyses (`launch_exe` → `JobFinder.exe`, `updater` → `Updater.exe`) merged via `MERGE` so dependencies are stored once. Hidden imports cover `pkgutil`-discovered submodules (`app.migrations`, `app.providers`) and the LLM SDKs imported lazily inside try/except.
- `scripts/build_exe.py` + `make build-exe`: idempotent local build that wipes `build/`, runs PyInstaller, and zips `dist/JobFinder/` into `dist/JobFinder-windows.zip`.
- **Banner signup links**: the no-API-key sticky banner now renders three CTAs (Cerebras free key, Groq key, Open Settings) so a non-developer can register in 30 s without reading docs. 4 new i18n keys × 5 locales (`banner.signupHint`, `signupCerebras`, `signupGroq`, `openSettings`).
- **README "For non-developers (Windows)"**: 5-step download → extract → run → register → paste-key flow, plus SmartScreen workaround. New shields.io release badge linking to the latest GitHub release.
- **CI release workflow** (`.github/workflows/release.yml`): on tag `v*` push, runs `python scripts/build_exe.py` on `windows-latest` and uploads `JobFinder-windows.zip` as a release asset (auto-generated notes). `workflow_dispatch` trigger uploads it as an artifact instead, for dry runs.

### Self-update (standalone bundle)
- `app/update_sync.py`: `sync_install_dir(source, target)` copies a freshly-extracted bundle over the install dir, skipping any path whose first component is `data`, `.env`, or `.env.local`. User DB, secrets, settings, and logs are guaranteed to survive every update.
- `scripts/updater.py` (bundled as `Updater.exe`): waits for the parent JobFinder PID to exit (Windows `OpenProcess` / POSIX `os.kill(pid, 0)`), downloads the latest `*windows.zip` asset from GitHub Releases, extracts to a temp dir, runs the sync, restarts JobFinder.exe. Every step logged to `data/logs/updater.log`. Failures leave the install dir untouched.
- `POST /api/update/start` (`app/main.py`): refuses with 409 in dev mode or when already on latest, refuses with 500 if `Updater.exe` is missing. On success, spawns the updater detached and schedules `os._exit(0)` 0.8 s later so the response flushes and files unlock.
- `app/version.py:get_version_info` reports `frozen: bool` so the frontend picks the right update flow.
- Frontend update banner branches on `info.frozen`: bundle users see a progress modal that polls `/api/health` every 2 s, detects the file-replacement outage window, and auto-reloads the page when the new process answers. Dev users keep the existing `git pull && pip install` flow.
- 6 new unit tests (`tests/unit/test_update_sync.py`) cover: data dir survives, app/ files are replaced, brand new files land, `.env` stays put, source `data/` subtree is ignored, missing source raises.
