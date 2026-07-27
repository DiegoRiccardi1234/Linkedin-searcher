"""Work units respect the model's context, not just a job count.

Cerebras' free tier caps context at 8K tokens. Three long descriptions plus the
CV, the rules and the schema go past that, and the call fails — which looked
like "the model is down" rather than "the batch was too big".
"""

from __future__ import annotations

from typing import Any

from app.services import scanner_service as ss


def _offer(chars: int, name: str = "job") -> dict[str, Any]:
    return {"titolo": name, "azienda": "Co", "descrizione": "x" * chars}


def test_short_offers_fill_the_batch() -> None:
    units = ss._scoring_units([_offer(500) for _ in range(7)], 3, 8000, 3000)
    assert [len(u) for u in units] == [3, 3, 1]


def test_long_offers_split_below_the_batch_size() -> None:
    """Same batch size, longer postings: fewer per call."""
    units = ss._scoring_units([_offer(6000) for _ in range(4)], 3, 2500, 3000)
    assert all(len(u) <= 2 for u in units)
    assert sum(len(u) for u in units) == 4  # nothing is dropped


def test_a_single_huge_offer_still_gets_its_own_unit() -> None:
    units = ss._scoring_units([_offer(40000)], 3, 2000, 3000)
    assert [len(u) for u in units] == [1]


def test_cerebras_gets_a_smaller_budget_than_the_default() -> None:
    class _PM:
        class settings:  # noqa: N801 - mimics the real attribute shape
            llm_provider_order = ["cerebras", "openrouter"]

    class _Other:
        class settings:  # noqa: N801
            llm_provider_order = ["openrouter"]

    assert ss._context_budget(_PM()) < ss._context_budget(_Other())  # type: ignore[arg-type]
    assert ss._context_budget(_PM()) > 1000  # type: ignore[arg-type]


def test_unknown_provider_uses_the_generous_default() -> None:
    class _PM:
        settings = None

    assert ss._context_budget(_PM()) > 20000  # type: ignore[arg-type]
