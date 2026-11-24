"""Tests for the stdlib IMAP reader using an injected fake connection."""

from __future__ import annotations

import bootstrap  # noqa: F401

from email.message import EmailMessage
import imaplib
import unittest

from custom_components.parceltracker.imap_client import (
    ImapClient,
    MailboxError,
    parse_allowlist,
)


BODY = "Číslo zásilky: DR1234567890C\nČíslo balíku: 12345678"


def _message(body: str, sender: str) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["Message-ID"] = "<test@example.com>"
    message.set_content(body)
    return message.as_bytes()


class FakeImap:
    """Minimal IMAP4_SSL stand-in."""

    def __init__(self, messages: dict[int, bytes], *, fail_login: bool = False) -> None:
        self.messages = messages
        self.fail_login = fail_login
        self.stored: list[tuple[int, str, str]] = []
        self.selected: tuple[str, bool] | None = None
        self.logged_out = False

    def login(self, user: str, password: str) -> None:
        if self.fail_login:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED invalid credentials")

    def select(self, mailbox: str, readonly: bool = False) -> None:
        self.selected = (mailbox, readonly)

    def uid(self, command: str, *args: object) -> tuple[str, list[object]]:
        if command == "SEARCH":
            return "OK", [b" ".join(str(uid).encode() for uid in self.messages)]
        if command == "FETCH":
            uid = int(args[0])
            raw = self.messages.get(uid, b"")
            header = f"{uid} (RFC822 {{{len(raw)}}}".encode()
            return "OK", [(header, raw)]
        if command == "STORE":
            self.stored.append((int(args[0]), str(args[1]), str(args[2])))
            return "OK", [b""]
        raise AssertionError(f"unexpected IMAP command {command}")

    def logout(self) -> None:
        self.logged_out = True


class ImapClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.messages = {
            1: _message(BODY, "Carrier <no-reply@carrier.example>"),
            2: _message("Newsletter", "spam@example.org"),
        }
        self.connection = FakeImap(self.messages)
        self.client = ImapClient(
            "burner@example.com",
            "app-password",
            "imap.example.com",
            connection_factory=lambda *_: self.connection,
        )

    def test_fetch_with_allowlist_and_mark_seen(self) -> None:
        result = self.client.fetch_new_messages(
            None,
            10,
            True,
            parse_allowlist("carrier.example"),
            (),
        )
        self.assertEqual(result.scanned, 2)
        self.assertEqual(len(result.messages), 1)
        self.assertEqual(result.messages[0].parsed.tracking_code, "DR1234567890C")
        self.assertEqual(result.messages[0].parsed.pickup_code, "12345678")
        self.assertEqual(result.last_uid, 2)
        self.assertEqual([uid for uid, _, _ in self.connection.stored], [1])
        self.assertTrue(self.connection.logged_out)
        self.assertEqual(self.connection.selected, ("INBOX", False))

    def test_readonly_mode_does_not_mark_seen(self) -> None:
        self.client.fetch_new_messages(None, 10, False, (), ())
        self.assertEqual(self.connection.stored, [])
        self.assertEqual(self.connection.selected, ("INBOX", True))

    def test_uid_cursor_skips_old_messages(self) -> None:
        result = self.client.fetch_new_messages(1, 10, True, (), ())
        self.assertEqual(result.scanned, 1)
        self.assertEqual(result.messages, [])
        self.assertEqual(result.last_uid, 2)

    def test_validate_login(self) -> None:
        self.assertTrue(self.client.validate_login())
        failing = FakeImap(self.messages, fail_login=True)
        client = ImapClient(
            "burner@example.com",
            "bad-password",
            "imap.example.com",
            connection_factory=lambda *_: failing,
        )
        with self.assertRaises(MailboxError) as err:
            client.validate_login()
        self.assertIn("AUTHENTICATIONFAILED", str(err.exception))

    def test_uses_configured_host_and_port(self) -> None:
        """Any IMAP provider works; no host is hard-coded to Gmail."""
        calls: list[tuple[str, int]] = []

        def factory(host: str, port: int) -> FakeImap:
            calls.append((host, port))
            return self.connection

        client = ImapClient(
            "user@proton.example", "secret", "imap.example.org", 1143,
            connection_factory=factory,
        )
        self.assertTrue(client.validate_login())
        self.assertEqual(calls, [("imap.example.org", 1143)])

    def test_allowlist_helper(self) -> None:
        self.assertEqual(parse_allowlist("a@example.com, b@example.com"), ("a@example.com", "b@example.com"))
        self.assertEqual(parse_allowlist(None), ())


if __name__ == "__main__":
    unittest.main()
