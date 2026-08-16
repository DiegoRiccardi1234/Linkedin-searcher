"""Reading the ROLE out of a confirmation email, and nothing else.

The subject of a LinkedIn confirmation names the employer and never the job:
"Diego, la tua candidatura è stata inviata a Reply". So an application rebuilt
from the subject alone has no title, and 41 of them in a real archive is 41 rows
that say who but not what.

The title is in the body. Downloading a body is a thing this app promised not to
do, so it is a choice the user makes (see ``mail_config.BODY_MODE_*``): never,
once per message on request, or for every confirmation. In all three the body is
parsed in memory and never stored, never logged, and never sent to a model.

**How the title is found, and why it is not a search.** The body has a shape:

    La tua candidatura è stata inviata a AGM SOLUTIONS
    AI Developer                 <- the role
    AGM SOLUTIONS                <- the employer again
    Italia
    ------------------------------------
    Ora fai così per avere ancora più successo
    Visualizza offerte di lavoro simili che potrebbero interessarti
    Software Engineer (Junior) - AI Systems     <- NOT your application
    DAIDALOS

Anything that goes looking for "a line that reads like a job title" finds the
recommendations further down and files someone else's job as yours. So the
confirmation line is the anchor, the next line is the candidate, and it is only
accepted when the line AFTER it names the employer we already know — the same
two-independent-facts rule the rest of this package is built on.

Measured on 30 real confirmations from six different senders: 29 titles, no
wrong ones. The one miss simply had a different layout, and a miss costs
nothing — the application is still recorded, just without a title.
"""

from __future__ import annotations

import email
import re
from dataclasses import dataclass
from html import unescape

from app.mail.matcher import _CONFIRM_SUBJECT_RE
from app.services.scan.companies import canonical_company, company_matches

#: Where the confirmation ends and the marketing begins.
_STOP = re.compile(
    r"(?i)offerte di lavoro simili|similar jobs|potrebbero interessarti"
    r"|^-{5,}$|unsubscribe|annulla l|disiscriv"
)

#: A title is a line, not a paragraph and not a word.
#:
#: The ceiling was 90 and it was cutting real titles off: a LinkedIn open
#: application carries the whole list of accepted degrees ("Candidatura
#: Spontanea - Neolaureati in Ing. Informatica/Elettronica/Meccatronica/
#: Automazione - l. 68/99", 105 characters). That row was in a real queue with
#: six candidate offers and no way to tell them apart, which is the exact
#: failure this is all for. Prose runs longer than this, but length was never
#: the defence that mattered — the corroboration on the line below is.
_MIN_ROLE = 3
_MAX_ROLE = 130

#: How far past the anchor to look. The employer sits within a couple of lines
#: of the role; beyond that we are in the body copy.
_WINDOW = 6


def _visible_lines(raw: bytes) -> list[str]:
    """The text a human would see, as trimmed lines. Never stored anywhere."""
    message = email.message_from_bytes(raw)
    chunks: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_maintype() != "text":
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes | bytearray):
            continue
        chunks.append(bytes(payload).decode(part.get_content_charset() or "utf-8", "replace"))
    text = "\n".join(chunks)
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", "\n", text)
    return [" ".join(line.split()) for line in unescape(text).splitlines()]


#: A field with a label on it: "Job Title : AI Specialist", "Reference Role:
#: R00279839 Junior SAP Analyst". Read off two real senders whose confirmation
#: anchor is followed by an invitation to create an account, not by the job.
_LABELLED_ROLE_RE = re.compile(
    r"(?i)^(?:job\s*title|position|posizione|ruolo|reference\s*role|offerta)\s*[:\-]\s*(.+)$"
)

#: The role inside the sentence rather than on a line of its own: "Grazie per
#: aver inviato la tua candidatura per la posizione di Junior SAP Analyst -
#: Internship. Inizieremo…". The full stop ends it; without that bound the rest
#: of the paragraph comes along.
_INLINE_ROLE_RE = re.compile(
    r"(?i)candidatura\s+per\s+(?:la\s+posizione|il\s+ruolo)\s*(?:di\s+)?(.+?)\s*(?:[.!]|$)"
    r"|application\s+for\s+the\s+(?:position|role)\s+(?:of\s+)?(.+?)\s*(?:[.!]|$)"
    # Randstad, four times in one real queue: "grazie per esserti candidato
    # all'offerta X CX570302." The apostrophe is typographic in the wild.
    r"|candidat[oa]\s+all['’\s]*offerta\s+(.+?)\s*(?:[.!]|$)"  # noqa: RUF001
    # Bending Spoons: "apply for the X job at Y." The employer follows the job,
    # so the capture has to stop at "job at" and not run to the full stop.
    r"|appl(?:y|ied)\s+for\s+the\s+(.+?)\s+(?:job|position|role)\s+at\s+"
)

#: A reference code glued to a role. Workday puts it in front ("R00279839 Junior
#: SAP Analyst"), Randstad on the end ("Help desk CX558653"). Both are the
#: employer's own filing number and neither belongs in a job title.
_REF_CODE_RE = re.compile(r"^[A-Z]{1,3}[\d\-_/]{4,}\s+")
_REF_CODE_TAIL_RE = re.compile(r"\s+[A-Z]{1,3}[\d\-_/]{4,}$")


@dataclass(frozen=True)
class BodyFacts:
    """What a confirmation body says about the job and the employer."""

    role: str = ""
    company: str = ""
    rule: str = ""


def _looks_like_a_company(line: str) -> bool:
    """True when a line could be an employer's name and not prose.

    Deliberately mean. The cost of a false yes is an application filed under an
    invented company; the cost of a false no is a row the user completes by
    hand, which is what happens today for every one of them.
    """
    if not (2 <= len(line) <= 80) or _STOP.search(line):
        return False
    if line.startswith("-") or "@" in line or "http" in line.lower():
        return False
    if line.endswith(("!", "?", ":", ",")) or len(line.split()) > 8:
        return False
    return bool(canonical_company(line))


def _clean_role(role: str) -> str:
    role = _REF_CODE_TAIL_RE.sub("", _REF_CODE_RE.sub("", role.strip()))
    return role.strip(" .,;:-–—")  # noqa: RUF001


def facts_from_body(raw: bytes, *, company: str = "", role: str = "") -> BodyFacts:
    """The job and the employer this confirmation is about, as far as they can be read.

    Three shapes, tried in order, because three real senders write it three
    ways and no single rule reads all of them:

    1. **positional** — anchor line, role, employer, one under the other. This
       is LinkedIn's shape and, exactly, Indeed's. When the employer is already
       known it is used to corroborate the title (the measured 29-of-30 rule in
       :func:`role_from_body`); when it is not, the line below the title IS the
       answer, which is the only way an Indeed confirmation can name its
       employer at all.
    2. **labelled** — ``Job Title : AI Specialist``. The anchor is present but
       the line after it is an invitation to create an account.
    3. **inline** — the role inside the confirmation sentence itself.

    Anything already known is passed in and never overwritten: the subject is a
    stronger source than the body, and this function exists to fill the gaps it
    leaves.
    """
    if not raw:
        return BodyFacts(role=role, company=company)
    lines = [x for x in _visible_lines(raw) if x]
    found_role, found_company, rule = role, company, ""

    # 1. positional
    if not found_role or not found_company:
        for index, line in enumerate(lines):
            if not _CONFIRM_SUBJECT_RE.search(line):
                continue
            window = [x for x in lines[index + 1 : index + 1 + _WINDOW] if x]
            if len(window) < 2:
                continue
            title, following = window[0], window[1]
            if _STOP.search(title) or _STOP.search(following):
                continue
            if not (_MIN_ROLE <= len(title) <= _MAX_ROLE):
                continue
            if company:
                # The employer is known: the line below the title has to name it,
                # which is the corroboration the whole package is built on.
                if company_matches(following, company) or company_matches(company, following):
                    found_role, rule = _clean_role(title), "body_positional"
                    break
            elif _looks_like_a_company(following):
                found_role, found_company = _clean_role(title), following
                rule = "body_positional"
                break

    # 2. labelled field
    if not found_role:
        for line in lines:
            match = _LABELLED_ROLE_RE.match(line)
            if not match:
                continue
            candidate = _clean_role(match.group(1))
            if _MIN_ROLE <= len(candidate) <= _MAX_ROLE and not _STOP.search(candidate):
                found_role, rule = candidate, "body_labelled"
                break

    # 3. inside the sentence
    if not found_role:
        for line in lines:
            match = _INLINE_ROLE_RE.search(line)
            if not match:
                continue
            candidate = _clean_role(next((g for g in match.groups() if g), ""))
            if _MIN_ROLE <= len(candidate) <= _MAX_ROLE and not _STOP.search(candidate):
                found_role, rule = candidate, "body_inline"
                break

    return BodyFacts(role=found_role, company=found_company, rule=rule)


def role_from_body(raw: bytes, company: str) -> str:
    """The job title this confirmation is about, or "" when it cannot be read.

    Returning "" is a normal outcome and not a failure: the application is
    recorded either way, and an empty title the user can fill in beats a
    plausible one taken from the recommendations below the fold.
    """
    if not raw or not company:
        return ""
    lines = _visible_lines(raw)
    for index, line in enumerate(lines):
        if not line or not _CONFIRM_SUBJECT_RE.search(line):
            continue
        window = [x for x in lines[index + 1 : index + 1 + _WINDOW] if x]
        if len(window) < 2:
            continue
        title, following = window[0], window[1]
        if _STOP.search(title) or not (_MIN_ROLE <= len(title) <= _MAX_ROLE):
            continue
        # The corroboration. Without it this is "the line after a phrase", which
        # is exactly the kind of rule that quietly files the wrong job.
        if company_matches(following, company) or company_matches(company, following):
            return title
    return ""
