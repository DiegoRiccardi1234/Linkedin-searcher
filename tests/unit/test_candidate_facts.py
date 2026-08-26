"""The user's declared constraints must actually block offers.

Every case here comes from a real scan (04/08/2026): out of 35 offers scored 8
or more, eight demanded 1-2 years of experience — stated in the analysis' own
``anni_esperienza_richiesti`` field — one demanded a master's against a
bachelor, and on-site roles in other cities were recommended because the
posting's location never reached the model at all.
"""

from __future__ import annotations

from app.db import Database
from app.services import candidate_facts as cf
from app.services.scan.heuristics import _estimate_experience_band, education_requirement
from app.services.scan.hard_requirements import WEIGHTED_FLAGS, hard_block_reason
from app.services.scanner_service import (
    FLAG_EDUCATION,
    FLAG_EXPERIENCE,
    FLAG_LOCATION,
    BLOCKING_FLAGS,
    _detect_work_mode,
    enforce_hard_requirements,
)

_CV = "Laurea Triennale in Informatica, votazione 95/110. Python, React."
_JD = "Ruolo di analisi funzionale in team di prodotto, raccolta requisiti. " * 12


def _facts(**over: object) -> cf.CandidateFacts:
    base = {
        "years_experience": 0,
        "education_level": "Triennale",
        "grade": 95,
        "work_rule": cf.WorkRule(cities=("torino",), allow_remote=True),
    }
    base.update(over)
    return cf.CandidateFacts(**base)  # type: ignore[arg-type]


# ── work mode, read from the posting's own words ─────────────────────────────


def test_work_mode_reads_the_real_postings() -> None:
    """The two sentences that produced false "Full Remote" labels."""
    # Argo Logica, stored as Full Remote and shortlisted as such.
    argo = "Possibilità di lavorare da remoto (fino a 2 giornate su 5 settimanali), buono pasto."
    assert _detect_work_mode({}, argo, "Full Remote") == "Ibrido"
    # MESA: "ibride" is plural, and the old \bibrid[ao] missed it, so the text
    # fell through to "smart working" which used to mean full remote.
    mesa = "Orari flessibili e soluzioni ibride di smart working."
    assert _detect_work_mode({}, mesa, "Full Remote") == "Ibrido"
    # A genuine full-remote claim still reads as one.
    assert _detect_work_mode({}, "Posizione full remote.", "In sede") == "Full Remote"
    assert _detect_work_mode({}, "Sede di lavoro: da remoto", "In sede") == "Full Remote"


def test_work_mode_text_outranks_the_board_flag() -> None:
    onsite = "Il ruolo prevede lavoro in sede presso lo stabilimento."
    assert _detect_work_mode({"is_remote": True}, onsite, "Full Remote") == "In sede"


# ── how many years the posting asks for ──────────────────────────────────────


def test_experience_band_takes_the_highest_requirement() -> None:
    # Reading only the first number let a three-year role look like a one-year one.
    assert _estimate_experience_band("1 anno di esperienza in qa, 3 anni di esperienza in java") == "3+"
    # Investech: "esperienza di almeno 3/4 anni" — a range asks for its lower bound.
    assert _estimate_experience_band("esperienza di almeno 3/4 anni come analista") == "3+"
    assert _estimate_experience_band("almeno 2 anni di esperienza in test automation") == "2"
    # A number that isn't about experience is not a seniority requirement.
    assert _estimate_experience_band("azienda fondata 5 anni fa, cerchiamo neolaureati") == "0"


def test_experience_band_survives_markdown_escaping() -> None:
    """LinkedIn descriptions arrive with markdown-escaped hyphens (``1\\-2``).

    The escape broke the range: the regex could not join "1" to "anni" across
    ``\\-``, so it matched the SECOND number instead and read the range at its
    upper bound. Measured on the real archive: "3\\-5 anni" was read as five,
    "1\\-2 anni" as two — which is exactly the threshold that blocks.
    """
    assert _estimate_experience_band(r"almeno **1\-2 anni** di esperienza pratica") == "1"
    assert _estimate_experience_band(r"esperienza di 3\-5 anni nello sviluppo") == "3+"
    assert _estimate_experience_band(r"esperienza di 2\-4 anni maturata in contesti") == "2"


def test_experience_ignores_a_time_window() -> None:
    """"Six months earned over the last two years" asks for six months.

    Real posting (SECURITY ADVISER, apprenticeship): the "2 anni" is the window
    the experience was earned in, not the amount demanded — and an apprenticeship
    is precisely the kind of opening this must not hide.
    """
    text = "esperienza pregressa di almeno 6 mesi, maturata nel corso degli ultimi 2 anni"
    _band, reason = cf.experience_status(text, _facts(years_experience=0))
    assert reason is None, "a time window is not a seniority requirement"
    # Nothing else in the sentence states a requirement, so nothing is known.
    window_only = "competenza maturata negli ultimi 4 anni in ambito ict"
    assert _estimate_experience_band(window_only) == "Non specificato"


def test_experience_written_out_in_words_still_counts() -> None:
    """"Almeno quattro anni" is not a rarer way of writing "almeno 4".

    Measured on 348 real postings: fifteen spell the number out, six of those
    state a genuine requirement, and the detector saw none of them. One was a
    Project Manager role asking for four years, sitting in the shortlist at 6/10
    with no warning on it at all.
    """
    assert _estimate_experience_band("almeno quattro anni di esperienza nel ruolo") == "3+"
    assert _estimate_experience_band("esperienza pregressa di almeno due anni in test") == "2"
    assert _estimate_experience_band("with at least two years' experience in avionics") == "2"
    assert _estimate_experience_band("you have at least six years of product experience") == "3+"
    # A range still reads at its lower bound, words or digits.
    assert _estimate_experience_band("esperienza di uno o due anni come specialist") == "1"


def test_the_company_boasting_about_its_own_age_is_not_a_requirement() -> None:
    """Two real postings, both titled *Junior*, both read as demanding a career.

    The guard for this existed and missed both, in two different ways. Prime
    Engineering wrote "un'azienda di riferimento, con oltre **18 anni di
    esperienza**" — markdown asterisks between the lead and the digits, which is
    all it took, and no other number appeared in the ad at all. OPIS wrote
    "**Who we are:** OPIS is an international CRO with over 25 years of
    experience", where the company-introduction vocabulary was Italian-only.

    Under the old ceiling both landed at 6 and stayed visible. Now the flag
    blocks, so a miss here hides a junior opening outright — which is the one
    thing this whole change is meant not to do.
    """
    boast_in_bold = "primeit, un'azienda di riferimento, con oltre **18 anni di esperienza**"
    assert _estimate_experience_band(boast_in_bold) == "Non specificato"

    english = "who we are: opis is an international cro with over 25 years of experience"
    assert _estimate_experience_band(english) == "Non specificato"

    # The real ask underneath it survives: only the boast is discarded.
    both = english + ". at least 1 year of experience in a similar role"
    assert _estimate_experience_band(both) == "1"

    # And a demand phrased through the company is still a demand.
    assert _estimate_experience_band("l'azienda cerca almeno 3 anni di esperienza") == "3+"


def test_years_asked_for_as_a_wish_do_not_count() -> None:
    """Direction is the whole rule, and it was read off 163 real firings.

    "preferibile esperienza almeno di 2/3 anni" makes the years a wish. But
    "esperienza di 2-5 anni in software testing, preferibilmente su applicazioni
    embedded" prefers a SECTOR and demands the years all the same — and there are
    three of those for every one of the first. Looking on both sides of the
    number cancelled ten requirements in the archive and only five deserved it;
    clamped to the clause that precedes the number it cancels four, all correct.
    """
    wish = "preferibile esperienza almeno di 2/3 anni nell'implementazione di gestionali"
    assert _estimate_experience_band(wish) == "Non specificato"
    assert _estimate_experience_band("preferibile esperienza pregressa di almeno 3 anni") == "Non specificato"

    # The preference is about the sector; the years are still required.
    sector = "esperienza di 2-5 anni in software testing, preferibilmente su sistemi embedded"
    assert _estimate_experience_band(sector) == "2"
    # A wish voiced in the previous bullet is about the previous bullet.
    previous_bullet = (
        "laurea in ingegneria o affini (preferibile);\n"
        "* esperienza di almeno 4 anni in ruoli analoghi"
    )
    assert _estimate_experience_band(previous_bullet) == "3+"


def test_a_requirement_blocks_on_the_distance_not_on_its_own_size() -> None:
    """Two years above the CV closes the door. One year is the gap people argue.

    The rule used to be a floor on the REQUIREMENT — "two years or more blocks" —
    which read the same for everybody: someone with two years behind them was
    shut out of a three-year posting exactly as hard as a new graduate. And
    because everything above three collapsed into one band, a posting asking
    three years and one asking ten were the same distance from anyone.
    """
    graduate = _facts(years_experience=0)
    mid = _facts(years_experience=2)
    two_years = "esperienza di almeno 2 anni nel ruolo di analista funzionale"
    three_years = "esperienza di almeno 3 anni nel ruolo di analista funzionale"

    assert cf.experience_status(two_years, graduate)[1], "two above zero is two"
    assert cf.experience_status(three_years, mid)[1] is None, "one year of gap is arguable"
    assert cf.experience_status("almeno 5 anni di esperienza", mid)[1], "three is not"

    # The same distance at half-year resolution. Rounded down to a whole zero
    # this persona was two years from the two-year posting and lost it; the
    # fraction is what puts them one and a half away instead.
    fresh = _facts(years_experience=0.5)
    assert cf.experience_status(two_years, fresh)[1] is None, "1.5 of gap is arguable too"
    assert cf.experience_status(three_years, fresh)[1], "2.5 is not"

    # The reason now names the real number, which the band could not.
    reason = cf.experience_status("minimum 8 years of experience required", graduate)[1]
    assert reason is not None and "8 anni" in reason


def test_experience_ignores_how_long_the_programme_lasts() -> None:
    """"Al termine dei due anni otterrai il diploma" is a duration, not a demand.

    Real Lidl apprenticeship posting. Widening the detector to spelled-out
    numbers made this one fire, and an ad whose entire premise is that it wants
    people with no experience is the worst possible thing to hide.
    """
    text = (
        "contratto di apprendistato con retribuzione per ore di lavoro e formazione. "
        "al termine dei due anni, al superamento degli esami, otterrai il diploma its "
        "e maturerai esperienza sul campo."
    )
    # Nothing in the sentence demands experience, so nothing is known — and an
    # unknown requirement blocks nothing, which is the whole point.
    assert _estimate_experience_band(text) == "Non specificato"


def test_experience_ignores_the_companys_own_years() -> None:
    """The company's age is not the candidate's experience.

    Real posting: "JUNIOR CONSULTANT - Neolaureato/a", described as "una realtà
    consolidata con oltre 30 anni di esperienza" — read as a 30-year seniority
    demand and capped to 3, a graduate role hidden from a graduate.
    """
    text = (
        "Siamo una realtà consolidata nel panorama IT, con oltre 30 anni di esperienza "
        "nella fornitura di servizi. Cerchiamo un neolaureato da inserire nel team."
    )
    assert _estimate_experience_band(text.lower()) == "0"
    _band, reason = cf.experience_status(text, _facts(years_experience=0))
    assert reason is None


def test_experience_ignores_a_pay_table() -> None:
    """A salary bracket per seniority band is not a requirement."""
    text = r"**Total compensation** indicativa 1 \- 2 anni di esperienza: 30000\-35000 lordi annui"
    _band, reason = cf.experience_status(text, _facts(years_experience=0))
    assert reason is None


def test_experience_blocks_only_from_two_years_up() -> None:
    facts = _facts(years_experience=0)
    _band, reason = cf.experience_status("almeno 2 anni di esperienza maturata", facts)
    assert reason is not None
    # One year is a wish in Italian postings, not a gate: it must not hide the job.
    _band, reason = cf.experience_status("1 anno di esperienza nel ruolo", facts)
    assert reason is None


def test_experience_never_blocks_when_the_cv_is_unreadable() -> None:
    facts = _facts(years_experience=None)
    _band, reason = cf.experience_status("almeno 3 anni di esperienza maturata", facts)
    assert reason is None, "an unparsed CV must not hide real jobs"


def test_a_company_bragging_about_forty_years_is_not_a_requirement() -> None:
    """Four real postings, and the guard that already exists misses all four.

    It wants something introducing the company in the 80 characters BEFORE the
    number, and in these the subject comes after it. Widening that search
    forwards was measured on 466 real postings first: it fires on 29 and only 2
    are the boast, so it was discarded. Size is the discriminator instead — the
    largest genuine requirement in the same archive is eight years.
    """
    graduate = _facts(years_experience=0)
    for text in (
        "Con oltre 40 anni di esperienza, eGlue affianca i propri clienti nella "
        "trasformazione digitale. Cerchiamo un AI Engineer.",
        "With 40 years of experience in monetization topics of all kinds, we are "
        "regarded as the world's leading advisors.",
        "Da oltre 40 anni supportiamo le aziende nella gestione dei rischi ambientali.",
        "Il gruppo, con quasi 400 collaboratori, da 40 anni è a fianco delle aziende "
        "nell'offrire servizi in ambito sicurezza e formazione.",
    ):
        _band, reason = cf.experience_status(text, graduate)
        assert reason is None, text[:48]


def test_a_real_senior_requirement_still_blocks() -> None:
    """The other side of the same threshold, so it cannot be widened by accident.

    Eight years is the largest genuine requirement measured on the real archive;
    twelve and ten are in there too and must keep their meaning.
    """
    graduate = _facts(years_experience=0)
    for text in (
        "Almeno 8 anni di esperienza nel ruolo di Business Analyst.",
        "Richiesti 12 anni di esperienza maturata in ambito regolatorio.",
    ):
        _band, reason = cf.experience_status(text, graduate)
        assert reason is not None, text[:48]


def test_format_years_reads_like_a_person_wrote_it() -> None:
    """"2.0" and "0,5 anni" are how a number looks, not how a CV reads."""
    assert cf.format_years(0.5) == "6 mesi"
    assert cf.format_years(1 / 12) == "1 mese"
    assert cf.format_years(1) == "1 anno"
    assert cf.format_years(2.0) == "2 anni", "a whole number keeps no decimal"
    assert cf.format_years(1.5) == "1,5 anni"
    assert cf.format_years(None) == ""


# ── degree level ─────────────────────────────────────────────────────────────


def test_education_blocks_a_required_master_but_not_a_preferred_one() -> None:
    facts = _facts(education_level="Triennale")
    _level, reason = cf.education_status("Richiesta laurea magistrale in informatica", facts)
    assert reason is not None
    _level, reason = cf.education_status("Laurea magistrale gradita, sufficiente triennale", facts)
    assert reason is None, "'gradita' is a wish, not a gate"


def test_education_accepts_a_posting_open_to_either_degree() -> None:
    """"bachelor's or master's" is satisfied by a bachelor.

    Real posting (AI, Data and Emerging Tech Consultant): the master's pattern
    matched first and the "bachelor's or" in front of it was never read, so a
    role explicitly open to a three-year degree was capped to 3.
    """
    facts = _facts(education_level="Triennale")
    text = "We are looking for an outstanding bachelor's or master's degree in Computer Science"
    _level, reason = cf.education_status(text, facts)
    assert reason is None
    _level, reason = cf.education_status("Laurea triennale o magistrale in informatica", facts)
    assert reason is None
    # "BSc/MSc in Computer Science" is the same offer written with a slash.
    _level, reason = cf.education_status("BSc/MSc in Computer Science or related field", facts)
    assert reason is None
    # The slash must not swallow a genuine master-only requirement.
    _level, reason = cf.education_status("Laurea magistrale/specialistica in ingegneria", facts)
    assert reason is not None


def test_education_treats_a_scored_title_as_preferential() -> None:
    """A degree that only earns points in a ranking is not a gate.

    Real posting: "Elementi con attribuzione di punteggio. Se possiedi: Laurea
    Magistrale…" — a preference expressed as a score, blocked as a requirement.
    """
    facts = _facts(education_level="Triennale")
    text = (
        "Elementi con attribuzione di punteggio. Se possiedi: * Laurea Magistrale "
        "e/o Ciclo Unico e/o Vecchio Ordinamento."
    )
    _level, reason = cf.education_status(text, facts)
    assert reason is None


def test_education_ignores_a_preference_stated_about_something_else() -> None:
    """"gradita" in the NEXT bullet is not about the degree.

    Real posting (EY, Junior Consultant Technology Risk), which scored 9/10 with
    no blocker against a three-year degree: the proximity window took 90
    characters blindly and ran 68 of them into the following bullet, where
    "Fortemente gradita" qualifies the *experience*, not the master's.
    """
    facts = _facts(education_level="Triennale")
    text = (
        "Cerchiamo una persona che abbia:\n"
        "* Laurea magistrale STEM (Ingegneria Gestionale, Informatica, Cybersecurity e affini);\n"
        "* Fortemente gradita una minima esperienza professionale con coinvolgimento diretto "
        "in progetti di cybersecurity governance;\n"
    )
    level, reason = cf.education_status(text, facts)
    assert level == "Magistrale"
    assert reason is not None, "the master's is required here, only the experience is preferred"
    # The wish must still be read as a wish when it IS about the degree.
    same_clause = "Laurea magistrale gradita, in informatica o affini;\n* Ottimo inglese"
    _level, reason = cf.education_status(same_clause, facts)
    assert reason is None


def test_education_requirement_reads_every_level() -> None:
    assert education_requirement("Laurea in Ingegneria Informatica")[0] == "Triennale"
    assert education_requirement("diploma di perito informatico")[0] == "Diploma"
    assert education_requirement("PhD in machine learning")[0] == "PhD"


# ── where the job is worked from ─────────────────────────────────────────────


def test_location_blocks_onsite_elsewhere_and_keeps_full_remote() -> None:
    facts = _facts()
    # Verisure: perfect role, on-site in Rome. Scored 9 before this check existed.
    _label, reason = cf.location_status("Rome, Latium, Italy", "In sede", facts)
    assert reason is not None
    # Milan hybrid: the user said Turin.
    _label, reason = cf.location_status("Milan, Lombardy, Italy", "Ibrido", facts)
    assert reason is not None
    # jobspy writes the city in English; the user typed it in Italian.
    _label, reason = cf.location_status("Turin, Piedmont, Italy", "In sede", facts)
    assert reason is None
    # Full remote is judged on the mode, not on where the office happens to be.
    _label, reason = cf.location_status("Savona, Liguria, Italy", "Full Remote", facts)
    assert reason is None


def test_location_blocks_nothing_without_a_declared_rule() -> None:
    facts = _facts(work_rule=cf.WorkRule())
    _label, reason = cf.location_status("Naples, Campania, Italy", "In sede", facts)
    assert reason is None


def test_unknown_work_mode_blocks_nothing() -> None:
    _label, reason = cf.location_status("Naples, Campania, Italy", "Non specificato", _facts())
    assert reason is None


# ── the parsed rule ──────────────────────────────────────────────────────────


def test_parse_work_rule_reads_the_users_own_sentence() -> None:
    rule = cf.parse_work_rule("Remoto, Torino in sede oppure ibrido su Torino", ["Torino"])
    assert rule.allow_remote and rule.allow_onsite and rule.allow_hybrid
    assert "torino" in rule.cities


def test_parse_work_rule_without_modes_constrains_nothing() -> None:
    rule = cf.parse_work_rule("", [])
    assert rule.allow_remote and rule.allow_onsite and rule.allow_hybrid
    assert rule.cities == ()


# ── end to end through the single enforcement point ──────────────────────────


def _enforced(descrizione: str, sede: str = "Turin", punteggio: int = 9) -> dict[str, object]:
    return enforce_hard_requirements(
        {"punteggio": punteggio, "consiglio": "Candidati subito"},
        profile_markdown=_CV,
        descrizione=descrizione,
        sede=sede,
        modalita="In sede",
        facts=_facts(),
    )


def test_enforce_caps_and_hides_a_constraint_that_cannot_be_argued_with() -> None:
    out = _enforced(_JD, sede="Rome, Latium, Italy")
    assert FLAG_LOCATION in out["blocchi"]  # type: ignore[operator]
    assert out["punteggio"] == 3
    assert out["consiglio"] == "Salta"
    assert FLAG_LOCATION in BLOCKING_FLAGS, "must be hidden by the 'applicable only' filter"


def test_a_requirement_the_candidate_does_not_meet_closes_the_door() -> None:
    """Years and degree used to lower a ceiling of 6. Now they cap at 3, like the rest.

    The ceiling was the right idea with the wrong number. Six is exactly where a
    person sets their own filter — "I only look from 6 up" — so a posting asking
    for three years more than the CV holds landed in the same place as one that
    was merely unremarkable, and on a real archive fifteen of the twenty-seven
    offers sitting at 6 were put there by the ceiling rather than by their own
    merits. Whatever else that is, it is not a shortlist.
    """
    for descrizione, flag in (
        (_JD + " Richiesti almeno 2 anni di esperienza maturata.", FLAG_EXPERIENCE),
        (_JD + " Richiesta laurea magistrale.", FLAG_EDUCATION),
    ):
        out = _enforced(descrizione)
        assert flag in out["blocchi"], flag  # type: ignore[operator]
        assert out["punteggio"] == 3, flag
        assert out["consiglio"] == "Salta", flag
        assert flag in BLOCKING_FLAGS and flag not in WEIGHTED_FLAGS, flag


def test_two_unmet_requirements_are_not_worse_than_one() -> None:
    """The ceiling counted them; the cap does not. Closed is closed."""
    out = _enforced(_JD + " Richiesta laurea magistrale e almeno 2 anni di esperienza maturata.")
    assert {FLAG_EDUCATION, FLAG_EXPERIENCE} <= set(out["blocchi"])  # type: ignore[arg-type]
    assert out["punteggio"] == 3


def test_a_cap_never_raises_a_score() -> None:
    """A cap is a ceiling too: an offer already below it keeps its own number."""
    out = _enforced(_JD + " Richiesta laurea magistrale.", punteggio=2)
    assert out["punteggio"] == 2


def test_a_hard_block_still_wins_over_a_weighted_one() -> None:
    out = _enforced(_JD + " Richiesta laurea magistrale.", sede="Rome, Latium, Italy")
    assert out["punteggio"] == 3
    assert out["consiglio"] == "Salta"


def test_the_model_is_not_asked_about_a_requirement_the_candidate_does_not_meet() -> None:
    """The saving that comes free with the decision: no call to reach a 3.

    ``hard_block_reason`` runs BEFORE the model. Every requirement it reads is
    decided from the posting's own words, so an offer demanding a master's the
    candidate does not have never costs a request — it used to spend one and then
    have its answer capped. On a real scan, 27 of 30 new offers were decided this
    way and the whole run cost three calls.
    """
    facts = _facts()
    unmet = _JD + " Richiesta laurea magistrale e almeno 2 anni di esperienza maturata."
    assert hard_block_reason(_CV, unmet, "Turin", facts=facts, modalita="In sede")
    assert hard_block_reason(_CV, _JD, "Rome, Latium, Italy", facts=facts, modalita="In sede")
    # A clean offer in a reachable city still reaches the model.
    assert hard_block_reason(_CV, _JD, "Turin", facts=facts, modalita="In sede") is None


def test_enforce_leaves_a_clean_offer_alone() -> None:
    out = enforce_hard_requirements(
        {"punteggio": 9, "consiglio": "Candidati subito"},
        profile_markdown=_CV,
        descrizione=_JD,
        sede="Turin, Piedmont, Italy",
        modalita="Ibrido",
        facts=_facts(),
    )
    assert out["blocchi"] == []
    assert out["punteggio"] == 9


def test_enforce_without_facts_behaves_exactly_as_before() -> None:
    """Every existing call site passes no facts: none of them may start blocking."""
    out = enforce_hard_requirements(
        {"punteggio": 9, "consiglio": "Candidati subito"},
        profile_markdown=_CV,
        descrizione=_JD + " Richiesti almeno 5 anni di esperienza maturata.",
        sede="Naples, Campania, Italy",
        modalita="In sede",
    )
    assert out["punteggio"] == 9
    assert out["blocchi"] == []


# ── the facts themselves ─────────────────────────────────────────────────────


def test_manual_correction_beats_the_cv(tmp_path) -> None:
    db = Database(tmp_path / "x.db")
    try:
        db.save_candidate_profile(
            source_name="cv.pdf",
            markdown="Laurea Magistrale. Votazione 110/110.",
            summary={"years_experience": 7},
        )
        assert candidate_facts_years(db) == 7
        db.set_preference(cf.FACT_YEARS, "0")
        db.set_preference(cf.FACT_EDUCATION, "Triennale")
        facts = cf.candidate_facts(db)
        assert facts.years_experience == 0
        assert facts.education_level == "Triennale"
        assert facts.sources["years_experience"] == "manuale"
    finally:
        db.close()


def candidate_facts_years(db: Database) -> int | None:
    return cf.candidate_facts(db).years_experience


# ── the SUBJECT of the degree ────────────────────────────────────────────────
#
# Every sentence below is verbatim from the 423-description archive. The check
# fires on 78 of them (18%); these pin the line it draws, in both directions.

_MINE = cf.CandidateFacts(degree_fields=frozenset({"informatica"}))

#: Postings whose subject list a computer-science graduate belongs to.
_FIELD_OK = (
    "Laurea in informatica",
    "laurea/diploma in **discipline informatiche** alla ricerca del primo impiego",
    "Degree or equivalent experience in Engineering or Computer Science",
    "Laurea triennale in Ingegneria dell'informazione, Informatica o Ingegneria informatica",
    "Laurea o diploma tecnico in Informatica, Telecomunicazioni, Elettronica o discipline affini",
    "Laurea in ambito tecnico-scientifico (Elettrico, Elettronico o Informatico)",
    "laureata o laureanda in Ingegneria delle Telecomunicazioni, Informatica, Cybersecurity",
    "Laurea ad indirizzo STEM preferibilmente in Informatica o Biomedica",
    "Laurea triennale e/o magistrale in ambito tecnico scientifico",
    # Named in English, and the two that used to slip through: bare "Data" and
    # the acronym, both real subjects in an English subject list.
    "Bachelor's degree in Data, Supply Chain Management, Finance, Marketing, Economics",
    "Bachelor's degree in Business, Engineering, IT, or Data Analytics",
    # The Italian umbrella that contains computer engineering.
    "Laurea in ingegneria industriale o dell'informazione",
    # A subject named as a wish is not a gate, exactly like the degree LEVEL.
    "Bachelor's degree, preferably with emphasis in Finance, Accounting, Engineering",
)

#: Postings that ask for a subject this CV is not in.
_FIELD_NOT_MINE = (
    "Ti stai per laureare o hai una laurea in Economia (Magistrale o Triennale più Master)",
    "che hanno già conseguito una laurea magistrale in Economia",
    "Laurea in Giurisprudenza o discipline legali",
    "Bachelor's degree in a Life Science discipline",
    "Laurea Specialistica in Ingegneria Gestionale e/o affini",
    "Laurea specialistica in Economia, Marketing o un campo correlato",
    "Laurea in Ingegneria Gestionale/Meccanica/Aerospaziale o matterie attinenti",
    "Laureando/neolaureato in Amministrazione, Finanza Aziendale e Controllo",
)


def test_a_degree_in_the_wrong_subject_is_seen() -> None:
    """The most-stated requirement in the archive, and nothing read it.

    ``education_status`` compares a bachelor's against a master's and says
    nothing about what the degree is IN, so PwC's "hai una laurea in Economia"
    read as satisfied and its junior auditor sat at 8/10 in an IT job hunt.
    """
    for text in _FIELD_OK:
        _label, reason = cf.degree_field_status(text, _MINE)
        assert reason is None, f"must accept: {text!r}"
    for text in _FIELD_NOT_MINE:
        _label, reason = cf.degree_field_status(text, _MINE)
        assert reason is not None, f"must flag: {text!r}"


def test_one_acceptable_subject_anywhere_in_the_ad_is_enough() -> None:
    """Ads list an acceptable subject in one line and a preferred one in another.

    Refusing on the second while the first accepts you is the false positive that
    matters: MSF asks for "a degree in Information and Technology (IT)" and then
    "Desirable degree (or masters) in Epidemiology or Public Health".
    """
    ad = (
        "Essential: degree in Information and Technology (IT). "
        "Desirable degree in Epidemiology or Public Health."
    )
    assert cf.degree_field_status(ad, _MINE)[1] is None


def test_an_unknown_subject_blocks_nothing() -> None:
    """Same rule as every other fact here: silence beats guessing."""
    blank = cf.CandidateFacts(degree_fields=frozenset())
    assert cf.degree_field_status("hai una laurea in Economia", blank)[1] is None
    # And an ad that names no subject at all says nothing about anyone.
    assert cf.degree_field_status("Laurea triennale conseguita", _MINE)[1] is None


def test_the_subject_is_read_from_the_education_lines_not_the_whole_cv(tmp_path) -> None:
    """A developer's skill list mentions half the table of subjects."""
    db = Database(tmp_path / "field.db")
    try:
        db.save_candidate_profile(
            source_name="cv.pdf",
            markdown=(
                "Laurea Triennale in Scienze e Tecnologie Informatiche\n"
                "Competenze: analisi di bilancio, business intelligence, diritto del lavoro\n"
            ),
            summary={"education": "Laurea in Informatica"},
        )
        facts = cf.candidate_facts(db)
        assert facts.degree_fields == frozenset({"informatica"})
        assert cf.degree_field_status("hai una laurea in Economia", facts)[1] is not None
    finally:
        db.close()


def test_a_posting_that_needs_a_car_is_blocked_only_when_you_said_you_have_none() -> None:
    """"No driving licence" was listed among the non-arguable blockers for months
    while nothing checked one — the barrier was assumed to be covered by the
    unreachable-office rule, which it is not: a field role in your own city still
    needs the car. It cost a Tier-1 recommendation and an application, to
    Siemens' "Valid driving license and willingness to travel within Italy".

    Rare enough to read precisely: 11 of 423 real descriptions mention a licence,
    and the check fires on 7 — nothing like the L. 68/99 boilerplate trap.
    """
    none = cf.CandidateFacts(driving_licence=False)
    has = cf.CandidateFacts(driving_licence=True)
    unsaid = cf.CandidateFacts()

    required = (
        "Valid driving license and willingness to travel within Italy (company car provided)",
        "Buona familiarita con l'uso del PC * Patente di guida B * Spiccata propensione",
        "richiesta residenza su Torino e autonomia negli spostamenti (automunito/a)",
        "Patente B in corso di validita. Disponibilita a trasferte su territorio nazionale",
    )
    for text in required:
        assert cf.driving_licence_status(text, none)[1] is not None, text
        assert cf.driving_licence_status(text, has)[1] is None, "holds one: nothing to block"
        assert cf.driving_licence_status(text, unsaid)[1] is None, "never said: blocks nothing"

    # Verbatim from EY, and the only one of the eleven phrased as a wish.
    wish = "Nice to have: buona conoscenza della lingua inglese e patente B."
    assert cf.driving_licence_status(wish, none)[1] is None

    # And an ad that never mentions it says nothing about anyone.
    assert cf.driving_licence_status("Sviluppatore backend a Torino, ibrido.", none)[1] is None


def test_half_a_year_of_experience_is_half_a_year(tmp_path) -> None:
    """Six months is neither a mystery nor a zero.

    Two regressions meet in this one CV. The first: ``0.5`` parsed through
    ``int(str(...))`` and became "unknown", and an unknown year count switches
    the experience check off entirely, so an archive full of "3+ anni" offers
    stopped being flagged. That half is unchanged and still asserted below.

    The second is why this test was renamed. The fix for the first floored the
    fraction, on the argument that "half a year of experience is not one" — and
    it landed before the rule that reads a DISTANCE rather than the requirement's
    own size. Floored to zero, this CV sits two years from a posting asking for
    two and the door shuts; at its real 0.5 it sits one and a half away and
    passes. On a real 466-posting archive that was 19 offers hidden from exactly
    the people this app is for.
    """
    db = Database(tmp_path / "half.db")
    try:
        db.save_candidate_profile(
            source_name="cv.pdf",
            markdown="Laurea Triennale. Sei mesi di ricerca linguistica.",
            summary={"years_experience": 0.5},
        )
        facts = cf.candidate_facts(db)
        assert facts.years_experience == 0.5, "the fraction is the whole point"
        assert facts.sources["years_experience"] == "cv"
        assert "years_experience" not in facts.missing()
        # Unknown would switch the check off; half a year does not.
        _band, reason = cf.experience_status("richiesti almeno 3 anni di esperienza", facts)
        assert reason is not None
        # ...and the boundary the floor used to move.
        _band, reason = cf.experience_status("richiesti almeno 2 anni di esperienza", facts)
        assert reason is None, "1.5 years of gap is arguable, and 0.5 is not 0"
    finally:
        db.close()


def test_missing_facts_are_reported_not_guessed(tmp_path) -> None:
    db = Database(tmp_path / "y.db")
    try:
        db.save_candidate_profile(source_name="cv.pdf", markdown="Sviluppatore.", summary={})
        facts = cf.candidate_facts(db)
        assert "years_experience" in facts.missing()
        assert facts.years_experience is None
    finally:
        db.close()


# ── what the ad says it pays ─────────────────────────────────────────────────

#: Verbatim shapes from the 469-ad archive. Every honest figure sits after a
#: label; none of the noise does.
_PAY_CASES = (
    # (text, the figures the reader must return as (amount, ad said yearly))
    ("salary range min. RAL 35.000 € – max RAL 38.000 €", [35000, 38000]),  # noqa: RUF001
    ("proposta retributiva compresa tra 27.000€ e 30.000€", [27000, 30000]),
    ("RAL di partenza per figure junior: da 23.300€ a 26.000€", [23300, 26000]),
    ("range di RAL compreso tra 25K e 28K + ticket restaurant", [25000, 28000]),
    ("RAL: Salario mensile: EUR 1600 - EUR 2000", [1600, 2000]),
    (
        "Rimborso spese (curriculare - rimborso spese a partire da 600€; "
        "extracurriculare - rimborso spese a partire da 800€)",
        [600, 800],
    ),
    ("è prevista un'indennità di tirocinio pari a 1000 euro lordi al mese", [1000]),
)

#: The noise, each one measured sitting next to a real salary in the archive.
_NOT_PAY = (
    # The anti-discrimination statutes, in the footer of a third of the ads.
    "ai sensi delle leggi 903/77 e 125/91, e dei DLgs 215/03 e 216/03",
    # The decree the pay-transparency footers cite, whose number reads as money.
    "Stiamo aggiornando i nostri annunci ai sensi del Dlgs del 7 maggio 2026, n.ro 96",
    # 267 hits under a hundred euros, against 632 real annual figures.
    "Retribuzione: buoni pasto 8 euro al giorno",
    "Meal Vouchers € 8 e Welfare Band € 1.000",
    # Pay that is not the pay.
    "Compenso: Variable Pay Band € 1.000-3000",
    # A currency this app does not convert.
    "Typically, we offer an annual salary of £85,797 in London",
    "Nessuna cifra qui dentro, solo requisiti e benefit generici.",
)


def test_the_pay_is_read_from_the_ad_and_not_from_the_model() -> None:
    """186 ads print a figure; the model returned one for 19 of them.

    It answered "Non stimabile" to "salary range min. RAL 35.000 € - max RAL
    38.000 €". Worse than the coverage, the answer moved: the same unchanged ad
    was capped one day and not the next, because the only thing that had changed
    was what the model felt like estimating.
    """
    for text, expected in _PAY_CASES:
        found = sorted({value for value, _yearly in cf._pay_figures(text)})
        assert found == sorted(expected), text


def test_a_number_near_a_money_word_is_not_a_salary() -> None:
    """Why the reader anchors on the label instead of hunting for numbers."""
    for text in _NOT_PAY:
        assert cf._pay_figures(text) == [], text


def test_the_period_is_never_guessed() -> None:
    """The rule that lets this work in a country whose pay scales we never encode.

    An ad writing "rimborso spese a partire da 600€" does not say per what.
    Deciding it means per month would be an assumption about one market, in an
    app other people install with their own CV — so each figure is annualised the
    most GENEROUS way its text allows and the door shuts only when even that
    falls short. 600 cannot reach a 20.000 floor whatever it meant; 25.000 clears
    it on any reading; the ambiguous middle stays open.
    """
    floor = _facts(ral_min=20000)
    _label, reason = cf.salary_status("Rimborso spese a partire da 600€", floor)
    assert reason is not None, "800x12 is still under 20.000"

    _label, reason = cf.salary_status("RAL 25.000€", floor)
    assert reason is None, "already over the floor, whatever the period meant"

    # 1.850 with no period stated: monthly it is 22.200, and that clears.
    _label, reason = cf.salary_status("Retribuzione: 1.850€", floor)
    assert reason is None

    # No floor declared, nothing to be under — like every other check here.
    _label, reason = cf.salary_status("Rimborso spese a partire da 600€", _facts())
    assert reason is None


def test_a_range_is_read_at_its_top() -> None:
    """An ad offering 18.000 to 24.000 may pay 24.000, and you are the one who asks."""
    floor = _facts(ral_min=20000)
    assert cf.salary_status("RAL da 18.000€ a 24.000€", floor)[1] is None
    assert cf.salary_status("RAL da 12.000€ a 15.000€", floor)[1] is not None


def test_an_internship_you_are_offered_is_not_one_you_are_asked_to_have_done() -> None:
    """The bare substrings put 322 of 469 real ads in the internship bucket.

    English uses "stage" for a phase ("depending on what we agree at the offer
    stage"), Italian lists it among kinds of experience ("esperienza, anche
    tramite stage o tirocini"), and "intern" sits inside "internazionali" and
    "stakeholder interni/esterni". One ad offered a permanent role whose holder
    would *supervise* an intern.
    """
    from app.services.scan.heuristics import _estimate_contract_type

    for text in (
        "depending on what we agree at the offer stage",
        "esperienza, anche tramite stage o tirocini, in società di consulenza",
        "esperienza pregressa di 1 anno, anche sotto forma di stage o tirocinio",
        "collaborazione con stakeholder interni/esterni",
        "contesti nazionali e internazionali",
        "potrà coordinare una risorsa junior in stage, affiancandola",
    ):
        assert _estimate_contract_type(text) != "Stage", text

    for text in (
        "cerchiamo neolaureate per il nostro programma di stage 2026",
        "l'inserimento avverrà in stage (curriculare o extra curriculare)",
        "la posizione prevede un inserimento in tirocinio",
        "è previsto un inserimento in stage con indennità di tirocinio",
    ):
        assert _estimate_contract_type(text) == "Stage", text


# ── reserved to the protected-categories register ────────────────────────────

#: Verbatim from the 238-offer archive (06/08/2026). Twenty-nine postings cite
#: L. 68/99 and exactly ONE is reserved — the rest is equal-opportunity
#: boilerplate on jobs anyone can apply for, including the highest-scoring offer
#: in the archive. A detector that fires on "categorie protette" would delete
#: half the shortlist, which is why every one of these is pinned here.
_RESERVED = (
    "Per un cliente in ambito dei Servizi e Consulenza IT, la specializzazione Digital & "
    "Technologies di Adecco sta cercando una figura di uno Junior Data Engineer "
    "appartenente alle categorie protette (L.68/99) sul territorio di Torino."
)
#: The second one, found on 2026-08-19 sitting at 8/10 at the top of the
#: shortlist. It addresses several people at once, so the participle is plural —
#: and the detector only knew the singular, which is the whole of the bug.
_RESERVED_PLURAL = (
    "Per ampliamento del nostro organico, ricerchiamo giovani neolaureati / laureandi, "
    "diplomati appartenenti alle categorie protette legge 68/99 da inserire in uno dei "
    "nostri centri di competenza."
)
_BOILERPLATE = (
    # Capgemini Engineering, twice in the archive: the footer that let the
    # detector through when the subject list was widened to plurals to catch
    # _RESERVED_PLURAL. "Attention and sensitivity towards future resources" is
    # a promise, not a requirement.
    "L'offerta di lavoro si intende rivolta all'uno e all'altro sesso in ottemperanza al "
    "D.Lgs. 198/2006. Inoltre, prestiamo attenzione e sensibilità alle future risorse "
    "appartenenti alle categorie protette, ai sensi degli articoli 1 e 18 della legge 68 "
    "del '99.",
    # Teoresi
    "L'offerta è rivolta ad entrambi i sessi in ottemperanza al D.Lgs. 198/2006 ed è aperta "
    "anche a candidati appartenenti alle categorie protette e iscritti al collocamento "
    "mirato, in conformità con la Legge 68/99 (Art. 1 e Art.18).",
    # EY — the best-scoring offer in the whole archive
    "assicuriamo che tutte le nostre offerte siano aperte anche a persone con disabilità, "
    "in linea con la legge italiana L.68/99.",
    # Reply
    "regardless of age, gender, sexual orientation, religion, nationality or disabilities "
    "as protected by Italian Law (L.68/99). Reply is committed to ensuring a fair process.",
    # BIP
    "Lavoriamo per un ambiente etico, equo e accogliente, anche attraverso politiche attive "
    "per le categorie protette (L. 68/99).",
    # ALTEN
    "Promuoviamo l'inserimento e l'integrazione lavorativa delle persone appartenenti alle "
    "categorie protette - in base a quanto disciplinato dalla legge 68/99.",
    # agap2 / ADENTIS
    "Inoltre, teniamo fede ai nostri impegni prestando attenzione alle risorse appartenenti "
    "alle categorie protette ai sensi degli articoli 1 e 18 della Legge 68/99.",
    # Skytechnology / Akronos
    "valutiamo candidature indipendentemente da genere, etnia, disabilità (artt. 1 e 18, "
    "legge 68/99), età, orientamento sessuale, religione.",
    # PRAXI — a preference in a public-sector ranking, not a gate
    "Costituirà titolo preferenziale la candidatura presentata da soggetti appartenenti "
    "alle categorie di cui all'art. 1 della legge n. 68/99.",
    # Unipol
    "Rappresenta requisito preferenziale per la selezione l'appartenenza alle categorie "
    "protette (ex art° 1 L. 68/99).",
    # Teoresi V&V
    "La ricerca è rivolta anche a candidati appartenenti alle categorie protette, con "
    "requisiti indicati nella legge 68/99 art.1 e art. 18.",
    # sennder
    "Do not hesitate to apply as a member of the protected categories law 68/99.",
    # Topnetwork
    "a persone di tutte le età e tutte le nazionalità, ai sensi dei decreti legislativi "
    "215/03 e 216/03 e ai facenti parte di Categorie Protette, legge 68/99.",
)


def test_a_reserved_posting_is_blocked_only_when_the_user_said_they_are_not_on_the_register() -> None:
    not_registered = _facts(protected_category=False)
    _label, reason = cf.protected_category_status(_RESERVED, not_registered)
    assert reason is not None

    # Plural: "diplomati appartenenti", because the sentence addresses several
    # people. Missing it put NTT DATA's reserved opening at 8/10, first in a
    # shortlist built for someone who is not on the register.
    _label, reason = cf.protected_category_status(_RESERVED_PLURAL, not_registered)
    assert reason is not None

    # Registered: the posting is an advantage, not an obstacle.
    _label, reason = cf.protected_category_status(_RESERVED, _facts(protected_category=True))
    assert reason is None
    # Never stated: an unknown fact hides nothing, like every other check here.
    _label, reason = cf.protected_category_status(_RESERVED, _facts(protected_category=None))
    assert reason is None


def test_equal_opportunity_boilerplate_is_not_a_reserved_posting() -> None:
    """The 28 postings that mention the law and are open to everyone."""
    facts = _facts(protected_category=False)
    for text in _BOILERPLATE:
        _label, reason = cf.protected_category_status(text, facts)
        assert reason is None, f"boilerplate treated as reserved: {text[:70]}"


# ── the end-of-scan audit ────────────────────────────────────────────────────


def _scored(job_id: int, punteggio: int, riassunto: str = "", blocchi=None) -> dict:
    return {
        "job_id": job_id,
        "analysis": {
            "punteggio": punteggio,
            "riassunto": riassunto or f"Sintesi diversa per l'offerta numero {job_id}, abbastanza lunga.",
            "blocchi": blocchi or [],
        },
    }


def test_audit_spots_a_flat_score_distribution() -> None:
    from app.services.scanner_service import audit_scan

    out = audit_scan([_scored(i, 8) for i in range(6)])
    assert any(a["codice"] == "voti_piatti" for a in out["anomalie"])
    assert len(out["sospetti"]) == 6


def test_audit_does_not_denounce_correctly_blocked_offers() -> None:
    """A run where most offers are capped at 3 by a hard block is HEALTHY.

    On the real archive 143 offers out of 238 are correctly out of reach, so the
    flat-score check fired on every scan, called the cap a malfunction, and sent
    those offers back to the model only to be capped to 3 again.
    """
    from app.services.scanner_service import audit_scan

    blocked = [_scored(i, 3, blocchi=["sede_non_raggiungibile"]) for i in range(8)]
    judged = [_scored(100 + i, s) for i, s in enumerate([9, 7, 5, 8])]
    out = audit_scan(blocked + judged)
    assert not any(a["codice"] == "voti_piatti" for a in out["anomalie"])
    # And a genuinely flat run is still caught, blocked offers or not.
    flat = [_scored(200 + i, 6) for i in range(5)]
    out = audit_scan(blocked + flat)
    assert any(a["codice"] == "voti_piatti" for a in out["anomalie"])
    assert all(jid >= 200 for jid in out["sospetti"])


def test_audit_does_not_call_the_apps_own_sentences_clones() -> None:
    """A blocked offer's summary is written by the app, not by a model.

    "Non candidabile: richiede 3+ anni di esperienza" is identical across every
    offer that breaks the same rule — 34 of them on the real archive — so the
    clone check reported an epidemic on every scan and sent them off to be
    re-scored, which is both wrong and expensive.
    """
    from app.services.scanner_service import audit_scan

    canned = "Non candidabile: richiede 3+ anni di esperienza (il profilo ne dichiara 0)."
    blocked = [_scored(i, 3, riassunto=canned, blocchi=["esperienza_richiesta"]) for i in range(6)]
    judged = [_scored(100 + i, s) for i, s in enumerate([9, 7, 5, 8])]
    out = audit_scan(blocked + judged)
    assert not any(a["codice"] == "riassunti_clonati" for a in out["anomalie"])

    # A model pasting one summary over unrelated offers is still caught.
    real_clone = "Ottima opportunità in linea col profilo, con margini di crescita interessanti."
    cloned = [_scored(300 + i, 7 + (i % 2), riassunto=real_clone) for i in range(3)]
    out = audit_scan(blocked + cloned + judged)
    assert any(a["codice"] == "riassunti_clonati" for a in out["anomalie"])


def test_audit_spots_the_same_summary_on_different_offers() -> None:
    from app.services.scanner_service import audit_scan

    same = "Ottima opportunità in ambito consulenza, perfettamente in linea col profilo."
    results = [_scored(1, 9, same), _scored(2, 4, same), _scored(3, 6), _scored(4, 2)]
    out = audit_scan(results)
    assert any(a["codice"] == "riassunti_clonati" for a in out["anomalie"])
    assert set(out["sospetti"]) >= {1, 2}


def test_audit_spots_a_recommended_but_blocked_offer() -> None:
    from app.services.scanner_service import audit_scan

    results = [_scored(1, 9, blocchi=["sede_non_raggiungibile"]), _scored(2, 4), _scored(3, 6), _scored(4, 2)]
    out = audit_scan(results)
    assert any(a["codice"] == "consigliata_ma_bloccata" for a in out["anomalie"])


def test_audit_is_quiet_on_a_healthy_scan() -> None:
    from app.services.scanner_service import audit_scan

    out = audit_scan([_scored(i, s) for i, s in enumerate([9, 7, 5, 3, 2, 8])])
    assert out["anomalie"] == []
    assert out["sospetti"] == []


def test_clone_guard_catches_a_repeated_summary_with_different_axes() -> None:
    """The old guard compared axes only, so varying one digit slipped through."""
    from app.services.scanner_service import _cloned_slots

    same = "Ruolo di analisi funzionale in una societa di consulenza, molto in linea."
    parsed = [
        {"match_axes": {"a": 8}, "riassunto": same},
        {"match_axes": {"a": 7}, "riassunto": same},
        {"match_axes": {"a": 3}, "riassunto": "Tutt'altra cosa, sintesi indipendente e lunga."},
    ]
    assert _cloned_slots(parsed) == {0, 1}


def test_a_country_is_not_an_accepted_city() -> None:
    """The bug the dry run caught on the real archive.

    A scan run over "Italy" put "italy" among the accepted cities, and since
    every Italian posting's location ends in ", Italy", the city check matched
    all of them — on-site roles in Rome and Savona were never blocked.
    """
    rule = cf.parse_work_rule("Remoto, Torino in sede oppure ibrido su Torino", ["Italy", "Torino"])
    assert rule.cities == ("torino",)
    facts = _facts(work_rule=rule)
    _label, reason = cf.location_status("Rome, Latium, Italy", "In sede", facts)
    assert reason is not None
    _label, reason = cf.location_status("Savona, Liguria, Italy", "Ibrido", facts)
    assert reason is not None
    _label, reason = cf.location_status("Turin, Piedmont, Italy", "In sede", facts)
    assert reason is None
