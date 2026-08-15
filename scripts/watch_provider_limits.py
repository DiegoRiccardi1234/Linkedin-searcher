#!/usr/bin/env python
"""Notice when a provider changes what it gives away.

Run weekly by ``.github/workflows/provider-watch.yml``. Deliberately modest
about what it can know:

* the OpenRouter model catalogue IS public JSON, so which models are free today
  is a fact this script can read and diff;
* the free-tier RATE LIMITS are not. Google moved theirs behind a logged-in
  console, and they are per project anyway — the same model is 500 requests a
  day for one user and 50 for another. Anything claiming otherwise would be
  guessing, and the app already learns its real ceiling by watching its own
  calls (app/services/rate_limits.py).

So this does two honest things: it diffs the public catalogue, and it keeps a
fingerprint of each provider's pricing/limits page so a change can be reported
as "go and look" rather than invented. Exit code 1 means something moved.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

SNAPSHOT = Path(__file__).resolve().parents[1] / "app" / "data" / "provider_watch.json"
UA = "Mozilla/5.0 (compatible; JobFinderProviderWatch/1.0)"

#: Pages whose wording decides what we ship as defaults. Fingerprinted, never
#: parsed: a heading move must not become a fake limit.
WATCHED_PAGES = {
    "google_pricing": "https://ai.google.dev/gemini-api/docs/pricing",
    "google_rate_limits": "https://ai.google.dev/gemini-api/docs/rate-limits",
    "openrouter_limits": "https://openrouter.ai/docs/api-reference/limits",
    "groq_limits": "https://console.groq.com/docs/rate-limits",
    "cerebras_pricing": "https://www.cerebras.ai/pricing",
}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "replace")


def _fingerprint(html: str) -> str:
    """A hash of the readable text, so a changed build id is not a change."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def free_openrouter_models() -> list[str]:
    data = json.loads(_get("https://openrouter.ai/api/v1/models"))
    return sorted(
        m["id"] for m in data.get("data", []) if str(m.get("id", "")).endswith(":free")
    )


def main() -> int:
    previous: dict[str, Any] = {}
    if SNAPSHOT.exists():
        previous = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    current: dict[str, Any] = {"openrouter_free_models": [], "pages": {}}
    problems: list[str] = []

    try:
        current["openrouter_free_models"] = free_openrouter_models()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not read the OpenRouter catalogue: {exc}")
        current["openrouter_free_models"] = previous.get("openrouter_free_models", [])

    for name, url in WATCHED_PAGES.items():
        try:
            current["pages"][name] = _fingerprint(_get(url))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"could not read {name} ({url}): {exc}")
            current["pages"][name] = previous.get("pages", {}).get(name, "")

    changes: list[str] = []
    before = set(previous.get("openrouter_free_models") or [])
    after = set(current["openrouter_free_models"])
    if before and after != before:
        gone = sorted(before - after)
        new = sorted(after - before)
        if new:
            changes.append(f"OpenRouter: {len(new)} new free model(s): {', '.join(new)}")
        if gone:
            changes.append(f"OpenRouter: {len(gone)} free model(s) withdrawn: {', '.join(gone)}")

    for name, digest in current["pages"].items():
        old = (previous.get("pages") or {}).get(name)
        if old and digest and old != digest:
            changes.append(f"{name}: the page changed — {WATCHED_PAGES[name]}")

    SNAPSHOT.write_text(
        json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    for line in problems:
        print(f"WARN {line}")
    if not changes:
        print("Nothing moved.")
        return 0
    print("CHANGES")
    for line in changes:
        print(f"- {line}")
    print()
    print(
        "These are pointers, not new limits: check the page, then edit "
        "app/data/provider_limits.json by hand if a number really changed."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
