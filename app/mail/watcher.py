"""When to look at the mailbox, and what to do with what is found.

The expensive question is not "is this a confirmation" but "which offer is it
about", and the answer comes from something the app already knows: which
postings the user opened, and when. That turns a search through a whole mailbox
into a check on the four companies opened this week.

Three behaviours here are decisions, not implementation details:

* **A connected mailbox gets read.** This used to say "nothing pending, nothing
  fetched": with no offer whose link had been opened from inside the app, the
  tick returned ``idle`` without opening a connection. The intention was
  restraint; the effect was an off switch, because that flag is set by a gesture
  — pressing an offer's link in Job Finder — that someone who applies on
  LinkedIn never makes. Confirmations then piled up unread until a historic
  sweep was run by hand. Now a configured, enabled account is looked at, over
  the pending-days window, and a message that records an application to a
  company the archive never saw becomes a proposal instead of nothing.
  A periodic connection is still kept deliberately when there is truly nothing
  to do: a Microsoft refresh token expires after about 90 days of inactivity.
* **The last-run stamp is written even when the run failed.** Learned the
  expensive way in ``autoscan``: without it, a mailbox that cannot be reached is
  retried every single tick, forever.
* **A dry run is a first-class mode.** How well the rules actually do is unknown
  until they meet a real mailbox, so they can be pointed at one and made to
  report what they *would* have marked, with nothing written.
"""

from __future__ import annotations

import time
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.log import get_logger
from app.mail import config as mail_config
from app.mail.auth import refresh_access_token, scope_for
from app.mail.body import BodyFacts, facts_from_body
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
    classify,
    extract_application,
    is_known_sender,
    rank_candidates,
    sender_domain,
    subject_facts,
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

#: How many message bodies one automatic tick may download, and one user-asked
#: sweep. Headers come back fifty to a command (1.554 of them in 16 seconds on a
#: real mailbox); a body is one command each, so the cost here is round trips,
#: not bytes. A tick has to stay a tick — the sweep has a progress bar and was
#: asked for. For scale: 110 proposals over 90 days on a real mailbox.
MAX_BODY_READS_PER_TICK = 20
MAX_BODY_READS_PER_SWEEP = 200

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


class _BodyReader:
    """Reads message bodies during one run: one connection, a budget, no state.

    Before this, every body opened its own connection — ``_fetch_body`` did the
    whole login/select/logout for a single message. In "always" mode a sweep
    with sixty imports meant sixty logins, and that was the cost BEFORE this
    release started reading bodies for the attach branch as well.

    Lazy on purpose: a run where nothing needs a body must not talk to the
    server at all, which is the promise the tick already makes. Local to the
    run and never a field on the watcher, because ``run_once`` executes on a
    background thread.
    """

    def __init__(self, watcher: MailWatcher, account: MailAccount, budget: int) -> None:
        self._watcher = watcher
        self._account = account
        self._budget = max(0, int(budget))
        self._box: Any = None
        self.read = 0
        self.skipped = 0

    @property
    def supported(self) -> bool:
        """Graph cannot fetch a body at all; saying so beats failing quietly."""
        return self._account.auth != "graph"

    def facts(self, mail_key: str, *, company: str = "", role: str = "") -> BodyFacts:
        """What the body adds to what is already known. Never raises."""
        if not self.supported or not mail_key.startswith("imap:"):
            return BodyFacts(role=role, company=company)
        if self._budget <= 0:
            self.skipped += 1
            return BodyFacts(role=role, company=company)
        try:
            uid = int(mail_key.rsplit(":", 1)[-1])
        except ValueError:
            return BodyFacts(role=role, company=company)
        try:
            if self._box is None:
                self._box = self._open()
            raw = bytes(self._box.fetch_body(uid))
        except (MailError, OSError, ValueError) as exc:
            # One unreadable message must not cost the rest of the run: the
            # proposal is still queued, just without what the body would add.
            log.debug("body unreadable: %s", safe_error(exc))
            return BodyFacts(role=role, company=company)
        self._budget -= 1
        self.read += 1
        return facts_from_body(raw, company=company, role=role)

    def _open(self) -> Any:
        account = self._account
        if account.auth == "imap_oauth":
            token = self._watcher._oauth_token(account)
            factory = self._watcher._imap_factory or (
                lambda acc: ImapMailbox(acc, token_provider=lambda: token)
            )
        else:
            factory = self._watcher._imap_factory or (lambda acc: ImapMailbox(acc))
        box = factory(account)
        box.__enter__()
        box.select_readonly(account.folder or "INBOX")
        return box

    def close(self) -> None:
        if self._box is None:
            return
        try:
            self._box.__exit__(None, None, None)
        except Exception as exc:
            log.debug("closing the mailbox: %s", safe_error(exc))
        finally:
            self._box = None


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

    def body_mode(self) -> str:
        raw = str(self._db.get_preference(mail_config.PREF_BODY_MODE, "") or "").strip().lower()
        return raw if raw in mail_config.BODY_MODES else mail_config.DEFAULT_BODY_MODE

    def attach_mode(self) -> str:
        raw = str(self._db.get_preference(mail_config.PREF_ATTACH_MODE, "") or "").strip().lower()
        return raw if raw in mail_config.ATTACH_MODES else mail_config.DEFAULT_ATTACH_MODE

    def _may_attach_alone(self, header: MailHeader) -> bool:
        """Whether this message is allowed to file itself, given the setting."""
        mode = self.attach_mode()
        if mode == mail_config.ATTACH_MODE_AUTO:
            return True
        if mode == mail_config.ATTACH_MODE_KNOWN:
            return is_known_sender(header.from_addr, header.list_id)
        return False

    def _attach_if_certain(
        self,
        account: MailAccount,
        header: MailHeader,
        role: str,
        titled: list[tuple[int, str]],
        rule: str,
        *,
        allow_auto: bool,
    ) -> int | None:
        """File the application when the title leaves exactly one answer.

        Two facts have to agree — the employer (which is what put these
        candidates on the list) and the job title — and they have to leave ONE
        offer standing. Two matches, or a title that only matches in part, stays
        a question: narrowing is not deciding.
        """
        _, only = rank_candidates(role, titled)
        if only is None or not allow_auto or not self._may_attach_alone(header):
            return None
        self._db.confirm_application_from_mail(only, header.message_id, rule)
        self._db.record_mail_seen(
            account=account.address,
            mail_key=header.key,
            verdict="applied",
            message_id=header.message_id,
            received_at=header.date.isoformat() if header.date else "",
            job_id=only,
            matched_rule=rule,
            overwrite=True,
        )
        log.info("Mail confirmation filed by title (job %s, rule %s)", only, rule)
        return only

    def fetch_role(self, mail_key: str, company: str) -> str:
        """Read the job title out of one message's body, on request.

        Downloads a body for a single message the user pointed at. Refuses
        outright in ``never`` mode rather than quietly obliging: a setting that
        can be bypassed by an endpoint is not a setting.

        Uses the same three-shape reader as the automatic path. It used to call
        ``role_from_body`` alone, which reads one shape — so pressing the button
        on a Randstad or an Experis confirmation returned an empty string and
        looked like a message with no title in it, rather than a message written
        in a form nothing had been taught to read.
        """
        if self.body_mode() == mail_config.BODY_MODE_NEVER:
            raise MailConfigError("body reading is switched off")
        account = self.account()
        if not account or not account.configured:
            raise MailConfigError("mailbox not connected")
        raw = self._fetch_body(account, mail_key)
        return facts_from_body(raw, company=company).role if raw else ""

    def _fetch_body(self, account: MailAccount, mail_key: str) -> bytes:
        """One message, whole, in memory. Graph is not wired for this yet."""
        if account.auth == "graph" or not mail_key.startswith("imap:"):
            return b""
        try:
            uid = int(mail_key.rsplit(":", 1)[-1])
        except ValueError:
            return b""
        if account.auth == "imap_oauth":
            token = self._oauth_token(account)
            factory = self._imap_factory or (
                lambda acc: ImapMailbox(acc, token_provider=lambda: token)
            )
        else:
            factory = self._imap_factory or (lambda acc: ImapMailbox(acc))
        with factory(account) as box:
            box.select_readonly(account.folder or "INBOX")
            return bytes(box.fetch_body(uid))

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
            "body_mode": self.body_mode(),
            "attach_mode": self.attach_mode(),
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

    def _headers_since(
        self,
        account: MailAccount,
        since: datetime,
        limit: int,
        *,
        seen_verdicts: Collection[str] | None = None,
    ) -> list[MailHeader]:
        """Headers not examined yet. ``seen_verdicts`` narrows what "examined" means."""
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
            fresh = set(self._db.filter_unseen_mail(account.address, keys, verdicts=seen_verdicts))
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
        # The window. With offers waiting it is anchored to the oldest of them,
        # which is the tightest honest bound. With none, it is the last few days:
        # the gate used to be "no pending offers, no connection", and that gate
        # is set by a gesture — opening an offer's link from inside the app —
        # that someone who applies on LinkedIn never makes. The mailbox then
        # goes unread for weeks while confirmations arrive, and the only way to
        # see them is to run the historic sweep by hand.
        if pending:
            since = min(job.opened_at for job in pending) - CLOCK_SLACK
        else:
            if self._keep_warm_due():
                self.check_connection()
            since = _now() - timedelta(days=ttl)
        headers = self._headers_since(account, since, MAX_PER_RUN)
        if account.auth == "graph":
            keys = [h.key for h in headers]
            fresh = set(self._db.filter_unseen_mail(account.address, keys))
            headers = [h for h in headers if h.key in fresh]

        matched = ambiguous = imported = 0
        reader = _BodyReader(self, account, MAX_BODY_READS_PER_TICK)
        try:
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
                        self._queue_review(
                            account, header, result, pending, reader, allow_auto=True
                        )
                elif (evidence := extract_application(header)) is not None:
                    # "This records an application, and it is to nobody I am
                    # waiting on." Until now only the manual historic sweep ever
                    # asked that question, so an application to a company the
                    # archive has never seen stayed invisible until someone went
                    # looking. It costs no extra fetch: the header is in hand.
                    imported += 1
                    if not dry_run:
                        self._queue_import(account, header, evidence, reader, allow_auto=True)
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
            if not dry_run:
                self._backfill_roles(account, reader)
        finally:
            reader.close()
        self._set_state(STATE_OK)
        self._db.set_preference(mail_config.PREF_LAST_OK, str(int(self._clock())))
        return {
            "status": "done",
            "checked": len(headers),
            "matched": matched,
            "ambiguous": ambiguous,
            "imported": imported,
            "bodies_read": reader.read,
            "bodies_skipped": reader.skipped,
            "dry_run": dry_run,
        }

    def _backfill_roles(self, account: MailAccount, reader: _BodyReader) -> None:
        """Fill in the title on proposals that were queued before it was read.

        A queue built by an older version, or by a run whose budget ran out, is
        full of rows whose title was never fetched — and the title is what makes
        the question answerable. Without this, upgrading changes nothing about
        the six proposals already on screen: only messages arriving from now on
        would be legible, which is the wrong half.

        Left alone in ``ask`` and ``never``: there the button on the row is the
        whole point.
        """
        if self.body_mode() != mail_config.BODY_MODE_ALWAYS or not reader.supported:
            return
        for row in self._db.list_mail_review(account.address):
            if str(row.get("role") or "").strip():
                continue
            company = str(row.get("company") or "")
            found = reader.facts(str(row.get("mail_key") or ""), company=company)
            if found.role:
                self._db.set_mail_review_role(int(row["id"]), found.role)

    def _queue_review(
        self,
        account: MailAccount,
        header: MailHeader,
        result: Any,
        pending: list[PendingJob],
        reader: _BodyReader | None = None,
        *,
        allow_auto: bool = False,
    ) -> None:
        """Write a proposal to the queue, keeping only facts and never the message.

        ``allow_auto`` is False for the historic sweep and stays that way: over a
        year a company name and a date are not proof, and that screen exists
        precisely because the user is the one supplying the missing certainty.
        """
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
        facts = subject_facts(header.subject)
        company = facts.company[:80]
        if not company and candidates:
            company = by_id[candidates[0]].company
        # The title, which is what makes the question answerable. "Teoresi" with
        # six offers in the archive is six identical radio buttons; "Teoresi ·
        # AI Engineer" answers itself. Until now this branch never read a body
        # at all, not even in "always" — only the import branch did — so every
        # attach proposal in a real queue carried an empty role.
        role = facts.role[:120]
        if not role and reader is not None and self.body_mode() == mail_config.BODY_MODE_ALWAYS:
            role = reader.facts(header.key, company=company).role[:120]
        if role and candidates:
            titled = [(job_id, by_id[job_id].title) for job_id in candidates]
            if self._attach_if_certain(
                account, header, role, titled, result.rule, allow_auto=allow_auto
            ):
                return
        self._db.add_mail_review(
            account=account.address,
            mail_key=header.key,
            kind="attach",
            message_id=header.message_id,
            received_at=header.date.isoformat() if header.date else "",
            company=company,
            sender=_safe_sender(header),
            rule=result.rule,
            role=role,
            candidates=candidates,
        )

    def _queue_import(
        self,
        account: MailAccount,
        header: MailHeader,
        evidence: Any,
        reader: _BodyReader | None = None,
        *,
        allow_auto: bool = False,
    ) -> None:
        """Queue "you applied to X on that day, and X is not in the archive".

        Still a proposal, never an automatic write. The evidence is a company
        name and a date, which is enough to record that it happened and not
        enough to decide what it was — so the user sees it before anything is
        created. Where the company IS in the archive with open offers, those come
        along as candidates: attaching to the real posting beats a stub, and the
        stub can never be improved into one (its description stays empty, so a
        score would be computed on a text it does not contain).
        """
        company = str(getattr(evidence, "company", "") or "")
        role = str(getattr(evidence, "role", "") or "")
        # Indeed names the JOB in its subject and the employer only in the body,
        # so for those the dedup below has nothing to look up until the body has
        # been read. Reading it first is therefore not an optimisation, it is
        # the order the data forces.
        if not company and reader is not None and self.body_mode() != mail_config.BODY_MODE_NEVER:
            found = reader.facts(header.key, role=role)
            company, role = found.company[:80], (found.role or role)[:120]
        if not company:
            # Nothing to file it under. A stub with an empty company collides in
            # ``add_application_from_mail``, whose hash is company + day: two of
            # them on one day would silently become one.
            self._db.record_mail_seen(
                account=account.address,
                mail_key=header.key,
                verdict="no_match",
                message_id=header.message_id,
                received_at=header.date.isoformat() if header.date else "",
                matched_rule="employer_unreadable",
            )
            return

        day = evidence.sent_at.date().isoformat()
        if self._db.applications_near(company, day):
            # Already recorded, by hand or by an earlier sweep. Nothing to ask.
            self._db.record_mail_seen(
                account=account.address,
                mail_key=header.key,
                verdict="already_applied",
                message_id=header.message_id,
                received_at=header.date.isoformat() if header.date else "",
                matched_rule=evidence.rule,
                # Settles the message for good, so it must beat a no_match the
                # sweep may have written before the import question existed.
                overwrite=True,
            )
            return
        # In "always" the title is read here, once, while the sweep is already
        # talking to the server. A failure is not an error: the application is
        # recorded titleless, which is what "never" produces anyway.
        if not role and reader is not None and self.body_mode() == mail_config.BODY_MODE_ALWAYS:
            role = reader.facts(header.key, company=company).role[:120]
        candidates = self._db.open_offers_from(company)
        if role and candidates:
            titles = {row["id"]: str(row["titolo"] or "") for row in self._db.list_jobs(limit=2000)}
            titled = [(job_id, titles.get(job_id, "")) for job_id in candidates]
            if self._attach_if_certain(
                account, header, role, titled, evidence.rule, allow_auto=allow_auto
            ):
                return
        self._db.add_mail_review(
            account=account.address,
            mail_key=header.key,
            kind="import",
            message_id=header.message_id,
            received_at=header.date.isoformat() if header.date else "",
            company=company,
            sender=_safe_sender(header),
            rule=evidence.rule,
            role=role,
            candidates=candidates,
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
            # No early return on an empty archive any more. It used to make
            # sense — with nothing to attach to there was nothing to ask — but
            # the import path does not need candidates at all: it reads what the
            # message says happened. Returning here left someone who connects a
            # mailbox before running a scan with no way to recover anything.

            # Only a DECISION closes a message for this sweep. The routine one
            # files everything it walks past as no_match, meaning "confirms none
            # of the offers I am waiting for" — an answer to a question this
            # sweep is not asking. Inheriting that filter let the routine check
            # quietly eat the recovery: on a real mailbox 673 messages had been
            # written off before the import existed, and a 365-day recovery
            # could not see one of them. Re-reading costs one IMAP fetch, and
            # both queues are keyed by message, so nothing is proposed twice.
            headers = self._headers_since(
                account, since, MAX_HISTORIC, seen_verdicts=mail_config.DECIDED_VERDICTS
            )
            truncated = len(headers) >= MAX_HISTORIC
            found = 0
            imports = 0
            reader = _BodyReader(self, account, MAX_BODY_READS_PER_SWEEP)
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
                        self._queue_review(
                            account, header, _as_proposal(result), candidates, reader
                        )
                elif (evidence := extract_application(header)) is not None:
                    imports += 1
                    if not dry_run:
                        self._queue_import(account, header, evidence, reader)
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
            reader.close()
            if not dry_run:
                self._db.set_preference(mail_config.PREF_RECOVERY_DONE, "1")
            yield {
                "status": "complete",
                "proposals": found,
                "imports": imports,
                "checked": len(headers),
                "days": window,
                "dry_run": dry_run,
                "bodies_read": reader.read,
                "bodies_skipped": reader.skipped,
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
            # Allowed for BOTH kinds. An "attach" proposal only means the archive
            # holds offers from that employer — not that one of them is the one
            # applied for. Measured on a real queue: of 53 such proposals, the
            # title read from the body matched an archive offer 15 times; the
            # other 38 were applications to roles the archive never collected
            # (Teoresi's "AI Engineer" against six unrelated Teoresi postings).
            # With create refused here, the only answers on offer were attach to
            # the wrong offer or dismiss and lose the application — so the app
            # forced a false record or no record at all.
            if not row:
                refused.append(review_id)
                continue
            new_id = self._db.add_application_from_mail(
                company=str(row.get("company") or ""),
                applied_at=str(row.get("received_at") or ""),
                message_id=str(row.get("message_id") or ""),
                rule="import",
                role=str(row.get("role") or ""),
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
