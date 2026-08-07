"""What can go wrong reading a mailbox, split by what the user must do about it.

The distinction is the whole point: a network blip must be retried silently, an
expired consent must stop the retrying and ask for a click, and a wrong password
must say so instead of looking like an outage.
"""

from __future__ import annotations

import re


class MailError(Exception):
    """Base for everything this package raises."""


class MailConfigError(MailError):
    """The account is missing or malformed — nothing to connect to."""


class MailAuthError(MailError):
    """The server refused the credentials. A wrong or revoked app password."""


class MailReauthRequired(MailAuthError):
    """The OAuth grant is gone: the user has to connect the mailbox again.

    Microsoft expires a personal refresh token after roughly 90 days of
    inactivity, and revoking access from the account page does it immediately.
    Both arrive as ``invalid_grant``, and both are permanent until a human acts —
    so this must stop the retry loop rather than feed it.
    """


class MailAuthPending(MailAuthError):
    """The device-code flow is still waiting for the user to approve it."""


class MailTransientError(MailError):
    """Network, TLS or server hiccup. Try again on the next tick."""


# An address is enough to identify a person, and a token is a credential. Both
# end up in exception text, and exception text ends up in the log file — which
# the settings screen offers to open, and which a user may well send to someone
# for help.
_TOKEN_LIKE_RE = re.compile(r"[A-Za-z0-9_\-\.]{24,}")
_ADDRESS_RE = re.compile(r"[\w\.\-\+]+@[\w\.\-]+")


def safe_error(exc: BaseException, limit: int = 160) -> str:
    """The message with addresses and token-shaped strings taken out."""
    text = str(exc)[:limit]
    text = _ADDRESS_RE.sub("<address>", text)
    return _TOKEN_LIKE_RE.sub("<redacted>", text)
