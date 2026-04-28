import re
from io import BytesIO
from typing import Any

from app.log import get_logger

log = get_logger(__name__)


def _extract_text_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install pypdf for PDF support") from exc

    reader = PdfReader(BytesIO(data))
    chunks: list[str] = []
    for page in reader.pages:
        chunks.append(page.extract_text() or "")
    return "\n".join(chunks).strip()


def _extract_text_docx(data: bytes) -> str:
    try:
        from docx import Document
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Install python-docx for DOCX support") from exc

    doc = Document(BytesIO(data))
    lines = [p.text for p in doc.paragraphs]
    return "\n".join(lines).strip()


def extract_markdown_from_upload(filename: str, data: bytes) -> str:
    lower = filename.lower()
    if lower.endswith((".md", ".txt")):
        return data.decode("utf-8", errors="replace").strip()
    if lower.endswith(".pdf"):
        return _extract_text_pdf(data)
    if lower.endswith(".docx"):
        return _extract_text_docx(data)
    raise RuntimeError("Unsupported CV format. Use .md, .txt, .pdf or .docx")


CV_KEYWORDS = (
    "experience",
    "skill",
    "education",
    "work",
    "esperienza",
    "competenze",
    "formazione",
    "lavoro",
    "cv",
    "resume",
    "curriculum",
)
MIN_CV_CHARS = 200


class InvalidCVContent(ValueError):
    """Raised when the uploaded file does not look like a CV/resume."""


def validate_cv_content(markdown: str) -> None:
    text = markdown.strip()
    if len(text) < MIN_CV_CHARS:
        raise InvalidCVContent(f"CV content too short ({len(text)} chars, min {MIN_CV_CHARS}).")
    lower = text.lower()
    if not any(kw in lower for kw in CV_KEYWORDS):
        raise InvalidCVContent(
            "File does not appear to be a CV/resume (missing common section keywords)."
        )


def summarize_profile(markdown_text: str) -> dict[str, Any]:
    """Keyword-based profile summary (fast, no LLM needed)."""
    lower = markdown_text.lower()

    skill_keywords = [
        "qa",
        "testing",
        "analista",
        "analyst",
        "cybersecurity",
        "soc",
        "python",
        "typescript",
        "javascript",
        "react",
        "java",
        "sql",
        "automation",
        "devops",
        "docker",
        "kubernetes",
        "git",
        "machine learning",
        "data analysis",
        "agile",
        "scrum",
        "api",
        "rest",
        "graphql",
        "node",
        "fastapi",
        "django",
        "c#",
        ".net",
        "azure",
        "aws",
        "gcp",
        "linux",
    ]
    found_skills = [kw for kw in skill_keywords if kw in lower]

    preferred_roles: list[str] = []
    role_map = [
        ("analyst", "Junior Business Analyst"),
        ("analista", "Junior Business Analyst"),
        ("qa", "Junior QA Tester"),
        ("testing", "Junior QA Tester"),
        ("cybersecurity", "Junior Cybersecurity Analyst"),
        ("soc", "Junior SOC Analyst"),
        ("data analy", "Junior Data Analyst"),
        ("machine learning", "Junior ML Engineer"),
        ("automation", "Junior Automation Engineer"),
        ("devops", "Junior DevOps Engineer"),
        ("python", "Junior Python Developer"),
        ("react", "Junior Frontend Developer"),
        ("full stack", "Junior Full Stack Developer"),
    ]
    for trigger, role in role_map:
        if trigger in lower and role not in preferred_roles:
            preferred_roles.append(role)

    years = re.findall(r"(20\d{2})", markdown_text)
    graduation_year = ""
    if years:
        graduation_year = years[-1]

    years_experience = _estimate_years_experience(markdown_text)
    experience_level = _estimate_experience_level(years_experience)
    languages = _extract_languages(markdown_text)

    return {
        "skills": found_skills,
        "preferred_roles": preferred_roles,
        "graduation_year": graduation_year,
        "years_experience": years_experience,
        "experience_level": experience_level,
        "languages": languages,
    }


# Match date ranges with hyphen, en dash, or em dash separators.
_DATE_RANGE_RE = re.compile(
    r"(?:(?P<start_m>\d{1,2})[/.\-])?(?P<start_y>(?:19|20)\d{2})\s*[-–—]\s*"  # noqa: RUF001
    r"(?:(?:(?P<end_m>\d{1,2})[/.\-])?(?P<end_y>(?:19|20)\d{2})"
    r"|presente|present|current|in\s+corso|today|attuale|oggi|now)",
    re.IGNORECASE,
)


_WORK_SECTION_HEADER_RE = re.compile(
    r"(?:^|\n)\s*("
    r"esperienza\s+lavorativa|esperienze\s+lavorative|esperienza\s+professionale|esperienza"
    r"|work\s+experience|professional\s+experience|experience"
    r"|experiencia\s+laboral|experiencia\s+profesional"
    r"|expérience\s+professionnelle|expériences\s+professionnelles|expérience"
    r"|berufserfahrung|berufliche\s+erfahrung"
    r")\s*[:\n]",
    re.IGNORECASE,
)
_OTHER_SECTION_HEADER_RE = re.compile(
    r"(?:^|\n)\s*("
    r"istruzione|formazione|education|educación|formation|ausbildung|"
    r"competenze|skills|skill|hard\s+skills|soft\s+skills|"
    r"lingue|languages|idiomas|langues|sprachen|"
    r"certificaz|certificat|certifications|"
    r"progetti|projects|proyectos|projets|projekte|"
    r"profilo|profile|summary|sobre|à\s+propos|über\s+mich|"
    r"interessi|interests|hobby|hobbies|aficiones|loisirs"
    r")\s*[:\n]",
    re.IGNORECASE,
)


_LANG_SECTION_HEADER_RE = re.compile(
    r"(?:^|\n)\s*(lingue|languages|idiomas|langues|sprachen)\s*[:\n]",
    re.IGNORECASE,
)

# Common language names across the five supported locales (IT/EN/ES/FR/DE).
_LANG_NAME_PATTERN = (
    r"italiano|italian|italien|italienisch|"
    r"inglese|english|inglés|anglais|englisch|"
    r"spagnolo|spanish|español|espagnol|spanisch|"
    r"francese|french|francés|français|französisch|"
    r"tedesco|german|alemán|allemand|deutsch|"
    r"portoghese|portuguese|portugués|portugais|portugiesisch|"
    r"cinese|chinese|chino|chinois|chinesisch|"
    r"giapponese|japanese|japonés|japonais|japanisch|"
    r"russo|russian|ruso|russe|russisch|"
    r"arabo|arabic|árabe|arabe|arabisch"
)
_LANG_NAME_RE = re.compile(rf"\b(?P<name>{_LANG_NAME_PATTERN})\b", re.IGNORECASE)


def _extract_languages(text: str) -> list[str]:
    """Parse the Languages section of a CV into a list like ``"Italiano (Madrelingua)"``.

    Returns an empty list if no explicit Languages section header is found, to
    avoid pulling random language mentions from prose elsewhere in the CV.
    Deduplicates by normalized language name (case-insensitive).
    """
    header = _LANG_SECTION_HEADER_RE.search(text)
    if not header:
        return []
    rest = text[header.end() :]
    next_section = _OTHER_SECTION_HEADER_RE.search(rest)
    scope = rest[: next_section.start()] if next_section else rest

    found: list[str] = []
    seen: set[str] = set()
    lines = [raw.strip(" \t*-•·•") for raw in scope.splitlines()]  # noqa: B005
    for idx, line in enumerate(lines):
        if not line:
            continue
        match = _LANG_NAME_RE.search(line)
        if not match:
            continue
        name = match.group("name").strip().title()
        key = name.lower()
        if key in seen:
            continue
        # Level = everything after the matched name, stripped of separators and any parens.
        level = line[match.end() :].strip(" \t:-–—.,()")  # noqa: RUF001
        # PDF extraction often splits "Lang:\n  Level" across two lines — pull the
        # next non-empty line if it doesn't start with another language name.
        if not level:
            for next_line in lines[idx + 1 : idx + 3]:
                if next_line and not _LANG_NAME_RE.match(next_line):
                    level = next_line.strip(" \t:-–—.,()")  # noqa: RUF001
                    break
                if next_line:
                    break
        level = level.replace("(", "").replace(")", "").strip()
        if len(level) > 60:
            level = level[:60].rstrip()
        if level:
            found.append(f"{name} ({level})")
        else:
            found.append(name)
        seen.add(key)
    return found


def _scope_work_section(text: str) -> str:
    """Return only the substring inside the work-experience section, if detectable."""
    work_match = _WORK_SECTION_HEADER_RE.search(text)
    if not work_match:
        return text
    start = work_match.end()
    rest = text[start:]
    next_section = _OTHER_SECTION_HEADER_RE.search(rest)
    if next_section:
        return rest[: next_section.start()]
    return rest


def _estimate_years_experience(text: str) -> int:
    from datetime import datetime as _dt

    now = _dt.now()
    now_months = now.year * 12 + now.month

    scoped = _scope_work_section(text)
    intervals: list[tuple[int, int]] = []
    for match in _DATE_RANGE_RE.finditer(scoped):
        start_y = int(match.group("start_y"))
        start_m = int(match.group("start_m") or 1)
        end_y_raw = match.group("end_y")
        end_m_raw = match.group("end_m")
        if end_y_raw:
            end_y = int(end_y_raw)
            end_m = int(end_m_raw or 12)
        else:
            end_y = now.year
            end_m = now.month
        if start_y < 1970 or end_y < start_y or end_y > now.year + 1:
            continue
        sm = start_y * 12 + start_m
        em = end_y * 12 + end_m
        em = min(em, now_months)
        if em <= sm:
            continue
        intervals.append((sm, em))

    if not intervals:
        return 0

    intervals.sort()
    merged: list[list[int]] = [list(intervals[0])]
    for sm, em in intervals[1:]:
        if sm <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], em)
        else:
            merged.append([sm, em])

    months_total = sum(end - start for start, end in merged)
    years = round(months_total / 12)
    return max(0, min(60, years))


def _estimate_experience_level(years: int) -> str:
    if years >= 8:
        return "senior"
    if years >= 4:
        return "mid"
    if years >= 1:
        return "junior"
    return "entry"


def summarize_profile_with_llm(
    markdown_text: str, provider_manager: "Any", on_retry: Any = None
) -> dict[str, Any]:
    """Rich profile summary using LLM. Falls back to keyword-based if LLM fails.

    Retries up to 5 times with longer waits (3s, 5s, 7s, 9s) on transient
    errors like 429 rate-limits. Calls ``on_retry(attempt, wait_seconds, exc)``
    between attempts, if provided, so the caller can stream progress events.
    """
    prompt = (
        "You are an expert career advisor. Analyze this CV/resume and extract a structured profile.\n"
        "Return a JSON object with these fields:\n"
        "- skills: list of technical and soft skills found\n"
        "- preferred_roles: list of 3-5 job titles that best match this candidate\n"
        "- experience_level: 'entry' | 'junior' | 'mid' | 'senior'\n"
        "- years_experience: estimated years of professional experience (number)\n"
        "- strengths: list of 3 key strengths\n"
        "- industries: list of industries the candidate has experience in\n"
        "- education: highest education level and field\n"
        "- languages: list of spoken languages with level if stated, e.g. 'Italian (Native)', 'English (B2)'\n"
        "- summary: 2-3 sentence professional summary\n\n"
        f"CV Content:\n{markdown_text[:3000]}"
    )
    import time as _time

    delays = [3.0, 5.0, 7.0, 9.0]
    last_exc: Exception | None = None
    for attempt in range(1, 1 + len(delays) + 1):
        try:
            result = provider_manager.complete_json(prompt=prompt, max_tokens=600)
            if isinstance(result, dict):
                heuristic = summarize_profile(markdown_text)
                # Merge LLM output with heuristic-derived fields so years_experience
                # and experience_level always have a sensible fallback.
                heuristic.update({k: v for k, v in result.items() if v not in (None, "", [])})
                return heuristic
        except Exception as exc:
            last_exc = exc
            if attempt > len(delays):
                break
            wait = delays[attempt - 1]
            log.warning(
                "LLM CV summarization attempt %d/%d failed (%s); retrying in %.1fs",
                attempt,
                len(delays) + 1,
                exc.__class__.__name__,
                wait,
            )
            if callable(on_retry):
                import contextlib

                with contextlib.suppress(Exception):
                    on_retry(attempt, wait, exc)
            _time.sleep(wait)

    if last_exc is not None:
        log.warning("LLM CV summarization gave up after retries; using heuristic: %s", last_exc)
    return summarize_profile(markdown_text)
