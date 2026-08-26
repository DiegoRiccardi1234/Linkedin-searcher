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
    # Order matters: the first matching triggers are the first roles proposed.
    # AI/LLM triggers come first because this heuristic used to have none — a CV
    # built on LLM evaluation work came out as "Junior Python Developer, Junior
    # Frontend Developer" purely because it mentioned python and react.
    role_map = [
        ("llm", "AI QA / LLM Evaluation"),
        ("hallucination", "AI QA / LLM Evaluation"),
        ("prompt engineering", "AI QA / LLM Evaluation"),
        ("annotation", "AI Data Specialist"),
        ("annotator", "AI Data Specialist"),
        ("generative ai", "AI Specialist"),
        ("prompt", "AI Specialist"),
        ("nlp", "AI Specialist"),
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
    # A CV mentioning a dozen technologies would otherwise propose a dozen roles;
    # the scan turns these into search terms, so keep the strongest few.
    preferred_roles = preferred_roles[:5]

    graduation_year = _estimate_graduation_year(markdown_text)

    years_experience = _estimate_years_experience(markdown_text)
    experience_level = _estimate_experience_level(years_experience)
    # A recent graduate with little measured experience is "junior", not raw
    # "entry" — but never override a mid/senior signal from real work history.
    if experience_level == "entry" and _DEGREE_LINE_RE.search(markdown_text):
        experience_level = "junior"
    languages = _extract_languages(markdown_text)

    return {
        "skills": found_skills,
        "preferred_roles": preferred_roles,
        "graduation_year": graduation_year,
        "years_experience": years_experience,
        "experience_level": experience_level,
        # Normalised degree level, not the free-text "education" the LLM writes.
        # An offer demanding a master's is now compared against THIS, so it has
        # to be one of the ordered levels rather than a sentence.
        "education_level": _degree_level(markdown_text),
        "languages": languages,
        # Read locally, on purpose: Privacy Mode redacts the address before the
        # CV reaches a model, so the model cannot answer this even when asked.
        # And this is the field that turns a CV into a search — without it the
        # app only learnt where you live from a scan it had already chosen the
        # city for.
        "base_city": _extract_base_city(markdown_text),
    }


#: A residence line, as CV headers actually write it. Deliberately narrow: a
#: wrong city sends every scan to the wrong place, and an empty answer is a
#: question the app knows how to ask.
_BASE_CITY_RE = re.compile(
    r"(?im)^\s*(?:residenza|residente\s+(?:a|in)|domicilio|indirizzo|citt[aà]|"
    r"address|based\s+in|location|city)\s*[:\-–]?\s*(.+)$"  # noqa: RUF001
)
#: "20139 Milano (MI), Italia" -> "Milano". Postcodes, province codes and the
#: country say nothing a job board needs.
_CITY_NOISE_RE = re.compile(
    r"(?i)\b\d{4,6}\b|\(\s*[a-z]{2}\s*\)|\b(?:italia|italy|france|españa|spain|"
    r"deutschland|germany|schweiz|suisse)\b"
)


#: Words that appear where a city would and are not one.
_NOT_A_CITY_LINE = {"remote", "da remoto", "worldwide", "europe", "europa", "n/a", "-"}

#: The header line of a real CV, which is where the city actually lives: no
#: "Residenza:" label anywhere, just contacts separated by pipes or bullets —
#: ``334 5829619 | name@example.com | Torino, Italia``. Measured on a real CV,
#: where the labelled pattern above found nothing at all.
_CONTACT_LINE_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}|\d[\d\s().\-]{7,}\d"
)
#: "Torino, Italia" — one or two capitalised words, then a country.
_CITY_COUNTRY_RE = re.compile(
    r"^\s*([A-ZÀ-Ü][\w'’\-]+(?:\s+[A-ZÀ-Ü][\w'’\-]+)?)\s*,\s*[A-ZÀ-Ü][\w'’\-]+\.?\s*$"  # noqa: RUF001
)


def _city_from_contact_header(text: str) -> str:
    """The city out of a CV's contact line, when nothing is labelled."""
    for line in (text or "").splitlines()[:15]:
        if not _CONTACT_LINE_RE.search(line):
            continue
        for chunk in re.split(r"[|•·]|\s{3,}", line):
            match = _CITY_COUNTRY_RE.match(chunk)
            if match and match.group(1).lower() not in _NOT_A_CITY_LINE:
                return match.group(1).strip()
    return ""


def _extract_base_city(text: str) -> str:
    for match in _BASE_CITY_RE.finditer(text or ""):
        raw = _CITY_NOISE_RE.sub(" ", match.group(1))
        # A street address on the same line ("Via Roma 12, 20139 Milano, Italia"):
        # the city is the last part that still has words in it — the country and
        # the postcode have just been stripped, which can empty the tail.
        parts = [p for p in (raw.split(",") if "," in raw else [raw]) if p.strip()]
        city = parts[-1] if parts else ""
        city = re.sub(r"[^\w\s'\-]", " ", city, flags=re.UNICODE).strip()
        city = re.sub(r"\s{2,}", " ", city)
        if city.lower() in _NOT_A_CITY_LINE:
            continue
        # Two words is a city ("Reggio Emilia"); more is a sentence.
        if city and len(city) <= 40 and len(city.split()) <= 3 and not city.isdigit():
            return city
    return _city_from_contact_header(text)


def _degree_level(text: str) -> str:
    """The highest degree the CV shows, as one of the ordered levels."""
    from app.services.scan.heuristics import EDUCATION_LEVELS, education_requirement

    level, _preferred = education_requirement(text or "")
    return level if level in EDUCATION_LEVELS else ""


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
    r")[^\n]*[:\n]",  # header keyword may be followed by more words on the line
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
    r")[^\n]*[:\n]",  # e.g. "ISTRUZIONE E FORMAZIONE" — keyword not at line end
    re.IGNORECASE,
)

# A line that names a degree/education, used to locate the graduation year and to
# recognise a recent graduate (bias entry-level → junior).
_DEGREE_LINE_RE = re.compile(
    r"laurea|degree|bachelor|master|dottorat|phd|universit|graduat|diplom", re.IGNORECASE
)
# A 4-digit year, but NOT one that is the numerator of a regulation/law number
# like "2016/679" (which used to leak in as the graduation year).
_YEAR_TOKEN_RE = re.compile(r"(?:19|20)\d{2}(?!/\d)")


def _estimate_graduation_year(text: str) -> str:
    """Best-effort graduation year: prefer the max year on a degree/education
    line; fall back to the latest plausible year in the whole CV. Regulation
    numbers (``2016/679``) are excluded via ``_YEAR_TOKEN_RE``."""

    def years_in(s: str) -> list[int]:
        return [int(y) for y in _YEAR_TOKEN_RE.findall(s)]

    lines = text.splitlines()
    degree_lines = [i for i, line in enumerate(lines) if _DEGREE_LINE_RE.search(line)]

    # 1. A year written on the degree line itself is the strongest signal.
    own = [y for i in degree_lines for y in years_in(lines[i])]
    if own:
        return str(max(own))
    # 2. None there: PDF layouts right-align dates and extraction often drops
    #    them onto the next line. Only reached when step 1 found nothing, so a
    #    CV that does put the year on the degree line is unaffected — without
    #    this ordering "Università di Helsinki" borrowed the internship's year.
    windowed = [y for i in degree_lines for y in years_in(" ".join(lines[i : i + 2]))]
    if windowed:
        return str(max(windowed))
    # 3. Last resort. This is what used to return the end of a certification
    #    course as the graduation year (measured: 2026 for a 2025 degree).
    ys = years_in(text)
    return str(max(ys)) if ys else ""


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
    # A one-line "Italiano madrelingua · Inglese livello intermedio (B2)" is a
    # single line holding TWO languages: splitting only on newlines produced the
    # whole sentence as one bogus entry ("Italiano (madrelingua · Inglese …)").
    # The bullet separators CVs use for this are treated as line breaks.
    flattened = re.sub(r"\s*[·•|]\s*", "\n", scope)
    lines = [raw.strip(" \t*-•·•") for raw in flattened.splitlines()]  # noqa: B005
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


# UI locale code -> language name used in the LLM prompt. The narrative fields
# of the profile summary must come back in the site language, not the CV's.
_SUMMARY_LANGUAGES = {
    "en": "English",
    "it": "Italian",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
}


def _cv_parse_call_kwargs(provider_manager: Any) -> dict[str, Any]:
    """Provider/model kwargs for reading a CV into JSON.

    This was the only CV call-site that passed nothing at all, so it ran on the
    global default policy while the other four (review, improve, RAL and goals
    suggestions) all state what they need. getattr-guarded because test stubs
    stand in for the real manager and have neither ``settings`` nor ``pin_kwargs``.
    """
    from app.services.generation import CV_PARSE_POLICY

    pin = getattr(provider_manager, "pin_kwargs", None)
    if not callable(pin):
        return {}
    settings = getattr(provider_manager, "settings", None)
    kwargs = pin(getattr(settings, "cv_model", None), CV_PARSE_POLICY)
    return kwargs if isinstance(kwargs, dict) else {}


def summarize_profile_with_llm(
    markdown_text: str,
    provider_manager: "Any",
    on_retry: Any = None,
    language: str | None = None,
    privacy: bool = False,
) -> dict[str, Any]:
    """Rich profile summary using LLM. Falls back to keyword-based if LLM fails.

    ``language`` is the UI locale code (en/it/es/fr/de): narrative fields
    (summary, strengths, industries, education) are written in that language.
    Makes a single ``complete_json`` call — that method already owns
    transient-error retry (429/5xx) and cross-provider failover, so no outer
    retry loop here (the old 5x loop could balloon one upload to ~15 min).
    ``on_retry`` is accepted for backward compatibility but no longer fires.
    """
    language_name = _SUMMARY_LANGUAGES.get((language or "en").lower()[:2], "English")
    content = markdown_text
    name_bullet = (
        "- name: the candidate's full name as written on the CV (string, e.g. 'Mario Rossi'). "
        "Use null if you cannot determine it confidently.\n"
    )
    if privacy:
        # Privacy Mode: never send the name/contacts to the LLM. The real name is
        # recovered locally by extract_candidate_name in the upload handler.
        from app.services.pii import redact_pii

        content, _ = redact_pii(markdown_text, extract_candidate_name(markdown_text))
        name_bullet = ""
    prompt = (
        "You are an expert career advisor. Analyze this CV/resume and extract a structured profile.\n"
        "Return a JSON object with these fields:\n"
        f"{name_bullet}"
        "- skills: list of technical and soft skills found\n"
        "- preferred_roles: list of 3-5 job titles that best match this candidate. "
        "Prefer SPECIFIC, board-searchable titles; avoid bare generic titles like "
        "'Quality Analyst' or 'QA Analyst' that a job board fuzzy-matches to unrelated "
        "roles (e.g. manufacturing quality control) — qualify them with the domain "
        "(e.g. 'AI Quality Engineer', 'NLP Data Annotator') or use the concrete tech title.\n"
        "- experience_level: one of 'entry' | 'junior' | 'mid' | 'senior', based ONLY on "
        "professional work experience. A recent graduate whose only roles are internships or "
        "short jobs is 'entry' or 'junior', NEVER 'senior'.\n"
        "- years_experience: total years of PROFESSIONAL WORK experience only (jobs and "
        "internships). Do NOT count education, degree/coursework years, certifications, or "
        "high-school years, and never treat a law/regulation number (e.g. '2016/679') as a year. "
        # This used to say "If under a year, use 0", and a graduate with two
        # internships is the commonest CV this app sees: zero is the one answer
        # that makes every posting asking for two years unreachable. A decimal
        # costs nothing downstream — the fraction is carried all the way into the
        # blocking decision.
        "Use a DECIMAL when it is under a year or falls between years: 0.5 means six months, "
        "1.5 means eighteen months. Use 0 only when there is no work experience at all.\n"
        "- strengths: list of 3 key strengths\n"
        "- industries: list of industries the candidate has experience in\n"
        "- education: highest education level and field\n"
        "- languages: list of spoken languages with level if stated, e.g. 'Italian (Native)', 'English (B2)'\n"
        # Nothing extracted a place before this, so "where do you want to work"
        # could only be learnt from a scan the user had already run — with a
        # location the app had supplied. Asked for plainly: it is the one field
        # that turns a CV into a search.
        "- base_city: the city the candidate lives in or nearest to, as written "
        "on the CV (string). Use null unless the CV actually states a place of "
        "residence or contact address — never guess it from an employer's or a "
        "university's location.\n"
        "- summary: 2-3 sentence professional summary\n\n"
        f"Write the values of 'summary', 'strengths', 'industries' and 'education' in "
        f"{language_name}, regardless of the CV's language. Keep 'skills' and "
        "'preferred_roles' as commonly written in job postings (do not translate "
        "technology names or job titles).\n\n"
        f"CV Content:\n{content[:3000]}"
    )
    try:
        result = provider_manager.complete_json(
            prompt=prompt,
            # The profile JSON carries seven lists and a summary; 600 tokens cut
            # off the richer CVs, and a cut-off answer silently becomes the
            # heuristic profile below. Rare call (once per upload), so the budget
            # is generous on purpose.
            max_tokens=1200,
            **_cv_parse_call_kwargs(provider_manager),
        )
        if isinstance(result, dict):
            heuristic = summarize_profile(markdown_text)
            # Merge LLM output with heuristic-derived fields so years_experience
            # and experience_level always have a sensible fallback.
            heuristic.update({k: v for k, v in result.items() if v not in (None, "", [])})
            # Re-apply the recent-graduate bias after the merge: the LLM may
            # answer "entry", but a graduate reads as "junior" on both paths.
            if heuristic.get("experience_level") == "entry" and _DEGREE_LINE_RE.search(
                markdown_text
            ):
                heuristic["experience_level"] = "junior"
            return heuristic
    except Exception as exc:
        log.warning("LLM CV summarization failed; using heuristic: %s", exc)
    return summarize_profile(markdown_text)
