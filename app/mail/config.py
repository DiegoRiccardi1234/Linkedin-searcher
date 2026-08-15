"""Which mailbox to read, how to reach it, and how to prove who you are.

Two ways in, chosen by the address's domain and overridable by hand:

* ``password`` — plain IMAP with an app password. Works for Gmail, Yahoo,
  iCloud, Libero, Virgilio, Aruba, Tiscali and any corporate server that still
  allows it.
* ``graph`` — Microsoft accounts. Outlook.com closed IMAP basic authentication
  on 16/09/2024, app passwords included, so a password there is not a supported
  option at all. The remaining choice is between IMAP with an OAuth token and
  Microsoft Graph, and Graph wins on one point that matters: its ``Mail.Read``
  scope is read-only, while the only delegated IMAP scope Microsoft publishes
  (``IMAP.AccessAsUser.All``) also grants write access this app would never use.
  Asking for a permission you do not need is not a detail when it is somebody
  else's mailbox.

Host presets were read off each provider's own configuration page, not off a
list — the same rule this project already applies to provider quotas. An unknown
domain gets ``imap.<domain>`` as a starting point, always editable, because
corporate servers are named whatever their admin decided.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.config import LOCAL_SECRETS_FILE, _load_optional_json

#: ``password`` — plain IMAP with an app password.
#: ``graph`` — Microsoft Graph with the read-only ``Mail.Read`` scope. Preferred
#: whenever the registration in hand can ask for it.
#: ``imap_oauth`` — IMAP with an OAuth token, for registrations that only carry
#: the IMAP scope. Same mailbox, wider permission than this app wants.
MailAuth = Literal["password", "graph", "imap_oauth"]

IMAP_SSL_PORT = 993

#: Microsoft consumer domains. These never reach IMAP: see the module docstring.
_MICROSOFT_DOMAINS = frozenset(
    {"outlook.com", "hotmail.com", "hotmail.it", "live.com", "live.it", "msn.com", "outlook.it"}
)

#: Verified on each provider's official configuration page (08/2026).
_IMAP_HOSTS: dict[str, str] = {
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
    "yahoo.com": "imap.mail.yahoo.com",
    "yahoo.it": "imap.mail.yahoo.com",
    "icloud.com": "imap.mail.me.com",
    "me.com": "imap.mail.me.com",
    "libero.it": "imapmail.libero.it",
    "virgilio.it": "in.virgilio.it",
    "aruba.it": "imaps.aruba.it",
    "tiscali.it": "imap.tiscali.it",
}

#: Keys in ``local_secrets.json``. The app password and the refresh token are
#: both "the thing that opens the mailbox", so they share one slot: an account
#: is one or the other, never both.
SECRET_KEY = "mail_secret"
CLIENT_ID_KEY = "mail_client_id"

#: Preference keys. Deliberately NOT under the ``_WRITABLE_PREFIX`` allowlist of
#: ``POST /api/preferences``: that endpoint is unauthenticated, and letting it
#: rewrite the IMAP host would let a stray request point the mailbox somewhere
#: else. They are written only by ``POST /api/mail/config``.
PREF_ADDRESS = "mailwatch_address"
PREF_AUTH = "mailwatch_auth"
PREF_HOST = "mailwatch_host"
PREF_PORT = "mailwatch_port"
PREF_FOLDER = "mailwatch_folder"
PREF_ENABLED = "mailwatch_enabled"
PREF_INTERVAL = "mailwatch_interval_minutes"
PREF_STATE = "mailwatch_state"
PREF_LAST_RUN = "mailwatch_last_run_ts"
PREF_LAST_OK = "mailwatch_last_ok_connect_ts"
PREF_LAST_ERROR = "mailwatch_last_error"
PREF_PENDING_DAYS = "mailwatch_pending_days"
PREF_RECOVERY_DONE = "mailwatch_recovery_done"
PREF_BODY_MODE = "mailwatch_body_mode"

DEFAULT_INTERVAL_MINUTES = 15
DEFAULT_PENDING_DAYS = 14

#: Verdicts in ``mail_seen`` that record a DECISION rather than bookkeeping.
#:
#: The routine sweep files every message it walks past, and its ``no_match``
#: means only "this confirms none of the offers I am waiting for". The recovery
#: asks a different question — "does this record an application at all?" — which
#: none of those rows ever answered, so it must be allowed to re-read them.
#: Measured on a real mailbox: 673 messages had been written off by the sweep
#: before the import existed, and a 365-day recovery could not see one of them.
#:
#: These five are different: each one means a human, or the confirmed match that
#: stands in for one, has already settled the message. Re-proposing those is the
#: bug the queue was built to end — a dismissed proposal coming back forever.
DECIDED_VERDICTS = frozenset({"applied", "imported", "dismissed", "already_applied", "match"})

#: Whether the app may download the BODY of a message, and on whose initiative.
#:
#: The subject of a confirmation names the employer and never the role, so the
#: job title only exists in the body — and downloading a body is precisely what
#: this package promised not to do. Rather than quietly redefine the promise,
#: the promise becomes a setting with three honest positions:
#:
#: * ``never``   — no command that fetches a body is ever issued. The original
#:                 guarantee, unchanged, and still asserted on the literal IMAP
#:                 command by a test.
#: * ``ask``     — the default. Nothing is downloaded until you press the button
#:                 on one specific queued message, and only that message.
#: * ``always``  — every message that has already passed the confirmation gate
#:                 gets its body read, so imported applications arrive titled.
#:
#: In all three the body is parsed in memory and never stored, never logged and
#: never sent to a model.
BODY_MODE_NEVER = "never"
BODY_MODE_ASK = "ask"
BODY_MODE_ALWAYS = "always"
BODY_MODES = (BODY_MODE_NEVER, BODY_MODE_ASK, BODY_MODE_ALWAYS)
DEFAULT_BODY_MODE = BODY_MODE_ASK


def domain_of(address: str) -> str:
    return str(address or "").strip().lower().rpartition("@")[2]


#: Microsoft's IMAP endpoint. Only reachable with an OAuth token since 2024.
MICROSOFT_IMAP_HOST = "outlook.office365.com"


def is_microsoft(address: str) -> bool:
    return domain_of(address) in _MICROSOFT_DOMAINS


def imap_host_for(address: str) -> str:
    """The IMAP server for this address, Microsoft included."""
    domain = domain_of(address)
    if domain in _MICROSOFT_DOMAINS:
        return MICROSOFT_IMAP_HOST
    return _IMAP_HOSTS.get(domain, f"imap.{domain}" if domain else "")


def default_host_for(address: str) -> tuple[MailAuth, str, int]:
    """``(auth, host, port)`` to start from for this address.

    Microsoft defaults to Graph rather than IMAP: both need OAuth, and only Graph
    can ask for a read-only permission.
    """
    if is_microsoft(address):
        return "graph", "", 0
    return "password", imap_host_for(address), IMAP_SSL_PORT


@dataclass(frozen=True, repr=False)
class MailAccount:
    """A mailbox this app may read, and nothing more than read.

    ``__repr__`` is overridden rather than left to the dataclass: a frozen
    dataclass prints every field, and this one would print the credential into
    any traceback that reaches the log file.
    """

    address: str
    auth: MailAuth = "password"
    host: str = ""
    port: int = IMAP_SSL_PORT
    folder: str = "INBOX"
    #: App password, or OAuth refresh token. Never leaves this process.
    secret: str = ""
    #: Public identifier of the OAuth client. Not a credential.
    client_id: str = ""

    def __repr__(self) -> str:
        return (
            f"MailAccount(address={self.address!r}, auth={self.auth!r}, "
            f"host={self.host!r}, folder={self.folder!r})"
        )

    @property
    def uses_oauth(self) -> bool:
        return self.auth in ("graph", "imap_oauth")

    @property
    def configured(self) -> bool:
        if not self.address or not self.secret:
            return False
        if self.auth == "graph":
            return bool(self.client_id)
        if self.auth == "imap_oauth":
            return bool(self.client_id and self.host)
        return bool(self.host)


def load_account(db: Any, data_dir: Path) -> MailAccount | None:
    """The saved mailbox, or None when none has been connected."""
    address = str(db.get_preference(PREF_ADDRESS, "") or "").strip()
    if not address:
        return None
    secrets = _load_optional_json(data_dir / LOCAL_SECRETS_FILE)
    auth_raw = str(db.get_preference(PREF_AUTH, "") or "").strip()
    fallback_auth, fallback_host, fallback_port = default_host_for(address)
    auth: MailAuth = (
        auth_raw if auth_raw in ("password", "graph", "imap_oauth") else fallback_auth  # type: ignore[assignment]
    )
    try:
        port = int(str(db.get_preference(PREF_PORT, "") or fallback_port))
    except (TypeError, ValueError):
        port = fallback_port or IMAP_SSL_PORT
    return MailAccount(
        address=address,
        auth=auth,
        host=str(db.get_preference(PREF_HOST, "") or fallback_host),
        port=port,
        folder=str(db.get_preference(PREF_FOLDER, "") or "INBOX"),
        secret=str(secrets.get(SECRET_KEY) or ""),
        client_id=str(secrets.get(CLIENT_ID_KEY) or ""),
    )


def save_account(
    db: Any,
    data_dir: Path,
    *,
    address: str,
    auth: MailAuth,
    host: str = "",
    port: int = IMAP_SSL_PORT,
    folder: str = "INBOX",
    secret: str | None = None,
    client_id: str | None = None,
) -> None:
    """Store the mailbox settings, keeping the secret out of the database.

    ``secret``/``client_id`` follow the three-state convention the provider keys
    already use: ``None`` leaves what is stored alone, a value replaces it, and
    an empty string removes it.
    """
    db.set_preference(PREF_ADDRESS, address.strip())
    db.set_preference(PREF_AUTH, auth)
    db.set_preference(PREF_HOST, host.strip())
    db.set_preference(PREF_PORT, str(port))
    db.set_preference(PREF_FOLDER, folder.strip() or "INBOX")
    write_secrets(data_dir, secret=secret, client_id=client_id)


def write_secrets(
    data_dir: Path, *, secret: str | None = None, client_id: str | None = None
) -> None:
    """Merge the credential file, never rewriting it from scratch.

    Called on every token refresh too: Microsoft rotates the refresh token, and a
    rotation that is not persisted works fine until the day the old one expires,
    which is the kind of failure that arrives months later with no clue attached.
    """
    import json

    path = data_dir / LOCAL_SECRETS_FILE
    current = _load_optional_json(path)
    for key, value in ((SECRET_KEY, secret), (CLIENT_ID_KEY, client_id)):
        if value is None:
            continue
        if value:
            current[key] = value
        else:
            current.pop(key, None)
    data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def forget_account(db: Any, data_dir: Path) -> None:
    """Disconnect: settings cleared and, above all, the credential removed."""
    for key in (
        PREF_ADDRESS,
        PREF_AUTH,
        PREF_HOST,
        PREF_PORT,
        PREF_FOLDER,
        PREF_STATE,
        PREF_LAST_ERROR,
        PREF_LAST_OK,
        PREF_RECOVERY_DONE,
    ):
        db.set_preference(key, "")
    write_secrets(data_dir, secret="", client_id="")
