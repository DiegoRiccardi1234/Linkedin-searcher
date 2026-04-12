from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    open = "open"
    applied = "applied"
    rejected = "rejected"
    archived = "archived"


class JobActionType(str, Enum):
    applied = "applied"
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


class ScanResponse(BaseModel):
    totale_trovati: int
    totale_nuovi: int
    totale_analizzati: int
    totale_scartati: int
    run_id: int


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    updated_preferences: dict[str, Any] = Field(default_factory=dict)


class PreferenceUpdateRequest(BaseModel):
    key: str
    value: str
