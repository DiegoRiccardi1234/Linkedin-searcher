"""Deterministic hard-requirement checks applied after the model.

All three defects reproduced here were measured on a real scan (2026-07-21):
a posting demanding "min. 102/110" scored 10 against a 95/110 CV, ten US-based
jobs scored up to 8 (the candidate has no visa and won't relocate), and every
single job of the scan was stored as "Full Remote" because the work mode came
from the search flag instead of the posting.
"""

from __future__ import annotations

from typing import Any

from app.services import scanner_service as ss

_CV = "Laurea Triennale in Informatica, votazione 95/110. Python, React, LLM evaluation."
_JD = "Ruolo AI QA in team di prodotto. " * 20


def _analysis(**over: Any) -> dict[str, Any]:
    base = {"punteggio": 9, "consiglio": "Candidati subito"}
    base.update(over)
    return base


# ── pre-LLM gate and post-LLM cap must agree ─────────────────────────────────


def test_pre_and_post_checks_share_one_predicate() -> None:
    """The blocker is evaluated twice — once to skip the LLM call, once to cap
    the answer — and the two used to be separate implementations of the same
    condition. Whenever the gate blocks, the cap must apply, and vice versa.
    """
    cases = [
        ("San Francisco, CA", _JD, _CV),  # non-EU, no EU-remote wording
        ("Torino, Italy", _JD, _CV),  # nothing wrong
        ("New York, NY", _JD + " Open to remote candidates based in the EU.", _CV),
        ("Torino, Italy", _JD + " Richiesta votazione minima 102/110.", _CV),
        ("Torino, Italy", _JD + " Richiesta votazione minima 90/110.", _CV),
    ]
    for sede, descrizione, cv in cases:
        blocked = ss.hard_block_reason(cv, descrizione, sede)
        out = ss.enforce_hard_requirements(
            _analysis(), profile_markdown=cv, descrizione=descrizione, sede=sede
        )
        capped = out["punteggio"] < 9
        assert capped is bool(blocked), (sede, descrizione[-40:], blocked, out["punteggio"])


# ── geographic eligibility ───────────────────────────────────────────────────


def test_us_location_is_capped_and_flagged() -> None:
    out = ss.enforce_hard_requirements(
        _analysis(), profile_markdown=_CV, descrizione=_JD, sede="Denver, CO"
    )
    assert out["punteggio"] == 3
    assert out["consiglio"] == "Salta"
    assert out["eleggibilita_geografica"] == "Fuori UE: non candidabile"
    assert any("fuori UE" in m for m in out["skills_match"]["mancano"])


def test_uk_location_is_capped() -> None:
    out = ss.enforce_hard_requirements(
        _analysis(), profile_markdown=_CV, descrizione=_JD, sede="London, England, United Kingdom"
    )
    assert out["punteggio"] == 3


def test_italian_location_untouched() -> None:
    out = ss.enforce_hard_requirements(
        _analysis(), profile_markdown=_CV, descrizione=_JD, sede="Turin, Piedmont, Italy"
    )
    assert out["punteggio"] == 9
    assert out["eleggibilita_geografica"] == "Italia/UE"


def test_german_location_not_mistaken_for_delaware() -> None:
    # ", DE" is Delaware in the US-state list — an EU location must win.
    out = ss.enforce_hard_requirements(
        _analysis(), profile_markdown=_CV, descrizione=_JD, sede="Berlin, DE, Germany"
    )
    assert out["punteggio"] == 9


def test_us_location_with_explicit_eu_remote_is_not_capped() -> None:
    jd = _JD + " This is a fully remote role open to candidates based in Europe."
    out = ss.enforce_hard_requirements(
        _analysis(), profile_markdown=_CV, descrizione=jd, sede="Austin, TX"
    )
    assert out["punteggio"] == 9
    assert "apertura remota" in out["eleggibilita_geografica"]


def test_cap_never_raises_a_low_score() -> None:
    out = ss.enforce_hard_requirements(
        _analysis(punteggio=2), profile_markdown=_CV, descrizione=_JD, sede="Chicago, IL"
    )
    assert out["punteggio"] == 2


# ── minimum degree grade ─────────────────────────────────────────────────────


def test_grade_threshold_above_candidate_caps_score() -> None:
    jd = _JD + " Requisiti: Laurea STEM con votazione minima 102/110."
    out = ss.enforce_hard_requirements(_analysis(), profile_markdown=_CV, descrizione=jd)
    assert out["punteggio"] == 3
    assert out["voto_minimo_richiesto"] == "102/110"
    assert any("102/110" in m for m in out["skills_match"]["mancano"])


def test_grade_threshold_below_candidate_is_recorded_not_capped() -> None:
    jd = _JD + " Gradita laurea con votazione minima 90/110."
    out = ss.enforce_hard_requirements(_analysis(), profile_markdown=_CV, descrizione=jd)
    assert out["punteggio"] == 9
    assert out["voto_minimo_richiesto"] == "90/110"


def test_no_grade_in_cv_means_no_cap() -> None:
    jd = _JD + " Votazione minima 105/110."
    out = ss.enforce_hard_requirements(
        _analysis(), profile_markdown="CV senza voto di laurea", descrizione=jd
    )
    assert out["punteggio"] == 9


# ── schema normalisation ─────────────────────────────────────────────────────


def test_normalizer_fills_every_documented_key() -> None:
    out = ss.enforce_hard_requirements(
        {"punteggio": 7}, profile_markdown=_CV, descrizione=_JD, sede="Milan, Italy"
    )
    for key in ("skills_match", "match_axes", "requisiti", "responsabilita", "benefit"):
        assert key in out
    assert set(out["match_axes"]) == set(ss._MATCH_AXES_KEYS)
    assert out["skills_match"] == {"hai": [], "mancano": []}
    assert out["voto_minimo_richiesto"] == "Non specificato"


def test_normalizer_moves_stray_top_level_mancano() -> None:
    raw = {"punteggio": 6, "mancano": ["Kubernetes"], "skills_match": {"hai": ["Python"]}}
    out = ss.enforce_hard_requirements(raw, profile_markdown=_CV, descrizione=_JD)
    assert "mancano" not in out
    assert out["skills_match"]["hai"] == ["Python"]
    assert out["skills_match"]["mancano"] == ["Kubernetes"]


def test_normalizer_clamps_out_of_range_axes() -> None:
    raw = {"punteggio": 6, "match_axes": {"skills_match": 42, "seniority_match": "x"}}
    out = ss.enforce_hard_requirements(raw, profile_markdown=_CV, descrizione=_JD)
    assert out["match_axes"]["skills_match"] == 10
    assert out["match_axes"]["seniority_match"] == 5  # unparseable → neutral
    assert out["match_axes"]["remote_match"] == 5  # missing → neutral


def test_capped_paths_carry_the_staleness_marker() -> None:
    """Every persisted analysis must carry ``eleggibilita_geografica`` or
    ``job_has_analysis`` would treat it as stale and re-score it forever."""
    out = ss.enforce_hard_requirements(
        ss._insufficient_description_analysis(_CV, "T", "A", "corta"),
        profile_markdown=_CV,
        descrizione="corta",
    )
    assert "eleggibilita_geografica" in out


# ── work mode read from the posting, not from the scan flag ──────────────────


def test_work_mode_prefers_the_posting_over_the_scan_flag() -> None:
    onsite = "Il ruolo prevede lavoro in sede presso lo stabilimento di Orbassano."
    assert ss._detect_work_mode({}, onsite, "Full Remote") == "In sede"
    assert (
        ss._detect_work_mode({}, "Lavoro ibrido, 2 giorni in ufficio.", "Full Remote") == "Ibrido"
    )
    assert ss._detect_work_mode({}, "Posizione full remote.", "In sede") == "Full Remote"


def test_work_mode_uses_row_flag_then_falls_back() -> None:
    assert (
        ss._detect_work_mode({"is_remote": True}, "descrizione neutra", "In sede") == "Full Remote"
    )
    assert (
        ss._detect_work_mode({"is_remote": False}, "descrizione neutra", "Full Remote") == "In sede"
    )
    # No evidence at all: the remote SEARCH flag is not a fact about the posting.
    assert ss._detect_work_mode({}, "descrizione neutra", "Full Remote") == "Non specificato"
    assert ss._detect_work_mode({}, "descrizione neutra", "In sede") == "In sede"


# ── declared salary vs the candidate's floor (flag, never a cap) ─────────────

_RAL_CTX = "RAL minima accettabile (EUR lordi/anno): 35000 EUR"


def test_parse_ral_reads_the_common_formats() -> None:
    assert ss._parse_ral("30.000€-45.000€") == (30000, 45000)
    assert ss._parse_ral("35k-50k") == (35000, 50000)
    assert ss._parse_ral("Non stimabile") == (None, None)
    assert ss._parse_ral("Non estimabile") == (None, None)  # model answered in Spanish
    assert ss._parse_ral("") == (None, None)


def test_salary_below_minimum_lowers_the_ceiling_without_hiding_the_offer() -> None:
    """A stated figure under a stated floor is a fact, so it moves the number.

    It used to flag and nothing else, on the grounds that capping would punish
    the rare posting honest enough to publish a figure. What that produced on the
    real archive: a TIM internship declaring up to 9.600 EUR against a declared
    floor of 20.000 held the model's 8/10 and led the "best for you" panel. So it
    is weighted, not blocking — the offer keeps its place and its badge, it just
    cannot outrank one that pays enough. An internship can still be worth taking.
    """
    out = ss.enforce_hard_requirements(
        _analysis(ral_stimata="22.000€-25.000€"),
        profile_markdown=_CV,
        descrizione=_JD,
        sede="Milan, Italy",
        extra_context=_RAL_CTX,
    )
    assert out["punteggio"] == 6, "held under the recommend band, not written off"
    assert out["punteggio"] > 3, "and above the hard-block cap, which means something else"
    assert ss.FLAG_SALARY_BELOW in out["blocchi"]
    assert any("sotto la tua minima" in m for m in out["skills_match"]["mancano"])
    assert out["match_axes"]["salary_match"] == 1
    assert "Salta" not in str(out.get("consiglio") or ""), "still worth reading"


def test_a_posting_that_declares_no_salary_is_untouched() -> None:
    """The asymmetry the original note worried about, kept true."""
    out = ss.enforce_hard_requirements(
        _analysis(ral_stimata="Non stimabile"),
        profile_markdown=_CV,
        descrizione=_JD,
        sede="Milan, Italy",
        extra_context=_RAL_CTX,
    )
    assert out["punteggio"] == 9
    assert ss.FLAG_SALARY_BELOW not in out["blocchi"]


def test_salary_above_minimum_lifts_the_axis() -> None:
    out = ss.enforce_hard_requirements(
        _analysis(ral_stimata="40.000€-55.000€"),
        profile_markdown=_CV,
        descrizione=_JD,
        sede="Milan, Italy",
        extra_context=_RAL_CTX,
    )
    assert out["match_axes"]["salary_match"] >= 7


def test_salary_axis_is_none_without_any_signal() -> None:
    out = ss.enforce_hard_requirements(
        _analysis(), profile_markdown=_CV, descrizione=_JD, sede="Milan, Italy"
    )
    assert out["match_axes"]["salary_match"] is None
    assert out["match_axes"]["skills_match"] is not None  # other axes untouched


# ── engagement type ──────────────────────────────────────────────────────────


def test_gig_platform_is_detected_from_the_company() -> None:
    out = ss.enforce_hard_requirements(
        _analysis(), profile_markdown=_CV, descrizione=_JD, sede="Remote", azienda="Toloka AI"
    )
    assert out["tipo_ingaggio"] == "Gig a task"


def test_gig_is_detected_from_the_text() -> None:
    jd = _JD + " Compensation is pay per task, no minimum hours guaranteed."
    out = ss.enforce_hard_requirements(
        _analysis(), profile_markdown=_CV, descrizione=jd, azienda="Acme Srl"
    )
    assert out["tipo_ingaggio"] == "Gig a task"


def test_piva_is_detected() -> None:
    jd = _JD + " Contratto: collaborazione con partita IVA."
    out = ss.enforce_hard_requirements(
        _analysis(), profile_markdown=_CV, descrizione=jd, azienda="Acme Srl"
    )
    assert out["tipo_ingaggio"] == "Freelance P.IVA"


def test_normal_employer_stays_unspecified() -> None:
    out = ss.enforce_hard_requirements(
        _analysis(), profile_markdown=_CV, descrizione=_JD, azienda="Generali Italia"
    )
    assert out["tipo_ingaggio"] == "Non specificato"


# ── hard blockers skip the LLM entirely ──────────────────────────────────────


class _ExplodingPM:
    """Any scoring call is a bug: the blocker was decidable from the text."""

    def preview_scoring_model(self, _policy: object) -> str:
        return "should-not-be-used"

    def complete_json(self, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("LLM called for an offer with a hard blocker")


def test_hard_block_reason_covers_both_blockers() -> None:
    assert ss.hard_block_reason(_CV, _JD, "Denver, CO")
    assert ss.hard_block_reason(_CV, _JD + " Votazione minima 102/110.", "Milan, Italy")
    assert ss.hard_block_reason(_CV, _JD, "Milan, Italy") is None
    # An explicit EU-remote opening is not a blocker even from a US address.
    assert ss.hard_block_reason(_CV, _JD + " Remote from Europe welcome.", "Austin, TX") is None


def test_analyze_offer_skips_the_llm_on_a_blocked_offer() -> None:
    out = ss.analyze_offer(_ExplodingPM(), _CV, "AI Engineer", "Morningstar", _JD, sede="Chicago, IL")
    assert out["punteggio"] == 3
    assert out["consiglio"] == "Salta"
    assert "Non candidabile" in out["riassunto"]


def test_batch_keeps_blocked_offers_out_of_the_prompt() -> None:
    prompts: list[str] = []

    class _RecordingPM:
        def preview_scoring_model(self, _policy: object) -> str:
            return "m"

        def complete_json(self, prompt: str, max_tokens: int = 700, **_k: object) -> dict[str, Any]:
            prompts.append(prompt)
            return {"valutazioni": [{"punteggio": 8}]}

    offers = [
        {"titolo": "A", "azienda": "Co", "descrizione": _JD, "sede": "Denver, CO"},
        {"titolo": "B", "azienda": "Co", "descrizione": _JD, "sede": "Milan, Italy"},
    ]
    out = ss.analyze_offers_batch(_RecordingPM(), _CV, offers)
    assert len(prompts) == 1
    assert prompts[0].count("--- OFFERTA ") == 1  # the blocked one never shipped
    assert out[0]["punteggio"] == 3  # answered locally
    assert out[1]["punteggio"] == 8  # the model's verdict, in the right slot
