"""Unit tests for `app.cv_ingest`."""

from __future__ import annotations

import pytest

from app.cv_ingest import (
    InvalidCVContent,
    extract_markdown_from_upload,
    summarize_profile,
    summarize_profile_with_llm,
    validate_cv_content,
)


def test_extract_txt_roundtrip() -> None:
    text = "Diego Developer\nPython, SQL, QA"
    out = extract_markdown_from_upload("cv.txt", text.encode("utf-8"))
    assert out == text


def test_extract_md_roundtrip() -> None:
    text = "# CV\n\n- Python\n- Selenium"
    out = extract_markdown_from_upload("cv.md", text.encode("utf-8"))
    assert out == text


def test_extract_rejects_unsupported_format() -> None:
    """Genuinely unknown formats must still raise. .jpg now routes to OCR."""
    with pytest.raises(RuntimeError):
        extract_markdown_from_upload("cv.exe", b"not-supported")


def test_extract_is_case_insensitive() -> None:
    out = extract_markdown_from_upload("CV.TXT", b"hello")
    assert out == "hello"


def test_summarize_profile_detects_skills_and_roles() -> None:
    md = "I have experience with Python, React, QA automation and Selenium."
    result = summarize_profile(md)

    assert "python" in result["skills"]
    assert "react" in result["skills"]
    assert "qa" in result["skills"] or "testing" in result["skills"]

    preferred = " ".join(result["preferred_roles"]).lower()
    assert "python" in preferred or "automation" in preferred or "qa" in preferred


# A junior CV shaped like Diego's — reproduces the bugs: education date ranges
# inflating years, and a regulation number ("2016/679") leaking as graduation.
_JUNIOR_CV = """\
Diego Riccardi
Dottore in Scienze e Tecnologie Informatiche
PROFILO
Laureato in Scienze e Tecnologie Informatiche con esperienza in sviluppo web.
ESPERIENZA LAVORATIVA
AI Data Annotator / Language Researcher 04/2026 - 05/2026
Universita di Helsinki - Language Technology Research Group
Sviluppatore Frontend B2B - Tirocinio Curriculare 05/2023 - 09/2023
Finwave S.p.A. Torino
ISTRUZIONE E FORMAZIONE
Laurea Triennale in Scienze e Tecnologie Informatiche 09/2018 - 07/2025
Universita degli Studi di Torino
Certificazioni e Diploma 2012 - 2018
Liceo Scientifico Dante Alighieri
COMPETENZE TECNICHE
Python, TypeScript, React, Java
LINGUE
Italiano: Madrelingua
Autorizzo il trattamento dei dati ai sensi del Regolamento UE 2016/679 (GDPR).
"""


def test_junior_cv_not_reported_as_senior() -> None:
    """Regression: a junior CV was parsed as 'Senior · 14 anni' because education
    date ranges (laurea 2018-2025, liceo 2012-2018) were summed as experience."""
    result = summarize_profile(_JUNIOR_CV)
    assert result["years_experience"] <= 2, result["years_experience"]
    assert result["experience_level"] not in ("senior", "mid")


def test_education_ranges_excluded_from_experience() -> None:
    result = summarize_profile(_JUNIOR_CV)
    # The 7-year laurea span (2018-2025) and 6-year liceo span must NOT count.
    assert result["years_experience"] < 5


def test_graduation_year_ignores_regulation_number() -> None:
    """'Regolamento UE 2016/679' must not be picked as the graduation year;
    the degree line (…07/2025) is the graduation."""
    result = summarize_profile(_JUNIOR_CV)
    assert result["graduation_year"] == "2025"


def test_summarize_profile_graduation_prefers_degree_line() -> None:
    md = "ISTRUZIONE\nLaurea in Informatica 2018 - 2021\nESPERIENZA\nDeveloper 2022 - 2024"
    result = summarize_profile(md)
    assert result["graduation_year"] == "2021"


def test_summarize_profile_empty_markdown() -> None:
    result = summarize_profile("")
    assert result["skills"] == []
    assert result["preferred_roles"] == []
    assert result["graduation_year"] == ""


def test_validate_cv_content_rejects_too_short() -> None:
    with pytest.raises(InvalidCVContent, match="too short"):
        validate_cv_content("hi there, this is way too short")


def test_validate_cv_content_rejects_random_long_text() -> None:
    bogus = "lorem ipsum dolor sit amet " * 30
    with pytest.raises(InvalidCVContent, match="does not appear"):
        validate_cv_content(bogus)


def test_validate_cv_content_accepts_realistic_cv_italian() -> None:
    cv = (
        "Diego Riccardi\n"
        "Curriculum Vitae\n\n"
        "Esperienza professionale: 3 anni come sviluppatore Python presso varie aziende.\n"
        "Competenze tecniche: Python, FastAPI, SQLite, Docker, Linux, Git, REST API.\n"
        "Formazione: Laurea triennale in Informatica conseguita nel 2022.\n"
        "Lavoro attuale: stage curriculare in un team di backend development."
    )
    validate_cv_content(cv)


def test_validate_cv_content_accepts_realistic_cv_english() -> None:
    cv = (
        "John Doe — Resume\n\n"
        "Work experience: 5 years as a backend engineer in fintech and SaaS.\n"
        "Skills: Python, FastAPI, PostgreSQL, Redis, Kubernetes, AWS, CI/CD.\n"
        "Education: BSc in Computer Science (2018) plus relevant certifications.\n"
        "Languages: English (C1), Italian (native)."
    )
    validate_cv_content(cv)


def test_summarize_profile_skill_word_boundary_no_false_positives() -> None:
    """`soc`, `git`, `api` must not match inside unrelated Italian words.

    Regression for the heuristic that previously matched `soc` in
    `associato`, `git` in `logistica`, `api` in `capi` — producing fake
    skills on sales/admin CVs without any tech background.
    """
    md = (
        "CV - Curriculum Vitae di Mario Rossi.\n"
        "Esperienza nella logistica e gestione capi reparto. "
        "Lavoro associato in un negozio di abbigliamento. "
        "Competenze: vendita al dettaglio, gestione clienti, social skills."
    )
    result = summarize_profile(md)
    assert "soc" not in result["skills"]
    assert "git" not in result["skills"]
    assert "api" not in result["skills"]


def test_summarize_profile_no_default_role_for_non_tech_cv() -> None:
    """Non-tech CV must produce empty preferred_roles, not hardcoded 'Junior SOC Analyst'."""
    md = (
        "CV - Anna Bianchi, Store Manager con esperienza nella vendita.\n"
        "Competenze: gestione personale, customer service, visual merchandising. "
        "Lingue: italiano madrelingua, inglese avanzato."
    )
    result = summarize_profile(md)
    assert result["preferred_roles"] == []


def test_summarize_profile_detects_data_analyst_role() -> None:
    """`data analyst` and `data analysis` triggers must still map to Data Analyst role."""
    md = "Esperienza in data analysis con Python e SQL. Lavoro come data analyst."
    result = summarize_profile(md)
    assert "Junior Data Analyst" in result["preferred_roles"]


def test_estimate_years_from_italian_phrase() -> None:
    """Catch 'Opero da 7 anni' / 'Lavoro da 5 anni' — Italian explicit phrases."""
    md = (
        "CV - Anna Bianchi\nProfilo professionale\n"
        "Opero da 7 anni come Store Manager con curriculum nel retail. "
        "Esperienza pluriennale nella gestione del personale."
    )
    result = summarize_profile(md)
    assert result["years_experience"] == 7
    assert result["experience_level"] == "mid"


def test_estimate_years_from_english_phrase() -> None:
    md = (
        "John Doe Resume\nWork experience\n"
        "Over 10 years of experience as a senior backend engineer."
    )
    result = summarize_profile(md)
    assert result["years_experience"] >= 10


def test_estimate_years_picks_max_of_phrase_and_dates() -> None:
    """If CV has both date ranges and an explicit phrase, take the larger value."""
    md = (
        "CV\nWork experience: 2020-2022 at Acme. "
        "Esperienza pluriennale: opero da 8 anni nel settore tecnologico."
    )
    result = summarize_profile(md)
    assert result["years_experience"] == 8


def test_extract_image_routes_through_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """``.jpg`` upload must go through the OCR path; we mock pytesseract.

    Verifies the routing — not Tesseract output quality. Lets the test suite
    pass on machines without the Tesseract binary (CI, fresh checkouts).
    """
    import io

    from PIL import Image

    from app import cv_ingest

    def _fake_ocr(image, lang="ita+eng"):  # type: ignore[no-untyped-def]
        return (
            "Mario Rossi - Curriculum Vitae\n"
            "Esperienza professionale: 5 anni come addetto alle vendite. "
            "Competenze: customer service, gestione cassa. "
            "Formazione: diploma di scuola superiore."
        )

    class _FakePytesseract:
        TesseractNotFoundError = RuntimeError
        image_to_string = staticmethod(_fake_ocr)

        class pytesseract:
            tesseract_cmd: str = ""

    monkeypatch.setitem(__import__("sys").modules, "pytesseract", _FakePytesseract)

    # Build a 10x10 white PNG byte payload so PIL.Image.open succeeds.
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buf, format="PNG")

    text = cv_ingest.extract_markdown_from_upload("scan.jpg", buf.getvalue())
    assert "Mario Rossi" in text
    assert "Esperienza" in text


def test_extract_unsupported_format_message_mentions_images() -> None:
    """Error message after OCR support must list image formats so users know."""
    with pytest.raises(RuntimeError, match=r"image|\.jpg|\.png"):
        extract_markdown_from_upload("cv.exe", b"binary garbage")


def test_extract_accepts_image_extensions() -> None:
    """All image extensions route to the image branch (no 'unsupported' error)."""
    from unittest import mock

    from app import cv_ingest

    for ext in (".jpg", ".jpeg", ".png", ".webp", ".avif", ".tiff", ".tif", ".bmp"):
        with mock.patch.object(cv_ingest, "_extract_text_image", return_value="dummy") as mocked:
            cv_ingest.extract_markdown_from_upload(f"cv{ext}", b"x")
            mocked.assert_called_once()


def test_get_ocr_lang_uses_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_get_ocr_lang`` reads the env var so AppContainer can plumb settings.ocr_languages through."""
    from app.cv_ingest import _get_ocr_lang

    monkeypatch.delenv("JOBFINDER_OCR_LANG", raising=False)
    assert "eng" in _get_ocr_lang()
    assert "ita" in _get_ocr_lang()

    monkeypatch.setenv("JOBFINDER_OCR_LANG", "spa+eng")
    assert _get_ocr_lang() == "spa+eng"

    # Explicit override beats the env var.
    assert _get_ocr_lang("fra+deu") == "fra+deu"


def test_validate_cv_content_accepts_spanish_cv() -> None:
    cv = (
        "Currículum Vítae - María García\n\n"
        "Experiencia laboral: 5 años como desarrolladora frontend en startups tecnológicas.\n"
        "Habilidades: React, TypeScript, accesibilidad web, testing automatizado. "
        "Educación: Grado en Informática (2018) por la Universidad Politécnica. "
        "Idiomas: Español (nativo), Inglés (C1)."
    )
    validate_cv_content(cv)


def test_validate_cv_content_accepts_french_cv() -> None:
    cv = (
        "Curriculum Vitae - Pierre Dupont\n\n"
        "Expérience professionnelle: 4 ans comme ingénieur logiciel chez Acme Corp. "
        "Compétences techniques: Python, FastAPI, PostgreSQL, Docker, tests unitaires. "
        "Formation: Master en informatique à l'École Polytechnique. "
        "Langues: Français (natif), Anglais (B2)."
    )
    validate_cv_content(cv)


def test_validate_cv_content_accepts_german_cv() -> None:
    cv = (
        "Lebenslauf - Anna Schmidt\n\n"
        "Berufserfahrung: 6 Jahre als Backend-Entwicklerin bei verschiedenen Firmen in Berlin. "
        "Kenntnisse: Java, Spring Boot, AWS, Microservices, agile Methoden. "
        "Ausbildung: Diplom-Informatikerin von der Technischen Universität München. "
        "Sprachen: Deutsch (Muttersprache), Englisch (C1)."
    )
    validate_cv_content(cv)


class _FakeProviderManager:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def complete_json(self, prompt: str, max_tokens: int = 600) -> dict:
        return self._payload


def test_llm_entry_graduate_upgraded_to_junior() -> None:
    """The recent-graduate bias (entry → junior when a degree line is present)
    must survive the LLM merge, not only the heuristic-only path."""
    result = summarize_profile_with_llm(
        _JUNIOR_CV,
        _FakeProviderManager({"experience_level": "entry", "years_experience": 0}),
    )
    assert result["experience_level"] == "junior"


def test_llm_senior_level_not_overridden_by_graduate_bias() -> None:
    result = summarize_profile_with_llm(
        _JUNIOR_CV,
        _FakeProviderManager({"experience_level": "mid", "years_experience": 5}),
    )
    assert result["experience_level"] == "mid"


class _PromptCapturingManager(_FakeProviderManager):
    def __init__(self) -> None:
        super().__init__({})
        self.prompt = ""

    def complete_json(self, prompt: str, max_tokens: int = 600) -> dict:
        self.prompt = prompt
        return super().complete_json(prompt, max_tokens)


def test_llm_summary_language_follows_ui_locale() -> None:
    manager = _PromptCapturingManager()
    summarize_profile_with_llm(_JUNIOR_CV, manager, language="it")
    assert "in Italian" in manager.prompt


def test_llm_summary_language_defaults_to_english() -> None:
    manager = _PromptCapturingManager()
    summarize_profile_with_llm(_JUNIOR_CV, manager)
    assert "in English" in manager.prompt
    summarize_profile_with_llm(_JUNIOR_CV, manager, language="xx")
    assert "in English" in manager.prompt


class _FailingManager:
    """complete_json always raises — simulates a provider that keeps 429ing."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, prompt: str, max_tokens: int = 600) -> dict:
        self.calls += 1
        raise Exception("429 rate limit")


def test_llm_summary_calls_provider_once_then_heuristic(monkeypatch) -> None:
    """No outer retry amplification: complete_json (which now retries and fails
    over internally) is called exactly once; on failure we fall back to the
    heuristic instead of looping 5x with long sleeps."""
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    mgr = _FailingManager()
    result = summarize_profile_with_llm(_JUNIOR_CV, mgr)
    assert mgr.calls == 1
    assert isinstance(result, dict)
    assert result.get("experience_level")


class _PolicyCapturingManager(_FakeProviderManager):
    """A manager that DOES look like the real one: it has ``settings`` and
    ``pin_kwargs``, so the CV-parsing call must state which model it wants."""

    class _Settings:
        cv_model = ""

    def __init__(self) -> None:
        super().__init__({})
        self.settings = self._Settings()
        self.kwargs: dict = {}
        self.max_tokens = 0

    def pin_kwargs(self, model_id, policy):  # type: ignore[no-untyped-def]
        return {"policy_override": policy}

    def complete_json(self, prompt: str, max_tokens: int = 600, **kwargs) -> dict:  # type: ignore[no-untyped-def]
        self.kwargs = kwargs
        self.max_tokens = max_tokens
        return super().complete_json(prompt, max_tokens)


def test_cv_parsing_asks_for_a_json_capable_model_and_room_to_answer() -> None:
    """Reading a CV into JSON was the one CV call-site that stated no policy at
    all: it ran on the global default, and a reasoning model's cut-off answer is
    silently replaced by the keyword profile."""
    from app.providers.model_selector import SCORING_MIN_SIZE_B

    manager = _PolicyCapturingManager()
    summarize_profile_with_llm(_JUNIOR_CV, manager)

    policy = manager.kwargs.get("policy_override")
    assert policy is not None
    assert policy["hard_floor"] is True
    assert policy["min_size_b"] == SCORING_MIN_SIZE_B
    assert policy["weights"]["reasoning"] == 0
    assert manager.max_tokens >= 1000  # the profile JSON does not fit in 600
