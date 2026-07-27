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
