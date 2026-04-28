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


class JobActionRequest(BaseModel):
    action: JobActionType
    notes: str = ""


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


class ScanRequest(BaseModel):
    search_terms: list[str] = Field(default_factory=list)
    location: str | None = None
    is_remote: bool = False
    sites: list[str] = Field(default_factory=lambda: ["linkedin", "indeed"])


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


class RoleShortlistRequest(BaseModel):
    roles: list[str] = Field(default_factory=list)


class PreferenceUpdateRequest(BaseModel):
    key: str
    value: str


class ProviderKeysRequest(BaseModel):
    cerebras_api_key: str | None = None
    groq_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    openrouter_api_key: str | None = None
    primary_provider: str | None = None
    preferred_model: str | None = None
