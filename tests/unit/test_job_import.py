"""Import-a-job-from-URL helpers."""

from __future__ import annotations

from typing import Any

from app.services.job_import import extract_job_fields, fetch_page_text


class _FakePM:
    def __init__(self, resp: Any) -> None:
        self.resp = resp
        self.last_prompt = ""

    def complete_json(self, prompt: str, max_tokens: int = 700, **_: Any) -> Any:
        self.last_prompt = prompt
        return self.resp


def test_extract_job_fields_from_dict() -> None:
    pm = _FakePM(
        {
            "titolo": "Backend Dev",
            "azienda": "Acme",
            "sede": "Torino, Italy",
            "descrizione": "APIs",
        }
    )
    assert extract_job_fields(pm, "raw posting") == {
        "titolo": "Backend Dev",
        "azienda": "Acme",
        "sede": "Torino, Italy",
        "descrizione": "APIs",
    }


def test_extract_job_fields_asks_for_the_location() -> None:
    """Without a location an imported job can never be geo-capped."""
    pm = _FakePM({})
    extract_job_fields(pm, "raw posting")
    assert '"sede"' in pm.last_prompt


def test_extract_job_fields_non_dict_result() -> None:
    assert extract_job_fields(_FakePM("garbage"), "x") == {
        "titolo": "",
        "azienda": "",
        "sede": "",
        "descrizione": "",
    }


def test_extract_job_fields_strips_and_defaults() -> None:
    out = extract_job_fields(_FakePM({"titolo": "  Dev  "}), "x")
    assert out["titolo"] == "Dev"
    assert out["azienda"] == "" and out["descrizione"] == ""


def test_fetch_page_text_rejects_bad_input() -> None:
    assert fetch_page_text("") is None
    assert fetch_page_text("not-a-url") is None
