"""Tests for the ČP/Balíkovna JSON provider."""

from __future__ import annotations

import bootstrap  # noqa: F401

import asyncio
from typing import Any
import unittest

from custom_components.parceltracker.models import ParcelStatus
from custom_components.parceltracker.parcels import derive_status, normalize_parcel
from custom_components.parceltracker.providers import (
    _PROVIDERS,
    detect_provider,
    get_provider,
    registered_providers,
    register_provider,
)
from custom_components.parceltracker.providers.ceska_posta import CeskaPostaProvider


CODE = "DR1234567890C"

BACKBONE: dict[str, Any] = {
    CODE: {
        "id": CODE,
        "attributes": {"weight": 1.25, "dorucovaniOd": None, "dorucovaniDo": None},
        "states": {
            "state": [
                {"id": "-L", "date": "2026-01-01", "text": "Data received"},
                {"id": "21", "date": "2026-01-02", "text": "In transit"},
                {"id": "P2", "date": "2026-01-05", "text": "Ready for pickup"},
                {"id": "91", "date": "2026-01-08", "text": "Delivered"},
                {"id": "BG1", "date": "2026-01-09", "text": None},
            ]
        },
    }
}
ENRICHMENT: dict[str, Any] = {
    CODE: {
        "sender": "Example sender",
        "recipient": "Example recipient",
        "pickupPlace": {"name": "Balíkovna", "address": "Praha 1"},
        "storedTo": "2026-01-12T00:00:00",
        "packageStates": [
            {"stateId": "P2", "date": "2026-01-05", "time": "10:30", "text": "Ready"}
        ],
    }
}


def fake_fetch(calls: list[str], *, fail: bool = False):
    """Return a fetch_json implementation backed by the fixtures."""

    async def fetch(url: str) -> Any:
        calls.append(url)
        if fail:
            raise OSError("network down")
        if "ParcelHistory" in url:
            raw_ids = url.split("idParcel=", 1)[1].split("&", 1)[0]
            return [BACKBONE[code] for code in raw_ids.split(";") if code in BACKBONE]
        code = url.split("/package/", 1)[1].split("?", 1)[0]
        return ENRICHMENT.get(code, {})

    return fetch


class ProviderTests(unittest.TestCase):
    def test_registry_detects_carrier(self) -> None:
        self.assertIs(detect_provider(CODE), CeskaPostaProvider)
        self.assertEqual(
            [provider.CARRIER_ID for provider in registered_providers()], ["ceska_posta"]
        )
        self.assertIsNotNone(get_provider("ceska_posta"))
        self.assertTrue(CeskaPostaProvider.match_tracking_code("12345678"))
        self.assertFalse(CeskaPostaProvider.match_tracking_code("NOT-A-CODE"))

    def test_tracking_url(self) -> None:
        self.assertIn(CODE, CeskaPostaProvider().tracking_url(CODE) or "")

    def test_new_carrier_can_be_registered_without_entity_changes(self) -> None:
        class DummyProvider:
            CARRIER_ID = "dummy_test"
            CARRIER_NAME = "Dummy carrier"

            @classmethod
            def match_tracking_code(cls, code: str) -> bool:
                return code.startswith("DUMMY")

            def tracking_url(self, code: str) -> str:
                return f"https://example.invalid/{code}"

            async def async_track(self, codes, fetch_json):
                return {}

        register_provider(DummyProvider)
        try:
            self.assertIs(get_provider("dummy_test"), DummyProvider)
            self.assertIs(detect_provider("DUMMY123"), DummyProvider)
        finally:
            _PROVIDERS.pop("dummy_test", None)

    def test_provider_merges_surfaces_and_status(self) -> None:
        calls: list[str] = []
        parcels = asyncio.run(
            CeskaPostaProvider().async_track([CODE], fake_fetch(calls))
        )
        parcel = parcels[CODE]
        self.assertEqual(len(calls), 2)
        self.assertEqual(parcel.carrier, "ceska_posta")
        self.assertEqual(parcel.sender, "Example sender")
        self.assertEqual(parcel.pickup_point, "Balíkovna, Praha 1")
        self.assertEqual(derive_status(parcel.events), ParcelStatus.DELIVERED)
        pickup_event = next(event for event in parcel.events if event["id"] == "P2")
        self.assertEqual(pickup_event["time"], "10:30")

        canonical = normalize_parcel(parcel, include_history=True, pickup_code="12345678")
        self.assertTrue(canonical["delivered"])
        self.assertEqual(canonical["status"], "delivered")
        self.assertEqual(canonical["delivered_at"], "2026-01-08T00:00:00")
        self.assertEqual(canonical["pickup_code"], "12345678")
        self.assertIsNone(canonical["planned_to"])
        self.assertEqual(len(canonical["history"]), 4)  # BG1 is excluded
        self.assertEqual(canonical["raw_status"], "Delivered")

    def test_not_found_sentinel(self) -> None:
        async def fetch(url: str) -> Any:
            if "ParcelHistory" in url:
                return [
                    {
                        "id": "RR0000000000CZ",
                        "attributes": {},
                        "states": {
                            "state": [
                                {
                                    "id": "-3",
                                    "date": "2026-01-01",
                                    "text": "No record",
                                }
                            ]
                        },
                    }
                ]
            return {"sender": "-"}

        parcel = asyncio.run(
            CeskaPostaProvider().async_track(["RR0000000000CZ"], fetch)
        )["RR0000000000CZ"]
        self.assertTrue(parcel.not_found)
        self.assertIsNone(parcel.error)
        self.assertEqual(normalize_parcel(parcel)["status"], "unknown")

    def test_batching_ten_codes(self) -> None:
        codes = [f"DR12345678{i:02d}C" for i in range(11)]
        calls: list[str] = []
        asyncio.run(CeskaPostaProvider().async_track(codes, fake_fetch(calls)))
        backbone_calls = [url for url in calls if "ParcelHistory" in url]
        enrichment_calls = [url for url in calls if "/package/" in url]
        self.assertEqual(len(backbone_calls), 2)
        self.assertEqual(len(enrichment_calls), 11)

    def test_failure_is_reported_not_raised(self) -> None:
        calls: list[str] = []
        parcels = asyncio.run(
            CeskaPostaProvider().async_track([CODE], fake_fetch(calls, fail=True))
        )
        self.assertIn("error", parcels[CODE].raw or {})
        self.assertIsNotNone(parcels[CODE].error)


if __name__ == "__main__":
    unittest.main()
