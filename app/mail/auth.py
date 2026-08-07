"""Getting a Microsoft token, and keeping it alive.

Outlook.com stopped accepting a password over IMAP on 16/09/2024, app passwords
included, so a Microsoft mailbox needs OAuth whichever way it is read. Two scopes
are offered because the choice is not ours to make:

* ``Mail.Read`` on Microsoft Graph — **read-only, enforced by the server**. This
  is what to ask for whenever the registration allows it.
* ``IMAP.AccessAsUser.All`` — the only delegated IMAP scope Microsoft publishes,
  and it grants write access this app never uses. It exists here because most
  registrations that people can actually get their hands on are IMAP-scoped.

Why that matters: since June 2024 an app registration must live in a directory,
and a personal Microsoft account has none — the portal answers 401 and the
"create a tenant" button is disabled. So a user with a personal Outlook cannot
register an app at all without a paid Azure account. No ``client_id`` is shipped
with this app: the field is theirs to fill, and the UI says plainly where one
comes from instead of leaving them in front of an empty box.

The device-code flow needs no redirect URI registered anywhere: the app shows a
short code, the user types it on Microsoft's own page, and the consent screen is
Microsoft's. The whole consent step therefore lives inside this app and is
versioned with it.
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
SCOPE_GRAPH = "offline_access https://graph.microsoft.com/Mail.Read"
#: Wider than this app needs, and the only one IMAP has. Used only when the
#: registration in hand cannot ask for the read-only one.
SCOPE_IMAP = "offline_access https://outlook.office.com/IMAP.AccessAsUser.All"

#: ``auth`` value -> scope. Keys match ``MailAccount.auth``.
SCOPES = {"graph": SCOPE_GRAPH, "imap_oauth": SCOPE_IMAP}

_TIMEOUT = 15.0


def scope_for(auth: str) -> str:
    return SCOPES.get(auth, SCOPE_GRAPH)


def xoauth2_string(address: str, access_token: str) -> bytes:
    """The SASL blob IMAP wants in place of a password.

    ``imaplib`` base64-encodes whatever the callback returns, so this is the raw
    form: ``user=<addr>\\x01auth=Bearer <token>\\x01\\x01``.
    """
    return f"user={address}\x01auth=Bearer {access_token}\x01\x01".encode()


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


def begin_device_code(client_id: str, scope: str = SCOPE_GRAPH) -> DeviceCodeStart:
    payload = _post(DEVICE_CODE_URL, {"client_id": client_id, "scope": scope})
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


def refresh_access_token(
    client_id: str, refresh_token: str, scope: str = SCOPE_GRAPH
) -> TokenBundle:
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
            "scope": scope,
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
