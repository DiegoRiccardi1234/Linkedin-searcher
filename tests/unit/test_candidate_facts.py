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


def test_enforce_weighs_a_negotiable_constraint_instead_of_hiding_it() -> None:
    """Years and degree lower the ceiling; they no longer make the offer vanish.

    An on-site role in another city is not reachable without a car. A posting
    asking for a master's is one you can still apply to and sometimes get — so
    capping it to 3 and hiding it behind "applicable only" threw away real
    chances, while leaving it at the model's 9 (EY, "Laurea magistrale STEM")
    put a requirement the candidate does not meet at the top of the shortlist.
    """
    for descrizione, flag in (
        (_JD + " Richiesti almeno 2 anni di esperienza maturata.", FLAG_EXPERIENCE),
        (_JD + " Richiesta laurea magistrale.", FLAG_EDUCATION),
    ):
        out = _enforced(descrizione)
        assert flag in out["blocchi"], flag  # type: ignore[operator]
        assert out["punteggio"] == 6, flag
        assert out["consiglio"] != "Salta", flag
        assert flag in WEIGHTED_FLAGS and flag not in BLOCKING_FLAGS, flag


def test_each_further_unmet_requirement_lowers_the_ceiling_by_one() -> None:
    out = _enforced(_JD + " Richiesta laurea magistrale e almeno 2 anni di esperienza maturata.")
    assert {FLAG_EDUCATION, FLAG_EXPERIENCE} <= set(out["blocchi"])  # type: ignore[arg-type]
    assert out["punteggio"] == 5


def test_a_weighted_constraint_never_raises_a_score() -> None:
    """A ceiling is a ceiling: an offer already below it keeps its own number."""
    out = _enforced(_JD + " Richiesta laurea magistrale.", punteggio=4)
    assert out["punteggio"] == 4


def test_a_hard_block_still_wins_over_a_weighted_one() -> None:
    out = _enforced(_JD + " Richiesta laurea magistrale.", sede="Rome, Latium, Italy")
    assert out["punteggio"] == 3
    assert out["consiglio"] == "Salta"


def test_the_model_is_still_asked_about_a_weighted_constraint() -> None:
    """Wiring, not logic: a ceiling needs a score to lower.

    ``hard_block_reason`` runs BEFORE the model and skips the call entirely.
    Leaving the weighted constraints in it would mean no offer asking for a
    master's ever reaches a model, and its 6 would be the ceiling itself rather
    than a judgement — the invented number this app stopped producing in 1.7.9.
    """
    facts = _facts()
    weighted = _JD + " Richiesta laurea magistrale e almeno 2 anni di esperienza maturata."
    assert hard_block_reason(_CV, weighted, "Turin", facts=facts, modalita="In sede") is None
    assert hard_block_reason(_CV, _JD, "Rome, Latium, Italy", facts=facts, modalita="In sede")


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


def test_missing_facts_are_reported_not_guessed(tmp_path) -> None:
    db = Database(tmp_path / "y.db")
    try:
        db.save_candidate_profile(source_name="cv.pdf", markdown="Sviluppatore.", summary={})
        facts = cf.candidate_facts(db)
        assert "years_experience" in facts.missing()
        assert facts.years_experience is None
    finally:
        db.close()


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
_BOILERPLATE = (
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
