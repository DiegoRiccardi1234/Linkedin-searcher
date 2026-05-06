"""Best-effort recruiter/poster extraction from a LinkedIn job posting page.

Some public LinkedIn job pages embed the hiring manager / poster card in
the rendered HTML. We attempt a quick fetch and parse a small set of known
selectors. On any failure (timeout, 401, 429, missing selectors) we return
``None`` silently — this is a nice-to-have, not a hard requirement.
"""

from __future__ import annotations

from typing import Any

from app.log import get_logger

log = get_logger(__name__)

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore[assignment]


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
}


def fetch_recruiter(job_url: str, timeout: float = 3.0) -> dict[str, Any] | None:
    """Return a dict with recruiter info, or ``None`` if not found / fetch failed.

    Keys: ``name``, ``title``, ``headline``, ``profile_url``, ``raw_text``.
    """
    if not job_url or httpx is None or BeautifulSoup is None:
        return None
    if "linkedin.com" not in job_url:
        return None

    try:
        with httpx.Client(headers=_HEADERS, timeout=timeout, follow_redirects=True) as client:
            resp = client.get(job_url)
            if resp.status_code >= 400:
                return None
            html = resp.text
    except Exception as exc:
        log.debug("recruiter fetch failed for %s: %s", job_url, exc)
        return None

    soup = BeautifulSoup(html, "html.parser")

    selectors = [
        ".jobs-poster",
        ".hirer-card",
        ".job-poster",
        "[data-test-id='job-posters-container']",
        ".jobs-details__main-content .jobs-poster",
    ]
    block = None
    for sel in selectors:
        block = soup.select_one(sel)
        if block:
            break

    if block is None:
        # Fallback: first anchor with /in/ profile link inside the description column.
        link = soup.select_one("a[href*='/in/']")
        if not link:
            return None
        name = link.get_text(strip=True)
        if not name or len(name) > 80:
            return None
        return {
            "name": name,
            "title": None,
            "headline": None,
            "profile_url": link.get("href"),
            "raw_text": None,
        }

    name_el = block.select_one("a, h2, h3, .name")
    title_el = block.select_one(".jobs-poster__title, .hirer-card__subtitle, .subtitle")
    headline_el = block.select_one(".headline, .summary, .description")
    link_el = block.select_one("a[href*='/in/']")

    name = name_el.get_text(strip=True) if name_el else None
    title = title_el.get_text(strip=True) if title_el else None
    headline = headline_el.get_text(strip=True) if headline_el else None
    profile_url = link_el.get("href") if link_el else None
    raw_text = block.get_text(" ", strip=True)[:500]

    if not (name or headline or title):
        return None

    return {
        "name": name,
        "title": title,
        "headline": headline,
        "profile_url": profile_url,
        "raw_text": raw_text,
    }
