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


def title_off_topic(titolo: str, skill_tokens: set[str] | None = None) -> bool:
    """True when the title names no trade the candidate practises.

    The relevance gate used to read title+description and fire only on ZERO
    overlap, which no corporate posting ever reaches. Reading the title alone,
    against a narrow list, is what actually separates "this is my job" from
    "this ad contains words I know".
    """
    tokens = _title_tokens(titolo)
    if not tokens:
        return False  # nothing to judge: keep it and let the rest decide
    allowed = _TITLE_DOMAIN | _TITLE_ENTRY_ROUTES | (skill_tokens or set())
    return not (tokens & allowed)


# The rescue for a title that hides the trade behind an acronym. Real case:
# "RAI Specialist" at Accenture is a RESPONSIBLE AI role — a genuine match the
# title gate would have thrown away, since "RAI" is also a television network.
#
# Deliberately narrow: no "dati", no "software", no "cloud". Those are in every
# corporate ad, which is precisely why the old description gate never fired. And
# it takes SEVERAL distinct markers, not one: measured on 47 real postings, the
# Responsible AI role hit 5 of these, while every off-domain posting hit at most
# 2 — one stray "AI" in a company boilerplate paragraph is not a subject.
_STRONG_DOMAIN_RE = re.compile(
    r"\b(a\.?i\.?|ml|nlp|llm|genai|machine learning|deep learning|intelligenza artificiale"
    r"|artificial intelligence|responsible ai|prompt|annotation|annotazione|labeling|dataset"
    r"|qa|quality assurance|testing|test automation|automation|automazione"
    r"|valutazione dei modelli|model evaluation|hallucination|generative ai|gen ai)\b",
    re.IGNORECASE,
)

#: How many DISTINCT strong markers a description needs to overrule the title.
MIN_STRONG_DOMAIN_HITS = 3


def description_on_topic(descrizione: str, min_hits: int = MIN_STRONG_DOMAIN_HITS) -> bool:
    """True when the posting is about this trade for more than one stray word."""
    hits = {match.group(0).lower() for match in _STRONG_DOMAIN_RE.finditer(descrizione or "")}
    return len(hits) >= min_hits


def pre_filtro(titolo: str, descrizione: str) -> tuple[bool, str]:
    testo = (titolo + " " + descrizione).lower()
    for frase in BLACKLIST:
        if frase in testo:
            return True, frase
    return False, ""


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9+#\.-]{2,}", text.lower())
    return {token for token in tokens if token not in STOPWORDS}
