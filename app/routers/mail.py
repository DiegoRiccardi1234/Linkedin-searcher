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
from app.mail.auth import begin_device_code, poll_device_code
from app.mail.errors import MailAuthPending, MailError, safe_error
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
        address = payload.address.strip()
        if "@" not in address:
            raise HTTPException(status_code=400, detail="invalid_address")
        auth, default_host, default_port = mail_config.default_host_for(address)
        if payload.auth in ("password", "graph"):
            auth = payload.auth  # type: ignore[assignment]
        host = (payload.host or default_host).strip()
        if auth == "password" and not host:
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
            container.db.purge_mail_seen(account.address)
        mail_config.forget_account(container.db, container.settings.data_dir)
        container.db.set_preference(mail_config.PREF_ENABLED, "0")
        container.mailwatch.review.clear()
        return {"ok": True}

    @router.post("/api/mail/oauth/start")
    def oauth_start(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        rate_limit.check(request, bucket="mail_oauth", limit=10, window_seconds=60)
        client_id = str((payload or {}).get("client_id") or "").strip() or _client_id(container)
        if not client_id:
            raise HTTPException(status_code=400, detail="client_id_required")
        try:
            start = begin_device_code(client_id)
        except MailError as exc:
            raise HTTPException(status_code=502, detail=safe_error(exc)) from exc
        flow["device_code"] = start.device_code
        flow["client_id"] = client_id
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
        container.db.set_preference(mail_config.PREF_AUTH, "graph")
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
    def mail_recovery(request: Request, days: int = Query(default=90, ge=1, le=365)):  # type: ignore[no-untyped-def]
        """Look for applications sent before the mailbox was connected.

        Streams progress like a scan, and applies nothing: over three months the
        only link between a message and an offer is the company name, which is a
        reason to ask rather than to decide.
        """
        rate_limit.check(request, bucket="mail_recovery", limit=2, window_seconds=600)
        account = container.mailwatch.account()
        if not account or not account.configured:
            raise HTTPException(status_code=412, detail="mail_unconfigured")
        if container.mail_control.running:
            raise HTTPException(status_code=409, detail="mail_busy")

        def event_generator() -> Any:
            import json

            try:
                for event in container.mailwatch.run_historic(days):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as exc:  # the stream must always close cleanly
                yield f"data: {json.dumps({'status': 'error', 'error': safe_error(exc)})}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.get("/api/mail/review")
    def mail_review() -> dict[str, Any]:
        return {
            "items": [
                {
                    "job_id": item.job_id,
                    "job_title": item.job_title,
                    "company": item.company,
                    "subject": item.subject,
                    "from_domain": item.from_domain,
                    "received_at": item.received_at,
                    "rule": item.rule,
                    "candidates": list(item.candidates),
                }
                for item in container.mailwatch.review
            ]
        }

    @router.post("/api/mail/review/resolve")
    def mail_review_resolve(payload: MailReviewResolveRequest) -> dict[str, Any]:
        result = container.mailwatch.resolve_review(payload.apply, payload.dismiss)
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
