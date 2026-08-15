"""The title has to name the trade, or the posting is not worth an LLM call.

The old relevance gate read title+description and fired only on ZERO overlap
with the domain vocabulary — a bar no corporate ad ever fails, since "data",
"software" and "cloud" appear in all of them. Measured on 47 real postings: 26
were off-domain from the title alone and 11 of those still scored >=6, all of
them from one over-broad search term ("AI Specialist", which job boards happily
match against payroll, sales and partnership roles).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.config import load_settings
from app.db import Database
from app.models import ScanRequest
from app.services import scanner_service as ss

# The vocabulary is the USER's, not the app's: built from the terms they search,
# the skills on their CV and the roles they said they want. These are the real
# ones from the 2026-07-27 scan.
_VOCAB = ss.title_vocabulary(
    search_terms=["LLM Evaluation", "AI QA", "AI Specialist"],
    skills=["Python", "Java", "TypeScript", "React", "PostgreSQL", "Git", "Quarkus"],
    roles=["AI QA / LLM Evaluation", "AI Specialist", "QA Engineer", "Prompt Engineer"],
)
_SKILLS = _VOCAB

# Real titles from the 2026-07-27 scan, with the score they were given.
_OFF_TOPIC = [
    "PAYROLL SPECIALIST",
    "Application Specialist - sistemi di Back Office",
    "Security Associate Specialist",
    "DIGITAL COMMUNICATION SPECIALIST",
    "Partnership Specialist",
    "PRODUCT INSIGHTS SPECIALIST",
    "SALES AND SERVICE SPECIALIST - BRESCIA",
    "TECHNICAL TRAINER & PROMOTER",
    "RAI Specialist",  # the title alone says nothing — see the rescue below
    "Key Account Manager",
    "Aiuto Cuoco Spa",
]

_ON_TOPIC = [
    "Automation tester - AI - Augmented QA",
    "AI Specialist INTERN",
    "Data Analyst - AI Translation Quality",
    "ML Engineer – Generative AI",
    "Junior AI & Digital Automation Specialist",
    "Prompt Engineer",
    "React Developer",  # the CV's own skills count as the trade too
]


@pytest.mark.parametrize("titolo", _OFF_TOPIC)
def test_off_topic_titles_are_dropped(titolo: str) -> None:
    assert ss.title_off_topic(titolo, _SKILLS) is True


@pytest.mark.parametrize("titolo", _ON_TOPIC)
def test_on_topic_titles_are_kept(titolo: str) -> None:
    assert ss.title_off_topic(titolo, _SKILLS) is False


def test_two_letter_trades_are_visible() -> None:
    """``_tokenize`` needs three characters, so a gate built on it would have
    missed "AI", "QA" and "ML" — the very words that matter here."""
    vocab = ss.title_vocabulary(search_terms=["AI QA", "ML Engineer"])
    for titolo in ("AI Specialist", "QA Engineer", "ML Ops"):
        assert ss.title_off_topic(titolo, vocab) is False


def test_entry_routes_survive_without_naming_the_trade() -> None:
    """"Tirocinio curriculare" names no trade, and dropping it would cut exactly
    the openings a recent graduate is looking for."""
    for titolo in ("Tirocinio curriculare", "Graduate Program 2026", "Stage in azienda"):
        assert ss.title_off_topic(titolo, _VOCAB) is False


def test_candidate_skills_widen_the_gate() -> None:
    narrow = ss.title_vocabulary(search_terms=["AI QA"])
    assert ss.title_off_topic("Quarkus Consultant", narrow) is True
    assert ss.title_off_topic("Quarkus Consultant", _VOCAB) is False


def test_nothing_known_about_the_user_means_nothing_is_filtered() -> None:
    """There is no built-in vocabulary to fall back on any more.

    There used to be one, and it contained "software" and "data" — words every
    corporate ad repeats — so as a rescue it waved everything through, and as a
    gate it dropped every trade it had never heard of. With nothing to go on,
    both checks now stand down: the scan refuses long before this point unless
    the user has said what they are looking for.
    """
    boilerplate = "Gestione dei dati e dei software aziendali. " * 5
    assert ss.description_on_topic(boilerplate, set()) is False
    assert ss.title_off_topic("Qualsiasi titolo", set()) is False


# --- the vocabulary belongs to the user, not to this app ---------------------


def test_the_gate_works_for_a_trade_this_app_never_heard_of() -> None:
    """A nurse searching "infermiere pediatrico" must not have every posting
    dropped by a gate that only knows AI and software words. The vocabulary is
    built from what THEY asked for."""
    nurse = ss.title_vocabulary(
        search_terms=["Infermiere pediatrico", "OSS"],
        skills=["Assistenza pediatrica", "Triage"],
        roles=["Infermiere"],
    )
    assert ss.title_off_topic("Infermiere pediatrico - reparto", nurse) is False
    assert ss.title_off_topic("Operatore socio sanitario (OSS)", nurse) is False
    assert ss.title_off_topic("AI QA Engineer", nurse) is True  # not their trade
    # And the AI-shaped default no longer decides for them.
    assert ss.title_off_topic("PAYROLL SPECIALIST", nurse) is True


def test_a_bare_role_word_in_a_search_term_teaches_the_gate_nothing() -> None:
    """Searching "AI Specialist" must not make "PAYROLL SPECIALIST" on-topic."""
    vocab = ss.title_vocabulary(search_terms=["AI Specialist"])
    assert "specialist" not in vocab
    assert ss.title_off_topic("PAYROLL SPECIALIST", vocab) is True
    assert ss.title_off_topic("AI Specialist", vocab) is False


# --- the rescue: a title that hides the trade behind an acronym ---------------

# Shortened from the real Accenture posting for "RAI Specialist".
_RESPONSIBLE_AI_JD = (
    "Come Responsible AI Specialist contribuirai alla progettazione di architetture AI "
    "affidabili. Lavorerai su AI governance, sul testing dei sistemi AI e sulla "
    "valutazione dei prompt in ambito GenAI, con il team QA di prodotto. Richiesta "
    "esperienza con Python e con framework di AI evaluation."
)

# Shortened from the real "PAYROLL SPECIALIST" and "Application Specialist" ads:
# one stray "AI" inside a company boilerplate paragraph.
_BOILERPLATE_JD = (
    "Azienda leader nel settore, con un percorso di trasformazione digitale che include "
    "anche progetti AI, cerca una figura per la gestione amministrativa del personale."
)


def test_a_domain_rich_description_overrules_a_silent_title() -> None:
    """"RAI Specialist" is a Responsible AI role: the title says nothing, the ad
    says it over and over. Losing it would be the gate's worst failure."""
    assert ss.title_off_topic("RAI Specialist", _VOCAB) is True
    assert ss.description_on_topic(_RESPONSIBLE_AI_JD, _VOCAB) is True


def test_one_stray_mention_does_not_rescue_a_payroll_ad() -> None:
    """The old gate fired on zero overlap, so a single "AI" saved anything."""
    assert ss.description_on_topic(_BOILERPLATE_JD, _VOCAB) is False


def test_the_italian_preposition_ai_is_not_the_acronym_AI() -> None:
    """"ai clienti", "ai dati", "ai processi": counting those case-insensitively
    would rescue every Italian posting ever written."""
    italian = (
        "Ti occuperai di fornire supporto ai clienti, di rispondere ai loro quesiti "
        "amministrativi e di dare seguito ai processi interni, affiancando i colleghi "
        "ai vari livelli e partecipando ai progetti di miglioramento continuo."
    )
    assert ss.description_on_topic(italian, _VOCAB) is False


# --- bait postings and aggregators -------------------------------------------


def test_spontaneous_application_is_not_a_position() -> None:
    """It scored 8/10 on a real scan. There is no role in it to score."""
    assert ss.is_bait_posting("Candidatura Spontanea in Joinrs | RAL €22K – €27K") is True
    assert ss.is_bait_posting("Spontaneous Application - Talent Pool") is True
    assert ss.is_bait_posting("AI QA Engineer") is False


def test_aggregator_is_flagged_not_dropped() -> None:
    """A job board sometimes reposts a real role: say who is really hiring."""
    out = ss.enforce_hard_requirements(
        {"punteggio": 7},
        profile_markdown="CV",
        descrizione="Valutazione modelli. " * 20,
        sede="Torino, Italy",
        azienda="Jobbydoo",
    )
    assert ss.FLAG_AGGREGATOR in out["blocchi"]
    assert out["punteggio"] == 7, "a flag, never a cap"


# --- the gate inside a real scan ---------------------------------------------

_COLS = [
    "title",
    "company",
    "description",
    "location",
    "site",
    "job_url",
    "min_amount",
    "max_amount",
]


def _row(title: str, company: str = "Co") -> dict[str, object]:
    return {
        "title": title,
        "company": company,
        # A full, plausible corporate description: the OLD gate let every one of
        # these through, because "dati" and "software" are in all of them.
        "description": (
            "Azienda leader cerca una figura da inserire nel team. Si occuperà di "
            "gestire i dati, usare i software aziendali e collaborare con il team. "
            "Requisiti: diploma, buone capacità relazionali, inglese. " * 3
        ),
        "location": "Milano, Italy",
        "site": "linkedin",
        "job_url": f"https://example.com/{abs(hash(title))}",
        "min_amount": None,
        "max_amount": None,
    }


class _PM:
    def __init__(self) -> None:
        self.calls = 0

    def preview_scoring_model(self, _p: object) -> str:
        return "m"

    def clear_model_penalties(self, reason: str | None = None) -> None:
        pass

    def complete_json(self, prompt: str, **_k: object) -> object:
        self.calls += 1
        n = prompt.count("--- OFFERTA ")
        return {"valutazioni": [{"punteggio": 7}] * n} if n else {"punteggio": 7}


def test_scan_drops_off_topic_titles_before_spending_a_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    df = pd.DataFrame(
        [
            _row("PAYROLL SPECIALIST", "Reverse"),
            _row("Partnership Specialist", "Sirti"),
            _row("Candidatura Spontanea in Joinrs", "Joinrs"),
            _row("AI QA Engineer", "Acme"),
        ],
        columns=_COLS,
    )
    monkeypatch.setattr(ss, "scrape_jobs", lambda **k: df)
    settings = load_settings(tmp_path)
    settings.delay_tra_ricerche = 0.0
    pm = _PM()
    db = Database(tmp_path / "t.db")
    try:
        # A real term, because the vocabulary IS the user's terms now: with
        # nothing recognisable to gate on, the gate correctly stands down.
        events = list(
            ss.run_scan(
                db,
                settings,
                pm,
                ScanRequest(search_terms=["AI QA"], sites=["linkedin"], location="Milano"),
            )
        )
    finally:
        db.close()
    complete = next(e for e in events if e.get("status") == "complete")
    analyzed = [e for e in events if e.get("status") == "analyzed"]
    assert [e["job"]["titolo"] for e in analyzed] == ["AI QA Engineer"]
    assert complete["scartati_per_titolo"] == 2  # the two Specialist roles
    assert complete["totale_scartati"] >= 3  # plus the bait posting
    assert pm.calls == 1, "three postings were dropped before any LLM call"
