"""Application version and update check against GitHub Releases."""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, cast

from app.log import get_logger

log = get_logger(__name__)

__version__ = "1.2.2"

GITHUB_REPO = "DiegoRiccardi1234/Linkedin-searcher"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
CACHE_TTL_SECONDS = 3600

_cache: dict[str, Any] = {"fetched_at": 0.0, "data": None}


def _parse_version(tag: str) -> tuple[int, ...]:
    """Strip leading 'v' and return tuple of integers for comparison."""
    cleaned = re.sub(r"^v", "", tag.strip(), flags=re.IGNORECASE)
    parts = re.findall(r"\d+", cleaned)
    return tuple(int(p) for p in parts) if parts else (0,)


def _fetch_latest_release() -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(
            RELEASES_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "JobFinder"},
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            return cast(dict[str, Any], json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            log.info("No GitHub releases yet for %s", GITHUB_REPO)
        else:
            log.warning("GitHub releases fetch failed (%s): %s", exc.code, exc.reason)
    except Exception as exc:
        log.warning("GitHub releases fetch error: %s", exc)
    return None


def get_version_info(force_refresh: bool = False) -> dict[str, Any]:
    """Return current vs latest release info with TTL caching."""
    now = time.time()
    if (
        not force_refresh
        and _cache["data"] is not None
        and now - _cache["fetched_at"] < CACHE_TTL_SECONDS
    ):
        return cast(dict[str, Any], _cache["data"])

    frozen = bool(getattr(sys, "frozen", False))
    release = _fetch_latest_release()
    if release is None:
        info = {
            "current": __version__,
            "latest": None,
            "update_available": False,
            "release_url": None,
            "release_notes": None,
            "checked": False,
            "frozen": frozen,
        }
    else:
        latest_tag = release.get("tag_name", "")
        update = _parse_version(latest_tag) > _parse_version(__version__)
        info = {
            "current": __version__,
            "latest": latest_tag or None,
            "update_available": update,
            "release_url": release.get("html_url"),
            "release_notes": (release.get("body") or "")[:2000],
            "checked": True,
            "frozen": frozen,
        }

    _cache["fetched_at"] = now
    _cache["data"] = info
    return info
