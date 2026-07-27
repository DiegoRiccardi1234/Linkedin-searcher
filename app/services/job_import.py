"""Import a single job posting from a URL (or pasted text) and turn it into the
fields the scorer needs.

``fetch_page_text`` best-effort downloads and flattens a posting page (many
sites — LinkedIn especially — block bots, so this often returns too little);
the caller then falls back to text the user pasted. ``extract_job_fields`` asks
the LLM to pull title/company/description out of whatever raw text we have. The
resulting fields are scored by the existing ``analyze_offer``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.log import get_logger

if TYPE_CHECKING:
    from app.providers.factory import ProviderManager

log = get_logger(__name__)

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore[assignment,misc]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
}


def fetch_page_text(url: str, timeout: float = 6.0) -> str | None:
    """Return the visible text of ``url`` (script/style stripped), or ``None``.

    Returns ``None`` on missing deps, non-URL input, HTTP error, or any
    exception — the caller treats that as "couldn't fetch" and falls back to
    pasted text.
    """
    if not url or httpx is None or BeautifulSoup is None:
        return None
    if not url.lower().startswith(("http://", "https://")):
        return None
    try:
        with httpx.Client(headers=_HEADERS, timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                return None
            html = resp.text
    except Exception as exc:
        log.debug("job page fetch failed for %s: %s", url, exc)
        return None

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    return text or None


def extract_job_fields(provider_manager: ProviderManager, raw_text: str) -> dict[str, str]:
    """LLM-extract ``{titolo, azienda, sede, descrizione}`` from raw posting text.

    ``sede`` matters beyond display: the deterministic eligibility check reads
    the location, so an imported posting without one could never be geo-capped
    (a US-based job imported by URL scored like any other).

    Returns empty strings for any field the model can't find. Raises whatever
    ``complete_json`` raises (no provider / network) so the caller surfaces it.
    """
    snippet = (raw_text or "")[:6000]
    prompt = (
        "You are given the raw text of a job posting (possibly noisy web content). "
        "Extract the core fields and reply ONLY with valid JSON, no extra text, with keys:\n"
        '- "titolo": the job title\n'
        '- "azienda": the hiring company name\n'
        '- "sede": the work location as written (city and country, or "Remote")\n'
        '- "descrizione": a concise 3-6 sentence summary of the role, key '
        "responsibilities and requirements\n"
        "Use an empty string for any field you cannot determine.\n\n"
        f"TEXT:\n{snippet}"
    )
    result = provider_manager.complete_json(prompt=prompt, max_tokens=700)
    if not isinstance(result, dict):
        return {"titolo": "", "azienda": "", "sede": "", "descrizione": ""}
    return {
        "titolo": str(result.get("titolo") or "").strip(),
        "azienda": str(result.get("azienda") or "").strip(),
        "sede": str(result.get("sede") or "").strip(),
        "descrizione": str(result.get("descrizione") or "").strip(),
    }
