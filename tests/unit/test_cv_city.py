"""Reading the candidate's city off their own CV.

Nothing extracted a place before 2.0.0, so "where do you want to work" could
only be learnt from a scan the user had already run — with a location the app
itself had supplied. The detector is deliberately narrow: a wrong city sends
every future scan to the wrong place, while an empty answer is a question the
app knows how to ask.

Written against the shapes a real CV uses, including the one that made the
first attempt useless: a header line with no label at all.
"""

from __future__ import annotations

import pytest

from app.cv_ingest import _extract_base_city, summarize_profile


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Residenza: 10121 Torino (TO), Italia", "Torino"),
        ("Indirizzo: Via Roma 12, 20139 Milano", "Milano"),
        ("Citta: Reggio Emilia", "Reggio Emilia"),
        ("residente in Napoli", "Napoli"),
        ("Based in: Berlin, Germany", "Berlin"),
        ("Address: 221B Baker Street, London", "London"),
        # No label anywhere — the shape a real CV actually uses.
        ("Mario Rossi\ntel 333 1234567 | mario@x.it | Bologna, Italia\n", "Bologna"),
    ],
)
def test_the_shapes_a_cv_writes_a_city_in(text: str, expected: str) -> None:
    assert _extract_base_city(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Location: Remote",
        "Nato a Bari il 12/03/1999",
        "Universita di Helsinki (Finlandia)",
        # An employer's address is not the candidate's: no contacts on the line,
        # and it is nowhere near the header.
        "CURRICULUM\n\nESPERIENZA\nFinwave S.p.A. (Gruppo Lutech) | Torino, Italia",
        "Jane Doe\njane@example.com\n\nEXPERIENCE\nGoogle | Dublin, Ireland",
        "",
    ],
)
def test_what_must_not_be_read_as_a_home(text: str) -> None:
    assert _extract_base_city(text) == ""


def test_the_summary_carries_it(tmp_path) -> None:
    cv = (
        "Anna Bianchi\n"
        "anna.bianchi@example.com | 340 1122334 | Padova, Italia\n\n"
        "ESPERIENZA\nInfermiera presso Ospedale civile, 3 anni.\n"
        "ISTRUZIONE\nLaurea triennale in Infermieristica.\n"
    )
    assert summarize_profile(cv)["base_city"] == "Padova"
