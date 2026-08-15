"""Skill-gap analysis.

Aggregates the skills that recurring job analyses flag as *missing* for the
candidate (``analysis_json.skills_match.mancano``) across all scored jobs, so
the user sees which competencies to learn to unlock more matches. Pure
aggregation over already-stored analysis — no extra LLM calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.db import Database
    from app.providers.factory import ProviderManager

# UI locale code -> language name for the learning-suggestions prompt (F8).
_LEARN_LANG = {
    "en": "English",
    "it": "Italian",
    "es": "Spanish",
    "de": "German",
    "fr": "French",
}


def _normalize(skill: str) -> str:
    return " ".join(skill.strip().lower().split())


def _profile_skills(db: Database) -> set[str]:
    profile = db.get_active_candidate_profile()
    if not profile:
        return set()
    summary = profile.get("summary_json") or {}
    skills = summary.get("skills") if isinstance(summary, dict) else None
    if not isinstance(skills, list):
        return set()
    return {_normalize(str(s)) for s in skills if str(s).strip()}


def compute_skill_gap(db: Database, *, top: int = 12) -> dict[str, Any]:
    """Return the most frequently missing skills across analyzed jobs.

    A skill the candidate already has (per their profile) is never reported as
    a gap. Each entry carries the occurrence count and a few example job ids.
    """
    have = _profile_skills(db)
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    examples: dict[str, list[int]] = {}
    analyzed_jobs = 0

    for job in db.list_jobs(limit=2000):
        analysis = job.get("analysis_json")
        if isinstance(analysis, str):
            import json

            try:
                analysis = json.loads(analysis)
            except (ValueError, TypeError):
                analysis = None
        if not isinstance(analysis, dict):
            continue
        match = analysis.get("skills_match")
        if not isinstance(match, dict):
            continue
        analyzed_jobs += 1
        missing = match.get("mancano")
        if not isinstance(missing, list):
            continue
        # The deterministic blockers are copied into the same list, because the
        # offer's own page has to say why it is capped. They are not skills:
        # "Richiede 3+ anni di esperienza (il profilo ne dichiara 0)" is not
        # something anyone can go and learn, and on a real archive those
        # sentences took the top five rows of a twelve-row panel — 99, 40, 27,
        # 16 and 15 occurrences, burying Power BI at 7. Matched against the
        # offer's own ``blocchi_dettaglio`` rather than by shape, so a genuine
        # skill that happens to read like a sentence is still counted.
        blocked = analysis.get("blocchi_dettaglio")
        reasons = (
            {_normalize(str(v)) for v in blocked.values() if str(v).strip()}
            if isinstance(blocked, dict)
            else set()
        )
        job_id = int(job.get("id", 0) or 0)
        for raw in missing:
            label = str(raw).strip()
            if not label:
                continue
            key = _normalize(label)
            if key in have or key in reasons:
                continue
            counts[key] = counts.get(key, 0) + 1
            labels.setdefault(key, label)
            examples.setdefault(key, [])
            if job_id and len(examples[key]) < 5:
                examples[key].append(job_id)

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    gaps = [
        {"skill": labels[key], "count": count, "jobs": examples.get(key, [])}
        for key, count in ranked
    ]
    return {
        "gaps": gaps,
        "analyzed_jobs": analyzed_jobs,
        "max_count": gaps[0]["count"] if gaps else 0,
    }


def suggest_learning(
    provider_manager: ProviderManager,
    gaps: list[dict[str, Any]],
    *,
    language: str | None = None,
    max_skills: int = 8,
) -> dict[str, Any]:
    """Ask the LLM for concrete learning resources for the top gap skills (F8).

    Input is the ``gaps`` list from :func:`compute_skill_gap` (skill labels only —
    no CV/PII, so no redaction needed). Returns ``{"suggestions": {skill_lower:
    [{title, type, why}]}}`` keyed by lowercased skill for easy frontend lookup.
    URLs are deliberately not requested (models hallucinate them). Any failure or
    malformed reply degrades to an empty ``suggestions`` map.
    """
    labels: list[str] = []
    for g in gaps:
        skill = str(g.get("skill", "")).strip() if isinstance(g, dict) else str(g).strip()
        if skill:
            labels.append(skill)
        if len(labels) >= max_skills:
            break
    if not labels:
        return {"suggestions": {}}

    name = _LEARN_LANG.get(language or "")
    lang_line = f"Write all text in {name}. " if name else ""
    skills_list = "\n".join(f"- {s}" for s in labels)
    prompt = (
        "You are a career learning advisor. For each skill below, suggest 1-2 concrete, "
        "reputable ways to learn it (an online course, a book, or a hands-on project), "
        "each with a one-line reason. Do NOT invent URLs or links. "
        f"{lang_line}"
        "Reply ONLY with valid JSON shaped as "
        '{"skill name": [{"title": "...", "type": "course|book|project", "why": "..."}]}.\n\n'
        f"Skills:\n{skills_list}"
    )
    try:
        result = provider_manager.complete_json(prompt=prompt, max_tokens=900)
    except Exception:
        return {"suggestions": {}}
    if not isinstance(result, dict):
        return {"suggestions": {}}

    suggestions: dict[str, list[dict[str, str]]] = {}
    for skill, items in result.items():
        if not isinstance(items, list):
            continue
        clean = [
            {
                "title": str(item.get("title", "")).strip(),
                "type": str(item.get("type", "")).strip(),
                "why": str(item.get("why", "")).strip(),
            }
            for item in items
            if isinstance(item, dict) and (item.get("title") or item.get("why"))
        ]
        if clean:
            suggestions[str(skill).strip().lower()] = clean
    return {"suggestions": suggestions}
