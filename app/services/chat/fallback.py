"""Rule-based fallback when the LLM provider is unavailable or errors out.

Localized for the 5 supported UI languages (en/it/es/fr/de).
"""

from __future__ import annotations

from typing import Any

from app.db import Database
from app.services.chat.context import suggest_keywords_from_profile, suggest_locations
from app.services.chat.intents import has_role_guidance_intent, has_search_intent
from app.services.chat.state import get_ui_language

# message keys: top_picks_lead, top_picks_outro, no_jobs, role_guidance,
# search_prepared, default_saved
_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "top_picks_lead": "Here are the top picks right now:",
        "top_picks_outro": "\nOpen the Details panel on any job to learn more or apply.",
        "no_jobs": "No analyzed jobs available yet. Run a scan first from Settings.",
        "role_guidance": "From your CV, the most suitable roles are: {roles}. I also pre-filled search terms and location: run the scan and I will rank the best matches.",
        "search_prepared": "I prepared a search aligned with your CV and preferences. Keywords: {kw}. Locations: {loc}. Run the scan and I will prioritize the best opportunities.",
        "default_saved": "I've saved your message. I can suggest what to search and prioritize your best jobs — just ask!",
    },
    "it": {
        "top_picks_lead": "Ecco le offerte prioritarie in questo momento:",
        "top_picks_outro": "\nApri il pannello Details per vedere motivazioni e prossima azione consigliata.",
        "no_jobs": "Non ho ancora offerte analizzate. Avvia prima una scansione dalla pagina Settings.",
        "role_guidance": "Dal tuo CV i ruoli piu coerenti sono: {roles}. Ho anche precompilato la ricerca con parole chiave e location: avvia la scansione e poi ti dico quali offerte hanno fit migliore.",
        "search_prepared": "Ho preparato una ricerca coerente con CV e preferenze. Parole chiave: {kw}. Location: {loc}. Avvia la scansione e poi ti ordino le offerte per priorita.",
        "default_saved": "Messaggio salvato. Posso suggerirti cosa cercare, avviare una ricerca guidata e ordinare le offerte migliori.",
    },
    "es": {
        "top_picks_lead": "Estas son las mejores opciones ahora mismo:",
        "top_picks_outro": "\nAbre el panel Details en cualquier oferta para ver detalles o postularte.",
        "no_jobs": "Aun no hay ofertas analizadas. Lanza primero una busqueda desde Settings.",
        "role_guidance": "Segun tu CV, los puestos mas adecuados son: {roles}. Tambien he prerrellenado palabras clave y ubicacion: lanza la busqueda y te clasificare las mejores coincidencias.",
        "search_prepared": "He preparado una busqueda alineada con tu CV y preferencias. Palabras clave: {kw}. Ubicaciones: {loc}. Lanza la busqueda y priorizare las mejores oportunidades.",
        "default_saved": "Mensaje guardado. Puedo sugerirte que buscar y priorizar tus mejores ofertas — solo pidemelo!",
    },
    "fr": {
        "top_picks_lead": "Voici les meilleurs choix en ce moment :",
        "top_picks_outro": "\nOuvre le panneau Details d'une offre pour en savoir plus ou postuler.",
        "no_jobs": "Aucune offre analysee pour l'instant. Lance d'abord une recherche depuis Settings.",
        "role_guidance": "D'apres ton CV, les postes les plus adaptes sont : {roles}. J'ai aussi prerempli mots-cles et localisation : lance la recherche et je classerai les meilleures correspondances.",
        "search_prepared": "J'ai prepare une recherche alignee avec ton CV et tes preferences. Mots-cles : {kw}. Lieux : {loc}. Lance la recherche et je priorisera les meilleures opportunites.",
        "default_saved": "Message enregistre. Je peux suggerer quoi chercher et prioriser tes meilleures offres — il suffit de demander !",
    },
    "de": {
        "top_picks_lead": "Hier sind die besten Empfehlungen jetzt:",
        "top_picks_outro": "\nOeffne das Details-Panel einer Stelle fuer mehr Infos oder zur Bewerbung.",
        "no_jobs": "Noch keine analysierten Stellen vorhanden. Starte zuerst eine Suche in Settings.",
        "role_guidance": "Basierend auf deinem CV sind die passendsten Rollen: {roles}. Ich habe auch Suchbegriffe und Standort vorbefuellt: starte die Suche, dann ordne ich die besten Treffer.",
        "search_prepared": "Ich habe eine Suche passend zu deinem CV und deinen Praeferenzen vorbereitet. Suchbegriffe: {kw}. Orte: {loc}. Starte die Suche, dann priorisiere ich die besten Chancen.",
        "default_saved": "Nachricht gespeichert. Ich kann vorschlagen, wonach du suchen sollst und deine besten Stellen priorisieren — frag einfach!",
    },
}


def _t(lang: str, key: str, **fmt: Any) -> str:
    text = _MESSAGES.get(lang, _MESSAGES["en"]).get(key) or _MESSAGES["en"].get(key, "")
    return text.format(**fmt) if fmt else text


def _ranked_lines(lang: str, jobs: list[dict[str, Any]]) -> list[str]:
    advice_default = {
        "en": "Worth evaluating",
        "it": "Valutabile",
        "es": "Vale la pena evaluar",
        "fr": "A evaluer",
        "de": "Pruefenswert",
    }[lang if lang in {"en", "it", "es", "fr", "de"} else "en"]
    lines = [_t(lang, "top_picks_lead")]
    for idx, job in enumerate(jobs, 1):
        score = int(job.get("punteggio_ai") or 0)
        advice = job.get("consiglio") or advice_default
        lines.append(
            f"{idx}. {job.get('titolo')} @ {job.get('azienda')} (score {score}/10, {advice})"
        )
    lines.append(_t(lang, "top_picks_outro"))
    return lines


def fallback_answer(db: Database, message: str) -> tuple[str, dict[str, Any] | None]:
    """Return a localized canned answer + optional action when no LLM is reachable."""
    lower = message.lower()
    lang = get_ui_language(db)

    recommend_intent = any(
        token in lower
        for token in [
            "recommend",
            "best",
            "top",
            "consiglia",
            "miglior",
            "priorit",
            "candid",
            "mejor",
            "recomienda",
            "recommand",
            "empfeh",
            "beste",
        ]
    )

    recommended_jobs = db.get_recommended_jobs(limit=3)
    top_jobs = recommended_jobs or db.get_top_jobs(limit=3)

    if recommend_intent:
        if not top_jobs:
            return _t(lang, "no_jobs"), None
        return "\n".join(_ranked_lines(lang, top_jobs)), None

    if has_role_guidance_intent(message):
        keywords = suggest_keywords_from_profile(db)
        locations = suggest_locations(db)
        action = {"type": "FILL_SCAN_FORM", "keywords": keywords, "locations": locations}
        roles = ", ".join(keywords[:3])
        return _t(lang, "role_guidance", roles=roles), action

    if has_search_intent(message):
        keywords = suggest_keywords_from_profile(db)
        locations = suggest_locations(db)
        action = {"type": "FILL_SCAN_FORM", "keywords": keywords, "locations": locations}
        return _t(lang, "search_prepared", kw=", ".join(keywords), loc=", ".join(locations)), action

    return _t(lang, "default_saved"), None
