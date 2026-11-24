"""Tests for pure email/tracking-number parsing."""

from __future__ import annotations

import bootstrap  # noqa: F401

from dataclasses import fields
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
import re
import unittest

from custom_components.parceltracker.parsers import (
    decode_email_body,
    find_tracking_numbers,
    parse_email_text,
    parse_message,
)


SAMPLE_BODY = """Dobrý den,

Váš balík od Example Sender jsme převzali a přepravíme ho do Balíkovny.
Číslo balíku: 12345678
Adresa pro vyzvednutí: Balíkovna Praha

Číslo zásilky

DR1234567890C
"""


class ParserTests(unittest.TestCase):
    def test_czech_labels_on_separate_lines(self) -> None:
        parsed = parse_email_text(SAMPLE_BODY)
        self.assertEqual(parsed.tracking_code, "DR1234567890C")
        self.assertEqual(parsed.pickup_code, "12345678")
        self.assertEqual(parsed.carrier, "ceska_posta")

    def test_no_raw_email_content_in_result(self) -> None:
        parsed = parse_email_text(SAMPLE_BODY)
        self.assertEqual(
            {field.name for field in fields(parsed)},
            {"message_id", "tracking_code", "pickup_code", "carrier"},
        )
        self.assertNotIn("Example Sender", str(parsed))

    def test_html_body_is_decoded_and_scripts_ignored(self) -> None:
        html = (
            "<html><head><script>DR0000000000C</script></head><body>"
            "<p>Číslo zásilky: <b>DR1234567890C</b></p>"
            "</body></html>"
        )
        text = decode_email_body(_html_message(html))
        self.assertIn("DR1234567890C", text)
        self.assertNotIn("DR0000000000C", text)
        self.assertEqual(parse_email_text(text).tracking_code, "DR1234567890C")

    def test_unlabelled_postal_code_fallback(self) -> None:
        self.assertEqual(find_tracking_numbers("Sledujte DR1234567890C"), ["DR1234567890C"])

    def test_extra_user_pattern(self) -> None:
        text = "Objednávka ZAS 1234567 byla odeslána."
        self.assertEqual(find_tracking_numbers(text, [r"\bZAS\s*(\d{7})\b"]), ["1234567"])

    def test_invalid_extra_pattern_is_ignored(self) -> None:
        self.assertEqual(
            find_tracking_numbers("Číslo zásilky: DR1234567890C", ["(["]),
            ["DR1234567890C"],
        )

    def test_parse_message_keeps_message_id_only(self) -> None:
        message = EmailMessage()
        message["From"] = "Carrier <no-reply@example.com>"
        message["Message-ID"] = "<abc@example.com>"
        message.set_content(SAMPLE_BODY)
        parsed = parse_message(message)
        self.assertEqual(parsed.message_id, "<abc@example.com>")
        self.assertEqual(parsed.tracking_code, "DR1234567890C")

    def test_mixed_case_code_is_normalized(self) -> None:
        parsed = parse_email_text("Číslo zásilky: dr1234567890c")
        self.assertEqual(parsed.tracking_code, "DR1234567890C")

    def test_numeric_pickup_code_does_not_become_tracking(self) -> None:
        parsed = parse_email_text("Číslo balíku: 12345678")
        self.assertIsNone(parsed.tracking_code)
        self.assertEqual(parsed.pickup_code, "12345678")

    def test_pickup_only_postal_code_is_trackable(self) -> None:
        parsed = parse_email_text("Číslo balíku: NR0000000000CZ")
        self.assertEqual(parsed.tracking_code, "NR0000000000CZ")
        self.assertEqual(parsed.pickup_code, "NR0000000000CZ")
        self.assertEqual(parsed.carrier, "ceska_posta")

    def test_real_example_mails(self) -> None:
        """Every provided .eml must parse without inventing a redacted code."""
        path = Path(__file__).resolve().parents[1] / "exampleMails"
        mails = sorted(path.glob("*.eml")) if path.is_dir() else []
        if not mails:
            self.skipTest("no example mail present")

        for mail in mails:
            with self.subTest(mail=mail.name):
                with mail.open("rb") as handle:
                    message = BytesParser(policy=policy.default).parse(handle)
                parsed = parse_message(message)
                subject = str(message.get("Subject") or "")
                match = re.search(r"[A-Z]{2}\d{9,11}[A-Z]{0,2}", subject.upper())
                expected = match.group(0) if match else None
                self.assertEqual(parsed.tracking_code, expected)
                self.assertIn(parsed.pickup_code, (None, expected))
                if expected:
                    self.assertEqual(parsed.carrier, "ceska_posta")


def _html_message(html: str) -> EmailMessage:
    message = EmailMessage()
    message.set_content(html, subtype="html")
    return message


if __name__ == "__main__":
    unittest.main()
