from io import BytesIO
import re
from typing import Any


def _extract_text_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Per i PDF installa pypdf") from exc

    reader = PdfReader(BytesIO(data))
    chunks: list[str] = []
    for page in reader.pages:
        chunks.append(page.extract_text() or "")
    return "\n".join(chunks).strip()


def _extract_text_docx(data: bytes) -> str:
    try:
        from docx import Document
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Per i DOCX installa python-docx") from exc

    doc = Document(BytesIO(data))
    lines = [p.text for p in doc.paragraphs]
    return "\n".join(lines).strip()


def extract_markdown_from_upload(filename: str, data: bytes) -> str:
    lower = filename.lower()
    if lower.endswith(".md") or lower.endswith(".txt"):
        return data.decode("utf-8", errors="replace").strip()
    if lower.endswith(".pdf"):
        return _extract_text_pdf(data)
    if lower.endswith(".docx"):
        return _extract_text_docx(data)
    raise RuntimeError("Formato CV non supportato. Usa .md, .txt, .pdf o .docx")


def summarize_profile(markdown_text: str) -> dict[str, Any]:
    lower = markdown_text.lower()

    keywords = [
        "qa",
        "analista",
        "cybersecurity",
        "soc",
        "python",
        "typescript",
        "react",
        "java",
        "sql",
        "automation",
    ]
    found_skills = [kw for kw in keywords if kw in lower]

    preferred_roles: list[str] = []
    role_map = [
        ("analista", "Analista Funzionale Junior"),
        ("qa", "Junior QA Tester"),
        ("cybersecurity", "Junior Cybersecurity Analyst"),
        ("soc", "Junior SOC Analyst"),
        ("automation", "AI Automation Specialist Junior"),
    ]
    for trigger, role in role_map:
        if trigger in lower and role not in preferred_roles:
            preferred_roles.append(role)

    years = re.findall(r"(20\d{2})", markdown_text)
    graduation_year = ""
    if years:
        graduation_year = years[-1]

    return {
        "skills_rilevate": found_skills,
        "ruoli_preferiti": preferred_roles,
        "anno_riferimento_cv": graduation_year,
    }
