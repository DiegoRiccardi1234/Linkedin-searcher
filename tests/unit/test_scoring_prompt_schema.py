"""The scoring schema and rules the prompt asks a small free model to follow.

The schema is the app's real contract with the model: it decides how many
tokens the answer costs (i.e. whether it truncates), in what order the model
commits to its conclusions, and which fields anything downstream can rely on.
"""

from __future__ import annotations

from app.services import scanner_service as ss

_JD = (
    "Ruolo di AI QA Engineer per la valutazione di modelli linguistici. "
    "Requisiti: laurea triennale, Python, inglese B2. Sede a Torino, ibrido. "
) * 3


def test_verdict_fields_come_after_the_evidence() -> None:
    """The score must be emitted last: a model cannot revise what it already
    wrote, so asking for the number first made it guess before reading itself."""
    schema = ss._PER_OFFER_SCHEMA
    for evidence in ("requisiti", "skills_match", "match_axes", "punti_forza"):
        assert schema.index(evidence) < schema.index('"punteggio"'), evidence
    assert schema.index('"punteggio"') < schema.index('"consiglio"')


def test_schema_drops_fields_nothing_reads() -> None:
    """Every asked-for field costs output tokens on every single scored offer."""
    for dead in ("junior_friendly", "note_azienda", "reputazione_azienda"):
        assert dead not in ss._PER_OFFER_SCHEMA


def test_schema_drops_fields_the_app_decides_itself() -> None:
    """The deterministic checks overwrite these; asking was pure token cost."""
    for computed in ("voto_minimo_richiesto", "eleggibilita_geografica"):
        assert computed not in ss._PER_OFFER_SCHEMA


def test_schema_keeps_what_the_ui_and_the_checks_need() -> None:
    for kept in (
        "requisiti",
        "responsabilita",
        "benefit",
        "skills_match",
        "match_axes",
        "titolo_studio_richiesto",
        "livello_richiesto",
        "tipo_ingaggio",
        "ral_stimata",
        "adatta_neolaureati",
        "programmazione_richiesta",
        "smart_working",
        "riassunto",
    ):
        assert kept in ss._PER_OFFER_SCHEMA, kept


def test_no_user_name_in_the_public_schema() -> None:
    """The strengths/weaknesses keys used to carry the developer's first name."""
    assert "per_diego" not in ss._PER_OFFER_SCHEMA
    assert "punti_forza" in ss._PER_OFFER_SCHEMA
    assert "punti_deboli" in ss._PER_OFFER_SCHEMA


def test_legacy_strength_keys_are_mapped_on_read() -> None:
    out = ss._normalize_analysis(
        {"punteggio": 7, "punti_forza_per_diego": "match su python", "punti_deboli_per_diego": "x"}
    )
    assert out["punti_forza"] == "match su python"
    assert out["punti_deboli"] == "x"
    assert "punti_forza_per_diego" not in out


def test_rules_define_the_score_scale() -> None:
    """A 1-10 ask whose scale exists only in code the model cannot see is a
    number the model invents; the advice thresholds must match the heuristic."""
    rules = ss._SCORING_RULES
    for band in ("9-10", "7-8", "5-6", "3-4", "1-2"):
        assert band in rules, band
    assert "Candidati subito" in rules and "Valutabile" in rules and "Salta" in rules


def test_both_prompts_carry_schema_and_rules() -> None:
    single = ss._analysis_prompt("CV", "AI QA", "Acme", _JD)
    batch = ss._batch_analysis_prompt("CV", [{"titolo": "T", "azienda": "A", "descrizione": _JD}])
    for prompt in (single, batch):
        assert ss._PER_OFFER_SCHEMA in prompt
        assert ss._SCORING_RULES in prompt
