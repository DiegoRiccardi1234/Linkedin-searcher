"""What the app can say about an offer WITHOUT a model — and what it cannot.

Used when the AI is unreachable, when it answers unusably, and when the posting
is too thin to judge on merit. Until v1.7.8 this module also produced a score in
those cases, by counting words shared with the CV: on a real scan that put seven
postings no model had ever read above 6/10, a PAYROLL SPECIALIST among them.

A score is a judgement, and this module makes none. It reads the facts a regex
can honestly read from the text (contract, work mode, years, degree) and leaves
``punteggio`` empty. The result is marked as unevaluated so the job is re-scored
on the next scan, or on demand (see app.scoring_schema).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from app.log import get_logger
from app.scoring_schema import ANALYSIS_SOURCE_KEY, NOT_EVALUATED_SOURCE
from app.services.scan.hard_requirements import FLAG_SHORT_DESCRIPTION, _add_flag
from app.services.scan.vocab import TECH_KEYWORDS

log = get_logger(__name__)


def _estimate_programming_demand(offer_text: str) -> str:
    hits = sum(1 for kw in TECH_KEYWORDS if kw in offer_text)
    if hits >= 5:
        return "Alta"
    if hits >= 2:
        return "Media"
    return "Bassa"


# A number followed by "anni"/"years", optionally as a range ("3-5 anni") and
# optionally introduced by a minimum marker ("almeno 2 anni"). The range's LOWER
# bound is the bar to clear: "3-5 anni" asks for three, not five.
_YEARS_RE = re.compile(
    r"(?:almeno|minimo|min\.?|at least|oltre|più di|piu di)?\s*"
    r"(\d{1,2})\s*(?:\+|\-|–|—|/|\s+a\s+)?\s*(\d{1,2})?\s*\+?\s*(?:ann[oi]|years?|yrs?)\b",  # noqa: RUF001
    re.IGNORECASE,
)
# The number must be talking about EXPERIENCE. Without this, "azienda fondata 5
# anni fa" and "corso di 3 anni" were read as a seniority requirement.
_EXPERIENCE_CONTEXT_RE = re.compile(
    r"esperienz|experience|maturat|nel ruolo|seniority|anzianit|in ambito|di lavoro",
    re.IGNORECASE,
)

# Job boards hand us markdown-escaped punctuation ("almeno **1\-2 anni**"). The
# backslash sat between the two halves of the range, so _YEARS_RE could not join
# "1" to "anni" and matched the SECOND number instead — reading every escaped
# range at its UPPER bound. Measured on the real archive: "3\-5 anni" demanded
# five, and "1\-2 anni" demanded two, which is exactly the blocking threshold.
_MD_ESCAPE_RE = re.compile(r"\\([-–—/+*_.])")  # noqa: RUF001

# "sei mesi maturati negli ultimi 2 anni" says WHEN the experience was earned,
# not how much of it is demanded. Read as a requirement it hid an apprenticeship.
_TIME_WINDOW_RE = re.compile(r"ultim[oi]\s*$", re.IGNORECASE)

# "al termine dei due anni otterrai il Diploma ITS" is how long the PROGRAMME
# lasts. Caught on a real Lidl apprenticeship posting, which the years detector
# would otherwise have read as demanding two years of prior experience — from an
# ad whose whole point is that it wants people who have none.
_DURATION_LEAD_RE = re.compile(
    r"(?:al termine\s+(?:de[il]|dei|delle)?|(?:della\s+)?durata(?:\s+di)?"
    r"|nell.arco\s+(?:di|dei)?|percorso\s+(?:di|dei)?|corso\s+(?:di|dei)?"
    r"|programma\s+(?:di|dei)?|contratto\s+(?:di|della durata di)?"
    r"|for the (?:first|next)|over the (?:first|next))\s*$",
    re.IGNORECASE,
)

# "una realtà con oltre 30 anni di esperienza nel settore" is the COMPANY's age.
# It needs all three signals to be dismissed: something introducing the company,
# no wording that turns the number into a demand, and the "oltre/più di" shape
# these boasts always take — otherwise "l'azienda cerca almeno 3 anni" would be
# thrown away too.
_COMPANY_SUBJECT_RE = re.compile(
    r"azienda|realt|societ|gruppo|impresa|studio|siamo|fondat|nasce|opera|player|leader"
    # The English half was missing, and English is how the boast reads on an
    # international posting: "**Who we are:** OPIS is an international CRO with
    # over 25 years of experience" was read as demanding twenty-five years, on an
    # ad titled *Junior Programmer* whose real ask is one year, preferred.
    r"|who we are|about us|chi siamo|company|firm\b|\bcro\b|we are",
    re.IGNORECASE,
)
_DEMAND_RE = re.compile(
    r"almeno|minimo|richie|cerchiamo|ricerchiamo|candidat|profilo|risorsa|maturat"
    r"|possiedi|requisit|must have|we (?:are looking|require)|you have"
    # The English half, added when the company-introduction vocabulary above
    # learned English: without it, a genuine "at least 1 year of experience"
    # whose lead still carried the company's own boast was thrown away with it.
    # Every word here turns a number into a demand, so its presence is what
    # keeps the guard from firing.
    r"|at least|minimum|requirements?\b|qualifications?\b|experience:",
    re.IGNORECASE,
)
# Matched against the lead INCLUDING the number: _YEARS_RE swallows "oltre" as
# its own optional prefix, so looking only at what precedes the match never sees
# the boast that gives the company's age away.
# ``[\s*_]`` rather than ``\s``: job boards bold the number, and "con oltre
# **18 anni di esperienza**" puts two asterisks between the lead and the digits,
# which was all it took for the guard to miss the boast. That posting was titled
# *Junior Data Analyst* and carried no other number at all, so the company's age
# became the requirement.
_BOAST_RE = re.compile(
    r"(?:oltre|pi[uù] di|over|more than)[\s*_]*\d{1,2}\s*"
    r"(?:[-–—/+]\s*\d{1,2})?[\s*_]*(?:ann|year)",  # noqa: RUF001
    re.IGNORECASE,
)

#: Above this, a number of years is not a job requirement — it is a calendar year
#: ("dal 2026") or, far more often, the company's own age. The guards above catch
#: an age only when the sentence introduces the company BEFORE the number, and
#: four real postings put the subject after it: "Con oltre 40 anni di esperienza,
#: eGlue affianca…", "With 40 years of experience in monetization…, we are…",
#: "Da oltre 40 anni supportiamo…", "…da 40 anni è a fianco delle aziende".
#:
#: Widening the subject search forwards is the fix that does NOT work, and it was
#: measured before being discarded: on 466 real postings it fires on 29 — 27 of
#: them still open — and only 2 are the boast. The rest are genuine requirements
#: whose sentence happens to name a company next ("almeno 2 anni di esperienza
#: presso un'azienda del settore automotive"). A detector that fires on 29 to
#: catch 2 is the detector, not the data.
#:
#: Size is the honest discriminator instead. Every one of those boasts says 40;
#: the largest genuine requirement in the same archive is 8, and the whole
#: distribution is 0-8, 10, 12, then the two anomalies at 25 and 40. Fifteen
#: clears both and leaves 12 alone. Dropping a number never blocks anything, so
#: the failure mode here is an offer staying visible — the side to be wrong on.
_IMPLAUSIBLE_YEARS = 15

# The same requirement, written out. "Almeno quattro anni di esperienza nel ruolo
# di Project Manager" is not a rarer way of saying it than "almeno 4": measured
# on 348 real postings, 15 spell the number out, SIX of them state a genuine
# requirement — and not one of those six carried the flag. One of them was a
# senior Project Manager role sitting in the shortlist at 6/10 with no warning.
# The nine that are not requirements (a company's age, the length of an ITS
# diploma, a data-retention clause) are dismissed by the very same guards below,
# which is the reason this is a wider net and not a second detector.
_NUMBER_WORDS: dict[str, int] = {
    "un": 1, "uno": 1, "one": 1,
    "due": 2, "two": 2,
    "tre": 3, "three": 3,
    "quattro": 4, "four": 4,
    "cinque": 5, "five": 5,
    "sei": 6, "six": 6,
    "sette": 7, "seven": 7,
    "otto": 8, "eight": 8,
    "nove": 9, "nine": 9,
    "dieci": 10, "ten": 10,
}  # fmt: skip
_WORD_ALT = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_YEARS_WORD_RE = re.compile(
    r"(?:almeno|minimo|min\.?|at least|oltre|più di|piu di)?\s*"
    rf"\b({_WORD_ALT})\b"
    # "uno o due anni" asks for one, exactly like "1-2 anni" does.
    rf"(?:\s*(?:o|a|to|[-–—])\s*\b(?:{_WORD_ALT})\b)?"  # noqa: RUF001
    r"\s*(?:ann[oi]|years?|yrs?)\b",
    re.IGNORECASE,
)


def _year_matches(text: str) -> Iterator[tuple[re.Match[str], int]]:
    """Every "N years" in the text, digits or words, with N already read."""
    for match in _YEARS_RE.finditer(text):
        yield match, int(match.group(1))
    for match in _YEARS_WORD_RE.finditer(text):
        yield match, _NUMBER_WORDS[match.group(1).lower()]


#: A posting that calls itself junior asks for no years even when it names none.
#: Matched on whole words, which is not pedantry: the bare substring "intern"
#: sits inside "international", and "OPIS is an international CRO" was enough to
#: file a posting as entry level on the strength of the company's address.
_JUNIOR_HINT_RE = re.compile(
    r"\bjunior\b|\bentry.level\b|\bneolaureat|\bstage\b|\bintern(?:ship|s)?\b|\btirocin",
    re.IGNORECASE,
)


def experience_years_required(offer_text: str) -> int | None:
    """The most years the posting actually demands, or ``None`` when it demands none.

    The number itself, not the band. "3 anni" and "10 anni" land in the same band
    and sit a very different distance from someone with one year behind them, and
    only the caller knows whose CV it is measuring that distance against.

    Takes the HIGHEST requirement stated, not the first one found: a posting
    asking "1 anno di esperienza in QA, 3 anni in automation" demands three, and
    reading only the first number let it through as a one-year role. Ranges are
    read at their lower bound, and every number must sit near a word that means
    "experience" or it is not a seniority requirement at all.

    Four shapes name a number of years without demanding it, and each one was
    hiding real jobs: an escaped range, a time window, the company boasting about
    its own age, and a wish (see :func:`_years_are_preferred`).
    """
    offer_text = _MD_ESCAPE_RE.sub(r"\1", offer_text)
    best: int | None = None
    for match, low in _year_matches(offer_text):
        window = offer_text[max(0, match.start() - 70) : match.end() + 70]
        if not _EXPERIENCE_CONTEXT_RE.search(window):
            continue
        before = offer_text[max(0, match.start() - 80) : match.start()]
        if _TIME_WINDOW_RE.search(before) or _DURATION_LEAD_RE.search(before):
            continue
        # ``before + match`` on both halves, for the reason the note on
        # _BOAST_RE already gives: _YEARS_RE swallows the lead as its own
        # optional prefix, so "at least 1 year" keeps its demand word inside the
        # match and a guard reading only what precedes it never sees one.
        lead = before + match.group(0)
        if (
            _COMPANY_SUBJECT_RE.search(before)
            and not _DEMAND_RE.search(lead)
            and _BOAST_RE.search(lead)
        ):
            continue
        if _years_are_preferred(offer_text, match.start()):
            continue
        # A range is read at its lower bound: that is the bar to clear.
        if low > _IMPLAUSIBLE_YEARS:
            continue
        best = low if best is None else max(best, low)
    return best


def _estimate_experience_band(offer_text: str) -> str:
    """Years of experience the posting demands: ``0|1|2|3+|Non specificato``.

    The coarse form, for display and for the analysis schema. Everything above
    three collapses here, which is why the blocking decision reads the number
    from :func:`experience_years_required` instead.
    """
    best = experience_years_required(offer_text)
    if best is not None:
        if best <= 0:
            return "0"
        if best == 1:
            return "1"
        if best == 2:
            return "2"
        return "3+"

    return "0" if _JUNIOR_HINT_RE.search(offer_text) else "Non specificato"


#: An internship, said in a way that means the contract and not the moment.
#:
#: The bare substrings "stage" and "intern" put 322 of 469 real ads in this
#: bucket. English uses "stage" for a phase ("depending on what we agree at the
#: offer stage" — Bending Spoons), Italian lists it among kinds of experience
#: ("attività di AMS, stage e percorsi di consulenza" — NTT DATA), and "intern"
#: sits inside "internazionali", "interni/esterni" and "international". So the
#: word has to arrive attached to something that makes it a contract.
_INTERNSHIP_RE = re.compile(
    r"\btirocin|\bstagist|\binternship\b|\bintern\b"
    r"|stage\s+(?:curricular|extracurricular|formativ|retribuit|estiv)"
    r"|(?:programma|percorso|offerta|contratto|inserimento|periodo)\s+(?:di\s+)?stage"
    r"|\bin\s+stage\b|\bstage\s+(?:di|della\s+durata)",
    re.IGNORECASE,
)

#: The same word, about the candidate's PAST instead of the offer. Seven of the
#: thirteen ads the pattern above found are this: "esperienza, anche tramite
#: stage o tirocini", "esperienza pregressa di 1 anno, anche sotto forma di
#: stage", "una prima esperienza (tirocinio o primo impiego)". One of them —
#: Difa Cooper — offers a permanent role whose holder would *supervise* an
#: intern. Read as contracts they turn a salaried job into an unpaid one.
_INTERNSHIP_AS_EXPERIENCE_RE = re.compile(
    r"esperienz|maturat|pregress|primo\s+impiego|coordinare|affiancand|team\s+universitari",
    re.IGNORECASE,
)


def _is_internship(offer_text: str) -> bool:
    """True when the ad OFFERS an internship, not when it asks you to have done one.

    Each mention is judged inside its own clause, because that is the unit the
    distinction lives in: "l'inserimento avverrà in stage" and "esperienza, anche
    tramite stage" are the same word in the same ad, and only the first says what
    the contract is.
    """
    for match in _INTERNSHIP_RE.finditer(offer_text):
        clause = _clause_window(offer_text, match.start(), match.end(), 110)
        if not _INTERNSHIP_AS_EXPERIENCE_RE.search(clause):
            return True
    return False


def _estimate_contract_type(offer_text: str) -> str:
    if any(token in offer_text for token in ["apprendistat", "apprenticeship"]):
        return "Apprendistato"
    if _is_internship(offer_text):
        return "Stage"
    if any(token in offer_text for token in ["partita iva", "p.iva", "freelance", "contractor"]):
        return "Partita IVA"
    if any(
        token in offer_text
        for token in ["tempo indeterminato", "full-time", "dipendente", "permanent"]
    ):
        return "Dipendente"
    return "Non specificato"


def _estimate_smart_working(offer_text: str) -> str:
    if any(
        token in offer_text
        for token in ["remote", "full remote", "smart working", "hybrid", "ibrid"]
    ):
        return "Sì"
    if any(token in offer_text for token in ["on-site", "onsite", "in office"]):
        return "No"
    return "Non specificato"


def _fallback_analysis(
    reason: str,
    profile_markdown: str,
    titolo: str,
    azienda: str,
    descrizione: str,
) -> dict[str, Any]:
    """No model produced a usable verdict for this offer, so there is no score.

    ``reason`` (provider error / invalid response) is for diagnostics only — it
    must never leak into the user-facing ``riassunto``. ``profile_markdown`` is
    no longer read: matching a CV against the posting is exactly the guesswork
    this path stopped doing. It stays in the signature because every call site
    passes it by keyword.
    """
    log.warning("Unevaluated offer '%s' @ %s: %s", titolo, azienda, reason)
    return _unscored_analysis(titolo, azienda, descrizione, reason=reason)


_EDU_PHD = re.compile(r"\bph\.?d\b|dottorato di ricerca", re.IGNORECASE)
_EDU_MASTERS = re.compile(
    r"laurea\s+magistrale|laurea\s+specialistica|laurea\s+a\s+ciclo\s+unico"
    r"|master'?s\s+degree|\bmsc\b|\bm\.sc\b",
    re.IGNORECASE,
)
_EDU_BACHELOR = re.compile(
    r"laurea\s+triennale|laurea\s+di\s+primo\s+livello|bachelor'?s?\s+degree|\bbsc\b|\bb\.sc\b",
    re.IGNORECASE,
)
# A bare "laurea" with no level named: a three-year degree satisfies it.
_EDU_ANY_DEGREE = re.compile(r"\blaurea\b|\bdegree\b|\blaureat", re.IGNORECASE)
_EDU_DIPLOMA = re.compile(r"\bdiploma\b|\bperito\b|maturit[aà]|high school", re.IGNORECASE)

#: Ordered from lowest to highest. Used to compare what a posting asks against
#: what the candidate holds — the index IS the ranking.
EDUCATION_LEVELS: tuple[str, ...] = ("Nessuno", "Diploma", "Triennale", "Magistrale", "PhD")

# "laurea magistrale gradita" is a wish, not a gate. Without this an offer that
# explicitly says the bachelor is enough was read as demanding a master's.
# A degree listed under "elementi con attribuzione di punteggio" is the same
# thing said in the language of public-sector rankings: it earns points, it does
# not close the door — and reading it as a gate hid an apprenticeship.
_PREFERRED_RE = re.compile(
    r"preferib|preferred|gradit|costituisce\s+(?:un\s+)?(?:titolo\s+)?preferenziale|plus\b"
    r"|nice\s+to\s+have|apprezzat|desiderabil|a\s+plus\b"
    r"|attribuzione\s+di\s+punteggio|titolo\s+preferenziale|costituir[àa]\s+titolo",
    re.IGNORECASE,
)

#: How far back to look for a wish-word governing a number of years.
_PREFERENCE_LEAD = 120

#: What ends the clause a number lives in, looking BACKWARDS from it: a newline,
#: a bullet, a semicolon, or a full stop that really closes a sentence.
_LEAD_BOUNDARY_RE = re.compile(r"[\n\r;•]|(?<=[a-zà-ÿ0-9\)])\.\s|\*\s")


def _years_are_preferred(offer_text: str, start: int) -> bool:
    """True when a wish-word governs this number from inside its own clause.

    The DIRECTION is the whole rule, and it was read off 163 real firings rather
    than guessed. "preferibile esperienza almeno di 2/3 anni" makes the years a
    wish; "esperienza di 2-5 anni in software testing, preferibilmente su
    applicazioni embedded" prefers a SECTOR and demands the years all the same.
    Looking on both sides of the number cancelled ten requirements and only five
    deserved it. Clamped to the clause that precedes the number, it cancels four,
    all four correct, and misses one — a heading ("**preferred qualifications**")
    ruling the bullet below it, which is the shape :func:`_clause_window` already
    documents as the reason its own window keeps its head.

    A missed wish costs a posting shown lower than it deserves. A wrongly
    cancelled requirement costs nothing worse, now that the flag blocks: the
    offer stays visible and gets judged. Both errors point the same way here, so
    the tight rule is the one that keeps the four it is sure about.
    """
    lead = offer_text[max(0, start - _PREFERENCE_LEAD) : start]
    cuts = list(_LEAD_BOUNDARY_RE.finditer(lead))
    if cuts:
        lead = lead[cuts[-1].end() :]
    return bool(_PREFERRED_RE.search(lead))


# "bachelor's or master's degree" and "laurea triennale o magistrale" are open to
# BOTH: the master's pattern matched first and the "bachelor's or" in front of it
# was never read, so a posting that spelled out its openness to a three-year
# degree was treated as closed to one.
_EDU_EITHER = re.compile(
    r"(?:laurea\s+)?(?:triennale|bachelor'?s?(?:\s+degree)?|primo\s+livello|\bbsc\b)"
    r"\s*(?:(?:,\s*)?(?:o|od|or|e/o|and/or|oppure)\s+|\s*/\s*)"
    r"(?:laurea\s+)?(?:magistrale|specialistica|master'?s?(?:\s+degree)?|\bmsc\b)",
    re.IGNORECASE,
)


# Where one requirement ends and the next begins: a line break, a bullet, a
# semicolon, or a full stop that really closes a sentence. The last one needs the
# lookahead: "laurea magistrale in ing. elettronica" would otherwise end the
# clause at the abbreviation, and since this window only ever carries a veto,
# cutting it short means blocking more than the posting asks for.
_CLAUSE_BOUNDARY_RE = re.compile(
    r"[\n\r;•]|(?<=[a-zà-ÿ0-9\)])\.\s+(?=[A-ZÀ-Ý•*\-])",
)


def _clause_window(offer_text: str, start: int, end: int, span: int) -> str:
    """``span`` characters around a match, stopped at the NEXT clause boundary.

    Only the tail is clamped, and the asymmetry is the posting's, not ours: text
    that comes before a requirement often governs it — "Elementi con
    attribuzione di punteggio. Se possiedi: * Laurea Magistrale" is a heading
    ruling the list under it — while the next bullet is simply the next
    requirement, about something else entirely.

    Use only for windows carrying a VETO, a word whose presence cancels the match
    beside it. EY's "Laurea magistrale STEM;" is followed 68 characters later by
    "Fortemente gradita una minima esperienza", and a blind 90-character tail
    read that wish as being about the degree: the posting scored 9/10 with no
    blocker against a three-year degree.

    Deliberately NOT used for windows carrying a QUALIFIER — a word that must be
    present for the match to count, like the "esperienza" that turns a number
    into a seniority demand. Measured on the 238-posting archive, clamping the
    experience window destroyed 8 genuine requirements ("esperienza di almeno 2
    anni nel ruolo", "minimum 6+ years"), because job boards routinely put the
    noun on the line above the number; the same clamp on the protected-category
    veto lost the single truly reserved posting out of the 29 citing the law.
    """
    after = offer_text[end : end + span]
    boundary = _CLAUSE_BOUNDARY_RE.search(after)
    tail = after[: boundary.start()] if boundary else after
    return offer_text[max(0, start - span) : end] + tail


def education_requirement(offer_text: str) -> tuple[str, bool]:
    """``(required level, is only preferred)`` read from the posting.

    The second value matters as much as the first: a posting where the master's
    is "preferibile" must not be treated as closed to a bachelor. It is read
    within the degree's own clause (see :func:`_clause_window`) — a preference
    voiced in the next bullet is a preference about the next bullet.
    """
    for level, pattern in (
        ("PhD", _EDU_PHD),
        ("Magistrale", _EDU_MASTERS),
        ("Triennale", _EDU_BACHELOR),
    ):
        match = pattern.search(offer_text)
        if match:
            window = _clause_window(offer_text, match.start(), match.end(), 90)
            if level == "Magistrale" and _EDU_EITHER.search(window):
                # The same sentence offers the bachelor as an alternative.
                return "Triennale", bool(_PREFERRED_RE.search(window))
            return level, bool(_PREFERRED_RE.search(window))
    if _EDU_ANY_DEGREE.search(offer_text):
        # "laurea in informatica" with no level: a bachelor clears it.
        return "Triennale", False
    if _EDU_DIPLOMA.search(offer_text):
        return "Diploma", False
    return "Non specificato", False


def _detect_education_requirement(offer_text: str) -> str:
    """Required degree as the analysis schema spells it."""
    return education_requirement(offer_text)[0]


def _local_facts(titolo: str, descrizione: str) -> dict[str, Any]:
    """What the posting states about itself, read with regexes.

    Facts, not judgements: every field here is something the text says, so it
    stays true whether or not a model ever looked at the offer. Shared by the
    unevaluated path and by the hard-blocked path, which has a score of its own.
    """
    offer_text = f"{titolo} {descrizione}".lower()
    return {
        "programmazione_richiesta": _estimate_programming_demand(offer_text),
        "smart_working": _estimate_smart_working(offer_text),
        "contratto": _estimate_contract_type(offer_text),
        "anni_esperienza_richiesti": _estimate_experience_band(offer_text),
        "titolo_studio_richiesto": _detect_education_requirement(offer_text),
        "adatta_neolaureati": "Sì"
        if any(token in offer_text for token in ["junior", "stage", "intern", "entry"])
        else "Non specificato",
    }


def _unscored_analysis(
    titolo: str, azienda: str, descrizione: str, *, reason: str
) -> dict[str, Any]:
    """An offer nobody judged: the facts it states, and no verdict.

    ``punteggio`` is None rather than a low number — a low number is still a
    judgement, and it would sort among real ones. ``_normalize_analysis``
    enforces the rest (empty advice, null axes, the ``non_valutato`` flag) and
    leaves out the schema version, which is what gets the job re-scored later.
    """
    return {
        ANALYSIS_SOURCE_KEY: NOT_EVALUATED_SOURCE,
        "punteggio": None,
        "consiglio": "",
        # Diagnostics: why no model produced a verdict. Not user-facing copy.
        "motivo_non_valutazione": reason,
        "punti_forza": "",
        "punti_deboli": "",
        "riassunto": "",
        "ral_stimata": "Non stimabile",
        **_local_facts(titolo, descrizione),
    }


# Below this many chars a description carries no requirements/seniority signal
# (real case: an 82-char marketing blurb) — LLM-scoring it just hallucinates.
# Such jobs take the honest capped path instead. Length measured post-clean.
MIN_DESCRIPTION_CHARS = 300


def _insufficient_description_analysis(
    profile_markdown: str, titolo: str, azienda: str, descrizione: str = ""
) -> dict[str, Any]:
    """A job whose description is missing or too short to judge on merit
    (LinkedIn blocked the page, or served a marketing blurb without the JD).

    Nobody can judge a posting nobody could read, so it gets no score — not even
    a capped one. Sending it to the LLM anyway is worse: on a real 82-character
    blurb the model invented requirements wholesale. The offer stays visible,
    flagged, with the one useful instruction: open the ad.
    """
    result = _unscored_analysis(titolo, azienda, descrizione, reason="insufficient_description")
    _add_flag(result, FLAG_SHORT_DESCRIPTION)
    if descrizione.strip():
        result["riassunto"] = "Descrizione troppo breve per valutare. Apri l'annuncio."
        result["punti_deboli"] = (
            "Descrizione quasi assente: requisiti ed esperienza richiesta non verificati."
        )
    else:
        result["riassunto"] = "Descrizione non disponibile. Apri l'annuncio."
        result["punti_deboli"] = (
            "Descrizione non recuperata: requisiti ed esperienza richiesta non verificati."
        )
    return result
