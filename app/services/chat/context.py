"""Build textual context blocks fed to the LLM (profile, preferences, jobs)."""

from __future__ import annotations

import json
from typing import Any

from app.db import Database
from app.services.onboarding import linkedin_suffix, onboarding_context
from app.services.pii import redact_pii


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
        f"Score: {_score_text(job)} | "
        f"Advice: {job.get('consiglio')} | "
        f"Remote: {smart_working} | Contract: {contratto} | Exp: {exp_years}y"
    )


def _score_text(job: dict[str, Any]) -> str:
    """The score for a prompt — or the fact that there isn't one.

    Never interpolate the raw value: an unjudged job would reach the model as
    "Score: None/10", which reads as a zero and gets the offer written off.
    """
    score = job.get("punteggio_ai")
    return f"{score}/10" if score is not None else "non ancora valutato"


def _format_job_full(job: dict[str, Any], idx: int) -> str:
    desc = (job.get("descrizione") or "")[:800]
    return (
        f"[Pinned #{idx}] {job.get('titolo')} @ {job.get('azienda')}\n"
        f"Location: {job.get('sede') or 'N/A'} | Score: {_score_text(job)}\n"
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

    # The pasted profile text when there is one, the URL only as a fallback: the
    # coach was being handed a link it cannot open.
    profile_text += linkedin_suffix(db)

    # Privacy Mode (default ON): strip contact PII (email/phone/address/URL)
    # before the CV context reaches the LLM. name=None keeps the candidate's
    # name so the coach can still address them (no restore step in chat).
    privacy = db.get_preference("feature_privacy_mode", "1") not in ("0", "false", "off", "")
    if privacy:
        profile_text = redact_pii(profile_text, None)[0]

    return profile_text


def build_preferences_context(db: Database) -> str:
    prefs: list[str] = []
    # The search goals the user filled in on the Profile page. The coach read a
    # different, legacy set of keys and so was the one surface in the app that
    # could not see them — it asked about things the user had already answered.
    goals = onboarding_context(db)
    if goals:
        prefs.append(goals)
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


#: Two questions per page, because the same coach is useful in different ways
#: depending on what is on screen. The chips used to be picked from the CV
#: alone, so the app offered career advice while the user was looking at the
#: provider keys.
_VIEW_CHIPS: dict[str, dict[str, list[str]]] = {
    "dashboard": {
        "en": ["What do my numbers say?", "Which applications have gone quiet?"],
        "it": ["Cosa dicono i miei numeri?", "Quali candidature sono ferme?"],
        "es": ["¿Qué dicen mis números?", "¿Qué candidaturas están paradas?"],
        "fr": ["Que disent mes chiffres ?", "Quelles candidatures sont au point mort ?"],
        "de": ["Was sagen meine Zahlen?", "Welche Bewerbungen liegen still?"],
    },
    "job-search": {
        "en": ["Which search terms should I use?", "Where is it worth searching?"],
        "it": ["Che termini di ricerca mi consigli?", "Dove conviene cercare?"],
        "es": ["¿Qué términos de búsqueda uso?", "¿Dónde conviene buscar?"],
        "fr": ["Quels termes de recherche utiliser ?", "Où vaut-il mieux chercher ?"],
        "de": ["Welche Suchbegriffe soll ich nehmen?", "Wo lohnt sich die Suche?"],
    },
    "jobs": {
        "en": ["Which of these should I send first?", "Why did this offer score that?"],
        "it": ["Quali mando per prime?", "Perché questa offerta ha questo voto?"],
        "es": ["¿Cuáles envío primero?", "¿Por qué esta oferta tiene esa nota?"],
        "fr": ["Lesquelles envoyer en premier ?", "Pourquoi cette offre a cette note ?"],
        "de": ["Welche zuerst abschicken?", "Warum hat dieses Angebot diese Note?"],
    },
    "mail": {
        "en": ["What can the app read from my inbox?", "Why was this email not recognised?"],
        "it": [
            "Cosa riesce a leggere dalla mia posta?",
            "Perché questa mail non è stata riconosciuta?",
        ],
        "es": ["¿Qué puede leer de mi correo?", "¿Por qué no reconoció este correo?"],
        "fr": ["Que lit l'app dans ma boîte mail ?", "Pourquoi cet e-mail n'a pas été reconnu ?"],
        "de": ["Was liest die App aus meinem Postfach?", "Warum wurde diese Mail nicht erkannt?"],
    },
    "profile": {
        "en": ["What is missing from my profile?", "Does my CV fit the goal I set?"],
        "it": ["Cosa manca al mio profilo?", "Il mio CV è adatto all'obiettivo che ho scritto?"],
        "es": ["¿Qué le falta a mi perfil?", "¿Mi CV encaja con el objetivo que puse?"],
        "fr": ["Qu'est-ce qui manque à mon profil ?", "Mon CV correspond-il à mon objectif ?"],
        "de": ["Was fehlt in meinem Profil?", "Passt mein Lebenslauf zu meinem Ziel?"],
    },
    "settings": {
        "en": ["Which AI provider suits me?", "Why are some offers left unevaluated?"],
        "it": ["Quale provider IA mi conviene?", "Perché alcune offerte restano non valutate?"],
        "es": ["¿Qué proveedor de IA me conviene?", "¿Por qué algunas ofertas quedan sin evaluar?"],
        "fr": [
            "Quel fournisseur d'IA me convient ?",
            "Pourquoi certaines offres restent non évaluées ?",
        ],
        "de": ["Welcher KI-Anbieter passt zu mir?", "Warum bleiben manche Angebote unbewertet?"],
    },
}


def suggest_chat_prompts(
    db: Database, lang: str = "en", limit: int = 4, view: str | None = None
) -> list[str]:
    """Return localized, CV-derived quick-prompt suggestions for the chat.

    No CV → onboarding prompts. CV present → detect signals in the markdown
    and pick matching templates, padded with generic ones.
    """
    lang_key = lang if lang in CHIP_TEMPLATES else "en"
    templates = CHIP_TEMPLATES[lang_key]
    # What the user is looking at comes first: it is the most likely subject of
    # the next question, whatever their CV says.
    picks: list[str] = list(_VIEW_CHIPS.get(view or "", {}).get(lang_key, []))

    profile = db.get_active_candidate_profile()
    if not profile:
        return [
            *picks,
            templates["onboarding_goal"],
            templates["onboarding_area"],
            templates["onboarding_start"],
        ][:limit]

    profile_text = (profile.get("markdown") or "").lower()
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
