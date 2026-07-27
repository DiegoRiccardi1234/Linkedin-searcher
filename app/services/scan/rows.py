"""Reading a scraped row: cleaning, work mode, and the post-scrape filters.

jobspy hands back a pandas row whose cells can be NaN, None or free text in
either language. These helpers turn that into something the rest of the scan
can trust — including the work mode, which must come from the POSTING and not
from the search flag that was ticked.
"""

from __future__ import annotations

import math
import re
from typing import Any

from app.services.scan.scraping import _JOBSPY_JOB_TYPE


def _below_min_salary(max_amount: Any, min_salary: int) -> bool:
    """True only when a job's (known) top salary is below ``min_salary``.

    Jobs with no/unparseable salary are kept (return False) — most listings omit
    pay, so filtering them out would hide almost everything.
    """
    if not min_salary:
        return False
    try:
        amount = float(max_amount)
    except (TypeError, ValueError):
        return False
    return amount < min_salary


def _is_nan(val: Any) -> bool:
    return isinstance(val, float) and math.isnan(val)


def _clean_text(val: Any) -> str:
    """Coerce a jobspy cell to a clean string. ``str()`` of a pandas ``NaN`` or
    ``None`` yields the literal ``"nan"``/``"None"`` which then poisons the LLM
    prompt (and made LinkedIn jobs look like they had a description). Those and
    blank strings collapse to ``""``."""
    if val is None or _is_nan(val):
        return ""
    s = str(val).strip()
    return "" if s.lower() in ("nan", "none") else s


# Bilingual markers for the "requirements" section of a job posting. Used to keep
# that section in the scoring prompt even when it sits past the char budget.


def _norm_remote(val: Any) -> bool | None:
    """Normalize jobspy's ``is_remote`` (True/False/NaN/missing) to bool|None."""
    if val is None or _is_nan(val):
        return None
    return bool(val)


_HYBRID_RE = re.compile(r"\bibrid[ao]|\bhybrid\b|lavoro ibrido", re.IGNORECASE)
_ONSITE_RE = re.compile(
    r"\bin sede\b|\bon[- ]site\b|\bonsite\b|\bin presenza\b|presenza in sede|\bin office\b",
    re.IGNORECASE,
)
_REMOTE_RE = re.compile(
    r"full remote|100% remot|\bda remoto\b|\bfully remote\b|\bremote[- ]first\b|smart working",
    re.IGNORECASE,
)


def _detect_work_mode(row: Any, descrizione: str, scan_default: str) -> str:
    """Work mode of a single posting, read from the posting itself.

    Used to be a scan-level constant mirroring the ``is_remote`` search flag, so
    every job of a remote-flagged scan was stored as "Full Remote" — including
    plainly on-site ones (measured: 44/44 jobs of one scan, an Orbassano plant
    role among them). jobspy's per-row ``is_remote`` comes first, then the text,
    and only an undecidable row falls back to the scan flag.
    """
    text = descrizione or ""
    if _HYBRID_RE.search(text):
        return "Ibrido"
    is_remote = _norm_remote(row.get("is_remote") if hasattr(row, "get") else None)
    if is_remote is True:
        return "Full Remote"
    if _REMOTE_RE.search(text):
        return "Full Remote"
    if is_remote is False or _ONSITE_RE.search(text):
        return "In sede"
    # No evidence either way. The scan flag is a SEARCH filter, not a fact about
    # the posting — asserting "Full Remote" from it is how an on-site plant role
    # ended up labelled remote — so say so instead of guessing.
    return "Non specificato" if scan_default == "Full Remote" else scan_default


def _row_job_type_ok(row: Any, job_types: list[str]) -> bool:
    """Keep a scraped row when its job_type matches a selected one.

    jobspy takes a single ``job_type``, so multi-select is enforced here (the
    kwarg only narrows for a single pick). Rows whose type jobspy didn't report
    are kept — never over-drop on missing data.
    """
    selected = {j.lower() for j in job_types if j.lower() in _JOBSPY_JOB_TYPE}
    if not selected:
        return True
    raw = row.get("job_type")
    if raw is None or _is_nan(raw):
        return True
    types = {t.strip().lower() for t in re.split(r"[,\s]+", str(raw)) if t.strip()}
    return not types or bool(types & selected)


def _row_work_mode_ok(row: Any, work_types: list[str]) -> bool:
    """Best-effort work-mode filter from jobspy's ``is_remote``.

    'hybrid' isn't distinguishable in jobspy output, so any selection including
    it (or both remote+onsite) keeps everything. Unknown is_remote is kept.
    """
    modes = {w.lower() for w in work_types}
    if not modes or "hybrid" in modes or {"remote", "onsite"} <= modes:
        return True
    is_remote = _norm_remote(row.get("is_remote"))
    if is_remote is None:
        return True
    if "remote" in modes:
        return is_remote
    if "onsite" in modes:
        return not is_remote
    return True
