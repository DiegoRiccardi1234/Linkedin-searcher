"""Is this message a confirmation that I applied, and to which posting?

Pure functions: no network, no database, no model. Every decision is a regex
table or a company-name comparison, and the rule that fired is carried in the
result so a wrong answer can be explained from the database alone, without ever
storing the message.

**Why no model.** It would need the subject and probably the body, which means
sending someone's mail to a third party — a line this app crosses only for a
redacted CV, and only for the CV. The redactor in ``app.services.pii`` is tuned
for CV prose and would not make an email safe. And the L. 68/99 case already
taught this project what plausible-guessing on weak evidence does at scale: 29
postings in a real archive cite the law and exactly ONE is reserved. A detector
that fires on all of them is the problem, not the data. Here the cost of being
wrong is higher still, because this WRITES to the record of what you applied
for. If recall turns out to be poor, the way out is more patterns the user can
see and correct, not a model.

**The shape of the rule.** Two independent facts must agree: the subject has to
read like a confirmation AND the sender has to be either a known applicant
tracking system or the company the user actually opened. Subject alone catches
every job alert; sender alone catches every LinkedIn notification, including
"three people viewed your profile".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from app.services.scan.companies import canonical_company

Verdict = Literal["match", "ambiguous", "no_match"]

#: Clocks drift, and IMAP reports server time. Five minutes costs nothing in
#: precision and stops a confirmation that raced the click from being dropped.
CLOCK_SLACK = timedelta(minutes=5)


@dataclass(frozen=True)
class MailHeader:
    """The only parts of a message this app ever reads."""

    key: str
    subject: str = ""
    from_addr: str = ""
    from_name: str = ""
    message_id: str = ""
    date: datetime | None = None
    list_id: str = ""


@dataclass(frozen=True)
class PendingJob:
    """An offer whose posting was opened and which is still unanswered."""

    job_id: int
    company: str
    title: str
    opened_at: datetime


@dataclass(frozen=True)
class MatchResult:
    verdict: Verdict
    job_id: int | None = None
    rule: str = ""
    candidates: tuple[int, ...] = field(default_factory=tuple)


# ── gate 0: messages that are definitely not a confirmation ─────────────────
# A rejection is not a confirmation of sending. Reading it as one would move the
# offer to "applied", which is the wrong state and hides that it is over.
_REJECT_SUBJECT_RE = re.compile(
    r"job alert|nuove offerte|offerte di lavoro per te|consigliat[oi] per te"
    r"|recommended for you|newsletter|potrebbero interessarti"
    r"|non (?:sei|è|e) stat[oa] selezionat|non abbiamo dato seguito|purtroppo"
    r"|unfortunately|we (?:have )?decided|not (?:to )?(?:move|proceed)"
    r"|colloqui[oy]|invito a|interview invitation|convocazione"
    # "la tua candidatura è stata VISUALIZZATA da X" is LinkedIn saying someone
    # opened it, not that it was sent. It slipped through because the confirm
    # pattern only asked for "la tua candidatura è stata", and 21 of them landed
    # in one real 90-day mailbox — none of them assignable, because the "who"
    # regex wants "inviata a". Left in, they are pure noise in the review queue.
    r"|candidatur\w*[^.\n]{0,24}visualizzat"
    r"|application (?:was |has been )?viewed",
    re.IGNORECASE,
)

# ── gate 1a: does the subject read like a confirmation? ─────────────────────
_CONFIRM_SUBJECT_RE = re.compile(
    # "candidatura è stata inviata", "candidatura e' stata inviata", "candidatura
    # inviata": the words in between vary, and so does the accent — mail headers
    # arrive transliterated often enough that requiring "è" would drop real
    # confirmations.
    r"candidatura[^.\n]{0,24}(?:inviat|ricevut|registrat|confermat|sottomess)"
    r"|abbiamo ricevuto la (?:tua|sua) candidatura"
    r"|grazie per (?:esserti candidat|la tua candidatura|aver inviato|la candidatura)"
    r"|conferma (?:di )?candidatura"
    r"|la tua candidatura (?:per|a|presso|è stata)"
    r"|(?:your )?application (?:was|has been)? ?(?:received|submitted|sent)"
    r"|thank you for (?:your application|applying)"
    r"|we(?:'| ha)ve received your application"
    r"|application confirmation|applicazione ricevuta",
    re.IGNORECASE,
)

# ── gate 1b: senders that only ever send about applications ─────────────────
_KNOWN_SENDER_DOMAINS = frozenset(
    {
        "linkedin.com",
        "e.linkedin.com",
        "bounce.linkedin.com",
        "myworkday.com",
        "myworkdayjobs.com",
        "successfactors.com",
        "successfactors.eu",
        "greenhouse.io",
        "us.greenhouse-mail.io",
        "lever.co",
        "hire.lever.co",
        "smartrecruiters.com",
        "icims.com",
        "taleo.net",
        "ashbyhq.com",
        "workable.com",
        "recruitee.com",
        "teamtailor.com",
        "jobvite.com",
        "bamboohr.com",
        "personio.de",
        "breezy.hr",
        "eightfold.ai",
        "phenompeople.com",
        # Read off a real mailbox rather than off a vendor list: these are the
        # domains the messages actually arrive from. "teamtailor.com" above is a
        # list bug, not an omission — Teamtailor sends from
        # <tenant>.teamtailor-mail.com, which the registrable-domain rule never
        # folds back to teamtailor.com.
        "teamtailor-mail.com",
        "join.com",
        "ceipalmail.com",
        "oraclecloud.com",
        "allibo.com",
    }
)

_ADDRESS_RE = re.compile(r"[\w\.\-\+]+@([\w\.\-]+)")

# LinkedIn writes the employer into the subject, which is the strongest signal
# available: "La tua candidatura è stata inviata a Reply".
_SUBJECT_COMPANY_RE = re.compile(
    r"(?:inviata|inoltrata|sottomessa)\s+a\s+(.+?)\s*$"
    r"|(?:was|has been)\s+(?:sent|submitted)\s+to\s+(.+?)\s*$"
    r"|candidatura\s+(?:per|presso)\s+(.+?)\s*$",
    re.IGNORECASE,
)

#: Mail hosts that say nothing about the employer: matching a pending company
#: against "gmail" would pair a personal reply with whatever was opened.
_GENERIC_MAIL_DOMAINS = frozenset(
    {
        "gmail",
        "googlemail",
        "outlook",
        "hotmail",
        "live",
        "msn",
        "yahoo",
        "icloud",
        "me",
        "libero",
        "virgilio",
        "aruba",
        "tiscali",
        "pec",
        "email",
        "mail",
        "noreply",
        "no-reply",
    }
)


def sender_domain(from_addr: str) -> str:
    match = _ADDRESS_RE.search(str(from_addr or ""))
    return match.group(1).lower().strip(".") if match else ""


def _registrable(domain: str) -> str:
    """The last two labels: ``mail.eu.lever.co`` -> ``lever.co``."""
    parts = [p for p in domain.split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


def is_known_sender(from_addr: str, list_id: str = "") -> bool:
    """True when the sender is LinkedIn or a recognised hiring platform.

    Matched on whole domain labels, never as a substring: ``lever.co`` must not
    be found inside ``notlever.co.example.com``, which is a domain anyone can
    register.
    """
    for raw in (sender_domain(from_addr), sender_domain(list_id) or list_id.strip("<> ")):
        domain = str(raw or "").lower().strip(".")
        if not domain:
            continue
        if domain in _KNOWN_SENDER_DOMAINS or _registrable(domain) in _KNOWN_SENDER_DOMAINS:
            return True
        if any(domain.endswith("." + known) for known in _KNOWN_SENDER_DOMAINS):
            return True
    return False


def _company_tokens(domain: str) -> str:
    """The part of a sender domain that could name a company."""
    parts = [p for p in domain.split(".") if p]
    if not parts:
        return ""
    # Drop the TLD, plus a country second level ("co.uk", "com.br").
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net"}:
        parts = parts[:-2]
    elif len(parts) >= 2:
        parts = parts[:-1]
    name = parts[-1] if parts else ""
    return "" if name in _GENERIC_MAIL_DOMAINS else name


def sender_matches_company(from_addr: str, company: str) -> bool:
    """True when the sending domain names the employer.

    Reuses ``canonical_company`` so "RWS Group S.p.A." and a mail from
    ``careers@rws.com`` line up, and so the token-boundary rule that stops "bit"
    from matching "Bitpanda" is not reimplemented here with different bugs.
    """
    token = _company_tokens(sender_domain(from_addr))
    if not token:
        return False
    canonical = canonical_company(company)
    if not canonical:
        return False
    return token in canonical.split() or canonical.replace(" ", "") == token


def _subject_company(subject: str) -> str:
    match = _SUBJECT_COMPANY_RE.search(str(subject or "").strip())
    if not match:
        return ""
    return next((g for g in match.groups() if g), "").strip(" .!-–—")  # noqa: RUF001


def _in_window(header_date: datetime | None, job: PendingJob, ttl: timedelta) -> bool:
    if header_date is None:
        return False
    return job.opened_at - CLOCK_SLACK <= header_date <= job.opened_at + ttl


def looks_like_confirmation(header: MailHeader, pending: list[PendingJob]) -> bool:
    """Gate 0 and gate 1: a confirmation subject AND a sender we can place."""
    subject = str(header.subject or "")
    if _REJECT_SUBJECT_RE.search(subject):
        return False
    if not _CONFIRM_SUBJECT_RE.search(subject):
        return False
    if is_known_sender(header.from_addr, header.list_id):
        return True
    # An employer answering from its own address is a second independent fact,
    # even though nobody has ever heard of the domain.
    return any(sender_matches_company(header.from_addr, job.company) for job in pending)


def classify(header: MailHeader, pending: list[PendingJob], ttl_days: int = 14) -> MatchResult:
    """Which pending offer this message confirms, if any.

    ``ambiguous`` is a real answer, not a failure: two applications to the same
    company in the same week produce a message that genuinely cannot be assigned,
    and picking one would silently write the wrong history.
    """
    if not pending or not looks_like_confirmation(header, pending):
        return MatchResult("no_match")

    ttl = timedelta(days=max(1, int(ttl_days)))
    in_window = [job for job in pending if _in_window(header.date, job, ttl)]
    if not in_window:
        return MatchResult("no_match", rule="outside_window")

    # 1. The employer named in the subject. Strongest: it is the message itself
    #    saying who it is about.
    named = _subject_company(header.subject)
    if named:
        hits = [job for job in in_window if _same_company(job.company, named)]
        if len(hits) == 1:
            return MatchResult("match", hits[0].job_id, "subject_company")
        if len(hits) > 1:
            return MatchResult("ambiguous", None, "subject_company", tuple(j.job_id for j in hits))
        # The message says who it is about, and that employer is not in the
        # archive. Reading on would look for the offer somewhere else and find
        # one that the message just told us it is NOT about. Measured on a real
        # mailbox: 39 of these, every one of them applying for a company the
        # archive had never heard of.
        return MatchResult("no_match", rule="named_company_absent")

    # 2. The sending domain names the employer.
    hits = [job for job in in_window if sender_matches_company(header.from_addr, job.company)]
    if len(hits) == 1:
        return MatchResult("match", hits[0].job_id, "sender_domain")
    if len(hits) > 1:
        return MatchResult("ambiguous", None, "sender_domain", tuple(j.job_id for j in hits))

    # 3. The employer's name appears in the subject. The weakest of the three, so
    #    it is only allowed to decide when there is nothing it could be confused
    #    with: with two offers open, a one-word company would pick arbitrarily.
    if len(in_window) == 1:
        job = in_window[0]
        canonical = canonical_company(job.company)
        if canonical and canonical in canonical_company(header.subject):
            return MatchResult("match", job.job_id, "subject_contains_company")
        return MatchResult("ambiguous", None, "single_pending", (job.job_id,))

    # Nothing names an employer: not the subject, not the sending domain, and
    # there is more than one offer it could be about. "Ambiguous" here used to
    # hand back every pending id, and the review screen shows the first of them
    # — so a message about Hays was presented as a question about whichever
    # offer happened to sort first. On a real mailbox that was 66 of 116
    # proposals, all of them unanswerable. An honest no is worth more than a
    # question nobody can answer.
    return MatchResult("no_match", rule="no_evidence")


def _same_company(stored: str, named: str) -> bool:
    from app.services.scan.companies import company_matches

    return company_matches(stored, named)
