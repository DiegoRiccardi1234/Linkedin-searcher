"""Getting a read-only Microsoft token, and keeping it alive.

Outlook.com stopped accepting a password over IMAP on 16/09/2024, app passwords
included, so a Microsoft mailbox needs OAuth whichever way it is read. Given
that, this asks for ``Mail.Read`` on Microsoft Graph rather than the IMAP scope:
``IMAP.AccessAsUser.All`` is the only delegated IMAP scope Microsoft publishes
and it grants write access too, which this app has no use for and no business
holding on someone else's mailbox.

The device-code flow is used because it needs no redirect URI registered
anywhere: the app shows a short code, the user types it on Microsoft's own page,
and the consent screen is Microsoft's, not ours. That also means the whole
consent step lives inside this app and is versioned with it.

``client_id`` identifies the registered application. It is not a secret — public
clients are not issued one — and it is configurable so a user whose employer
blocks third-party apps can point this at their own registration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from app.mail.errors import MailAuthError, MailAuthPending, MailReauthRequired, safe_error

AUTHORITY = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
DEVICE_CODE_URL = f"{AUTHORITY}/devicecode"
TOKEN_URL = f"{AUTHORITY}/token"

#: Read, and only read. ``offline_access`` is what makes the grant survive the
#: hour-long access token.
SCOPE = "offline_access https://graph.microsoft.com/Mail.Read"

_TIMEOUT = 15.0


@dataclass(frozen=True)
class DeviceCodeStart:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


@dataclass(frozen=True, repr=False)
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_in: int

    def __repr__(self) -> str:  # tokens must not reach a traceback
        return f"TokenBundle(expires_in={self.expires_in})"


def _post(url: str, data: dict[str, str]) -> dict[str, Any]:
    try:
        response = requests.post(url, data=data, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise MailAuthError(safe_error(exc)) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise MailAuthError(f"unreadable response ({response.status_code})") from exc
    if response.ok:
        return dict(payload)
    error = str(payload.get("error") or "")
    if error == "authorization_pending":
        raise MailAuthPending("waiting for approval")
    if error in ("invalid_grant", "expired_token", "consent_required"):
        # Permanent until a human acts: a refresh token expires after ~90 days
        # of inactivity, and revoking access does it at once. Retrying is not a
        # remedy, so this has to stop the loop rather than feed it.
        raise MailReauthRequired(error)
    raise MailAuthError(f"{error or response.status_code}")


def begin_device_code(client_id: str) -> DeviceCodeStart:
    payload = _post(DEVICE_CODE_URL, {"client_id": client_id, "scope": SCOPE})
    return DeviceCodeStart(
        device_code=str(payload.get("device_code") or ""),
        user_code=str(payload.get("user_code") or ""),
        verification_uri=str(payload.get("verification_uri") or ""),
        expires_in=int(payload.get("expires_in") or 900),
        interval=int(payload.get("interval") or 5),
    )


def poll_device_code(client_id: str, device_code: str) -> TokenBundle:
    """One poll. Raises :class:`MailAuthPending` while the user has not acted."""
    payload = _post(
        TOKEN_URL,
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client_id,
            "device_code": device_code,
        },
    )
    return _bundle(payload)


def refresh_access_token(client_id: str, refresh_token: str) -> TokenBundle:
    """A fresh access token, and possibly a NEW refresh token.

    Microsoft rotates the refresh token, and the caller MUST persist the one
    that comes back when it differs. Skipping that works perfectly until the old
    token expires, which is a failure that shows up months later, on a machine
    nobody touched, with nothing in the logs to connect it to.
    """
    payload = _post(
        TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "scope": SCOPE,
        },
    )
    return _bundle(payload, fallback_refresh=refresh_token)


def _bundle(payload: dict[str, Any], fallback_refresh: str = "") -> TokenBundle:
    access = str(payload.get("access_token") or "")
    if not access:
        raise MailAuthError("no access token in response")
    return TokenBundle(
        access_token=access,
        refresh_token=str(payload.get("refresh_token") or fallback_refresh),
        expires_in=int(payload.get("expires_in") or 3600),
    )
