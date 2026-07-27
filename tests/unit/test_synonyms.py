"""Searching for the word you use finds the job posted under another one.

The case that started this: an Italian searching *tirocinio* sees none of the
postings titled *stage* — which is what most Italian employers write — and
neither matches *internship*. jobspy sends the term verbatim, and the app never
emitted anything but what was typed.
"""

from __future__ import annotations

from app.services.scan import synonyms
from app.services.scan.vocab import _DOMAIN_VOCAB


def test_the_internship_case() -> None:
    found = synonyms.synonyms_for("tirocinio")
    assert "stage" in found
    assert "internship" in found


def test_qualifiers_are_kept() -> None:
    """"tirocinio marketing" must not collapse into a bare "stage"."""
    assert "stage marketing" in synonyms.synonyms_for("tirocinio marketing")


def test_matching_is_on_whole_words() -> None:
    assert synonyms.synonyms_for("stagecoach driver") == []


def test_unknown_terms_expand_to_nothing() -> None:
    assert synonyms.synonyms_for("addetto alle pulizie") == []
    assert synonyms.expand_terms(["addetto alle pulizie"]) == ["addetto alle pulizie"]


def test_ordinary_role_titles_are_left_alone() -> None:
    """Both wordings of a normal job title already surface from one query — and
    expanding the commonest searches would double their scrape and scoring."""
    for term in ("react developer", "sviluppatore python", "data analyst"):
        assert synonyms.expand_terms([term]) == [term]


def test_expansion_keeps_the_user_words_first_and_is_capped() -> None:
    """Every extra term costs a scrape and its scoring."""
    terms = synonyms.expand_terms(["tirocinio", "AI QA"])
    assert terms[0] == "tirocinio"
    assert "AI QA" in terms
    assert len(terms) == 3  # one alternative for tirocinio, none for AI QA


def test_expansion_does_not_duplicate_what_the_user_already_typed() -> None:
    terms = synonyms.expand_terms(["tirocinio", "stage"])
    assert terms.count("stage") == 1


def test_entry_route_words_survive_the_relevance_gate() -> None:
    """A posting titled only "Tirocinio curriculare" used to be dropped: with an
    empty description the gate judges the title, and it shared no word with the
    vocabulary."""
    for word in ("tirocinio", "stage", "internship", "trainee", "apprendistato"):
        assert word in _DOMAIN_VOCAB
