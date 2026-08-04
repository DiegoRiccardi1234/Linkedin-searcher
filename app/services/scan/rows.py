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


# "\bibrid[ao]" missed the plurals: a posting offering "soluzioni ibride di
# smart working" fell through to the remote branch and was stored Full Remote.
_HYBRID_RE = re.compile(
    r"\bibrid\w*|\bhybrid\b|lavoro ibrido"
    # In Italy "smart working" means a couple of days from home, not full remote.
    # It used to live in _REMOTE_RE, which is how a hybrid role became remote.
    r"|smart working"
    # "possibilità di lavorare da remoto (fino a 2 giornate su 5 settimanali)":
    # a countable number of remote days is the definition of hybrid.
    r"|\d+\s*(?:giorn[ie]|giornate|days?)[^.\n]{0,40}(?:settiman|su\s*\d|a\s*week|week)",
    re.IGNORECASE,
)
_ONSITE_RE = re.compile(
    r"\bin sede\b|\bon[- ]site\b|\bonsite\b|\bin presenza\b|presenza in sede|\bin office\b",
    re.IGNORECASE,
)
# Only phrasings that claim the WHOLE job is remote. A bare "da remoto" is not
# one of them: "possibilità di lavorare da remoto (fino a 2 giornate su 5)" says
# the opposite, and it was being stored as Full Remote.
_REMOTE_RE = re.compile(
    r"full[- ]remote|100%\s*remot|totalmente\s+da\s+remoto|interamente\s+da\s+remoto"
    r"|completamente\s+da\s+remoto|\bfully remote\b|\bremote[- ]first\b"
    r"|remote\s*:\s*(?:yes|s[iì])|sede di lavoro\s*:\s*(?:da\s+)?remoto",
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
    if _REMOTE_RE.search(text):
        return "Full Remote"
    is_remote = _norm_remote(row.get("is_remote") if hasattr(row, "get") else None)
    # The posting's own words outrank the board's flag in BOTH directions. The
    # flag used to win over an explicit "in sede", which is how on-site roles at
    # a named plant were labelled remote.
    if _ONSITE_RE.search(text):
        return "In sede"
    if is_remote is True:
        return "Full Remote"
    if is_remote is False:
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


#: Work mode as detected -> the UI code the user ticks. Keeps the filter and the
#: stored label in agreement; they used to be two different implementations.
_MODE_TO_CODE = {"Full Remote": "remote", "Ibrido": "hybrid", "In sede": "onsite"}


def _row_work_mode_ok(row: Any, work_types: list[str], descrizione: str = "") -> bool:
    """Whether a posting matches the work modes the user ticked.

    Reads the posting with :func:`_detect_work_mode` instead of jobspy's boolean
    ``is_remote``. The old version short-circuited to ``True`` whenever 'hybrid'
    was among the selections — jobspy cannot express hybrid — so ticking Hybrid
    silently turned the whole filter off and every on-site row survived.

    An undecidable posting is kept: dropping on ignorance hides real jobs.
    """
    modes = {w.lower() for w in work_types}
    if not modes or set(_MODE_TO_CODE.values()) <= modes:  # every mode ticked: nothing to narrow
        return True
    detected = _detect_work_mode(row, descrizione, "")
    code = _MODE_TO_CODE.get(detected)
    if code is None:  # "Non specificato" / unknown
        return True
    return code in modes
