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


def test_enforce_caps_and_flags_each_broken_constraint() -> None:
    for descrizione, sede, modalita, flag in (
        (_JD + " Richiesti almeno 2 anni di esperienza maturata.", "Turin", "In sede", FLAG_EXPERIENCE),
        (_JD + " Richiesta laurea magistrale.", "Turin", "In sede", FLAG_EDUCATION),
        (_JD, "Rome, Latium, Italy", "In sede", FLAG_LOCATION),
    ):
        out = enforce_hard_requirements(
            {"punteggio": 9, "consiglio": "Candidati subito"},
            profile_markdown=_CV,
            descrizione=descrizione,
            sede=sede,
            modalita=modalita,
            facts=_facts(),
        )
        assert flag in out["blocchi"], flag
        assert out["punteggio"] <= 3, flag
        assert out["consiglio"] == "Salta", flag
        assert flag in BLOCKING_FLAGS, "must be hidden by the 'applicable only' filter"


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
