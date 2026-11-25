"""Structural checks for HACS metadata, translations and docs."""

from __future__ import annotations

import bootstrap  # noqa: F401

from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import unittest

from custom_components.parceltracker.const import (
    CONF_CLEANUP_DAYS,
    DEFAULT_CLEANUP_DAYS,
    DEFAULT_IMAP_HOST,
    DOMAIN,
    PLATFORMS,
    SERVICE_REMOVE_PARCEL,
)
from custom_components.parceltracker.parsers import (
    decode_header_value,
    decode_part,
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
        self.assertIn(CONF_CLEANUP_DAYS, en_fields)
        self.assertIn(CONF_CLEANUP_DAYS, cs_fields)

    def test_remove_service_metadata_is_bilingual(self) -> None:
        english = _load_json(COMPONENT / "translations" / "en.json")
        czech = _load_json(COMPONENT / "translations" / "cs.json")
        strings = _load_json(COMPONENT / "strings.json")
        for translations in (english, czech, strings):
            self.assertIn(SERVICE_REMOVE_PARCEL, translations["services"])
            self.assertIn("remove", translations["entity"]["button"])
        service = (COMPONENT / "services.yaml").read_text(encoding="utf-8")
        self.assertIn(SERVICE_REMOVE_PARCEL, service)
        self.assertIn("carrier:", service)
        self.assertIn("barcode:", service)

    def test_platforms_are_carrier_agnostic(self) -> None:
        self.assertEqual(set(PLATFORMS), {"sensor", "binary_sensor", "button"})

    def test_no_provider_specific_imap_default(self) -> None:
        self.assertEqual(DEFAULT_IMAP_HOST, "")
        for path in COMPONENT.rglob("*.py"):
            self.assertNotIn("gmail.com", path.read_text(encoding="utf-8").lower())

    def test_readme_is_bilingual(self) -> None:
        readme = (COMPONENT.parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertIn("## English", readme)
        self.assertIn("## Česky", readme)
        for carrier in ("ceska_posta", "allegro", "packeta"):
            self.assertIn(carrier, readme)

    def test_fetch_helper_supports_method(self) -> None:
        """The shared HTTP helper must allow the Packeta POST exception."""
        source = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
        self.assertIn('method: str = "GET"', source)
        self.assertIn("self._session.request(", source)

    def test_cleanup_and_service_are_wired(self) -> None:
        coordinator = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
        init = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        self.assertEqual(DEFAULT_CLEANUP_DAYS, 30)
        self.assertIn("prune_delivered(", coordinator)
        self.assertIn("async_remove_parcel(", coordinator)
        self.assertIn("async_get_entity_id(", coordinator)
        self.assertIn("async_setup(", init)
        self.assertIn(SERVICE_REMOVE_PARCEL, init)

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
            "Dalibor",
            "Dvorak",
            "rcsvet",
            "Rilská",
            "Modřany",
            "NR0224957370Y",
            "Z6027037619",
            "Z 602 7037 619",
            "A0069G83L2",
            "394421940",
            "18590591983",
            "17311083990",
            "143488659",
            "@gmail.com",
        )
        for mail in mails:
            with self.subTest(mail=mail.name):
                message = BytesParser(policy=policy.default).parsebytes(
                    mail.read_bytes()
                )
                parts = [
                    decode_part(part)
                    for part in message.walk()
                    if not part.is_multipart()
                    and part.get_content_type() in ("text/plain", "text/html")
                ]
                text = " ".join(parts) + " " + " ".join(
                    decode_header_value(message.get(header))
                    for header in ("From", "To", "Subject", "Message-ID")
                )
                lowered = text.casefold()
                for value in forbidden:
                    self.assertNotIn(value.casefold(), lowered)


if __name__ == "__main__":
    unittest.main()
