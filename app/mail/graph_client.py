"""Reading Microsoft mail through Graph, with the server enforcing read-only.

Same interface as the IMAP transport — it yields the same
:class:`~app.mail.matcher.MailHeader` — so nothing downstream knows or cares
which one answered.

Two things are better here than over IMAP, and both are properties of the
protocol rather than of this code: the ``Mail.Read`` scope makes writing
impossible at the server, and ``$select`` leaves the body out at the source, so
message text never crosses the network in the first place. Reading a message
through Graph also does not mark it as read; only an explicit write would, and
this token cannot perform one.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import requests

from app.mail.errors import MailAuthError, MailReauthRequired, MailTransientError, safe_error
from app.mail.matcher import MailHeader

GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages"

#: Everything the matcher needs, and not one field more. Body is not listed, so
#: the service never sends it.
_SELECT = "id,internetMessageId,receivedDateTime,subject,from"

_TIMEOUT = 20.0
_PAGE = 100


class GraphMailbox:
    """Microsoft mail over HTTPS. Same shape as :class:`ImapMailbox`."""

    def __init__(
        self,
        access_token: str,
        *,
        folder: str = "inbox",
        session: Any = None,
        timeout: float = _TIMEOUT,
    ) -> None:
        self._token = access_token
        self._folder = folder or "inbox"
        self._session = session or requests
        self._timeout = timeout

    def __enter__(self) -> GraphMailbox:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def select_readonly(self, folder: str = "inbox") -> int:
        self._folder = folder or "inbox"
        return 0  # no UIDVALIDITY here: Graph ids are stable on their own

    def fetch_since(self, since: datetime, limit: int = 300) -> Iterator[MailHeader]:
        """Headers of messages received after ``since``, newest first."""
        url: str | None = GRAPH_MESSAGES_URL.format(folder=self._folder)
        params: dict[str, Any] | None = {
            "$select": _SELECT,
            "$top": min(_PAGE, limit),
            "$orderby": "receivedDateTime desc",
            "$filter": f"receivedDateTime ge {_graph_time(since)}",
        }
        seen = 0
        while url and seen < limit:
            payload = self._get(url, params)
            params = None  # @odata.nextLink already carries the query
            for item in payload.get("value") or []:
                yield _to_header(item)
                seen += 1
                if seen >= limit:
                    return
            url = payload.get("@odata.nextLink")

    def _get(self, url: str, params: dict[str, Any] | None) -> dict[str, Any]:
        try:
            response = self._session.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise MailTransientError(safe_error(exc)) from exc
        if response.status_code in (401, 403):
            # The token was accepted an hour ago and is not now: the grant is
            # gone, not the network.
            raise MailReauthRequired(f"graph {response.status_code}")
        if response.status_code >= 500 or response.status_code == 429:
            raise MailTransientError(f"graph {response.status_code}")
        if not response.ok:
            raise MailAuthError(f"graph {response.status_code}")
        try:
            return dict(response.json())
        except ValueError as exc:
            raise MailTransientError("unreadable Graph response") from exc


def _graph_time(moment: datetime) -> str:
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_header(item: dict[str, Any]) -> MailHeader:
    sender = (item.get("from") or {}).get("emailAddress") or {}
    return MailHeader(
        key=f"graph:{item.get('id') or ''}",
        subject=str(item.get("subject") or ""),
        from_addr=str(sender.get("address") or ""),
        from_name=str(sender.get("name") or ""),
        message_id=str(item.get("internetMessageId") or ""),
        date=_parse_time(item.get("receivedDateTime")),
    )


def _parse_time(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
