"""Profile-aware content generation shared across job artifacts.

One entry point builds a CV+offer prompt from a task template and asks the
active LLM for a single JSON ``{"content": ...}`` payload. Used by the
cover-letter, interview-prep and resume-tailoring endpoints so the prompt
plumbing lives in one place.

Templates hold only the *task description*; the CV / offer data and the
JSON-output instruction are assembled here in code, which keeps the literal
braces of the JSON example out of any ``str.format`` path.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.providers.model_selector import SCORING_MIN_SIZE_B
from app.services.pii import redact_pii, restore_contacts, restore_pii

if TYPE_CHECKING:
    from app.providers.factory import ProviderManager

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts" / "generation"

# Default output budgets per artifact type (cover letters are short, a tailored
# resume can be long).
MAX_TOKENS = {
    "cover_letter": 600,
    "interview_prep": 900,
    "resume_tailoring": 1300,
    "cv_review": 1200,
    "cv_improve": 1600,
    "recruiter_outreach": 450,
    # Two numbers plus a short rationale — no need for a long budget.
    "ral_suggest": 500,
    # Four short fields plus a short rationale.
    "goals_suggest": 600,
}

# Quality-biased policy for the CV tools (review + improve): these are done
# rarely and matter a lot, so pick a capable model (>=70B, e.g. gpt-oss-120b:free)
# rather than the fast small one the scan scoring uses. Free-preferring for the
# free-tier account. Passed as ``policy_override`` to generate_with_profile.
CV_POLICY: dict[str, Any] = {
    "prefer_fast": False,
    "prefer_quality": True,
    "prefer_free": True,
    "max_cost_tier": "high",
    "min_size_b": 70,
    "weights": {"size": 20, "family": 40, "instruct": 30, "json": 12, "reasoning": 6},
}

# Reading a CV *into JSON* is structured extraction, not writing: it wants the
# same kind of model the scan scoring wants — a clean instruct build that starts
# emitting JSON immediately — not the 70B+ quality bias above. CV_POLICY here
# would elect a reasoning giant that spends the completion budget thinking and
# returns a cut-off object, which this path swallows into the heuristic profile
# without a word. Same floor and same hard gate as scoring, one place apart
# because the two tasks answer to different budgets.
CV_PARSE_POLICY: dict[str, Any] = {
    "prefer_fast": True,
    "prefer_quality": True,
    "prefer_free": True,
    "max_cost_tier": "high",
    "min_size_b": SCORING_MIN_SIZE_B,
    "hard_floor": True,
    "weights": {"size": 16, "instruct": 25, "json": 12, "reasoning": 0, "small_penalty": -150},
}

# UI locale code -> language name for the optional "write in X" instruction.
# Generation prompts are authored in Italian and default to Italian output;
# passing ``language`` steers the reply to the user's UI language instead.
_LANG_NAMES = {
    "en": "English",
    "it": "Italian",
    "es": "Spanish",
    "de": "German",
    "fr": "French",
}


@cache
def _load_task(content_type: str) -> str:
    path = _PROMPTS_DIR / f"{content_type}.txt"
    return path.read_text(encoding="utf-8").strip()


def build_prompt(
    content_type: str,
    profile_markdown: str,
    job_info: dict[str, Any],
    *,
    extra_block: str = "",
    json_output: bool = True,
    language: str | None = None,
) -> str:
    """Assemble the full LLM prompt for a generation task.

    Tasks with no job attached (e.g. the standalone CV review) pass an empty
    ``job_info`` and the OFFERTA block is dropped. With ``json_output=False`` the
    prompt asks for plain markdown instead of the JSON wrapper — used as a
    fallback when a model can't produce the JSON envelope. ``language`` (a UI
    locale code) steers the output language; omit it to keep the Italian default.
    """
    task = _load_task(content_type)
    extra = f"{extra_block.strip()}\n" if extra_block.strip() else ""
    has_job = any(job_info.get(k) for k in ("titolo", "azienda", "descrizione"))
    if has_job:
        titolo = job_info.get("titolo", "N/A")
        azienda = job_info.get("azienda", "N/A")
        descrizione = job_info.get("descrizione", "") or ""
        descrizione_line = ("Descrizione: " + descrizione[:1800]) if descrizione else ""
        offer_block = f"OFFERTA:\nTitolo: {titolo}\nAzienda: {azienda}\n{descrizione_line}\n\n"
    else:
        offer_block = ""
    lang_line = ""
    if language:
        name = _LANG_NAMES.get(language)
        if name:
            lang_line = f"Write your response in {name}.\n"
    if json_output:
        tail = (
            "Non aggiungere testo extra. Rispondi SOLO con JSON valido con la chiave "
            '"content":\n{"content": "..."}'
        )
    else:
        tail = "Rispondi in testo semplice (markdown), senza JSON e senza testo extra."
    return f"{task}\n{extra}\nCV candidato:\n{profile_markdown[:3500]}\n\n{offer_block}{lang_line}{tail}"


def _stringify_content(value: Any) -> str:
    """Render an LLM content value as readable plain text / markdown.

    Weak models sometimes return a nested object/array for a field the prompt
    asked to be a string (e.g. interview-prep sections). A plain ``str(dict)``
    would surface a Python repr (``{'## Domande': [...]}``) to the user, so we
    walk the structure into readable text instead. The frontend renders this as
    ``textContent`` (features.js), so markdown-ish plain text is enough.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_stringify_content(item) for item in value]
        return "\n".join(p for p in parts if p)
    if isinstance(value, dict):
        parts = []
        for key, val in value.items():
            label = str(key).strip()
            body = _stringify_content(val)
            if not label:
                parts.append(body)
            elif label.startswith("#"):
                parts.append(f"{label}\n{body}")
            else:
                parts.append(f"{label}: {body}" if "\n" not in body else f"**{label}**\n{body}")
        return "\n\n".join(p for p in parts if p)
    return str(value)


def _extract_content(result: Any, content_type: str) -> str:
    if isinstance(result, dict):
        value = result.get("content") or result.get(content_type)
        if value is None and result:
            value = next(iter(result.values()), "")
        return _stringify_content(value)
    return _stringify_content(result)


def generate_with_profile(
    provider_manager: ProviderManager,
    content_type: str,
    profile_markdown: str,
    job_info: dict[str, Any],
    *,
    extra_block: str = "",
    max_tokens: int | None = None,
    redact: bool = False,
    candidate_name: str | None = None,
    language: str | None = None,
    restore_contact_info: bool = False,
    policy_override: dict[str, Any] | None = None,
    provider_name: str | None = None,
    model_name: str | None = None,
) -> str:
    """Generate profile-tailored content for a job. Returns the text body.

    When ``redact`` is set (Privacy Mode) the CV is PII-scrubbed before the
    provider call and the candidate's own name is restored in the returned text,
    so the letter/resume the user sends still reads correctly while nothing
    identifying leaves the machine.

    Happy path asks for a JSON ``{"content": ...}`` envelope. Weak/free models
    sometimes reply in prose ("Nessun JSON trovato"); rather than fail with a
    502, we retry once as a plain-text ``chat`` call and use that text directly.
    A genuine failure (no provider, network) still propagates from the fallback.
    """
    original_markdown = profile_markdown
    token_map: dict[str, str] = {}
    if redact:
        profile_markdown, token_map = redact_pii(profile_markdown, candidate_name)
    budget = max_tokens if max_tokens is not None else MAX_TOKENS.get(content_type, 700)
    try:
        prompt = build_prompt(
            content_type, profile_markdown, job_info, extra_block=extra_block, language=language
        )
        result = provider_manager.complete_json(
            prompt=prompt,
            max_tokens=budget,
            provider_name=provider_name,
            model_name=model_name,
            policy_override=policy_override,
        )
        content = _extract_content(result, content_type)
    except Exception:
        prose_prompt = build_prompt(
            content_type,
            profile_markdown,
            job_info,
            extra_block=extra_block,
            json_output=False,
            language=language,
        )
        content = provider_manager.chat(
            [{"role": "user", "content": prose_prompt}],
            max_tokens=budget,
            provider_name=provider_name,
            model_name=model_name,
            policy_override=policy_override,
        )
    content = restore_pii(content, token_map)
    if restore_contact_info and redact:
        # A tailored résumé is a document the user sends: put their real
        # contacts back into the output (the LLM never saw them).
        content = restore_contacts(content, original_markdown)
    return content
