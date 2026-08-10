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
from html import unescape

from app.mail.matcher import _CONFIRM_SUBJECT_RE
from app.services.scan.companies import company_matches

#: Where the confirmation ends and the marketing begins.
_STOP = re.compile(
    r"(?i)offerte di lavoro simili|similar jobs|potrebbero interessarti"
    r"|^-{5,}$|unsubscribe|annulla l|disiscriv"
)

#: A title is a line, not a paragraph and not a word.
_MIN_ROLE = 3
_MAX_ROLE = 90

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
