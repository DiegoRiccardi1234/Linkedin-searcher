"""What the market looks like, from the postings this app has actually read.

The coach was asked market questions ("which roles fit me?", "is this salary
normal?") knowing only the CV and a handful of top job rows. It had no idea
which companies keep hiring, which of the user's search terms return anything,
what the postings pay, or which skill keeps coming up as missing — even though
the app has scraped and scored hundreds of postings and stored every one.

This reads that back. No LLM call, no network, no new dependency: it is the
freshest market data the app can have, because the user's own scans produced it.

Deliberately about the LAST few weeks only. A snapshot of a market from three
months ago is worse than none, because it reads as current.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from app.log import get_logger

log = get_logger(__name__)

#: How far back a posting still counts as "the current market".
WINDOW_DAYS = 30

#: Rows read per snapshot. Enough to be representative, small enough to stay a
#: cheap query on the hot path.
MAX_ROWS = 400


def _floor(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


def collect(db: Any, *, days: int = WINDOW_DAYS) -> dict[str, Any]:
    """Aggregate the recent postings into a small, printable market picture."""
    try:
        conn = db._get_connection() if hasattr(db, "_get_connection") else db.conn
        rows = conn.execute(
            "SELECT titolo, azienda, sede, ricerca_usata, punteggio_ai, analysis_json "
            "FROM jobs WHERE last_seen_at >= ? ORDER BY last_seen_at DESC LIMIT ?",
            (_floor(days), MAX_ROWS),
        ).fetchall()
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("market snapshot unavailable: %s", exc)
        return {}

    if not rows:
        return {}

    companies: Counter[str] = Counter()
    locations: Counter[str] = Counter()
    terms: Counter[str] = Counter()
    term_hits: Counter[str] = Counter()
    missing_skills: Counter[str] = Counter()
    salaries: list[str] = []
    scores: list[int] = []
    engagements: Counter[str] = Counter()

    for row in rows:
        data = dict(row)
        if data.get("azienda"):
            companies[str(data["azienda"]).strip()] += 1
        if data.get("sede"):
            locations[str(data["sede"]).split(",")[0].strip()] += 1
        term = str(data.get("ricerca_usata") or "").strip()
        if term:
            terms[term] += 1
        try:
            score = int(data.get("punteggio_ai") or 0)
        except (TypeError, ValueError):
            score = 0
        if score:
            scores.append(score)
            if score >= 7 and term:
                term_hits[term] += 1

        raw = data.get("analysis_json")
        if not raw:
            continue
        try:
            analysis = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        skills = analysis.get("skills_match") if isinstance(analysis, dict) else None
        for item in (skills or {}).get("mancano", []) if isinstance(skills, dict) else []:
            text = str(item).strip()
            # The deterministic blockers land in the same list; they are not
            # skills anyone can go and learn.
            if text and len(text) < 40:
                missing_skills[text.lower()] += 1
        ral = str((analysis or {}).get("ral_stimata") or "").strip()
        if ral and "stimabile" not in ral.lower():
            salaries.append(ral)
        engagement = str((analysis or {}).get("tipo_ingaggio") or "").strip()
        if engagement and engagement != "Non specificato":
            engagements[engagement] += 1

    return {
        "window_days": days,
        "postings": len(rows),
        "top_companies": companies.most_common(8),
        "top_locations": locations.most_common(6),
        "productive_terms": [
            (term, term_hits.get(term, 0), count) for term, count in terms.most_common(8)
        ],
        "missing_skills": missing_skills.most_common(8),
        "salaries_seen": salaries[:10],
        "engagements": engagements.most_common(4),
        "median_score": sorted(scores)[len(scores) // 2] if scores else None,
    }


def as_prompt_block(db: Any, *, days: int = WINDOW_DAYS) -> str:
    """The snapshot as a few labelled lines, or "" when there is nothing to say.

    Kept short on purpose: it is prepended to prompts that already carry the CV
    and (for scoring) a posting, and the models this app runs on are small.
    """
    data = collect(db, days=days)
    if not data or not data.get("postings"):
        return ""

    lines = [
        f"Annunci visti negli ultimi {data['window_days']} giorni: {data['postings']}",
    ]
    if data["top_companies"]:
        lines.append(
            "Aziende che pubblicano piu' spesso: "
            + ", ".join(f"{name} ({n})" for name, n in data["top_companies"][:6])
        )
    if data["top_locations"]:
        lines.append(
            "Sedi ricorrenti: "
            + ", ".join(f"{name} ({n})" for name, n in data["top_locations"][:5])
        )
    if data["productive_terms"]:
        lines.append(
            "Termini di ricerca e quanti risultati buoni (>=7) hanno dato: "
            + "; ".join(f"{term}: {good}/{total}" for term, good, total in data["productive_terms"])
        )
    if data["missing_skills"]:
        lines.append(
            "Requisiti che mancano piu' spesso al candidato: "
            + ", ".join(f"{skill} ({n})" for skill, n in data["missing_skills"][:6])
        )
    if data["salaries_seen"]:
        lines.append("RAL dichiarate/ stimate viste: " + "; ".join(data["salaries_seen"][:6]))
    if data["engagements"]:
        lines.append(
            "Tipo di ingaggio negli annunci: "
            + ", ".join(f"{kind} ({n})" for kind, n in data["engagements"])
        )
    if data["median_score"]:
        lines.append(f"Punteggio mediano delle offerte trovate: {data['median_score']}/10")
    return "\n".join(lines)
