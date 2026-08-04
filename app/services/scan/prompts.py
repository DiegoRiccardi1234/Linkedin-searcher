"""What the model is actually asked, for one offer or for a batch.

The schema is the app's real contract with the model: it decides how many
tokens the answer costs (i.e. whether it truncates), in what order the model
commits to its conclusions, and which fields anything downstream can rely on.
Both prompts share it, so the two paths cannot drift apart.
"""

from __future__ import annotations

from typing import Any

_REQ_MARKERS = (
    "requisiti",
    "requirements",
    "cosa cerchiamo",
    "chi cerchiamo",
    "chi sei",
    "profilo ricercato",
    "profilo ideale",
    "your profile",
    "who you are",
    "qualifiche",
    "competenze richieste",
    "what we",
    "what you",
    "must have",
    "esperienza richiesta",
    "we are looking",
)


def _prep_description(desc: str, limit: int, head: int = 800) -> str:
    """Fit a job description into ``limit`` chars for the scoring prompt WITHOUT
    dropping the requirements. A plain ``desc[:limit]`` cuts off the "Requisiti"
    block (which sits after the intro/responsibilities), so a Master/PhD role was
    scored as junior. When the requirements marker falls beyond ``limit`` we keep
    a head slice + the requirements window instead of the head alone."""
    if not desc or len(desc) <= limit:
        return desc
    low = desc.lower()
    pos = min((low.find(m) for m in _REQ_MARKERS if low.find(m) >= 0), default=-1)
    if pos < 0 or pos + 40 <= limit:
        return desc[:limit]  # requirements already inside the window (or none found)
    head_part = desc[:head].rstrip()
    tail = desc[pos : pos + max(0, limit - len(head_part) - 3)]
    return f"{head_part}\n…\n{tail}"


# Per-offer analysis schema, shared by the single-offer prompt and the batch
# prompt so both stay identical (job_detail.js depends on this exact shape:
# radar match_axes, skills_match, requisiti…). Plain string with literal braces
# — inserted by concatenation, so no f-string escaping.
#
# Field order is deliberate: the EVIDENCE comes first and the verdict last.
# ``punteggio`` used to be the first key, so the model committed to a number
# before it had written a single requirement or skill — the worst possible
# ordering for a small model, which cannot revise what it already emitted.
#
# Fields the app computes deterministically are NOT asked for (the model's
# answer was overwritten anyway): ``voto_minimo_richiesto`` and
# ``eleggibilita_geografica`` come from the hard-requirement checks. Fields
# nothing ever read were dropped outright (``junior_friendly``, duplicate of
# ``adatta_neolaureati``; ``note_azienda``; ``reputazione_azienda``, which the
# model had no way of knowing and invented). 24 keys -> 19.
_PER_OFFER_SCHEMA = """{
  "requisiti": ["max 5 requisiti chiave dell'offerta, brevi"],
  "responsabilita": ["max 5 responsabilità principali, brevi"],
  "benefit": ["max 5 benefit menzionati, brevi"],
  "titolo_studio_richiesto": "Nessuno|Diploma|Triennale|Magistrale|PhD|Non specificato",
  "anni_esperienza_richiesti": "0|1|2|3+|Non specificato",
  "livello_richiesto": "internship|entry|junior|mid|senior|lead",
  "contratto": "Dipendente|Apprendistato|Stage|Partita IVA|Non specificato",
  "tipo_ingaggio": "Dipendente|Gig a task|Freelance P.IVA|Stage|Non specificato",
  "smart_working": "Sì|No|Non specificato",
  "programmazione_richiesta": "Bassa|Media|Alta",
  "adatta_neolaureati": "Sì|No|Non specificato",
  "ral_stimata": "XX.000€-YY.000€|Non stimabile",
  "skills_match": {
    "hai": ["skills che il candidato ha e l'offerta richiede"],
    "mancano": ["skills richieste che il candidato non ha"]
  },
  "match_axes": {
    "skills_match": <0-10>,
    "seniority_match": <0-10>,
    "remote_match": <0-10>,
    "salary_match": <0-10>,
    "contract_match": <0-10>
  },
  "punti_forza": "1 frase",
  "punti_deboli": "1 frase",
  "riassunto": "2 righe max",
  "punteggio": <1-10>,
  "consiglio": "Candidati subito|Valutabile|Salta"
}"""


# Shared scoring rules appended to both prompts. The education rule exists
# because a posting requiring a Master's scored 9 for a Bachelor's CV with no
# visible gap: the model must compare hard requirements against the CV and
# make any mismatch VISIBLE (lower score + listed in "mancano").
_SCORING_RULES = (
    "REGOLE DI VALUTAZIONE:\n"
    "- Compila i campi NELL'ORDINE dello schema: prima le prove (requisiti, "
    'skills_match, match_axes), poi "punteggio" e "consiglio". Il voto deve '
    "seguire quello che hai scritto, non precederlo.\n"
    "- Scala del punteggio (usala alla lettera, non a sensazione):\n"
    "  9-10 = requisiti soddisfatti, nessun blocco, ruolo in linea con l'obiettivo;\n"
    "  7-8  = buon match, manca qualche dettaglio o UNA skill recuperabile;\n"
    "  5-6  = match parziale: mancano requisiti importanti o la seniority non torna;\n"
    "  3-4  = requisiti chiave assenti, oppure ruolo lontano dall'obiettivo;\n"
    "  1-2  = non candidabile o del tutto fuori target.\n"
    '- "consiglio" segue il punteggio: >=8 "Candidati subito", 6-7 "Valutabile", '
    '<=5 "Salta".\n'
    "- Confronta i REQUISITI dell'offerta con il CV: titolo di studio, voto minimo, "
    "anni di esperienza, livello di lingua.\n"
    "- Se l'offerta richiede un titolo di studio superiore a quello del candidato "
    "(es. laurea magistrale o PhD quando il CV ha una triennale), un voto minimo più alto "
    "del suo, più anni di esperienza, o un livello di lingua superiore: ABBASSA "
    '"punteggio" e "match_axes.seniority_match" e aggiungi il requisito mancante in '
    '"skills_match.mancano". Il gap deve essere sempre visibile, mai ignorato.\n'
    "- Il candidato NON può trasferirsi e non ha visti extra-UE: se la sede è fuori "
    "dall'Unione Europea e l'annuncio non dichiara esplicitamente apertura a chi lavora "
    'da remoto dall\'UE, "punteggio" massimo 3 e "consiglio" = "Salta".\n'
    "- Valuta ogni offerta INDIPENDENTEMENTE dalle altre: due offerte diverse non "
    'possono avere gli stessi valori di "match_axes".\n'
    "- Stipendio: usa la RAL minima/target del candidato (se indicata nelle preferenze) "
    'come metro per "match_axes.salary_match". Se l\'annuncio NON dichiara una retribuzione, '
    'scrivi "ral_stimata": "Non stimabile" e NON inventare una cifra.\n'
    '- "tipo_ingaggio": distingui un\'assunzione da un lavoro a task/piattaforma '
    "(pagamento a task o a ora, nessun monte ore garantito) e dalla partita IVA.\n"
    "- SEDE E MODALITA: confronta la sede dell'offerta e la modalita di lavoro con la "
    "modalita preferita indicata nelle preferenze del candidato. Un ruolo in sede o "
    "ibrido in una citta che il candidato non ha indicato NON e' praticabile: "
    '"punteggio" massimo 3 e "consiglio" = "Salta", anche se il contenuto del ruolo '
    "e' perfetto. Un ruolo full remote va valutato sul contenuto, non sulla citta "
    "dell'ufficio.\n"
)


def _offer_place(sede: str = "", modalita: str = "") -> str:
    """The "Sede"/"Modalita" lines of an offer block.

    These were missing from both prompts, so the model judged every posting with
    no idea where it was: an on-site role in another city could be scored 9 and
    nobody could tell it apart from one round the corner. The work mode comes
    from ``_detect_work_mode``, which reads the posting rather than the search
    flag, so it is a fact and not an assumption.
    """
    lines = ""
    if str(sede or "").strip():
        lines += f"Sede: {sede}\n"
    if str(modalita or "").strip():
        lines += f"Modalita di lavoro: {modalita}\n"
    return lines


def _analysis_prompt(
    profile_markdown: str,
    titolo: str,
    azienda: str,
    descrizione: str,
    extra_context: str = "",
    sede: str = "",
    modalita: str = "",
) -> str:
    extra = f"\nPREFERENZE CANDIDATO:\n{extra_context}\n" if extra_context.strip() else ""
    return (
        "Analizza questa offerta IT e rispondi SOLO con JSON valido, senza testo extra.\n\n"
        f"CV candidato:\n{profile_markdown[:3500]}\n{extra}\n"
        f"OFFERTA:\nTitolo: {titolo}\nAzienda: {azienda}\n"
        + _offer_place(sede, modalita)
        + f"Descrizione: {_prep_description(descrizione, 2600)}\n\n"
        + _SCORING_RULES
        + "\nJSON richiesto:\n"
        + _PER_OFFER_SCHEMA
        + "\n"
    )


def _batch_analysis_prompt(
    profile_markdown: str,
    offers: list[dict[str, Any]],
    extra_context: str = "",
) -> str:
    """Prompt for scoring N offers for the SAME candidate in one LLM call.

    The CV is sent once; offers are numbered 1..N; the model must return a JSON
    object ``{"valutazioni": [ ...N objects... ]}`` in the same order, each with
    the per-offer schema. Wrapped in an object (not a bare array) so the shared
    ``complete_json`` extractor returns a dict as everywhere else.
    """
    extra = f"\nPREFERENZE CANDIDATO:\n{extra_context}\n" if extra_context.strip() else ""
    n = len(offers)
    blocks = [
        f"--- OFFERTA {i} ---\n"
        f"Titolo: {off['titolo']}\nAzienda: {off['azienda']}\n"
        + _offer_place(str(off.get("sede", "") or ""), str(off.get("modalita", "") or ""))
        + f"Descrizione: {_prep_description(str(off['descrizione']), 2200)}"
        for i, off in enumerate(offers, 1)
    ]
    offers_text = "\n\n".join(blocks)
    return (
        f"Analizza le {n} offerte IT qui sotto per lo STESSO candidato e rispondi "
        "SOLO con JSON valido, senza testo extra.\n\n"
        f"CV candidato:\n{profile_markdown[:3500]}\n{extra}\n"
        f"OFFERTE ({n}):\n{offers_text}\n\n"
        + _SCORING_RULES
        + f'\nRispondi con un oggetto JSON con una sola chiave "valutazioni" = array di '
        f"ESATTAMENTE {n} oggetti, UNO per offerta nello STESSO ordine "
        "(OFFERTA 1 -> primo elemento). Ogni oggetto ha questo schema:\n" + _PER_OFFER_SCHEMA + "\n"
    )
