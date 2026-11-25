"""Generic IMAP reader built on the standard library.

Runs synchronously; the HA coordinator calls it through
``hass.async_add_executor_job``. It returns only parsed numbers and UIDs —
never the raw subject or body — and advances the UID cursor past every scanned
message so a message without a tracking code is not reconsidered forever.

No Home Assistant imports: the connection factory is injectable for tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from email.message import Message
from email import message_from_bytes
import imaplib
import logging
import re
from typing import Any, Callable

from .parsers import ParsedEmail, parse_message

_LOGGER = logging.getLogger(__name__)


class MailboxError(Exception):
    """Raised for IMAP login/select/search failures with a safe message."""


@dataclass(slots=True)
class FetchedMessage:
    """A parsed message; ``parsed`` contains no raw email content."""

    uid: int
    parsed: ParsedEmail


@dataclass(slots=True)
class FetchResult:
    """Result of one mailbox poll."""

    messages: list[FetchedMessage] = field(default_factory=list)
    last_uid: int = 0
    scanned: int = 0


ConnectionFactory = Callable[..., Any]


def sender_allowed(message: Message, allowlist: tuple[str, ...] | list[str]) -> bool:
    """Whether the From header matches any allowlist entry (substring match)."""
    if not allowlist:
        return True
    sender = str(message.get("From") or "").casefold()
    return any(entry and entry.strip().casefold() in sender for entry in allowlist)


class ImapClient:
    """Minimal IMAP client for any provider (Gmail, Proton, Outlook, ...)."""

    def __init__(
        self,
        email_address: str,
        password: str,
        host: str,
        port: int = 993,
        mailbox: str = "INBOX",
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self.email_address = email_address
        self.password = password
        self.host = host
        self.port = int(port)
        self.mailbox = mailbox
        self._factory = connection_factory or imaplib.IMAP4_SSL

    def _connect(self, *, readonly: bool) -> Any:
        """Open an authenticated, selected mailbox connection."""
        try:
            client = self._factory(self.host, self.port, timeout=30)
            client.login(self.email_address, self.password)
            client.select(self.mailbox, readonly=readonly)
        except Exception as err:  # noqa: BLE001 - IMAP errors are client-specific
            raise MailboxError(f"IMAP login/select failed: {err}") from None
        return client

    def validate_login(self) -> bool:
        """Check credentials during the config flow; never logs the password."""
        client = self._connect(readonly=True)
        try:
            client.logout()
        except Exception:  # noqa: BLE001 - logout is best effort
            pass
        return True

    def fetch_new_messages(
        self,
        last_uid: int | None = None,
        limit: int = 50,
        mark_seen: bool = True,
        sender_allowlist: tuple[str, ...] | list[str] = (),
        extra_patterns: tuple[str, ...] | list[str] = (),
    ) -> FetchResult:
        """Fetch, parse and optionally mark new messages since ``last_uid``.

        ``limit`` applies only on the first poll (when no UID cursor exists),
        so a mailbox with years of mail is not scanned from the start.

        Messages are fetched with BODY.PEEK and a size cap so attachments are not
        downloaded and unread state is not disturbed by the fetch itself.
        """
        client = self._connect(readonly=not mark_seen)
        result = FetchResult(last_uid=int(last_uid or 0))
        try:
            status, data = client.uid("SEARCH", None, "ALL")
            if status != "OK" or not data or not data[0]:
                raise MailboxError("IMAP UID SEARCH failed")

            search_data = data[0]
            if isinstance(search_data, bytes):
                search_data = search_data.decode("ascii", errors="ignore")
            uids: list[int] = []
            for token in str(search_data).split():
                if token.isdigit():
                    uids.append(int(token))
            if last_uid:
                uids = [uid for uid in uids if uid > int(last_uid)]
            else:
                uids = uids[-max(1, int(limit)) :]

            for uid in uids:
                status, payload = client.uid(
                    "FETCH", str(uid).encode(), "(BODY.PEEK[]<0.300000>)"
                )
                if status != "OK":
                    _LOGGER.warning("IMAP FETCH failed for UID %s", uid)
                    continue
                raw = _extract_rfc822(payload)
                if raw is None:
                    continue
                result.scanned += 1
                # Advance past every scanned message, including filtered ones,
                # so the cursor never gets stuck on an unrelated email.
                result.last_uid = max(result.last_uid, uid)
                message = message_from_bytes(raw)
                if not sender_allowed(message, sender_allowlist):
                    continue

                parsed = parse_message(message, extra_patterns)
                if parsed.tracking_code:
                    result.messages.append(FetchedMessage(uid=uid, parsed=parsed))
                    if mark_seen:
                        try:
                            client.uid(
                                "STORE", str(uid).encode(), "+FLAGS", r"(\Seen)"
                            )
                        except Exception:  # noqa: BLE001 - marking is best effort
                            _LOGGER.debug("Could not mark UID %s as seen", uid)
        finally:
            try:
                client.logout()
            except Exception:  # noqa: BLE001 - logout is best effort
                pass
        return result


def _extract_rfc822(payload: Any) -> bytes | None:
    """Find message bytes in an IMAP FETCH response.

    Accepts RFC822 and BODY.PEEK/BODY[] partial-fetch responses.
    """
    if isinstance(payload, (list, tuple)):
        for item in payload:
            if isinstance(item, tuple) and len(item) >= 2:
                header = item[0]
                if isinstance(header, bytes):
                    header = header.decode("ascii", errors="ignore")
                header_text = str(header).upper()
                if "RFC822" in header_text or "BODY" in header_text:
                    body = item[1]
                    if isinstance(body, (bytes, bytearray)):
                        return bytes(body)
    return None


def parse_allowlist(raw: str | None) -> tuple[str, ...]:
    """Normalise a comma/newline separated sender allowlist from options."""
    if not raw:
        return ()
    return tuple(
        part.strip()
        for part in re.split(r"[,\n]", raw)
        if part.strip()
    )
