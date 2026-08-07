from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    open = "open"
    applied = "applied"
    interviewing = "interviewing"
    rejected = "rejected"
    archived = "archived"


class JobActionType(str, Enum):
    applied = "applied"
    interviewing = "interviewing"
    rejected = "rejected"
    reopened = "reopened"
    archived = "archived"


class JobActionRequest(BaseModel):
    action: JobActionType
    notes: str = ""


class JobNoteRequest(BaseModel):
    notes: str = ""


class LinkedinSaveRequest(BaseModel):
    """Save the LinkedIn URL and, optionally, pasted profile text (F7-bis).

    ``text`` is the fallback when LinkedIn blocks the server-side fetch."""

    url: str = ""
    text: str = ""


class ReminderRequest(BaseModel):
    """Manual follow-up reminder / deadline on a job application (F4).

    ``reminder_at`` is a date (YYYY-MM-DD) or ISO datetime; empty clears it.
    """

    reminder_at: str = ""
    note: str = ""


class FavoriteRequest(BaseModel):
    is_favorite: bool = True


class LocalPullRequest(BaseModel):
    """An Ollama tag to download, e.g. ``gemma3:12b``."""

    model: str = ""


class LocalUseRequest(BaseModel):
    """Point the app at a local model. ``base_url`` defaults to Ollama's
    OpenAI-compatible endpoint; the API key stays empty by design."""

    model: str = ""
    base_url: str = ""
    for_scoring: bool = True


class ScoreFeedbackRequest(BaseModel):
    """The user's verdict on an AI score. ``expected_score`` is optional: a
    thumbs-down must cost one click, or far fewer of them get collected."""

    verdict: str = "down"
    expected_score: int | None = None
    reason: str = ""


class JobOutcomeRequest(BaseModel):
    """How an application ended. Empty (or "pending") clears the outcome; the
    accepted values are :data:`app.db.Database.OUTCOMES`."""

    outcome: str = ""


class WatchlistCompanyRequest(BaseModel):
    """An employer to follow. ``note`` is the user's own reminder of why."""

    name: str
    note: str = ""


class WatchlistActiveRequest(BaseModel):
    active: bool = True


class ManualJobCreateRequest(BaseModel):
    titolo: str
    azienda: str
    descrizione: str = ""
    sede: str = ""
    link: str = ""
    fonte: str = "manual"
    ricerca_usata: str = "manual"
    modalita: str = "Manuale"


class JobImportRequest(BaseModel):
    """Import a posting from a URL, with pasted text as fallback when the fetch
    is blocked (LinkedIn) or yields too little. At least one must be non-empty."""

    url: str = ""
    text: str = ""


class ScanRequest(BaseModel):
    search_terms: list[str] = Field(default_factory=list)
    location: str | None = None
    # Multi-location scan: scrape each location (city/region/"remote"). When
    # empty, falls back to the single ``location`` (backward compat / saved searches).
    locations: list[str] = Field(default_factory=list)
    # Indeed/Glassdoor country (a jobspy Country name/alias). None → settings default.
    country: str | None = None
    is_remote: bool = False
    sites: list[str] = Field(default_factory=lambda: ["linkedin", "indeed"])
    experience_levels: list[str] = Field(default_factory=list)
    job_types: list[str] = Field(default_factory=list)
    work_types: list[str] = Field(default_factory=list)
    min_salary: int | None = None
    # Employers to search by name, on top of the keyword grid. Empty = use the
    # followed list, and only when the user enabled it (see _watchlist_for_scan).
    companies: list[str] = Field(default_factory=list)


class SavedSearchCreate(BaseModel):
    """A named snapshot of the Job Search filters (F7). ``config`` mirrors the
    frontend scan-form state (terms, location, sites, levels, salary…)."""

    name: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


class ScanResponse(BaseModel):
    totale_trovati: int
    totale_nuovi: int
    totale_analizzati: int
    totale_scartati: int
    run_id: int


class ProfileUpdate(BaseModel):
    preferred_roles: list[str] | None = None
    skills: list[str] | None = None
    languages: list[str] | None = None
    name: str | None = None
    markdown: str | None = None
    # The facts that decide whether an offer is applicable at all. The CV parser
    # gets them wrong often enough — a graduation year read off a certification
    # date, a degree it never found — and until now the only way to correct one
    # was to rewrite the whole CV text by hand. They are stored as preferences,
    # not in summary_json, so a correction survives re-uploading the CV.
    years_experience: int | None = None
    education_level: str | None = None
    grade: int | None = None
    base_cities: list[str] | None = None
    work_modes: list[str] | None = None


class ProfileFromTextRequest(BaseModel):
    markdown: str
    source_name: str | None = None


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    provider: str | None = None
    model: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    updated_preferences: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, Any] | None = None
    suggested_roles: list[dict[str, Any]] = Field(default_factory=list)
    # ``chat_state`` (str from get_chat_state) and ``degraded`` (True when the
    # answer is the rule-based fallback, not a real LLM reply) are returned by
    # handle_chat_message; declare them so ChatResponse(**result) doesn't drop
    # them and the frontend can render the "degraded" indicator.
    chat_state: str = ""
    degraded: bool = False


class RoleShortlistRequest(BaseModel):
    roles: list[str] = Field(default_factory=list)


class ChatSessionCreateRequest(BaseModel):
    title: str = ""


class ChatSessionRenameRequest(BaseModel):
    title: str


class PinJobRequest(BaseModel):
    job_id: int


class PreferenceUpdateRequest(BaseModel):
    key: str
    value: str


class SchedulerConfigRequest(BaseModel):
    enabled: bool | None = None
    interval_hours: int | None = Field(default=None, ge=1, le=168)
    threshold: int | None = Field(default=None, ge=0, le=10)


class MailConfigRequest(BaseModel):
    """Mailbox settings. ``secret``/``client_id`` follow the provider-key rule:
    omitted leaves what is stored, a value replaces it, "" removes it."""

    address: str = ""
    auth: str = ""
    host: str = ""
    port: int | None = Field(default=None, ge=1, le=65535)
    folder: str = ""
    secret: str | None = None
    client_id: str | None = None
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=240)


class MailReviewResolveRequest(BaseModel):
    apply: list[int] = Field(default_factory=list)
    dismiss: list[int] = Field(default_factory=list)


class ProviderKeysRequest(BaseModel):
    cerebras_api_key: str | None = None
    groq_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    openrouter_api_key: str | None = None
    deepseek_api_key: str | None = None
    xai_api_key: str | None = None
    glm_api_key: str | None = None
    mistral_api_key: str | None = None
    # "custom" = any OpenAI-compatible endpoint (local model server or gateway).
    # The base URL is the configuration; the key is optional.
    custom_api_key: str | None = None
    custom_base_url: str | None = None
    primary_provider: str | None = None
    preferred_model: str | None = None
    scoring_model: str | None = None
    chat_model: str | None = None
    cv_model: str | None = None
