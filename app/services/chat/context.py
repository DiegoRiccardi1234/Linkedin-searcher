"""Build textual context blocks fed to the LLM (profile, preferences, jobs)."""

from __future__ import annotations

import json
from typing import Any

from app.db import Database


def _format_job_brief(job: dict[str, Any], idx: int) -> str:
    analysis_raw = job.get("analysis_json") or "{}"
    try:
        analysis = json.loads(analysis_raw) if isinstance(analysis_raw, str) else analysis_raw
    except (json.JSONDecodeError, TypeError):
        analysis = {}
    smart_working = analysis.get("smart_working", "N/A")
    contratto = analysis.get("contratto", "N/A")
    exp_years = analysis.get("anni_esperienza_richiesti", "N/A")
    return (
        f"{idx}. {job.get('titolo')} @ {job.get('azienda')} | "
        f"Score: {job.get('punteggio_ai')}/10 | "
        f"Advice: {job.get('consiglio')} | "
        f"Remote: {smart_working} | Contract: {contratto} | Exp: {exp_years}y"
    )


def _format_job_full(job: dict[str, Any], idx: int) -> str:
    desc = (job.get("descrizione") or "")[:800]
    return (
        f"[Pinned #{idx}] {job.get('titolo')} @ {job.get('azienda')}\n"
        f"Location: {job.get('sede') or 'N/A'} | Score: {job.get('punteggio_ai')}/10\n"
        f"Advice: {job.get('consiglio')}\n"
        f"Description (excerpt): {desc}\n"
        f"Link: {job.get('link') or 'N/A'}"
    )


def jobs_context(db: Database, limit: int = 5, session_id: str | None = None) -> str:
    pinned: list[dict[str, Any]] = []
    if session_id:
        pinned = db.list_pinned_jobs(session_id)

    if pinned:
        pinned_block = "\n\n".join(_format_job_full(j, i + 1) for i, j in enumerate(pinned))
        remaining = max(0, limit)
        pinned_ids = {j.get("id") for j in pinned}
        top = [
            j
            for j in db.get_top_jobs(limit=remaining + len(pinned))
            if j.get("id") not in pinned_ids
        ]
        if top:
            other_block = "\n".join(
                _format_job_brief(j, i + 1) for i, j in enumerate(top[:remaining])
            )
            return (
                "=== Pinned jobs (user wants to discuss these) ===\n"
                f"{pinned_block}\n\n=== Other top jobs ===\n{other_block}"
            )
        return f"=== Pinned jobs ===\n{pinned_block}"

    jobs = db.get_top_jobs(limit=limit)
    if not jobs:
        return "No jobs scanned yet."
    return "\n".join(_format_job_brief(j, i + 1) for i, j in enumerate(jobs))


def profile_summary_dict(db: Database) -> dict[str, Any]:
    profile = db.get_active_candidate_profile()
    if not profile:
        return {}

    summary = profile.get("summary_json")
    if isinstance(summary, dict):
        return summary
    if isinstance(summary, str):
        try:
            loaded = json.loads(summary)
            if isinstance(loaded, dict):
                return loaded
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def build_profile_context(db: Database) -> str:
    profile = db.get_active_candidate_profile()
    if not profile:
        return "No CV uploaded yet."

    profile_text = str(profile["markdown"])[:2000]
    summary = profile.get("summary_json")
    if summary:
        try:
            summary_data = json.loads(summary) if isinstance(summary, str) else summary
            if isinstance(summary_data, dict):
                profile_text += "\n\n--- Profile Summary ---\n"
                for k, v in summary_data.items():
                    profile_text += f"- {k}: {v}\n"
        except (json.JSONDecodeError, TypeError):
            pass

    linkedin_url = db.get_preference("linkedin_url", "")
    if linkedin_url:
        profile_text += f"\nLinkedIn: {linkedin_url}"

    return profile_text


def build_preferences_context(db: Database) -> str:
    prefs: list[str] = []
    remote = db.get_preference("remote_mode", "")
    if remote:
        prefs.append(f"Work mode: {remote}")
    min_ral = db.get_preference("min_ral", "")
    if min_ral:
        prefs.append(f"Minimum salary: {min_ral}")
    if db.get_preference("prefer_role_qa", "") == "1":
        prefs.append("Interested in: QA/Testing roles")
    if db.get_preference("prefer_role_cyber", "") == "1":
        prefs.append("Interested in: Cybersecurity roles")
    if db.get_preference("prefer_role_data", "") == "1":
        prefs.append("Interested in: Data Analyst roles")
    return "\n".join(prefs) if prefs else "No specific preferences set yet."


def _append_unique(values: list[str], value: str) -> None:
    normalized = value.strip()
    if not normalized:
        return
    existing = {item.lower() for item in values}
    if normalized.lower() in existing:
        return
    values.append(normalized)


def suggest_keywords_from_profile(db: Database, limit: int = 4) -> list[str]:
    keywords: list[str] = []
    summary = profile_summary_dict(db)

    preferred_roles = summary.get("preferred_roles") or summary.get("ruoli_preferiti") or []
    if isinstance(preferred_roles, list):
        for role in preferred_roles:
            _append_unique(keywords, str(role))

    if db.get_preference("prefer_role_data", "") == "1":
        _append_unique(keywords, "Data Analyst")
    if db.get_preference("prefer_role_qa", "") == "1":
        _append_unique(keywords, "QA Tester")
    if db.get_preference("prefer_role_cyber", "") == "1":
        _append_unique(keywords, "Cybersecurity Analyst")

    profile_text = build_profile_context(db).lower()
    if "python" in profile_text:
        _append_unique(keywords, "Python Developer")
    if "data" in profile_text:
        _append_unique(keywords, "Data Analyst")
    if "qa" in profile_text or "testing" in profile_text:
        _append_unique(keywords, "QA Tester")

    if not keywords:
        keywords = ["Python Developer", "Data Analyst", "QA Tester"]

    return keywords[:limit]


def suggest_locations(db: Database) -> list[str]:
    remote_mode = db.get_preference("remote_mode", "")
    last_scan_location = db.get_preference("last_scan_location", "").strip()

    if remote_mode == "full_remote":
        return ["Italy"]
    if last_scan_location:
        return [last_scan_location]
    return ["Italy"]


# Deterministic chip templates. Each entry: (signal_keys, template_key).
# signal_keys = list of substrings looked up in lowercased CV text.
# template_key = lookup into CHIP_TEMPLATES[lang].
_CV_SIGNALS: list[tuple[tuple[str, ...], str]] = [
    (("python",), "python_ai"),
    (("java", "spring"), "java_backend"),
    (("javascript", "react", "vue", "angular", "frontend", "front-end"), "frontend"),
    (("sql", "database", "data warehouse", "etl", "power bi", "tableau"), "data"),
    (
        ("machine learning", "deep learning", " ai ", "ml engineer", "pytorch", "tensorflow"),
        "ai_ml",
    ),
    (("cyber", "security", "pentest", "soc", "siem", "firewall"), "cyber"),
    (("cloud", "aws", "azure", "gcp", "kubernetes", "docker", "devops"), "cloud_devops"),
    (("qa", "testing", "selenium", "cypress", "automation"), "qa"),
    (("network", "cisco", "ccna", "rete"), "network"),
]

CHIP_TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "python_ai": "Which AI/Data roles fit my Python skills?",
        "java_backend": "Which backend roles match my Java experience?",
        "frontend": "Which modern frontend roles suit my profile?",
        "data": "What Data Engineer/Analyst roles fit my CV?",
        "ai_ml": "Which Machine Learning roles can I actually apply to?",
        "cyber": "Which Cybersecurity roles suit my junior profile?",
        "cloud_devops": "Which Cloud/DevOps roles fit my experience?",
        "qa": "Which QA/Automation roles match my CV?",
        "network": "Which network/sysadmin roles fit me?",
        "generic_pivot": "I'm curious about a new area — where could my CV still fit?",
        "generic_match": "Which roles best fit my CV?",
        "top5": "Recommend the top 5 jobs I should apply for",
        "explain_role": "Explain what a Data Engineer actually does day to day",
        "onboarding_goal": "Help me pick a target role from my CV",
        "onboarding_area": "What areas could I work in with my background?",
        "onboarding_start": "How do I start the job search?",
    },
    "it": {
        "python_ai": "Quali ruoli IA/Data si adattano alle mie skill Python?",
        "java_backend": "Quali ruoli backend sono in linea con la mia esperienza Java?",
        "frontend": "Quali ruoli frontend moderni sono adatti al mio profilo?",
        "data": "Che ruoli Data Engineer/Analyst si adattano al mio CV?",
        "ai_ml": "A quali ruoli Machine Learning posso candidarmi davvero?",
        "cyber": "Quali ruoli Cybersecurity junior sono adatti a me?",
        "cloud_devops": "Quali ruoli Cloud/DevOps si adattano alla mia esperienza?",
        "qa": "Quali ruoli QA/Automation sono compatibili col mio CV?",
        "network": "Quali ruoli di rete/sistemista sono adatti al mio profilo?",
        "generic_pivot": "Mi incuriosisce una nuova area — dove potrei ancora andare bene?",
        "generic_match": "Quali ruoli si adattano meglio al mio CV?",
        "top5": "Consigliami i top 5 lavori a cui candidarmi",
        "explain_role": "Spiegami cosa fa davvero un Data Engineer tutti i giorni",
        "onboarding_goal": "Aiutami a scegliere un ruolo target dal mio CV",
        "onboarding_area": "In che aree potrei lavorare col mio background?",
        "onboarding_start": "Come posso iniziare la ricerca di lavoro?",
    },
    "es": {
        "python_ai": "¿Qué roles de IA/Data se ajustan a mis skills en Python?",
        "java_backend": "¿Qué roles backend encajan con mi experiencia en Java?",
        "frontend": "¿Qué roles frontend modernos se adaptan a mi perfil?",
        "data": "¿Qué roles de Data Engineer/Analyst encajan con mi CV?",
        "ai_ml": "¿A qué roles de Machine Learning puedo postular de verdad?",
        "cyber": "¿Qué roles de Ciberseguridad junior se ajustan a mí?",
        "cloud_devops": "¿Qué roles Cloud/DevOps encajan con mi experiencia?",
        "qa": "¿Qué roles QA/Automation encajan con mi CV?",
        "network": "¿Qué roles de redes/sysadmin me quedan bien?",
        "generic_pivot": "Me interesa un área nueva — ¿dónde podría encajar aún?",
        "generic_match": "¿Qué roles se ajustan mejor a mi CV?",
        "top5": "Recomiéndame los 5 mejores empleos para postular",
        "explain_role": "Explícame qué hace un Data Engineer en el día a día",
        "onboarding_goal": "Ayúdame a elegir un rol objetivo desde mi CV",
        "onboarding_area": "¿En qué áreas podría trabajar con mi background?",
        "onboarding_start": "¿Cómo empiezo la búsqueda de empleo?",
    },
    "fr": {
        "python_ai": "Quels rôles IA/Data correspondent à mes compétences Python ?",
        "java_backend": "Quels rôles backend correspondent à mon expérience Java ?",
        "frontend": "Quels rôles frontend modernes conviennent à mon profil ?",
        "data": "Quels rôles Data Engineer/Analyst correspondent à mon CV ?",
        "ai_ml": "À quels rôles Machine Learning puis-je vraiment postuler ?",
        "cyber": "Quels rôles Cybersécurité junior me conviennent ?",
        "cloud_devops": "Quels rôles Cloud/DevOps correspondent à mon expérience ?",
        "qa": "Quels rôles QA/Automation correspondent à mon CV ?",
        "network": "Quels rôles réseau/sysadmin me conviennent ?",
        "generic_pivot": "Un nouveau domaine m'intéresse — où pourrais-je encore convenir ?",
        "generic_match": "Quels rôles correspondent le mieux à mon CV ?",
        "top5": "Recommande-moi les 5 meilleurs jobs pour postuler",
        "explain_role": "Explique-moi ce que fait un Data Engineer au quotidien",
        "onboarding_goal": "Aide-moi à choisir un rôle cible depuis mon CV",
        "onboarding_area": "Dans quels domaines pourrais-je travailler avec mon parcours ?",
        "onboarding_start": "Comment démarrer la recherche d'emploi ?",
    },
    "de": {
        "python_ai": "Welche KI/Daten-Rollen passen zu meinen Python-Skills?",
        "java_backend": "Welche Backend-Rollen passen zu meiner Java-Erfahrung?",
        "frontend": "Welche modernen Frontend-Rollen passen zu mir?",
        "data": "Welche Data-Engineer/Analyst-Rollen passen zu meinem CV?",
        "ai_ml": "Auf welche Machine-Learning-Rollen kann ich mich wirklich bewerben?",
        "cyber": "Welche Cybersecurity-Juniorrollen passen zu mir?",
        "cloud_devops": "Welche Cloud/DevOps-Rollen passen zu meiner Erfahrung?",
        "qa": "Welche QA/Automation-Rollen passen zu meinem CV?",
        "network": "Welche Netzwerk/Sysadmin-Rollen passen zu mir?",
        "generic_pivot": "Ein neuer Bereich reizt mich — wo könnte ich noch passen?",
        "generic_match": "Welche Rollen passen am besten zu meinem CV?",
        "top5": "Empfiehl mir die Top 5 Jobs zum Bewerben",
        "explain_role": "Erklär mir, was ein Data Engineer täglich wirklich macht",
        "onboarding_goal": "Hilf mir eine Zielrolle aus meinem CV zu wählen",
        "onboarding_area": "In welchen Bereichen könnte ich mit meinem Background arbeiten?",
        "onboarding_start": "Wie starte ich die Jobsuche?",
    },
}


def suggest_chat_prompts(db: Database, lang: str = "en", limit: int = 2) -> list[str]:
    """Return localized, CV-derived quick-prompt suggestions for the chat.

    No CV → onboarding prompts. CV present → detect signals in the markdown
    and pick matching templates, padded with generic ones.
    """
    lang_key = lang if lang in CHIP_TEMPLATES else "en"
    templates = CHIP_TEMPLATES[lang_key]

    profile = db.get_active_candidate_profile()
    if not profile:
        return [
            templates["onboarding_goal"],
            templates["onboarding_area"],
            templates["onboarding_start"],
        ]

    profile_text = (profile.get("markdown") or "").lower()
    picks: list[str] = []
    seen_keys: set[str] = set()
    for signals, key in _CV_SIGNALS:
        if key in seen_keys:
            continue
        if any(sig in profile_text for sig in signals):
            picks.append(templates[key])
            seen_keys.add(key)

    # Pad with generic prompts to reach `limit`.
    for key in ("generic_match", "generic_pivot", "top5", "explain_role"):
        if len(picks) >= limit:
            break
        candidate = templates[key]
        if candidate not in picks:
            picks.append(candidate)

    return picks[:limit]
