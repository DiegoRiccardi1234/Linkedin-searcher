import re
from typing import Any

from app.db import Database
from app.providers.factory import ProviderManager


def _extract_pref_updates(message: str) -> dict[str, str]:
    updates: dict[str, str] = {}
    lower = message.lower()

    if "full remote" in lower or "solo remote" in lower:
        updates["remote_mode"] = "full_remote"
    elif "ibrido" in lower:
        updates["remote_mode"] = "hybrid"

    min_ral_match = re.search(r"min\s*ral\s*(\d{2,3})", lower)
    if min_ral_match:
        # Se l'utente scrive 30 assumiamo 30k.
        value = int(min_ral_match.group(1))
        if value < 1000:
            value = value * 1000
        updates["min_ral"] = str(value)

    if "qa" in lower and "no" not in lower:
        updates["prefer_role_qa"] = "1"
    if "cyber" in lower and "no" not in lower:
        updates["prefer_role_cyber"] = "1"

    return updates


def _jobs_context(db: Database) -> str:
    jobs = db.get_top_jobs(limit=5)
    if not jobs:
        return "Nessun annuncio disponibile."

    lines = []
    for idx, job in enumerate(jobs, 1):
        lines.append(
            f"{idx}) {job.get('titolo')} @ {job.get('azienda')} | score={job.get('punteggio_ai')} | consiglio={job.get('consiglio')}"
        )
    return "\n".join(lines)


def _fallback_answer(db: Database, message: str) -> str:
    lower = message.lower()
    top_jobs = db.get_top_jobs(limit=3)
    if "consiglia" in lower or "miglior" in lower:
        if not top_jobs:
            return "Al momento non ho annunci analizzati. Esegui prima una scansione."
        lines = ["Top consigliati adesso:"]
        for idx, job in enumerate(top_jobs, 1):
            lines.append(
                f"{idx}. {job.get('titolo')} @ {job.get('azienda')} (score {job.get('punteggio_ai')}/10, {job.get('consiglio')})"
            )
        lines.append("Suggerimento: apri Dettaglio sul primo e poi premi Candidata se confermi il fit.")
        return "\n".join(lines)
    return "Ho salvato il tuo messaggio. Posso consigliarti i lavori migliori o aggiornare le preferenze."


def handle_chat_message(
    db: Database,
    provider_manager: ProviderManager,
    message: str,
    session_id: str,
) -> dict[str, Any]:
    db.save_chat_message(session_id=session_id, role="user", content=message)

    updates = _extract_pref_updates(message)
    for key, value in updates.items():
        db.set_preference(key, value)

    profile = db.get_active_candidate_profile()
    profile_text = profile["markdown"][:1500] if profile else "Profilo non caricato."
    
    linkedin_url = db.get_preference("linkedin_url", "")
    if linkedin_url:
        profile_text += f"\n\nProfilo LinkedIn: {linkedin_url}"

    context_jobs = _jobs_context(db)

    prompt_messages = [
        {
            "role": "system",
            "content": (
                "Sei un job coach IT. Dai consigli pratici su dove candidarsi adesso, "
                "spiega in modo chiaro il motivo dei punteggi e suggerisci prossime azioni."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Profilo candidato:\n{profile_text}\n\n"
                f"Top annunci:\n{context_jobs}\n\n"
                f"Messaggio utente: {message}"
            ),
        },
    ]

    try:
        answer = provider_manager.chat(prompt_messages, max_tokens=600)
    except Exception:
        answer = _fallback_answer(db=db, message=message)

    db.save_chat_message(session_id=session_id, role="assistant", content=answer)
    return {
        "session_id": session_id,
        "answer": answer,
        "updated_preferences": updates,
    }
