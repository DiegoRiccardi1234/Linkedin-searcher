import contextlib
import os
import re
from io import BytesIO
from typing import Any

from app.log import get_logger

log = get_logger(__name__)


# Threshold below which a PDF is considered "no extractable text" and we try
# OCR. Most well-formed PDFs return thousands of chars; scanned PDFs typically
# return 0 or a handful of stray glyphs.
_OCR_FALLBACK_MIN_CHARS = 50

# Default Tesseract language list. Overridable per-call via the ``lang`` kwarg
# or globally via the ``JOBFINDER_OCR_LANG`` env var (set by ``AppContainer``
# from ``settings.ocr_languages``). Bundle ships ``eng+ita+spa+fra+deu+osd``.
_DEFAULT_OCR_LANG = "eng+ita+spa+fra+deu"


def _get_ocr_lang(override: str | None = None) -> str:
    if override:
        return override
    return os.environ.get("JOBFINDER_OCR_LANG", _DEFAULT_OCR_LANG)


def _resolve_tesseract_cmd() -> str | None:
    """Locate the ``tesseract`` binary, preferring a bundled portable copy.

    Search order:
    1. ``JOBFINDER_TESSERACT_CMD`` env var (explicit override).
    2. ``vendor/tesseract/tesseract.exe`` (Windows portable bundled with build).
    3. PATH-resolved ``tesseract``/``tesseract.exe`` (system install).
    4. Windows default install dirs (``C:/Program Files/Tesseract-OCR``).
    Returns None if Tesseract is not available.
    """
    override = os.environ.get("JOBFINDER_TESSERACT_CMD")
    if override and os.path.isfile(override):
        return override
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(here)
    bundled = os.path.join(project_root, "vendor", "tesseract", "tesseract.exe")
    if os.path.isfile(bundled):
        return bundled
    import shutil

    found = shutil.which("tesseract") or shutil.which("tesseract.exe")
    if found:
        return found
    for default in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    ):
        if os.path.isfile(default):
            return default
    return None


def _ocr_image_bytes(data: bytes, *, lang: str | None = None) -> str:
    """Run Tesseract OCR on raw image bytes, return extracted text (best-effort)."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "OCR requires pytesseract + Pillow. Install: pip install pytesseract Pillow"
        ) from exc

    # AVIF support is opt-in via the pillow-avif-plugin side-effect import.
    with contextlib.suppress(Exception):  # pragma: no cover - optional import
        import pillow_avif  # noqa: F401

    cmd = _resolve_tesseract_cmd()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        opened = Image.open(BytesIO(data))
        # Tesseract refuses some PIL modes (e.g. AVIF often opens as ``RGBA``
        # or palette mode). Re-encode through PNG so the OCR engine always
        # receives a format it accepts.
        img: Any = opened.convert("RGB") if opened.mode not in ("RGB", "L") else opened
        png_buf = BytesIO()
        img.save(png_buf, format="PNG")
        png_buf.seek(0)
        normalized = Image.open(png_buf)
        return str(pytesseract.image_to_string(normalized, lang=_get_ocr_lang(lang))).strip()
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError(
            "Tesseract OCR not found. Install Tesseract (Windows: "
            "`winget install UB-Mannheim.TesseractOCR`) or set JOBFINDER_TESSERACT_CMD."
        ) from exc
    except Exception as exc:
        log.warning("OCR failed: %s", exc)
        return ""


def _extract_text_image(data: bytes) -> str:
    """Extract text from a single image (JPG/PNG/AVIF/TIFF/BMP/WEBP) via OCR."""
    return _ocr_image_bytes(data)


def _extract_text_svg(data: bytes) -> str:
    """Rasterize SVG to PNG then OCR. Falls back to grepping <text> tags."""
    # Inline-text SVGs (text stored as XML, not paths) are best parsed directly.
    text_inline = " ".join(
        re.findall(r"<text[^>]*>([^<]+)</text>", data.decode("utf-8", errors="replace"))
    )
    if len(text_inline) >= _OCR_FALLBACK_MIN_CHARS:
        return text_inline.strip()
    # Otherwise rasterize and OCR. cairosvg is optional; if missing, return whatever we got.
    try:  # pragma: no cover - optional dependency
        import cairosvg

        png_bytes = cairosvg.svg2png(bytestring=data, output_width=1600)
        return _ocr_image_bytes(png_bytes)
    except Exception as exc:
        log.info("SVG rasterize unavailable (%s); returning inline text only", exc)
        return text_inline.strip()


def _extract_text_pdf(data: bytes) -> str:
    """Extract text from PDF, falling back to OCR per-page if pypdf yields nothing."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install pypdf for PDF support") from exc

    reader = PdfReader(BytesIO(data))
    chunks: list[str] = []
    for page in reader.pages:
        chunks.append(page.extract_text() or "")
    text = "\n".join(chunks).strip()

    if len(text) >= _OCR_FALLBACK_MIN_CHARS:
        return text
    # Scanned/image-only PDF: try rasterize + OCR.
    return _extract_text_pdf_via_ocr(data) or text


def _extract_text_pdf_via_ocr(data: bytes) -> str:
    """Render each PDF page to an image and OCR it. Heavy: only used as fallback."""
    try:
        from pdf2image import convert_from_bytes
    except ImportError:  # pragma: no cover
        log.info("pdf2image not installed; cannot OCR scanned PDF")
        return ""
    try:
        images = convert_from_bytes(data, dpi=200)
    except Exception as exc:
        # pdf2image needs poppler; on Windows the bundle ships it under vendor/poppler.
        log.warning("PDF rasterize failed (poppler missing?): %s", exc)
        return ""
    pages_text: list[str] = []
    try:
        import pytesseract
    except ImportError:  # pragma: no cover
        return ""
    cmd = _resolve_tesseract_cmd()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    lang = _get_ocr_lang()
    for img in images:
        try:
            pages_text.append(str(pytesseract.image_to_string(img, lang=lang)))
        except Exception as exc:
            log.warning("OCR page failed: %s", exc)
    return "\n".join(pages_text).strip()


def _extract_text_docx(data: bytes) -> str:
    try:
        from docx import Document
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Install python-docx for DOCX support") from exc

    doc = Document(BytesIO(data))
    lines = [p.text for p in doc.paragraphs]
    return "\n".join(lines).strip()


_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".tiff", ".tif", ".bmp")


def extract_markdown_from_upload(filename: str, data: bytes) -> str:
    lower = filename.lower()
    if lower.endswith((".md", ".txt", ".markdown")):
        return data.decode("utf-8", errors="replace").strip()
    if lower.endswith(".pdf"):
        return _extract_text_pdf(data)
    if lower.endswith(".docx"):
        return _extract_text_docx(data)
    if lower.endswith(_IMAGE_EXTS):
        return _extract_text_image(data)
    if lower.endswith(".svg"):
        return _extract_text_svg(data)
    raise RuntimeError(
        "Unsupported CV format. Use .md, .txt, .pdf, .docx or an image "
        "(.jpg, .png, .webp, .avif, .tiff, .bmp, .svg)."
    )


CV_KEYWORDS = (
    # English
    "experience",
    "skill",
    "education",
    "work",
    "employment",
    "career",
    "profile",
    "summary",
    "qualifications",
    "languages",
    # Italian
    "esperienza",
    "competenze",
    "formazione",
    "lavoro",
    "cv",
    "resume",
    "curriculum",
    "professionale",
    "abilitazion",  # abilitazioni / abilitazione (academic CVs)
    "qualifica",
    "carriera",
    "studi",
    "diploma",
    "laurea",
    # Spanish
    "experiencia",
    "habilidades",
    "formación",
    "educación",
    "trabajo",
    "currículum",
    "perfil",
    "idiomas",
    "titulación",
    # French
    "expérience",
    "compétence",
    "formation",
    "éducation",
    "travail",
    "profil",
    "langues",
    "diplôme",
    "qualification",
    # German
    "berufserfahrung",
    "kenntnisse",
    "ausbildung",
    "bildung",
    "arbeit",
    "lebenslauf",
    "sprachen",
    "abschluss",
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


def _keyword_present(keyword: str, lower_text: str) -> bool:
    """Word-boundary aware substring match.

    Plain ``in`` matched ``soc`` inside ``associato`` and ``git`` inside
    ``logistica``, polluting the heuristic with false skills. We require the
    keyword not to be flanked by alphanumerics so ``soc`` matches only in
    contexts like ``SOC analyst`` and ``git`` only in ``git, github, ...``.
    Works for multi-word keys and keys with special chars (``c#``, ``.net``).
    """
    pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
    return re.search(pattern, lower_text) is not None


_NAME_BLACKLIST = (
    "curriculum",
    "vitae",
    "resume",
    "cv",
    "personal information",
    "contact",
    "profile",
    "summary",
)


def extract_candidate_name(markdown_text: str) -> str | None:
    """Heuristic extraction of the candidate's name from CV markdown.

    Looks for the first non-empty heading (``# Name``, ``## Name``) or the first
    short line that looks like a person's name (2-4 capitalized words, no
    digits, length 3-60). Returns ``None`` if nothing plausible is found.
    """
    if not markdown_text:
        return None
    lines = [line.strip() for line in markdown_text.splitlines()]
    candidates: list[str] = []
    for raw in lines[:20]:
        if not raw:
            continue
        stripped = raw.lstrip("#").strip().strip("*_")
        if not stripped or len(stripped) > 60 or len(stripped) < 3:
            continue
        low = stripped.lower()
        if any(bad in low for bad in _NAME_BLACKLIST):
            continue
        if any(ch.isdigit() for ch in stripped):
            continue
        if "@" in stripped or "/" in stripped or "://" in stripped:
            continue
        words = stripped.split()
        if not (2 <= len(words) <= 4):
            continue
        if not all(w[0].isalpha() for w in words):
            continue
        # Each word must look like a name token: at least one uppercase letter.
        if not all(any(c.isupper() for c in w) for w in words):
            continue
        candidates.append(stripped)
    return candidates[0] if candidates else None


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
    found_skills = [kw for kw in skill_keywords if _keyword_present(kw, lower)]

    preferred_roles: list[str] = []
    role_map = [
        ("analyst", "Junior Business Analyst"),
        ("analista", "Junior Business Analyst"),
        ("qa", "Junior QA Tester"),
        ("testing", "Junior QA Tester"),
        ("cybersecurity", "Junior Cybersecurity Analyst"),
        ("soc", "Junior SOC Analyst"),
        ("data analyst", "Junior Data Analyst"),
        ("data analysis", "Junior Data Analyst"),
        ("data analytics", "Junior Data Analyst"),
        ("machine learning", "Junior ML Engineer"),
        ("automation", "Junior Automation Engineer"),
        ("devops", "Junior DevOps Engineer"),
        ("python", "Junior Python Developer"),
        ("react", "Junior Frontend Developer"),
        ("full stack", "Junior Full Stack Developer"),
    ]
    for trigger, role in role_map:
        if _keyword_present(trigger, lower) and role not in preferred_roles:
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


_YEARS_PHRASE_RE = re.compile(
    r"(?:opero|lavoro|esperienza|experience|with|over|più|circa)"
    r"(?:\s+(?:da|di|of|da circa|da oltre|over|than|pluriennale))*"
    r"\s+(\d{1,2})\+?\s*(?:anni|years|year|ans|años|jahre)",
    re.IGNORECASE,
)
_YEARS_PHRASE_PREFIX_RE = re.compile(
    r"(\d{1,2})\+?\s*(?:anni|years|year|ans|años|jahre)\s+"
    r"(?:di|of|d['e]|en)\s+(?:esperienza|experience|expérience|experiencia|erfahrung)",
    re.IGNORECASE,
)


def _estimate_years_from_phrases(text: str) -> int:
    """Catch explicit phrases like 'Opero da 7 anni' or '5+ years of experience'."""
    candidates: list[int] = []
    for rx in (_YEARS_PHRASE_RE, _YEARS_PHRASE_PREFIX_RE):
        for m in rx.finditer(text):
            try:
                n = int(m.group(1))
            except (ValueError, IndexError):
                continue
            if 0 < n <= 60:
                candidates.append(n)
    return max(candidates) if candidates else 0


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

    phrase_years = _estimate_years_from_phrases(text)

    if not intervals:
        return phrase_years

    intervals.sort()
    merged: list[list[int]] = [list(intervals[0])]
    for sm, em in intervals[1:]:
        if sm <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], em)
        else:
            merged.append([sm, em])

    months_total = sum(end - start for start, end in merged)
    years = round(months_total / 12)
    # Take the larger of computed-from-dates vs explicit phrase, capped at 60.
    return max(0, min(60, max(years, phrase_years)))


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
        "- name: the candidate's full name as written on the CV (string, e.g. 'Mario Rossi'). "
        "Use null if you cannot determine it confidently.\n"
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
