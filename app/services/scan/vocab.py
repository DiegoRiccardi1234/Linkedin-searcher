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
}


def pre_filtro(titolo: str, descrizione: str) -> tuple[bool, str]:
    testo = (titolo + " " + descrizione).lower()
    for frase in BLACKLIST:
        if frase in testo:
            return True, frase
    return False, ""


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9+#\.-]{2,}", text.lower())
    return {token for token in tokens if token not in STOPWORDS}
