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
from app.mail.auth import refresh_access_token
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
from app.mail.matcher import CLOCK_SLACK, MailHeader, PendingJob, classify

if TYPE_CHECKING:
    from app.db import Database
    from app.services.scan_control import ScanControl

log = get_logger(__name__)

#: Ceiling per run. A mailbox can hold thousands of messages in a fortnight and
#: a tick must stay a tick.
MAX_PER_RUN = 300
#: Ceiling for the one-off historic sweep, which reaches back 90 days.
MAX_HISTORIC = 2000
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

    Held in memory, never written down: showing it needs the subject and the
    sender, and those are exactly what this feature promises not to keep.
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
        self.review: list[ReviewItem] = []

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
            "review_count": len(self.review),
            "recovery_done": str(self._db.get_preference(mail_config.PREF_RECOVERY_DONE, "") or "")
            == "1",
            "running": self._control.running,
        }

    # ── the mailbox ─────────────────────────────────────────────────────────

    def _graph_token(self, account: MailAccount) -> str:
        """A valid access token, refreshing when it is about to run out.

        A rotated refresh token is written back immediately. Not doing so works
        until the stored one expires, which is a failure that arrives months
        later with nothing in the logs to connect it to.
        """
        if self._access_token and self._clock() < self._access_expires - 120:
            return self._access_token
        if not account.client_id or not account.secret:
            raise MailConfigError("Microsoft mailbox not connected")
        bundle = refresh_access_token(account.client_id, account.secret)
        if bundle.refresh_token and bundle.refresh_token != account.secret:
            mail_config.write_secrets(self._data_dir, secret=bundle.refresh_token)
        self._access_token = bundle.access_token
        self._access_expires = self._clock() + bundle.expires_in
        return bundle.access_token

    def _headers_since(self, account: MailAccount, since: datetime, limit: int) -> list[MailHeader]:
        if account.auth == "graph":
            token = self._graph_token(account)
            factory = self._graph_factory or GraphMailbox
            with factory(token, folder=account.folder.lower() or "inbox") as box:
                return list(box.fetch_since(since, limit=limit))
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
                self._queue_review(header, result, pending)
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

    def _queue_review(self, header: MailHeader, result: Any, pending: list[PendingJob]) -> None:
        from app.mail.matcher import sender_domain

        by_id = {job.job_id: job for job in pending}
        candidates = result.candidates or ()
        first = by_id.get(candidates[0]) if candidates else None
        item = ReviewItem(
            job_id=first.job_id if first else 0,
            job_title=first.title if first else "",
            company=first.company if first else "",
            subject=header.subject[:160],
            from_domain=sender_domain(header.from_addr),
            received_at=header.date.isoformat() if header.date else "",
            rule=result.rule,
            candidates=tuple(candidates),
        )
        if not any(
            existing.subject == item.subject and existing.received_at == item.received_at
            for existing in self.review
        ):
            self.review.append(item)

    def _keep_warm_due(self) -> bool:
        try:
            last_ok = float(str(self._db.get_preference(mail_config.PREF_LAST_OK, "0") or 0))
        except (TypeError, ValueError):
            last_ok = 0.0
        return bool(self._clock() - last_ok > KEEP_WARM_DAYS * 86400)

    def run_historic(self, days: int = 90) -> Iterator[dict[str, Any]]:
        """One-off sweep for applications sent before the mailbox was connected.

        The evidence is weaker than in the normal run — nothing was opened from
        the app, so there is no click to anchor a message to, only the company
        name and a three-month window. Which is exactly why **nothing here is
        applied**: every hit is a proposal, and a human confirms it.

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
            for index, header in enumerate(headers, start=1):
                result = classify(header, candidates, ttl_days=window)
                if result.verdict in ("match", "ambiguous"):
                    found += 1
                    # Deliberately reported as ambiguous whatever the rule said:
                    # over three months a company name is not proof, and this
                    # screen is where the user supplies the missing certainty.
                    self._queue_review(header, _as_proposal(result), candidates)
                else:
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
            self._db.set_preference(mail_config.PREF_RECOVERY_DONE, "1")
            yield {
                "status": "complete",
                "proposals": found,
                "checked": len(headers),
                # Said out loud rather than hidden: a truncated sweep that
                # reports "done" reads as "there was nothing else".
                "truncated": truncated,
            }
        except MailError as exc:
            yield {"status": "error", "error": safe_error(exc)}
        finally:
            self._db.set_preference(mail_config.PREF_LAST_RUN, str(int(self._clock())))
            self._control.end()

    def resolve_review(self, apply_ids: list[int], dismiss_ids: list[int]) -> dict[str, int]:
        """Apply or drop what the user decided about the queued messages."""
        applied = 0
        for job_id in apply_ids:
            if self._db.confirm_application_from_mail(job_id, "", "manual_review"):
                applied += 1
        resolved = set(apply_ids) | set(dismiss_ids)
        self.review = [
            item for item in self.review if not (resolved & (set(item.candidates) | {item.job_id}))
        ]
        return {"applied": applied, "dismissed": len(dismiss_ids)}


def _as_proposal(result: Any) -> Any:
    """Force a historic hit into the review queue, however confident the rule."""
    from app.mail.matcher import MatchResult

    candidates = result.candidates or ((result.job_id,) if result.job_id else ())
    return MatchResult("ambiguous", None, f"historic:{result.rule}", tuple(candidates))


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
