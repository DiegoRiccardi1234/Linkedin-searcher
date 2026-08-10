"""Reading headers over IMAP, with the mailbox physically protected.

Three choices here are load-bearing, and each one is asserted by a test rather
than trusted:

* ``SELECT`` is issued read-only, so the server refuses any change this code
  could attempt — including the ``\\Seen`` flag. An app that silently marks a
  mailbox as read is worse than one that does not work.
* Sweeping fetches HEADERS only, with ``BODY.PEEK``. A body is downloaded in
  exactly one place — :meth:`ImapMailbox.fetch_body`, never called from the
  sweep — and only when the user's ``body_mode`` setting allows it, because the
  job title exists nowhere else. Even then the message is parsed in memory and
  never stored, never logged and never sent to a model.
* ``smtplib`` is not imported anywhere in this package. Reading a mailbox and
  writing from it are different powers, and the second one is not needed.

``imaplib`` has no per-command timeout, only a socket one, so the caller is
responsible for an overall budget.
"""

from __future__ import annotations

import email
import imaplib
import re
import ssl
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, date, datetime
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any

from app.log import get_logger
from app.mail.auth import xoauth2_string
from app.mail.config import MailAccount
from app.mail.errors import MailAuthError, MailConfigError, MailTransientError, safe_error
from app.mail.matcher import MailHeader

log = get_logger(__name__)

#: Everything the matcher is allowed to see.
_HEADER_FIELDS = "FROM TO SUBJECT DATE MESSAGE-ID LIST-ID REPLY-TO"

#: Messages per FETCH command. ``imaplib`` is one round-trip per command, and a
#: 365-day sweep of a real job-hunting mailbox is 1.550 messages — one command
#: each turns a sweep into a coffee break. Measured against outlook.office365.com
#: at fifty per command: 1.554 headers in 16 seconds, 98 a second.
_FETCH_BATCH = 50

#: A UID FETCH response always carries the UID back, but *where* is up to the
#: server. Outlook puts it in the element AFTER the payload — the literal reply
#: is ``(b'7913 (BODY[HEADER.FIELDS ...] {334}', b'Date: ...')`` followed by
#: ``b' UID 69662)'`` — while others put it in the prefix. Both are read.
#: Learned by pointing the batched reader at a real mailbox and getting zero
#: headers back while every test stayed green: the fake server had been written
#: to match the parser instead of the protocol.
_UID_RE = re.compile(rb"UID\s+(\d+)")

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def imap_date(day: date) -> str:
    """``SEARCH SINCE`` wants ``01-Aug-2026`` and nothing else."""
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


def _decode(raw: Any) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(str(raw))))
    except (UnicodeDecodeError, LookupError, ValueError):
        return str(raw)


class ImapMailbox:
    """One connection, opened by ``with``, always closed.

    ``imap_factory`` exists so the tests can drive a fake server and assert on
    the exact commands sent: "we never mark anything read" is a claim about the
    command string, so that is what gets checked.
    """

    def __init__(
        self,
        account: MailAccount,
        *,
        timeout: float = 20.0,
        imap_factory: Callable[..., Any] = imaplib.IMAP4_SSL,
        token_provider: Callable[[], str] | None = None,
    ) -> None:
        self._account = account
        self._timeout = timeout
        self._factory = imap_factory
        # Present when the server wants an OAuth token instead of a password.
        # Microsoft closed password sign-in on Outlook.com in 2024, so a
        # Microsoft mailbox reached over IMAP can only authenticate this way.
        self._token_provider = token_provider
        self._conn: Any = None
        self.uidvalidity = 0

    def __enter__(self) -> ImapMailbox:
        account = self._account
        if not account.host or not account.address:
            raise MailConfigError("mailbox not configured")
        if self._token_provider is None and not account.secret:
            raise MailConfigError("mailbox not configured")
        try:
            self._conn = self._factory(
                account.host,
                account.port,
                ssl_context=ssl.create_default_context(),
                timeout=self._timeout,
            )
            if self._token_provider is not None:
                blob = xoauth2_string(account.address, self._token_provider())
                # imaplib base64-encodes whatever the callback returns.
                self._conn.authenticate("XOAUTH2", lambda _challenge: blob)
            else:
                self._conn.login(account.address, account.secret)
        except imaplib.IMAP4.error as exc:
            # The server said no. A network problem raises OSError instead, and
            # the two must not be reported the same way: one needs a new app
            # password, the other needs nothing at all.
            raise MailAuthError(safe_error(exc)) from exc
        except (OSError, ssl.SSLError) as exc:
            raise MailTransientError(safe_error(exc)) from exc
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._conn is None:
            return
        try:
            self._conn.logout()
        except Exception:  # a failed logout must not mask the real error
            log.debug("IMAP logout failed")
        self._conn = None

    def select_readonly(self, folder: str = "INBOX") -> int:
        """Open the folder without the right to change anything in it."""
        typ, _data = self._conn.select(folder, readonly=True)
        if typ != "OK":
            raise MailTransientError(f"cannot open folder {folder!r}")
        typ, raw = self._conn.status(folder, "(UIDVALIDITY)")
        self.uidvalidity = _parse_uidvalidity(raw) if typ == "OK" else 0
        return self.uidvalidity

    def search_since(self, day: date) -> list[int]:
        typ, data = self._conn.uid("search", None, f"(SINCE {imap_date(day)})")
        if typ != "OK" or not data or data[0] is None:
            return []
        raw = data[0]
        parts = raw.split() if isinstance(raw, bytes) else str(raw).split()
        out: list[int] = []
        for part in parts:
            token = part.decode() if isinstance(part, bytes) else str(part)
            if token.isdigit():
                out.append(int(token))
        return out

    def fetch_headers(self, uids: Sequence[int]) -> Iterator[MailHeader]:
        """Headers only, in batches, and without marking anything as read."""
        for start in range(0, len(uids), _FETCH_BATCH):
            chunk = uids[start : start + _FETCH_BATCH]
            if not chunk:
                continue
            typ, data = self._conn.uid(
                "fetch",
                ",".join(str(uid) for uid in chunk),
                f"(BODY.PEEK[HEADER.FIELDS ({_HEADER_FIELDS})])",
            )
            if typ != "OK" or not data:
                continue
            yield from self._parse_fetch(data)

    def _parse_fetch(self, data: Sequence[Any]) -> Iterator[MailHeader]:
        """Turn one FETCH response into headers, wherever the server put the UID.

        The reply alternates ``(prefix, payload)`` tuples with bare bytes, and
        the UID may be in either. A payload is only emitted once its UID is
        known: pairing by position with the request would look right and file
        every header under the wrong message the first time a server answered
        out of order.
        """
        held: tuple[bytes, bytes] | None = None

        def _uid(raw: bytes) -> int | None:
            found = _UID_RE.search(raw)
            return int(found.group(1)) if found else None

        for item in data:
            if isinstance(item, tuple) and len(item) >= 2:
                if held is not None:
                    # Previous payload never got a trailing UID line: its own
                    # prefix is the only place left to look.
                    uid = _uid(held[0])
                    if uid is not None:
                        yield self._header(uid, held[1])
                    held = None
                prefix = item[0] if isinstance(item[0], bytes | bytearray) else b""
                if not isinstance(item[1], bytes | bytearray):
                    continue
                held = (bytes(prefix), bytes(item[1]))
                uid = _uid(held[0])
                if uid is not None:
                    yield self._header(uid, held[1])
                    held = None
            elif held is not None and isinstance(item, bytes | bytearray):
                uid = _uid(bytes(item))
                if uid is not None:
                    yield self._header(uid, held[1])
                    held = None
        if held is not None:
            uid = _uid(held[0])
            if uid is not None:
                yield self._header(uid, held[1])

    def fetch_body(self, uid: int) -> bytes:
        """The whole message, for the one case the user asked for it.

        Separate from :meth:`fetch_headers` and never called from it, so "does
        this code path download a body" stays a question you can answer by
        looking at the call sites. Still ``BODY.PEEK``: even when reading the
        body, the mailbox is not marked as read.
        """
        typ, data = self._conn.uid("fetch", str(uid), "(BODY.PEEK[])")
        if typ != "OK" or not data:
            return b""
        for item in data:
            if (
                isinstance(item, tuple)
                and len(item) >= 2
                and isinstance(item[1], bytes | bytearray)
            ):
                return bytes(item[1])
        return b""

    def _header(self, uid: int, payload: bytes) -> MailHeader:
        message = email.message_from_bytes(payload)
        return MailHeader(
            key=f"imap:{self.uidvalidity}:{uid}",
            subject=_decode(message.get("Subject")),
            from_addr=_decode(message.get("From")),
            from_name=_decode(message.get("From")),
            message_id=str(message.get("Message-ID") or "").strip(),
            date=_parse_date(message.get("Date")),
            list_id=str(message.get("List-Id") or "").strip(),
        )


def _parse_uidvalidity(raw: Any) -> int:
    text = ""
    if isinstance(raw, list) and raw:
        first = raw[0]
        text = first.decode() if isinstance(first, bytes) else str(first)
    elif raw:
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
    digits = "".join(ch for ch in text.partition("UIDVALIDITY")[2] if ch.isdigit())
    return int(digits) if digits else 0


def _parse_date(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(str(raw))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
