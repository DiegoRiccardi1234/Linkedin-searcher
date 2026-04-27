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

    return {
        "skills": found_skills,
        "preferred_roles": preferred_roles,
        "graduation_year": graduation_year,
    }


def summarize_profile_with_llm(markdown_text: str, provider_manager: "Any") -> dict[str, Any]:
    """Rich profile summary using LLM. Falls back to keyword-based if LLM fails."""
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
        "- summary: 2-3 sentence professional summary\n\n"
        f"CV Content:\n{markdown_text[:3000]}"
    )
    try:
        result = provider_manager.complete_json(prompt=prompt, max_tokens=600)
        if isinstance(result, dict):
            return result
    except Exception as exc:
        log.warning("LLM profile summarization failed, falling back to heuristic: %s", exc)
    return summarize_profile(markdown_text)
