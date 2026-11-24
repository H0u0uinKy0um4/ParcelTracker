"""Structural checks for HACS metadata, translations and docs."""

from __future__ import annotations

import bootstrap  # noqa: F401

from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import unittest

from custom_components.parceltracker.const import DOMAIN, PLATFORMS
from custom_components.parceltracker.parsers import (
    decode_email_body,
    decode_header_value,
)


COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "parceltracker"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class MetadataTests(unittest.TestCase):
    def test_manifest(self) -> None:
        manifest = _load_json(COMPONENT / "manifest.json")
        self.assertEqual(manifest["domain"], DOMAIN)
        self.assertTrue(manifest["config_flow"])
        self.assertEqual(manifest["requirements"], [])
        self.assertEqual(manifest["iot_class"], "cloud_polling")

    def test_hacs(self) -> None:
        self.assertTrue(_load_json(COMPONENT.parents[1] / "hacs.json")["render_readme"])

    def test_translations_are_in_sync(self) -> None:
        english = _load_json(COMPONENT / "translations" / "en.json")
        czech = _load_json(COMPONENT / "translations" / "cs.json")
        strings = _load_json(COMPONENT / "strings.json")
        self.assertEqual(set(english), set(czech))
        self.assertEqual(set(strings), set(english))
        en_fields = set(english["config"]["step"]["user"]["data"])
        cs_fields = set(czech["config"]["step"]["user"]["data"])
        self.assertEqual(en_fields, cs_fields)

    def test_platforms_are_carrier_agnostic(self) -> None:
        self.assertEqual(set(PLATFORMS), {"sensor", "binary_sensor", "button"})

    def test_readme_is_bilingual(self) -> None:
        readme = (COMPONENT.parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertIn("## English", readme)
        self.assertIn("## Česky", readme)
        self.assertIn("ceska_posta", readme)

    def test_no_raw_email_fields_in_entities(self) -> None:
        source = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
        self.assertNotIn("subject", source.lower())
        self.assertNotIn("body", source.lower())

    def test_example_mails_are_anonymized(self) -> None:
        folder = COMPONENT.parents[1] / "exampleMails"
        mails = list(folder.glob("*.eml")) if folder.is_dir() else []
        if not mails:
            self.skipTest("no example mail present")
        forbidden = (
            "Zbyněk",
            "Augusta",
            "dalibordvorak",
            "NR0224957370Y",
            "@gmail.com",
        )
        for mail in mails:
            with self.subTest(mail=mail.name):
                message = BytesParser(policy=policy.default).parsebytes(
                    mail.read_bytes()
                )
                text = decode_email_body(message) + " " + " ".join(
                    decode_header_value(message.get(header))
                    for header in ("From", "To", "Subject", "Message-ID")
                )
                for value in forbidden:
                    self.assertNotIn(value, text)


if __name__ == "__main__":
    unittest.main()
