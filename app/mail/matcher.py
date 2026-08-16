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
from collections.abc import Sequence
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
    r"|application (?:was |has been )?viewed"
    # The same sentence with the words the other way round. Read off the real
    # mailbox, where the pattern above only ever matched one of the two orders.
    r"|visualizzat\w*[^.\n]{0,24}candidatur"
    # A refusal that opens with the same four words as a confirmation:
    # "Grazie per la tua candidatura MA al momento non coincide con le nostre
    # posizioni aperte". Four of them from one ATS in a real year. Read as a
    # confirmation it leaves the offer waiting for an answer that already came.
    r"|non\s+coincide\s+con|non\s+corrisponde\s+a[il]|no\s+longer\s+under\s+consideration"
    # "Complete your application for job: X" is a nudge to go back and FINISH an
    # application, and Oracle sends it days before "Thank you for completing
    # your application: X" about the same job. Anchored to the start of the
    # subject because it is the imperative that distinguishes them: "Candidatura
    # per la posizione di X completata!" is a confirmation and must survive.
    r"|^\s*complet[ae]\b",
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
        # Read off a real mailbox over a year: these send confirmations, and the
        # list had none of them. Indeed alone was 151 messages and 0 recognised.
        "indeed.com",
        "ncoreplat.com",
        "arca24.com",
        "tagcandidate.com",
        "cving.com",
        "mygigroup.com",
        # Found by reading the extraction by hand: without these, their own
        # names were being handed back as the employer. "icims.eu" is the
        # sibling of "icims.com" — the same list bug as Teamtailor.
        "icims.eu",
        "jobgether.com",
        "adeccoapps.com",
    }
)

#: Subdomains of a known sender that only ever carry marketing. Checked BEFORE
#: the suffix rule below, because that rule is what makes them a problem: adding
#: "indeed.com" as a known sender also admits ``match.indeed.com``, which is
#: where 94 of 151 real Indeed messages come from — every one a job alert, and
#: not one stopped by the reject gate ("Data Analyst presso Foo srl" reads like
#: nothing in particular). A sender list that cannot say "except this one" is a
#: list that has to choose between missing the confirmations and admitting the
#: adverts.
_ALERT_SENDER_DOMAINS = frozenset({"match.indeed.com"})

_ADDRESS_RE = re.compile(r"[\w\.\-\+]+@([\w\.\-]+)")

# LinkedIn writes the employer into the subject, which is the strongest signal
# available: "La tua candidatura è stata inviata a Reply".
_SUBJECT_COMPANY_RE = re.compile(
    r"(?:inviata|inoltrata|sottomessa)\s+a\s+(.+?)\s*$"
    r"|(?:was|has been)\s+(?:sent|submitted)\s+to\s+(.+?)\s*$"
    # Workday's Italian template, seven real messages from one employer:
    # "Grazie per il tuo interesse nei confronti di Accenture!"
    r"|interesse\s+nei\s+confronti\s+(?:di|dell[ae']?)\s*(.+?)\s*$"
    # An ATS putting its client after a dash: "Conferma ricezione candidatura -
    # Oggi Lavoro S.p.a."
    r"|conferma\s+ricezione\s+candidatura\s*[-–—]\s*(.+?)\s*$"  # noqa: RUF001
    # The agencies, measured: "Grazie per la tua candidatura con Gi Group"
    # (fourteen messages), "…con Wyser", "La tua candidatura presso Loacker",
    # "Grazie per la candidatura in Business Changers".
    r"|candidatura\s+(?:con|presso|in)\s+(.+?)\s*$"
    r"|(?:your\s+)?application\s+to\s+(.+?)\s*$"
    r"|appl(?:y|ying)\s+to\s+(.+?)[!.]?\s*$"
    # "Thank you for applying for the role of X at Y" — the employer follows
    # the job, so this has to run before the role pattern eats the line.
    r"|for\s+the\s+role\s+of\s+.+?\s+at\s+(.+?)[!.]?\s*$"
    # "Capgemini Group - New Job Application Received"
    r"|^(.+?)\s*[-–—]\s*new\s+job\s+application\s+received\s*$"  # noqa: RUF001
    # An ATS gluing its client in front: "Spindox_Candidatura ricevuta"
    r"|^([\w .&'-]+?)_candidatura\s+ricevuta\s*$",
    re.IGNORECASE,
)

#: The one shape that cannot tell an employer from a job: "candidatura per
#: Reply" names a company, "Candidatura per AI SPECIALIST JUNIOR attraverso
#: Indeed" names a role. Kept apart from the patterns above and consulted only
#: when nothing has recognised a role — otherwise it answers "who is this
#: about?" with the job title, which is exactly the bug this release exists to
#: fix and it would have re-introduced it through the back door.
_SUBJECT_COMPANY_LOOSE_RE = re.compile(r"candidatura\s+per\s+(.+?)\s*$", re.IGNORECASE)

#: The other half of the same question: subjects that name the JOB. Every
#: alternative is anchored to a phrase that only appears in a confirmation, so
#: none of them can fire on an advert. Measured: these five forms account for
#: every role-bearing confirmation in a year of one real mailbox.
_SUBJECT_ROLE_RE = re.compile(
    # Indeed: "Candidatura per AI SPECIALIST JUNIOR attraverso Indeed"
    r"candidatura\s+per\s+(.+?)\s+(?:attraverso|tramite)\s+indeed\s*$"
    r"|application\s+for\s+(.+?)\s+(?:via|through)\s+indeed\s*$"
    # Experis: "Candidatura eseguita con successo per AI Specialist"
    r"|candidatura\s+(?:eseguita|inviata|inoltrata)\s+con\s+successo\s+per\s+(.+?)\s*$"
    # Adecco/ncoreplat, tagcandidate: "Candidatura per la posizione di X completata!"
    r"|candidatura\s+per\s+(?:la\s+posizione|il\s+ruolo)\s*(?:di\s+)?(.+?)\s*completata[!.]?\s*$"
    # Oracle: "Thank you for completing your application: AI Engineer (65)"
    r"|thank\s+you\s+for\s+completing\s+your\s+application:\s*(.+?)\s*$"
    # The Italian agencies: "Conferma candidatura BACK END SOFTWARE DEVELOPER -
    # Orienta", "Conferma candidatura Help desk CX558653". The trailing agency
    # name or reference code is stripped by ``_strip_role_tail``; a bare
    # "Conferma candidatura" has nothing after it and yields nothing.
    r"|conferma\s+(?:di\s+)?candidatura\s+(?!ricevut|inviat|all|alla|per\b|del\b)(.+?)\s*$"
    # Workday for a client: "…applying for the role of Delivery Specialist at X"
    r"|for\s+the\s+role\s+of\s+(.+?)\s+at\s+.+?[!.]?\s*$"
    # Jobgether: "Application received: Fraud Analyst - View your match score"
    r"|application\s+received:\s*(.+?)\s*$",
    re.IGNORECASE,
)

#: Three letters in a row: the difference between a job title and a code.
_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ]{3,}")

#: What an agency appends to a role in the subject: its own name after a dash,
#: or its filing number. Both belong to the sender, not to the job.
_ROLE_SUBJECT_TAIL_RE = re.compile(
    r"\s*[-–—]\s*[\w .&']+$"  # noqa: RUF001
    r"|\s+[A-Z]{1,3}\d{4,}$"
    r"|\s*[-–—]\s*view\s+your\s+match\s+score\s*$",  # noqa: RUF001
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
        if domain in _ALERT_SENDER_DOMAINS:
            return False
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


def company_from_sender(from_addr: str) -> str:
    """The employer named by the sending domain, or "" when it names none.

    Only ever the SENDER's own name, and only when that means something: a mail
    host names nobody, and an applicant tracking system names itself rather
    than the employer that hired it. So the one case this answers is the one it
    was written for — an agency or a company writing from its own domain.
    """
    domain = sender_domain(from_addr)
    if not domain:
        return ""
    if domain in _ALERT_SENDER_DOMAINS or is_known_sender(from_addr):
        return ""
    token = _company_tokens(domain)
    if not token or len(token) < 3:
        return ""
    return token


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


@dataclass(frozen=True)
class SubjectFacts:
    """What the subject line says, split by WHICH question it answers.

    Both halves come out of the same sentence, and telling them apart is the
    whole point: for a year this app read "Candidatura per AI SPECIALIST JUNIOR
    attraverso Indeed" with a pattern that would have filed the job title as the
    name of an employer.
    """

    company: str = ""
    role: str = ""
    rule: str = ""


def _first_group(match: re.Match[str] | None) -> str:
    if not match:
        return ""
    return next((g for g in match.groups() if g), "").strip(" .!-–—")  # noqa: RUF001


def subject_facts(subject: str) -> SubjectFacts:
    """The employer and the job named in a subject, whichever of the two is there.

    The role is tried FIRST. Not a preference — a correctness requirement: the
    last company alternative ("candidatura per X") matches a role-bearing
    subject too, and whichever pattern runs first decides what the text is
    called. Asking "does this name a job?" before "does this name an employer?"
    is what stops a job title from being stored as a company.
    """
    flat = " ".join(str(subject or "").split())
    if not flat:
        return SubjectFacts()
    role = _ROLE_SUBJECT_TAIL_RE.sub("", _first_group(_SUBJECT_ROLE_RE.search(flat))).strip()
    # A job title has at least one word in it. Without this, "Candidatura per la
    # posizione JN -062026-740365 completata!" hands back "JN -062026" — the
    # agency's filing number, which then goes looking for an offer to match.
    if not _WORD_RE.search(role):
        role = ""
    company = _first_group(_SUBJECT_COMPANY_RE.search(flat))
    if role:
        # One subject can carry both: "applying for the role of X at Y". The
        # employer is kept only when an unambiguous pattern found one — the
        # loose shape below is never consulted here, or the job title would be
        # stored as the company again.
        return SubjectFacts(role=role[:120], company=company[:80], rule="subject_role")
    if not company:
        company = _first_group(_SUBJECT_COMPANY_LOOSE_RE.search(flat))
    return SubjectFacts(company=company[:80], rule="subject_company" if company else "")


def is_confirmation_subject(subject: str) -> bool:
    """Gate 0 and gate 1a on the subject alone: rejected first, then recognised.

    Separate from ``_CONFIRM_SUBJECT_RE`` on purpose. That pattern is also used
    by ``body.py`` as the ANCHOR for finding a job title inside a message body,
    where it is measured at 29 titles out of 30. Widening it to admit the agency
    formats would move that anchor on every LinkedIn message at the same time,
    which is a change to a measured thing made for an unrelated reason.
    """
    flat = " ".join(str(subject or "").split())
    if not flat or _REJECT_SUBJECT_RE.search(flat):
        return False
    return bool(_CONFIRM_SUBJECT_RE.search(flat) or _SUBJECT_ROLE_RE.search(flat))


def _subject_company(subject: str) -> str:
    return subject_facts(subject).company


def _in_window(header_date: datetime | None, job: PendingJob, ttl: timedelta) -> bool:
    if header_date is None:
        return False
    return job.opened_at - CLOCK_SLACK <= header_date <= job.opened_at + ttl


def looks_like_confirmation(header: MailHeader, pending: list[PendingJob]) -> bool:
    """Gate 0 and gate 1: a confirmation subject AND a sender we can place."""
    if not is_confirmation_subject(header.subject):
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


#: Words that belong to a reference number, not to a job.
_ROLE_NOISE = frozenset({"rif", "ref", "riferimento", "id", "job", "cod", "codice"})

#: Trailing decoration a posting carries and a confirmation does not, or the
#: other way round: "(Rif. 2026-97)", "(m/f/d)", "- Torino (Ibrido)".
_ROLE_TAIL_RE = re.compile(
    r"\(([^)]*)\)|\s+[·|]\s+.*$|\s+[-–—]\s+(?:remote|ibrido|hybrid).*$",  # noqa: RUF001
    re.I,
)


def _role_tokens(title: str) -> set[str]:
    """The words of a job title that actually name a trade.

    Reuses the scan vocabulary's tokenizer rather than growing a second one with
    different bugs: it goes down to two letters, so "AI" and "QA" survive, and
    the vague-role list is the same one that stops "AI Specialist" from teaching
    the title gate that "PAYROLL SPECIALIST" is on topic.
    """
    from app.services.scan.vocab import VAGUE_ROLE_WORDS, title_tokens

    cleaned = _ROLE_TAIL_RE.sub(" ", str(title or ""))
    return {t for t in title_tokens(cleaned) if t not in VAGUE_ROLE_WORDS and t not in _ROLE_NOISE}


def same_role(a: str, b: str) -> bool:
    """True when two titles name the same job.

    Subset, not equality: a confirmation carries the employer's reference
    ("Data Analytics Specialist (Rif. 2026-97)") and the archive holds the bare
    posting. Every meaningful word of the shorter title has to appear in the
    longer one, which lets the reference and the location fall away and still
    refuses "Data Engineer" against "Data Analytics Specialist".

    Deliberately silent when it cannot tell: a title made only of vague words
    ("Junior Specialist") has no meaningful tokens at all, and matching on it
    would attach a confirmation to whichever offer shared the word.
    """
    ta, tb = _role_tokens(a), _role_tokens(b)
    if not ta or not tb:
        return False
    smaller, larger = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    # Two meaningful words, minimum. Caught on a real queue one step before it
    # became an automatic write: "IT Specialist (F/M/NB)" loses its bracket and
    # its vague word and is left with {it}, which sits inside almost any
    # technical title — so it matched "Junior IT Infrastructure specialist", a
    # different job at a different company, and was offered as the answer.
    # The cost is the odd genuine single-word title staying a question, and
    # that is the side of this trade to be wrong on.
    if len(smaller) < 2:
        return False
    return smaller <= larger


def rank_candidates(
    role: str, candidates: Sequence[tuple[int, str]]
) -> tuple[list[int], int | None]:
    """Candidates with the title matches first, and the single one when there is one.

    Returns ``(ordered_ids, only_match)``. ``only_match`` is set **only** when
    exactly one candidate matches: narrowing three offers to two is worth
    showing and is not an answer.
    """
    ids = [job_id for job_id, _ in candidates]
    if not role:
        return ids, None
    hits = [job_id for job_id, title in candidates if same_role(role, title)]
    if not hits:
        return ids, None
    ordered = hits + [job_id for job_id in ids if job_id not in hits]
    return ordered, hits[0] if len(hits) == 1 else None


@dataclass(frozen=True)
class ApplicationEvidence:
    """A message saying an application was sent, and to whom — or for what.

    ``company`` is empty when the subject named the JOB instead of the employer,
    which is how Indeed writes every one of its confirmations. That is not a
    failure to extract: it is the message genuinely not saying, and the employer
    is then read from the body.
    """

    company: str
    sent_at: datetime
    rule: str
    role: str = ""


def extract_application(header: MailHeader) -> ApplicationEvidence | None:
    """Does this message record an application, and to which employer?

    A different question from :func:`classify`, and deliberately a separate
    function. ``classify`` asks "which offer ALREADY IN THE ARCHIVE does this
    message confirm", and for that, a subject naming a company the archive has
    never heard of is a ``no_match`` — reading on would settle on an offer the
    message just ruled out, which is what it used to do on 39 real messages.

    This one does not look at the archive at all. It asks what the message says
    happened, and the answer is worth having precisely in the case ``classify``
    rejects: a real mailbox named 84 employers over a year, and only 27 of them
    were offers the app had ever seen. The other 57 are applications that exist
    and that Job Finder believed had never happened.

    The sender still has to be a recognised hiring platform. Without a list of
    pending offers to check against, that is the only thing left holding up the
    "two independent facts" rule the whole matcher is built on — the subject
    alone would let any message that quotes the word "candidatura" invent an
    employer.
    """
    subject = str(header.subject or "")
    if not is_confirmation_subject(subject) or header.date is None:
        return None
    facts = subject_facts(subject)
    company, role, rule = facts.company, facts.role, facts.rule

    if not is_known_sender(header.from_addr, header.list_id):
        # Not on the platform list. Requiring membership dropped 25 real
        # applications in one measured year — Experis, Randstad, Orienta,
        # Loacker and the rest write from their own domain, and for those the
        # sender IS the employer. The two independent facts still hold: the
        # subject reads like a confirmation, AND the domain names a company
        # that is neither a mail host nor an applicant tracking system (from
        # ``myworkday.com`` the employer is the tenant's client, never
        # "Workday", so those keep being read from the subject or the body).
        from_domain = company_from_sender(header.from_addr)
        if not from_domain:
            return None
        company = company or from_domain
        rule = rule or "sender_domain"

    # Either half is enough to go on. A subject that names only the job still
    # records that an application happened — the employer comes from the body,
    # and without this branch every Indeed confirmation is dropped here.
    if not company and not role:
        return None
    return ApplicationEvidence(
        company=company[:80],
        sent_at=header.date,
        rule=rule or "subject_company",
        role=role[:120],
    )
