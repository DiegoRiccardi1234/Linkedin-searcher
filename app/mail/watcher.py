"""When to look at the mailbox, and what to do with what is found.

The expensive question is not "is this a confirmation" but "which offer is it
about", and the answer comes from something the app already knows: which
postings the user opened, and when. That turns a search through a whole mailbox
into a check on the four companies opened this week.

Three behaviours here are decisions, not implementation details:

* **Nothing pending, nothing fetched.** The common state of this feature is
  "no applications in flight", and in that state it must not touch the network
  at all. The one exception is a periodic connection kept deliberately: a
  Microsoft refresh token expires after about 90 days of inactivity, so a
  feature that works perfectly by doing nothing would eventually lock itself out.
* **The last-run stamp is written even when the run failed.** Learned the
  expensive way in ``autoscan``: without it, a mailbox that cannot be reached is
  retried every single tick, forever.
* **A dry run is a first-class mode.** How well the rules actually do is unknown
  until they meet a real mailbox, so they can be pointed at one and made to
  report what they *would* have marked, with nothing written.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.log import get_logger
from app.mail import config as mail_config
from app.mail.auth import refresh_access_token, scope_for
from app.mail.config import MailAccount
from app.mail.errors import (
    MailAuthError,
    MailConfigError,
    MailError,
    MailReauthRequired,
    safe_error,
)
from app.mail.graph_client import GraphMailbox
from app.mail.imap_client import ImapMailbox
from app.mail.matcher import (
    CLOCK_SLACK,
    MailHeader,
    PendingJob,
    _subject_company,
    classify,
    extract_application,
    is_known_sender,
    sender_domain,
)

if TYPE_CHECKING:
    from app.db import Database
    from app.services.scan_control import ScanControl

log = get_logger(__name__)

#: Ceiling per run. A mailbox can hold thousands of messages in a fortnight and
#: a tick must stay a tick.
MAX_PER_RUN = 300
#: Ceiling for the one-off historic sweep, which reaches back up to a year.
#:
#: Was 2000, and the truncation keeps the NEWEST messages — so a year-long sweep
#: over that ceiling would drop the oldest part in silence, which is the part
#: someone reaching back a year is reaching for. Measured on a real job-hunting
#: mailbox: 856 messages over 90 days, 1.068 over 180, 1.550 over 365. Set well
#: clear of that, because a mailbox busier than this one is ordinary; when it
#: still bites, ``truncated`` says so out loud rather than reporting "done".
MAX_HISTORIC = 6000
#: Reconnect at least this often so an idle grant does not expire unnoticed.
KEEP_WARM_DAYS = 30

STATE_OK = "ok"
STATE_UNCONFIGURED = "unconfigured"
STATE_REAUTH = "reauth_required"
STATE_AUTH_FAILED = "auth_failed"
STATE_ERROR = "error"


@dataclass
class ReviewItem:
    """A message that could be a confirmation but cannot be assigned alone.

    Used to be held in memory and never written down, on the grounds that showing
    it needs the subject and the sender. That reasoning had a hole: a 90-day
    sweep of a real mailbox produced **110 of these**, the app was restarted, and
    all of them vanished — leaving a card that said "recovery done, 0 to review",
    which reads as "there was nothing".

    What the queue actually needs to show is not the message but the FACT read
    out of it: which employer, and when. That is the same class of data as
    ``jobs.azienda``, so it is written to ``mail_review`` and survives. The
    subject and the sender's address still never touch the disk.
    """

    job_id: int
    job_title: str
    company: str
    subject: str
    from_domain: str
    received_at: str
    rule: str
    candidates: tuple[int, ...] = ()


class MailWatcher:
    """Checks the mailbox, and is the only thing here allowed to write."""

    def __init__(
        self,
        db: Database,
        data_dir: Path,
        control: ScanControl,
        *,
        clock: Any = time.time,
        imap_factory: Any = None,
        graph_factory: Any = None,
    ) -> None:
        self._db = db
        self._data_dir = data_dir
        self._control = control
        self._clock = clock
        self._imap_factory = imap_factory
        self._graph_factory = graph_factory
        self._access_token = ""
        self._access_expires = 0.0

    # ── settings ────────────────────────────────────────────────────────────

    def account(self) -> MailAccount | None:
        return mail_config.load_account(self._db, self._data_dir)

    def enabled(self) -> bool:
        raw = str(self._db.get_preference(mail_config.PREF_ENABLED, "") or "").strip().lower()
        return raw in ("1", "true", "on", "yes")

    def interval_minutes(self) -> int:
        try:
            value = int(
                str(
                    self._db.get_preference(
                        mail_config.PREF_INTERVAL, str(mail_config.DEFAULT_INTERVAL_MINUTES)
                    )
                )
            )
        except (TypeError, ValueError):
            value = mail_config.DEFAULT_INTERVAL_MINUTES
        return max(5, min(240, value))

    def pending_days(self) -> int:
        try:
            value = int(
                str(
                    self._db.get_preference(
                        mail_config.PREF_PENDING_DAYS, str(mail_config.DEFAULT_PENDING_DAYS)
                    )
                )
            )
        except (TypeError, ValueError):
            value = mail_config.DEFAULT_PENDING_DAYS
        return max(1, min(90, value))

    def _set_state(self, state: str, error: str = "") -> None:
        self._db.set_preference(mail_config.PREF_STATE, state)
        self._db.set_preference(mail_config.PREF_LAST_ERROR, error)

    def status(self) -> dict[str, Any]:
        account = self.account()
        return {
            "configured": bool(account and account.configured),
            "address": account.address if account else "",
            "auth": account.auth if account else "",
            "host": account.host if account else "",
            "folder": account.folder if account else "INBOX",
            "enabled": self.enabled(),
            "interval_minutes": self.interval_minutes(),
            "pending_days": self.pending_days(),
            "state": str(self._db.get_preference(mail_config.PREF_STATE, "") or STATE_UNCONFIGURED),
            "last_error": str(self._db.get_preference(mail_config.PREF_LAST_ERROR, "") or ""),
            "last_run_ts": str(self._db.get_preference(mail_config.PREF_LAST_RUN, "") or ""),
            "pending_count": len(self._db.list_pending_applications(self.pending_days())),
            "review_count": len(self.review_items()),
            "recovery_done": str(self._db.get_preference(mail_config.PREF_RECOVERY_DONE, "") or "")
            == "1",
            "running": self._control.running,
        }

    # ── the mailbox ─────────────────────────────────────────────────────────

    def _oauth_token(self, account: MailAccount) -> str:
        """A valid access token, refreshing when it is about to run out.

        A rotated refresh token is written back immediately. Not doing so works
        until the stored one expires, which is a failure that arrives months
        later with nothing in the logs to connect it to.
        """
        if self._access_token and self._clock() < self._access_expires - 120:
            return self._access_token
        if not account.client_id or not account.secret:
            raise MailConfigError("Microsoft mailbox not connected")
        bundle = refresh_access_token(account.client_id, account.secret, scope_for(account.auth))
        if bundle.refresh_token and bundle.refresh_token != account.secret:
            mail_config.write_secrets(self._data_dir, secret=bundle.refresh_token)
        self._access_token = bundle.access_token
        self._access_expires = self._clock() + bundle.expires_in
        return bundle.access_token

    def _headers_since(self, account: MailAccount, since: datetime, limit: int) -> list[MailHeader]:
        if account.auth == "graph":
            token = self._oauth_token(account)
            factory = self._graph_factory or GraphMailbox
            with factory(token, folder=account.folder.lower() or "inbox") as box:
                return list(box.fetch_since(since, limit=limit))
        if account.auth == "imap_oauth":
            token = self._oauth_token(account)
            factory = self._imap_factory or (
                lambda acc: ImapMailbox(acc, token_provider=lambda: token)
            )
        else:
            factory = self._imap_factory or (lambda acc: ImapMailbox(acc))
        with factory(account) as box:
            box.select_readonly(account.folder or "INBOX")
            uids = box.search_since(since.date())
            keys = [f"imap:{box.uidvalidity}:{uid}" for uid in uids]
            fresh = set(self._db.filter_unseen_mail(account.address, keys))
            wanted = [uid for uid, key in zip(uids, keys, strict=True) if key in fresh]
            return list(box.fetch_headers(wanted[-limit:]))

    def check_connection(self) -> dict[str, Any]:
        """Open the mailbox, read nothing, report what happened."""
        account = self.account()
        if not account or not account.configured:
            self._set_state(STATE_UNCONFIGURED)
            return {"ok": False, "state": STATE_UNCONFIGURED}
        try:
            self._headers_since(account, _now() - timedelta(days=1), limit=1)
        except MailReauthRequired as exc:
            self._set_state(STATE_REAUTH, safe_error(exc))
            return {"ok": False, "state": STATE_REAUTH}
        except MailAuthError as exc:
            self._set_state(STATE_AUTH_FAILED, safe_error(exc))
            return {"ok": False, "state": STATE_AUTH_FAILED}
        except MailError as exc:
            self._set_state(STATE_ERROR, safe_error(exc))
            return {"ok": False, "state": STATE_ERROR}
        self._set_state(STATE_OK)
        self._db.set_preference(mail_config.PREF_LAST_OK, str(int(self._clock())))
        return {"ok": True, "state": STATE_OK}

    # ── the run ─────────────────────────────────────────────────────────────

    def tick(self) -> None:
        """Called by the scheduler loop once a minute. Cheap when idle."""
        if not self.enabled():
            return
        try:
            last = float(str(self._db.get_preference(mail_config.PREF_LAST_RUN, "0") or 0))
        except (TypeError, ValueError):
            last = 0.0
        if self._clock() - last < self.interval_minutes() * 60:
            return
        self.run_once()

    def run_once(self, *, dry_run: bool = False) -> dict[str, Any]:
        """One pass over what arrived since the oldest unanswered open."""
        if not self._control.try_begin():
            return {"status": "already_running"}
        try:
            return self._run(dry_run=dry_run)
        except MailReauthRequired as exc:
            self._set_state(STATE_REAUTH, safe_error(exc))
            return {"status": "error", "state": STATE_REAUTH}
        except MailAuthError as exc:
            self._set_state(STATE_AUTH_FAILED, safe_error(exc))
            return {"status": "error", "state": STATE_AUTH_FAILED}
        except MailError as exc:
            self._set_state(STATE_ERROR, safe_error(exc))
            return {"status": "error", "state": STATE_ERROR}
        except Exception as exc:  # a mailbox must never take the app down
            log.warning("mail check failed: %s", safe_error(exc))
            self._set_state(STATE_ERROR, safe_error(exc))
            return {"status": "error", "state": STATE_ERROR}
        finally:
            # Even on failure. Otherwise an unreachable mailbox is retried on
            # every tick until someone notices.
            self._db.set_preference(mail_config.PREF_LAST_RUN, str(int(self._clock())))
            self._control.end()

    def _run(self, *, dry_run: bool) -> dict[str, Any]:
        account = self.account()
        if not account or not account.configured:
            self._set_state(STATE_UNCONFIGURED)
            return {"status": "skipped", "reason": STATE_UNCONFIGURED}

        ttl = self.pending_days()
        pending = [
            job
            for job in (_pending(row) for row in self._db.list_pending_applications(ttl))
            if job is not None
        ]
        if not pending:
            if self._keep_warm_due():
                self.check_connection()
                return {"status": "keep_warm", "checked": 0}
            return {"status": "idle", "checked": 0}

        since = min(job.opened_at for job in pending) - CLOCK_SLACK
        headers = self._headers_since(account, since, MAX_PER_RUN)
        if account.auth == "graph":
            keys = [h.key for h in headers]
            fresh = set(self._db.filter_unseen_mail(account.address, keys))
            headers = [h for h in headers if h.key in fresh]

        matched = ambiguous = 0
        for header in headers:
            result = classify(header, pending, ttl_days=ttl)
            if result.verdict == "match" and result.job_id is not None:
                matched += 1
                if not dry_run:
                    self._db.confirm_application_from_mail(
                        result.job_id, header.message_id, result.rule
                    )
                    pending = [job for job in pending if job.job_id != result.job_id]
            elif result.verdict == "ambiguous":
                ambiguous += 1
                if not dry_run:
                    self._queue_review(account, header, result, pending)
            if not dry_run:
                self._db.record_mail_seen(
                    account=account.address,
                    mail_key=header.key,
                    verdict=result.verdict,
                    message_id=header.message_id,
                    received_at=header.date.isoformat() if header.date else "",
                    job_id=result.job_id,
                    matched_rule=result.rule,
                )
        self._set_state(STATE_OK)
        self._db.set_preference(mail_config.PREF_LAST_OK, str(int(self._clock())))
        return {
            "status": "done",
            "checked": len(headers),
            "matched": matched,
            "ambiguous": ambiguous,
            "dry_run": dry_run,
        }

    def _queue_review(
        self, account: MailAccount, header: MailHeader, result: Any, pending: list[PendingJob]
    ) -> None:
        """Write a proposal to the queue, keeping only facts and never the message."""
        by_id = {job.job_id: job for job in pending}
        candidates = [c for c in (result.candidates or ()) if c in by_id]
        # The employer the message names, when it names one. Falls back to the
        # first candidate's company, which is a fact the archive already holds.
        #
        # Truncated, because the extractor takes everything after "inviata a" to
        # the end of the subject: on a real LinkedIn confirmation that is exactly
        # the employer, but the column must not become a place where a long tail
        # of somebody's subject line can end up. The part of the subject BEFORE
        # the employer — "Diego, la tua candidatura…", which carries the user's
        # own name — never reaches this at all.
        company = _subject_company(header.subject)[:80]
        if not company and candidates:
            company = by_id[candidates[0]].company
        self._db.add_mail_review(
            account=account.address,
            mail_key=header.key,
            kind="attach",
            message_id=header.message_id,
            received_at=header.date.isoformat() if header.date else "",
            company=company,
            sender=_safe_sender(header),
            rule=result.rule,
            candidates=candidates,
        )

    def _queue_import(self, account: MailAccount, header: MailHeader, evidence: Any) -> None:
        """Queue "you applied to X on that day, and X is not in the archive".

        Still a proposal, never an automatic write. The evidence is a company
        name and a date, which is enough to record that it happened and not
        enough to decide what it was — so the user sees it before anything is
        created. Where the company IS in the archive with open offers, those come
        along as candidates: attaching to the real posting beats a stub, and the
        stub can never be improved into one (its description stays empty, so a
        score would be computed on a text it does not contain).
        """
        day = evidence.sent_at.date().isoformat()
        if self._db.applications_near(evidence.company, day):
            # Already recorded, by hand or by an earlier sweep. Nothing to ask.
            self._db.record_mail_seen(
                account=account.address,
                mail_key=header.key,
                verdict="already_applied",
                message_id=header.message_id,
                received_at=header.date.isoformat() if header.date else "",
                matched_rule=evidence.rule,
            )
            return
        self._db.add_mail_review(
            account=account.address,
            mail_key=header.key,
            kind="import",
            message_id=header.message_id,
            received_at=header.date.isoformat() if header.date else "",
            company=evidence.company,
            sender=_safe_sender(header),
            rule=evidence.rule,
            candidates=self._db.open_offers_from(evidence.company),
        )

    def _keep_warm_due(self) -> bool:
        try:
            last_ok = float(str(self._db.get_preference(mail_config.PREF_LAST_OK, "0") or 0))
        except (TypeError, ValueError):
            last_ok = 0.0
        return bool(self._clock() - last_ok > KEEP_WARM_DAYS * 86400)

    def run_historic(self, days: int = 90, *, dry_run: bool = False) -> Iterator[dict[str, Any]]:
        """One-off sweep for applications sent before the mailbox was connected.

        The evidence is weaker than in the normal run — nothing was opened from
        the app, so there is no click to anchor a message to, only the company
        name and a three-month window. Which is exactly why **nothing here is
        applied**: every hit is a proposal, and a human confirms it.

        ``dry_run`` reports the same counts and writes nothing at all — not even
        the ``no_match`` rows. It exists because the interesting question before
        widening the window to a year is "how many messages is that, and how long
        does it take", and answering it should not consume the messages: a
        recorded ``no_match`` is never looked at again.

        Yields SSE-shaped events, like the scan and the bulk re-score.
        """
        if not self._control.try_begin():
            yield {"status": "error", "error": "mail_busy"}
            return
        try:
            account = self.account()
            if not account or not account.configured:
                yield {"status": "error", "error": STATE_UNCONFIGURED}
                return
            window = max(1, min(365, int(days)))
            since = _now() - timedelta(days=window)
            candidates = [
                PendingJob(
                    job_id=int(row["id"]),
                    company=str(row.get("azienda") or ""),
                    title=str(row.get("titolo") or ""),
                    opened_at=since,  # the window IS the anchor here
                )
                for row in self._db.list_jobs(limit=2000)
                if str(row.get("status") or "open") == "open" or not row.get("applied_at")
            ]
            yield {"status": "searching", "candidates": len(candidates)}
            if not candidates:
                yield {"status": "complete", "proposals": 0, "checked": 0, "truncated": False}
                return

            headers = self._headers_since(account, since, MAX_HISTORIC)
            truncated = len(headers) >= MAX_HISTORIC
            found = 0
            imports = 0
            for index, header in enumerate(headers, start=1):
                # Three questions in this order. Attach before import matters:
                # a message about a company the archive already knows must offer
                # to attach, not create a second entry beside the offer it is
                # about.
                result = classify(header, candidates, ttl_days=window)
                if result.verdict in ("match", "ambiguous"):
                    found += 1
                    if not dry_run:
                        # Deliberately reported as ambiguous whatever the rule
                        # said: over three months a company name is not proof,
                        # and this screen is where the user supplies the missing
                        # certainty.
                        self._queue_review(account, header, _as_proposal(result), candidates)
                elif (evidence := extract_application(header)) is not None:
                    imports += 1
                    if not dry_run:
                        self._queue_import(account, header, evidence)
                elif not dry_run:
                    # Only the definite misses are written down. A candidate left
                    # unrecorded is re-found after a restart instead of lost.
                    self._db.record_mail_seen(
                        account=account.address,
                        mail_key=header.key,
                        verdict="no_match",
                        message_id=header.message_id,
                        received_at=header.date.isoformat() if header.date else "",
                        matched_rule=result.rule,
                    )
                if index % 25 == 0:
                    yield {"status": "progress", "current": index, "total": len(headers)}
            if not dry_run:
                self._db.set_preference(mail_config.PREF_RECOVERY_DONE, "1")
            yield {
                "status": "complete",
                "proposals": found,
                "imports": imports,
                "checked": len(headers),
                "days": window,
                "dry_run": dry_run,
                # Said out loud rather than hidden: a truncated sweep that
                # reports "done" reads as "there was nothing else".
                "truncated": truncated,
            }
        except MailError as exc:
            yield {"status": "error", "error": safe_error(exc)}
        finally:
            # Not on a dry run: the stamp exists to stop the scheduler retrying
            # an unreachable mailbox every tick, and a sweep the user asked for
            # has no business postponing the next automatic check.
            if not dry_run:
                self._db.set_preference(mail_config.PREF_LAST_RUN, str(int(self._clock())))
            self._control.end()

    def review_items(self) -> list[dict[str, Any]]:
        """The pending queue, read from the table it now lives in."""
        account = self.account()
        return self._db.list_mail_review(account.address if account else None)

    def resolve_review(
        self, attach: list[Any], dismiss: list[int], create: list[int] | None = None
    ) -> dict[str, Any]:
        """Apply or drop what the user decided about the queued messages.

        ``attach`` is indexed by ``review_id`` rather than by ``job_id``, and the
        offer named has to be one of that row's own candidates. Without that
        check a mistaken UI could mark any offer in the archive as applied, and
        the point of this whole feature is that it writes to the record of what
        you applied for.
        """
        by_id = {int(item["id"]): item for item in self.review_items()}
        applied, refused = 0, []
        for choice in attach:
            review_id = int(getattr(choice, "review_id", 0) or 0)
            job_id = int(getattr(choice, "job_id", 0) or 0)
            row = by_id.get(review_id)
            if not row or job_id not in {int(c["id"]) for c in row["candidates"]}:
                refused.append(review_id)
                continue
            if self._db.confirm_application_from_mail(
                job_id, str(row.get("message_id") or ""), "manual_review"
            ):
                applied += 1
                self._db.close_mail_review([review_id], "applied")

        created: list[int] = []
        for review_id in create or []:
            row = by_id.get(review_id)
            if not row or row["kind"] != "import":
                refused.append(review_id)
                continue
            new_id = self._db.add_application_from_mail(
                company=str(row.get("company") or ""),
                applied_at=str(row.get("received_at") or ""),
                message_id=str(row.get("message_id") or ""),
                rule="import",
            )
            if new_id is not None:
                created.append(new_id)
            # Closed either way: an id already there means the application is
            # recorded, which is the outcome the user asked for.
            self._db.close_mail_review([review_id], "imported")

        dismissed = self._db.close_mail_review([i for i in dismiss if i in by_id], "dismissed")
        return {
            "applied": applied,
            "created": created,
            "dismissed": dismissed,
            "refused": refused,
        }


def _as_proposal(result: Any) -> Any:
    """Force a historic hit into the review queue, however confident the rule."""
    from app.mail.matcher import MatchResult

    candidates = result.candidates or ((result.job_id,) if result.job_id else ())
    return MatchResult("ambiguous", None, f"historic:{result.rule}", tuple(candidates))


def _safe_sender(header: MailHeader) -> str:
    """The sending domain, but ONLY when it is a recognised hiring platform.

    The queue is more useful when it can say a confirmation came from
    linkedin.com rather than from somewhere. It is also a mailbox, so the rule is
    drawn where it can be checked: the value is written only if
    ``is_known_sender`` says the domain is one of the twenty-five in
    ``_KNOWN_SENDER_DOMAINS``. Anything else — an employer's own address, a
    person — is stored as the empty string, so the column cannot hold somebody's
    identity even in principle.
    """
    if not is_known_sender(header.from_addr, header.list_id):
        return ""
    return sender_domain(header.from_addr) or sender_domain(header.list_id)


def _now() -> datetime:
    return datetime.now(UTC)


def _pending(row: dict[str, Any]) -> PendingJob | None:
    raw = str(row.get("link_opened_at") or "")
    if not raw:
        return None
    try:
        opened = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return PendingJob(
        job_id=int(row["id"]),
        company=str(row.get("azienda") or ""),
        title=str(row.get("titolo") or ""),
        opened_at=opened if opened.tzinfo else opened.replace(tzinfo=UTC),
    )
