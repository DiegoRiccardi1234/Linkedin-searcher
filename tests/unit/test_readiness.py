"""What the app knows before it searches — and what it refuses to invent.

The behaviour under test is the one that made this release necessary: an empty
search form used to resolve to the six terms and the city of the person who
wrote the app, and those values were then stored as if the user had chosen
them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import Database
from app.services import roles_shortlist
from app.services.readiness import profile_readiness
from app.services.search_intent import resolve_locations, resolve_search_terms, search_intent


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "searcher.db")
    try:
        yield database
    finally:
        database.close()


def test_an_empty_form_on_a_fresh_install_resolves_to_nothing(db: Database) -> None:
    terms, origin = resolve_search_terms(db, [])
    assert terms == [] and origin == "none"
    locations, loc_origin = resolve_locations(db, [])
    assert locations == [] and loc_origin == "none"


def test_what_was_typed_always_wins(db: Database) -> None:
    db.set_preference("last_scan_terms", json.dumps(["vecchio"]))
    terms, origin = resolve_search_terms(db, ["infermiere pediatrico"])
    assert terms == ["infermiere pediatrico"] and origin == "explicit"


def test_the_chain_falls_back_in_order(db: Database) -> None:
    db.set_preference("preferred_roles", "Analista funzionale, Data Analyst")
    assert resolve_search_terms(db, [])[1] == "cv"

    roles_shortlist.add(db, ["Junior Project Manager"])
    terms, origin = resolve_search_terms(db, [])
    assert origin == "shortlist" and terms == ["Junior Project Manager"]

    db.set_preference("last_scan_terms", json.dumps(["consulente applicativo"]))
    terms, origin = resolve_search_terms(db, [])
    assert origin == "last_scan" and terms == ["consulente applicativo"]


def test_roles_read_off_a_cv_are_enough_to_search_with(db: Database) -> None:
    """The link that was missing: the CV wrote one key and the form read another."""
    db.set_preference("preferred_roles", "Infermiere, Coordinatore infermieristico")
    terms, origin = resolve_search_terms(db, None)
    assert terms == ["Infermiere", "Coordinatore infermieristico"] and origin == "cv"


def test_the_city_comes_from_the_profile_when_no_scan_has_run(db: Database) -> None:
    db.set_preference("profile_fact_base_cities", "bari")
    locations, origin = resolve_locations(db, [])
    assert locations == ["Bari"] and origin == "profile"


def test_a_corrupt_stored_list_is_not_a_search(db: Database) -> None:
    db.set_preference("last_scan_terms", "{not json")
    assert resolve_search_terms(db, [])[1] == "none"


def test_readiness_blocks_on_exactly_two_things(db: Database) -> None:
    report = profile_readiness(db)
    assert report["ready"] is False
    assert sorted(report["blocking"]) == ["location", "search_terms"]
    # Everything else is advice: an unknown fact cannot hide an offer, so it
    # cannot stop a scan either.
    assert "grade" in report["warnings"] and "cv" in report["warnings"]


def test_readiness_clears_once_the_two_are_answered(db: Database) -> None:
    db.set_preference("preferred_roles", "Analista funzionale")
    db.set_preference("profile_fact_base_cities", "torino")
    report = profile_readiness(db)
    assert report["ready"] is True and report["blocking"] == []
    assert report["suggested_terms"] == ["Analista funzionale"]
    assert report["suggested_locations"] == ["Torino"]
    assert report["terms_origin"] == "cv" and report["locations_origin"] == "profile"


def test_every_item_says_which_panel_it_belongs_to(db: Database) -> None:
    items = profile_readiness(db)["items"]
    assert {i["group"] for i in items} <= {"about", "target", "constraints"}
    by_id = {i["id"]: i for i in items}
    assert by_id["search_terms"]["severity"] == "blocking"
    assert by_id["driving_licence"]["severity"] == "warning"
    assert by_id["driving_licence"]["status"] == "missing"


def test_a_stated_fact_reports_its_provenance(db: Database) -> None:
    db.set_preference("profile_fact_driving_licence", "0")
    by_id = {i["id"]: i for i in profile_readiness(db)["items"]}
    assert by_id["driving_licence"]["status"] == "ok"
    assert by_id["driving_licence"]["source"] == "manuale"


def test_search_intent_is_the_same_answer_the_scan_will_use(db: Database) -> None:
    db.set_preference("last_scan_terms", json.dumps(["junior business analyst"]))
    db.set_preference("last_scan_locations", json.dumps(["Milano"]))
    intent = search_intent(db)
    report = profile_readiness(db)
    assert intent.terms == report["suggested_terms"]
    assert intent.locations == report["suggested_locations"]
    assert intent.ready is True
