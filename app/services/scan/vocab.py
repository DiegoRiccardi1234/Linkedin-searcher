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

# Domain vocabulary (bilingual it+en) for the relevance gate: a scraped job whose
# title+description shares NONE of these — nor any of the candidate's own skill
# tokens — is off-topic (kitchen/spa/food-QC/pharma) and dropped before wasting an
# LLM scoring call. Deliberately excludes generic role words (analyst/engineer/
# quality) so it keys on the actual tech/AI/data domain, not the fuzzy match.
_DOMAIN_VOCAB = {
    # AI / data / ML
    "ai",
    "ml",
    "nlp",
    "llm",
    "genai",
    "data",
    "dati",
    "dataset",
    "analytics",
    "analisi",
    "machine",
    "learning",
    "apprendimento",
    "deep",
    "neural",
    "rete",
    "reti",
    "model",
    "modelli",
    "modello",
    "algorithm",
    "algoritmo",
    "algoritmi",
    "intelligenza",
    "artificiale",
    "annotation",
    "annotazione",
    "labeling",
    "etichettatura",
    "linguistic",
    "linguistica",
    "linguistico",
    "computational",
    "computazionale",
    "prompt",
    "embedding",
    # software / dev
    "software",
    "sviluppo",
    "sviluppatore",
    "developer",
    "development",
    "programmazione",
    "programming",
    "coding",
    "informatica",
    "informatico",
    "backend",
    "frontend",
    "fullstack",
    "api",
    "database",
    "cloud",
    "devops",
    "python",
    "java",
    "javascript",
    "typescript",
    "react",
    "node",
    "sql",
    "docker",
    # Entry routes. Without these a posting titled only "Tirocinio curriculare"
    # — no description, so the relevance gate judges the title alone — shares no
    # word with the vocabulary and is dropped before anyone can read it.
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


# What the TITLE has to say for the posting to be worth reading. Narrower than
# ``_DOMAIN_VOCAB`` on purpose: that list is matched against title+description,
# where words like "data", "software" and "cloud" appear in every corporate ad —
# which is why the gate below dropped nothing. A title names the trade, and a
# trade this candidate does not practise is not worth an LLM call.
#
# Measured on 47 real postings: this keeps 21 and drops 26, and every dropped
# posting that had scored >=7 was a false positive (Application Specialist,
# Security Associate Specialist, PAYROLL SPECIALIST, RAI Specialist…).
_TITLE_DOMAIN = {
    # AI / data / language
    "ai",
    "a.i",
    "ia",
    "ml",
    "nlp",
    "llm",
    "genai",
    "data",
    "dati",
    "dataset",
    "annotation",
    "annotator",
    "annotazione",
    "annotatore",
    "labeling",
    "prompt",
    "machine",
    "learning",
    "deep",
    "artificial",
    "artificiale",
    "intelligence",
    "intelligenza",
    "linguistic",
    "linguistica",
    "linguistico",
    "linguist",
    "computational",
    "computazionale",
    # quality / testing / automation
    "qa",
    "test",
    "tester",
    "testing",
    "quality",
    "automation",
    "automazione",
    "evaluation",
    "valutazione",
    # software
    "software",
    "developer",
    "sviluppatore",
    "sviluppatrice",
    "sviluppo",
    "engineer",
    "engineering",
    "programmatore",
    "informatico",
    "informatica",
    "backend",
    "frontend",
    "fullstack",
    "full-stack",
    "devops",
    "python",
    "java",
    "javascript",
    "typescript",
    "react",
    "sql",
}

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


def _title_tokens(titolo: str) -> set[str]:
    """Words of a job title, down to two letters."""
    return {token.strip(".-") for token in _TITLE_TOKEN_RE.findall(titolo.lower())}


#: Role words that name no trade: they sit in payroll, sales and partnership
#: titles just as happily as in a technical one. Dropped from the vocabulary
#: built below, or searching "AI Specialist" would teach the gate that
#: "PAYROLL SPECIALIST" is on topic.
_VAGUE_ROLE_WORDS = {
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

    :data:`_TITLE_DOMAIN` remains as the fallback for an empty profile, which is
    what a first scan on a fresh install looks like.
    """
    tokens: set[str] = set()
    for source in (search_terms, skills, roles):
        for item in source or []:
            tokens |= {
                token for token in _title_tokens(str(item)) if token not in _VAGUE_ROLE_WORDS
            }
    return (tokens | _TITLE_ENTRY_ROUTES) if tokens else set()


def default_title_vocabulary() -> set[str]:
    """The fallback for an empty profile — a first scan on a fresh install.

    Kept apart from :func:`title_vocabulary` because the two are used
    differently: this list is broad enough to gate a title on, but too broad to
    RESCUE one with. It contains "software" and "data", which every corporate ad
    repeats, so counting them in a description would wave everything through —
    precisely the hole the old gate had.
    """
    return _TITLE_DOMAIN | _TITLE_ENTRY_ROUTES


def title_off_topic(titolo: str, allowed_tokens: set[str] | None = None) -> bool:
    """True when the title names no trade in ``allowed_tokens``.

    The relevance gate used to read title+description and fire only on ZERO
    overlap, which no corporate posting ever reaches. Reading the title alone,
    against the user's own vocabulary, is what actually separates "this is my
    job" from "this ad contains words I know".
    """
    tokens = _title_tokens(titolo)
    if not tokens:
        return False  # nothing to judge: keep it and let the rest decide
    allowed = allowed_tokens or default_title_vocabulary()
    return not (tokens & allowed)


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
    # No text, or no idea what this user is after: nothing to rescue WITH. The
    # broad fallback list is deliberately not used here (see
    # :func:`default_title_vocabulary`).
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
