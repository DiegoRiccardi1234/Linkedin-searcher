"""Word lists the scan reasons with, and the tokenizer that reads them.

Pure data plus two tiny functions: no I/O, no app imports, no LLM. Extracted
from ``scanner_service`` where 160 lines of vocabulary sat in the middle of the
orchestration code.
"""

from __future__ import annotations

import re

BLACKLIST = [
    "senior developer",
    "senior engineer",
    "senior consultant",
    "senior analyst",
    "lead developer",
    "principal engineer",
    "5+ anni",
    "4+ anni",
    "partita iva",
    "p.iva",
    "freelance",
    "cto",
    "ciso",
]

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "have",
    "has",
    "will",
    "your",
    "you",
    "our",
    "all",
    "per",
    "con",
    "dei",
    "delle",
    "della",
    "dell",
    "una",
    "uno",
    "sono",
    "come",
    "sulla",
    "sulle",
    "degli",
    "nella",
    "nelle",
    "into",
    "about",
    "role",
    "lavoro",
    "lavori",
    "offerta",
    "annuncio",
    "candidate",
    "team",
    "company",
}

TECH_KEYWORDS = {
    "python",
    "java",
    "javascript",
    "typescript",
    "react",
    "node",
    "sql",
    "docker",
    "kubernetes",
    "aws",
    "azure",
    "gcp",
    "api",
    "fastapi",
    "django",
    "selenium",
    "playwright",
    "testing",
    "qa",
    "data",
    "analytics",
    "machine",
    "learning",
}

# There used to be two fixed word lists here — one for titles, one matched
# against title+description — and both described exactly one trade: the AI
# and software vocabulary of the person this app was first written for. A
# nurse's postings share no word with either, so they were dropped before
# anyone could read them. The vocabulary is now built from what THIS user
# asked for, on their CV and in their goals (see :func:`title_vocabulary`),
# and when there is nothing to build it from the gate simply does not run.

#: Entry-level routes never name the trade in the title ("Tirocinio curriculare",
#: "Graduate Program"), and dropping them would cut exactly the openings a recent
#: graduate is looking for.
_TITLE_ENTRY_ROUTES = {
    "tirocinio",
    "tirocinante",
    "stage",
    "stagista",
    "internship",
    "intern",
    "trainee",
    "apprendistato",
    "apprenticeship",
    "neolaureato",
    "neolaureati",
    "graduate",
}

#: Two-letter trades ("AI", "QA", "ML") are invisible to :func:`_tokenize`, whose
#: pattern needs three characters — so a title gate built on it would have missed
#: "AI QA Engineer" and kept "PAYROLL SPECIALIST".
_TITLE_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.\-]+")


def title_tokens(titolo: str) -> set[str]:
    """Words of a job title, down to two letters."""
    return {token.strip(".-") for token in _TITLE_TOKEN_RE.findall(titolo.lower())}


#: Role words that name no trade: they sit in payroll, sales and partnership
#: titles just as happily as in a technical one. Dropped from the vocabulary
#: built below, or searching "AI Specialist" would teach the gate that
#: "PAYROLL SPECIALIST" is on topic.
VAGUE_ROLE_WORDS = {
    "specialist",
    "specialista",
    "consultant",
    "consulente",
    "analyst",
    "analista",
    "manager",
    "coordinator",
    "coordinatore",
    "operator",
    "operatore",
    "assistant",
    "assistente",
    "junior",
    "senior",
    "lead",
    "expert",
    "esperto",
    "addetto",
    "responsabile",
    "impiegato",
    "profilo",
    "figura",
    "role",
    "ruolo",
    "remote",
    "remoto",
    "full",
    "time",
    "part",
}


def title_vocabulary(
    *,
    search_terms: list[str] | None = None,
    skills: list[str] | None = None,
    roles: list[str] | None = None,
) -> set[str]:
    """The words that, FOR THIS USER, mean "this is my trade".

    Built from what the user actually asked for — the terms of this scan, the
    skills on their CV, the roles they said they want — rather than from a fixed
    list. A fixed list would work only for the trade it was written for: someone
    searching "infermiere pediatrico" would have every posting dropped by a gate
    that only recognises AI and software words.

    Returns an empty set when there is nothing to build from: the callers
    treat that as "do not filter", not as "use a default trade".
    """
    tokens: set[str] = set()
    for source in (search_terms, skills, roles):
        for item in source or []:
            tokens |= {token for token in title_tokens(str(item)) if token not in VAGUE_ROLE_WORDS}
    return (tokens | _TITLE_ENTRY_ROUTES) if tokens else set()


def title_off_topic(titolo: str, allowed_tokens: set[str]) -> bool:
    """True when the title names no trade in ``allowed_tokens``.

    The relevance gate used to read title+description and fire only on ZERO
    overlap, which no corporate posting ever reaches. Reading the title alone,
    against the user's own vocabulary, is what actually separates "this is my
    job" from "this ad contains words I know".

    The vocabulary is required: it used to default to a fixed list, which meant
    a caller with nothing to say about the user silently got somebody else's
    trade. An empty set keeps everything, which is the right answer when we
    know nothing.
    """
    tokens = title_tokens(titolo)
    if not tokens or not allowed_tokens:
        return False  # nothing to judge: keep it and let the rest decide
    return not (tokens & allowed_tokens)


#: How often the user's own words must appear in a posting for it to overrule a
#: title that says nothing. Measured on 47 real postings with a real profile:
#: the Responsible AI role hidden behind "RAI Specialist" scored 27, a frontend
#: role matching the candidate's own React/TypeScript scored 8, and every
#: off-domain ad scored 4 or less. A single stray mention rescues nothing.
MIN_DESCRIPTION_MATCHES = 6


def description_on_topic(
    descrizione: str,
    allowed_tokens: set[str] | None = None,
    min_matches: int = MIN_DESCRIPTION_MATCHES,
) -> bool:
    """True when the posting talks about this trade throughout, not in passing.

    The rescue for a title that hides the trade behind an acronym. Real case:
    "RAI Specialist" at Accenture is a RESPONSIBLE AI role — a genuine match the
    title gate would have thrown away, since "RAI" is also a television network.

    Short tokens are matched case-SENSITIVELY: "ai" is an everyday Italian
    preposition ("ai clienti", "ai dati") and would score dozens of hits in any
    ad whatsoever, while "AI" is the acronym that actually means something.
    """
    text = descrizione or ""
    # No text, or no idea what this user is after: nothing to rescue WITH.
    if not text or not allowed_tokens:
        return False
    allowed = allowed_tokens
    total = 0
    for token in allowed:
        if len(token) < 2:
            continue
        if len(token) <= 3:
            total += len(re.findall(rf"\b{re.escape(token.upper())}\b", text))
        else:
            total += len(re.findall(rf"\b{re.escape(token)}\b", text, re.IGNORECASE))
        if total >= min_matches:
            return True
    return False


def pre_filtro(titolo: str, descrizione: str) -> tuple[bool, str]:
    testo = (titolo + " " + descrizione).lower()
    for frase in BLACKLIST:
        if frase in testo:
            return True, frase
    return False, ""


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9+#\.-]{2,}", text.lower())
    return {token for token in tokens if token not in STOPWORDS}
