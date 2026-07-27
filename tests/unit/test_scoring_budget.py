"""Token budget of a scoring call, and the salary axis built from real pay.

Two regressions this file locks down:

1. The single-offer path asked for ``max_tokens=200`` while the prompt demands a
   two-dozen-field JSON object. Every such call truncated, failed over through
   every candidate model, penalised each of them and landed on the keyword
   heuristic — and that path is also the batch's own fallback for missing or
   cloned slots.
2. The salary axis was re-enabled with a flat, invented ``5`` as soon as the
   posting carried any pay figure, one function after the code that had
   deliberately set it to ``None``. It now compares the posting's pay with the
   candidate's declared floor, or stays null.
"""

from __future__ import annotations

from typing import Any

from app.services import scanner_service as ss

# Long enough to clear MIN_DESCRIPTION_CHARS: a short blurb takes the capped
# path and never reaches the provider at all.
_FULL_JD = (
    "Ruolo di AI QA Engineer: valutazione di output LLM, definizione di rubriche "
    "di qualita', analisi degli errori e regressioni sui modelli generativi. "
    "Requisiti: laurea triennale in informatica, Python, inglese B2, attenzione "
    "al dettaglio e capacita' di documentare i casi di errore in modo ripetibile. "
    "Gradita esperienza con annotazione dati, prompt engineering e strumenti di "
    "test automatico. Offriamo lavoro ibrido a Torino, formazione continua e "
    "affiancamento a un team di ricerca che pubblica risultati. "
)


class _BudgetSpy:
    """Provider stub recording the token budget of every scoring call."""

    def __init__(self) -> None:
        self.budgets: list[int] = []

    def complete_json(self, prompt: str, max_tokens: int = 0, **_kwargs: Any) -> dict[str, Any]:
        self.budgets.append(max_tokens)
        return {"punteggio": 7, "consiglio": "Valutabile"}


def test_single_offer_budget_fits_the_schema() -> None:
    spy = _BudgetSpy()
    ss.analyze_offer(
        provider_manager=spy,  # type: ignore[arg-type]
        profile_markdown="CV: Python, LLM evaluation",
        titolo="AI QA Engineer",
        azienda="Acme",
        descrizione=_FULL_JD,
    )
    assert spy.budgets == [ss._scoring_max_tokens(1)]
    # The schema alone is ~24 fields with three short lists: 200 tokens could
    # never hold it, which is exactly how the bug stayed invisible for so long.
    assert spy.budgets[0] > 1000


def test_batch_budget_scales_with_offer_count() -> None:
    assert ss._scoring_max_tokens(3) > ss._scoring_max_tokens(1)
    assert ss._scoring_max_tokens(3) == 500 * 3 + 1600
    # A batch of one must not be cheaper than the single path it replaces.
    assert ss._scoring_max_tokens(0) == ss._scoring_max_tokens(1)


# --- salary axis ---------------------------------------------------------------


def test_annual_amount_normalises_the_interval() -> None:
    assert ss._annual_amount(30000, "yearly") == 30000
    assert ss._annual_amount(2500, "monthly") == 30000
    assert ss._annual_amount(None, "yearly") is None
    assert ss._annual_amount("", "") is None
    assert ss._annual_amount(0, "yearly") is None
    # No interval declared: a figure too small to be a yearly salary is not
    # silently treated as one.
    assert ss._annual_amount(25, "") is None
    assert ss._annual_amount(32000, "") == 32000


def test_salary_axis_compares_with_the_declared_floor() -> None:
    assert ss._salary_axis(None, None, 30000) is None  # posting says nothing
    assert ss._salary_axis(28000.0, 32000.0, None) is None  # user declared nothing
    assert ss._salary_axis(None, 20000.0, 30000) == 1  # well under the floor
    assert ss._salary_axis(None, 28000.0, 30000) == 3  # just under
    assert ss._salary_axis(None, 32000.0, 30000) == 6  # just over
    assert ss._salary_axis(None, 50000.0, 30000) == 10  # far above


def test_salary_axis_stays_null_when_nothing_is_known() -> None:
    """The v1.7.6 promise: no confident 5 for an offer nobody knows the pay of."""
    analysis = ss.enforce_hard_requirements(
        {"punteggio": 7, "ral_stimata": "Non stimabile"},
        profile_markdown="CV",
        descrizione=_FULL_JD,
    )
    assert analysis["match_axes"]["salary_match"] is None
