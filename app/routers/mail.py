"""Connecting a mailbox, testing it, and asking it about applications.

No endpoint ever returns the credential — ``configured: bool`` is the whole
answer, the same contract the provider keys already use.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app import rate_limit
from app.mail import config as mail_config
from app.mail.auth import begin_device_code, poll_device_code, scope_for
from app.mail.errors import MailAuthPending, MailError, safe_error
from app.mail.matcher import rank_candidates
from app.models import MailConfigRequest, MailReviewResolveRequest

if TYPE_CHECKING:
    from app.container import AppContainer

#: Public identifier of the registered desktop application. Not a secret —
#: Microsoft issues none for a public client — and overridable per install so a
#: user whose employer blocks third-party apps can point this at their own.
DEFAULT_CLIENT_ID = ""


def build_router(container: AppContainer) -> APIRouter:
    router = APIRouter()
    # The device-code flow is a two-step conversation, and the code only lives
    # for the few minutes the user takes to approve it. Process memory is the
    # right place: writing it down would outlive its usefulness.
    flow: dict[str, str] = {}

    @router.get("/api/mail/status")
    def mail_status() -> dict[str, Any]:
        return container.mailwatch.status()

    @router.post("/api/mail/config")
    def mail_configure(payload: MailConfigRequest) -> dict[str, Any]:
        # Written before the address is validated, because it is not part of the
        # account: it is a decision about what this app is allowed to read, and
        # someone must be able to set it to "never" BEFORE connecting anything.
        # Behind the check it was silently discarded along with a 400 whenever
        # the address field happened to be empty.
        if payload.body_mode in mail_config.BODY_MODES:
            container.db.set_preference(mail_config.PREF_BODY_MODE, payload.body_mode)
        if payload.attach_mode in mail_config.ATTACH_MODES:
            container.db.set_preference(mail_config.PREF_ATTACH_MODE, payload.attach_mode)

        address = payload.address.strip()
        if "@" not in address:
            raise HTTPException(status_code=400, detail="invalid_address")
        auth, default_host, default_port = mail_config.default_host_for(address)
        if payload.auth in ("password", "graph", "imap_oauth"):
            auth = payload.auth  # type: ignore[assignment]
        host = (payload.host or default_host).strip()
        if auth == "imap_oauth" and not host:
            # Microsoft has no host in the Graph default, so fill it in here
            # rather than making the user know outlook.office365.com by heart.
            host = mail_config.imap_host_for(address)
        if auth in ("password", "imap_oauth") and not host:
            raise HTTPException(status_code=400, detail="host_required")
        mail_config.save_account(
            container.db,
            container.settings.data_dir,
            address=address,
            auth=auth,
            host=host,
            port=payload.port or default_port or mail_config.IMAP_SSL_PORT,
            folder=payload.folder or "INBOX",
            secret=payload.secret,
            client_id=payload.client_id,
        )
        if payload.enabled is not None:
            container.db.set_preference(mail_config.PREF_ENABLED, "1" if payload.enabled else "0")
        if payload.interval_minutes is not None:
            container.db.set_preference(mail_config.PREF_INTERVAL, str(payload.interval_minutes))
        return {"ok": True, "status": container.mailwatch.status()}

    @router.post("/api/mail/test")
    def mail_test(request: Request) -> dict[str, Any]:
        rate_limit.check(request, bucket="mail_test", limit=5, window_seconds=60)
        return container.mailwatch.check_connection()

    @router.post("/api/mail/disconnect")
    def mail_disconnect() -> dict[str, Any]:
        account = container.mailwatch.account()
        if account:
            # Takes the examined-messages log AND the pending queue: proposals
            # about a mailbox that is no longer attached cannot be answered.
            container.db.purge_mail_seen(account.address)
        mail_config.forget_account(container.db, container.settings.data_dir)
        container.db.set_preference(mail_config.PREF_ENABLED, "0")
        return {"ok": True}

    @router.post("/api/mail/oauth/start")
    def oauth_start(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        rate_limit.check(request, bucket="mail_oauth", limit=10, window_seconds=60)
        client_id = str((payload or {}).get("client_id") or "").strip() or _client_id(container)
        if not client_id:
            # Nothing is shipped as a default: since June 2024 a personal
            # Microsoft account cannot register an application, so any id in
            # this field belongs to a registration the user actually has.
            raise HTTPException(status_code=400, detail="client_id_required")
        account = container.mailwatch.account()
        auth = str((payload or {}).get("auth") or (account.auth if account else "graph"))
        scope = scope_for(auth)
        try:
            start = begin_device_code(client_id, scope)
        except MailError as exc:
            raise HTTPException(status_code=502, detail=safe_error(exc)) from exc
        flow["device_code"] = start.device_code
        flow["client_id"] = client_id
        flow["auth"] = auth if auth in ("graph", "imap_oauth") else "graph"
        return {
            "user_code": start.user_code,
            "verification_uri": start.verification_uri,
            "expires_in": start.expires_in,
            "interval": start.interval,
        }

    @router.post("/api/mail/oauth/poll")
    def oauth_poll(request: Request) -> dict[str, Any]:
        rate_limit.check(request, bucket="mail_oauth", limit=60, window_seconds=60)
        if not flow.get("device_code"):
            raise HTTPException(status_code=409, detail="no_flow")
        try:
            bundle = poll_device_code(flow["client_id"], flow["device_code"])
        except MailAuthPending:
            return {"status": "pending"}
        except MailError as exc:
            flow.clear()
            return {"status": "failed", "detail": safe_error(exc)}
        mail_config.write_secrets(
            container.settings.data_dir,
            secret=bundle.refresh_token,
            client_id=flow["client_id"],
        )
        container.db.set_preference(mail_config.PREF_AUTH, flow.get("auth", "graph"))
        flow.clear()
        return {"status": "complete", "state": container.mailwatch.status()["state"]}

    @router.post("/api/mail/check", status_code=202)
    def mail_check(request: Request, dry_run: bool = Query(default=False)) -> dict[str, Any]:
        """Kick off a pass and return at once, like the scheduler's run-now."""
        rate_limit.check(request, bucket="mail_check", limit=6, window_seconds=60)
        account = container.mailwatch.account()
        if not account or not account.configured:
            raise HTTPException(status_code=412, detail="mail_unconfigured")
        if container.mail_control.running:
            raise HTTPException(status_code=409, detail="mail_busy")
        threading.Thread(
            target=container.mailwatch.run_once,
            kwargs={"dry_run": dry_run},
            daemon=True,
        ).start()
        return {"status": "started", "dry_run": dry_run}

    @router.post("/api/mail/dry-run")
    def mail_dry_run(request: Request) -> dict[str, Any]:
        """Run the rules and report what they WOULD have marked. Writes nothing.

        How well the recognition does is unknown until it meets a real mailbox,
        and the cost of it being wrong is a rewritten application history. So it
        gets to prove itself first, on the user's own mail, with the writing
        switched off.
        """
        rate_limit.check(request, bucket="mail_check", limit=6, window_seconds=60)
        account = container.mailwatch.account()
        if not account or not account.configured:
            raise HTTPException(status_code=412, detail="mail_unconfigured")
        return container.mailwatch.run_once(dry_run=True)

    @router.get("/api/mail/recovery/stream")
    def mail_recovery(  # type: ignore[no-untyped-def]
        request: Request,
        days: int = Query(default=90, ge=1, le=365),
        dry_run: bool = Query(default=False),
    ):
        """Look for applications sent before the mailbox was connected.

        Streams progress like a scan, and applies nothing: over three months the
        only link between a message and an offer is the company name, which is a
        reason to ask rather than to decide.

        ``dry_run`` reports the same counts and writes nothing, which is how you
        find out what a year's worth of mailbox costs before spending it — a
        message recorded as ``no_match`` is never looked at again.
        """
        # Six a run rather than two: a dry run at 90, 180 and 365 days is three
        # of them before the real sweep, and being rate-limited out of your own
        # measurement is a poor way to encourage measuring first.
        rate_limit.check(request, bucket="mail_recovery", limit=6, window_seconds=600)
        account = container.mailwatch.account()
        if not account or not account.configured:
            raise HTTPException(status_code=412, detail="mail_unconfigured")
        if container.mail_control.running:
            raise HTTPException(status_code=409, detail="mail_busy")

        def event_generator() -> Any:
            import json

            try:
                for event in container.mailwatch.run_historic(days, dry_run=dry_run):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as exc:  # the stream must always close cleanly
                yield f"data: {json.dumps({'status': 'error', 'error': safe_error(exc)})}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    def _review_row(item: dict[str, Any]) -> dict[str, Any]:
        """One queued proposal, with the answer the title points at.

        The suggestion is computed on every read rather than stored: press "get
        the job title" a minute after the row was queued and the suggestion
        appears, with no migration and no stale column. It is the same reason
        the candidates themselves are resolved at read time.
        """
        role = str(item.get("role") or "")
        titles: list[tuple[int, str]] = [
            (int(c["id"]), str(c.get("titolo") or "")) for c in item["candidates"]
        ]
        ordered, only = rank_candidates(role, titles)
        position = {job_id: i for i, job_id in enumerate(ordered)}
        candidates = [
            {
                "job_id": int(c["id"]),
                "titolo": c.get("titolo") or "",
                "azienda": c.get("azienda") or "",
            }
            for c in item["candidates"]
        ]
        candidates.sort(key=lambda c: position.get(int(c["job_id"]), len(position)))
        return {
            "review_id": int(item["id"]),
            "kind": item["kind"],
            "company": item.get("company") or "",
            "role": role,
            "sender": item.get("sender") or "",
            "received_at": item.get("received_at") or "",
            "rule": item.get("rule") or "",
            "candidates": candidates,
            # Set only when the title leaves exactly one answer. With a known
            # role and no candidate matching it, the honest suggestion is that
            # this is an application the archive never held: on a real queue
            # that was 38 of 53.
            "suggested_job_id": only,
            "suggestion": "attach" if only else ("create" if role and candidates else ""),
        }

    @router.get("/api/mail/review")
    def mail_review() -> dict[str, Any]:
        """The pending queue. Read from the table, so a restart does not empty it.

        No subject and no sender address: an employer name, a date, the rule that
        fired, and the offers it could be about.
        """
        items = container.mailwatch.review_items()
        return {
            "items": [_review_row(item) for item in items],
            "counts": {
                "attach": sum(1 for i in items if i["kind"] == "attach"),
                "import": sum(1 for i in items if i["kind"] == "import"),
            },
        }

    @router.post("/api/mail/review/{review_id}/role")
    def mail_review_role(review_id: int, request: Request) -> dict[str, Any]:
        """Read the job title out of THIS message's body, because you asked.

        The one action in this package that downloads a body, for one message,
        on an explicit press. Refused outright when the setting says never.
        """
        rate_limit.check(request, bucket="mail_check", limit=30, window_seconds=60)
        row = next(
            (i for i in container.mailwatch.review_items() if int(i["id"]) == review_id), None
        )
        if row is None:
            raise HTTPException(status_code=404, detail="not_found")
        if container.mailwatch.body_mode() == mail_config.BODY_MODE_NEVER:
            raise HTTPException(status_code=409, detail="body_reading_off")
        try:
            role = container.mailwatch.fetch_role(
                str(row["mail_key"]), str(row.get("company") or "")
            )
        except MailError as exc:
            raise HTTPException(status_code=502, detail=safe_error(exc)) from exc
        if role:
            container.db.set_mail_review_role(review_id, role)
        return {"ok": True, "review_id": review_id, "role": role}

    @router.post("/api/mail/review/resolve")
    def mail_review_resolve(payload: MailReviewResolveRequest) -> dict[str, Any]:
        """Answer queued proposals.

        Indexed by ``review_id``, not by ``job_id``: a proposal can name several
        offers, so "which one" is part of the answer and not something the server
        should pick. Nothing persisted used the old shape — the queue lived in
        memory — so there is no compatibility to keep.
        """
        result = container.mailwatch.resolve_review(payload.attach, payload.dismiss, payload.create)
        return {"ok": True, **result}

    @router.post("/api/mail/undo/{job_id}")
    def mail_undo(job_id: int) -> dict[str, Any]:
        """Take back a marking the mailbox made. Manual ones are left alone."""
        if not container.db.undo_mail_confirmation(job_id):
            raise HTTPException(status_code=400, detail="not_auto_confirmed")
        return {"ok": True}

    return router


def _client_id(container: AppContainer) -> str:
    account = container.mailwatch.account()
    return (account.client_id if account else "") or DEFAULT_CLIENT_ID
