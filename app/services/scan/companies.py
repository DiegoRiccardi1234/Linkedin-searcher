"""Matching an employer name against the one a job board printed.

The same company writes itself differently on every posting: "RWS Group",
"RWS Moravia", "RWS Holdings plc", "rws". A watchlist entry has to match all of
them without matching a different employer that merely shares a word, and the
check runs once per scraped row, inside the scan loop.

The rule: strip case, punctuation and the legal suffix, then accept when either
name contains the other as a whole *token sequence*. Containment is what makes
"RWS" find "RWS Moravia"; requiring token boundaries is what stops "bit" (the
Turin company) from matching "Bitpanda" or "Orbit Systems".

Pure string handling — no I/O, no app imports — so both the database layer and
the scan can use it.
"""

from __future__ import annotations

import re
import unicodedata

# Legal forms and generic tails that carry no identity: an entry typed as "bit
# spa" must match a posting from plain "bit", and "Welocalize Inc." from
# "Welocalize". Order matters only in that all of them are stripped repeatedly.
_LEGAL_SUFFIXES = {
    "spa",
    "s.p.a",
    "srl",
    "s.r.l",
    "srls",
    "sas",
    "snc",
    "inc",
    "llc",
    "ltd",
    "limited",
    "plc",
    "gmbh",
    "ag",
    "bv",
    "nv",
    "sa",
    "sarl",
    "ab",
    "oy",
    "as",
    "group",
    "holding",
    "holdings",
    "italia",
    "italy",
}

_PUNCT_RE = re.compile(r"[^a-z0-9]+")

# Dots die before the split, not with it: "S.p.A." would otherwise become three
# one-letter tokens and never match the "spa" suffix above.
_DOT_RE = re.compile(r"\.")


# Employers worth offering as a starting point in the AI-data / language-data
# market this app was built for: the ones that publish steadily under names a
# keyword scan does not produce. Offered as one-click suggestions in the UI and
# never added on the user's behalf — a watchlist nobody chose is just noise.
WATCHLIST_SUGGESTIONS: tuple[str, ...] = (
    "RWS Group",
    "Welocalize",
    "Translated",
    "Toloka",
    "Innodata",
    "OneForma",
    "Alignerr",
    "ShippyPro",
)


def canonical_company(name: str) -> str:
    """Normalised form of a company name, or "" when nothing usable is left.

    Accents folded, punctuation collapsed to single spaces, legal suffixes
    dropped from both ends. "RWS Group S.p.A." and "rws" both become "rws".
    """
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    tokens = [t for t in _PUNCT_RE.split(_DOT_RE.sub("", text)) if t]
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    while tokens and tokens[0] in _LEGAL_SUFFIXES:
        tokens.pop(0)
    return " ".join(tokens)


def company_matches(posting_company: str, watched_canonical: str) -> bool:
    """True when the employer on a posting is the watched company.

    Bidirectional: the user may type more than the board prints ("RWS Group" vs
    "RWS") or less ("RWS" vs "RWS Moravia"). Matching is on whole tokens, so a
    watched "bit" does not swallow "Bitpanda".
    """
    watched = canonical_company(watched_canonical)
    found = canonical_company(posting_company)
    if not watched or not found:
        return False
    if watched == found:
        return True
    watched_tokens = watched.split()
    found_tokens = found.split()
    return _contains_sequence(found_tokens, watched_tokens) or _contains_sequence(
        watched_tokens, found_tokens
    )


def _contains_sequence(haystack: list[str], needle: list[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[i : i + len(needle)] == needle for i in range(len(haystack) - len(needle) + 1)
    )
