"""Tests for canonical parcel mapping and storage merge semantics."""

from __future__ import annotations

import bootstrap  # noqa: F401

import unittest

from custom_components.parceltracker.models import ParcelStatus, ProviderParcel
from custom_components.parceltracker.parcels import (
    build_history,
    derive_status,
    new_local_parcel,
    normalize_parcel,
)
from custom_components.parceltracker.storage import (
    merge_parcel,
    parcel_key,
    split_key,
    status_counts,
)


class ParcelMappingTests(unittest.TestCase):
    def test_status_uses_highest_rank_reached(self) -> None:
        events = [
            {"id": "91", "date": "2026-01-05", "text": "Delivered"},
            {"id": "BG1", "date": "2026-01-06", "text": None},
            {"id": "P2", "date": "2026-01-04", "text": "Ready"},
        ]
        self.assertEqual(derive_status(events), ParcelStatus.DELIVERED)

    def test_notification_codes_are_ignored(self) -> None:
        events = [
            {"id": "42", "date": "2026-01-01", "text": "SMS sent"},
            {"id": "21", "date": "2026-01-02", "text": "In transit"},
        ]
        self.assertEqual(derive_status(events), ParcelStatus.IN_TRANSIT)

    def test_history_is_capped_and_sorted(self) -> None:
        events = [
            {"id": "21", "date": "2026-01-03", "text": "third"},
            {"id": "-L", "date": "2026-01-01", "text": "first"},
            {"id": "53", "date": "2026-01-02", "text": "second"},
        ]
        history = build_history(events, max_events=2)
        self.assertEqual([entry["raw_status"] for entry in history], ["second", "third"])

    def test_pickup_deadline_fallback(self) -> None:
        parcel = ProviderParcel(
            carrier="ceska_posta",
            barcode="DR1234567890C",
            events=[{"id": "P2", "date": "2026-01-05", "text": "Ready"}],
            stored_to="2026-01-12T00:00:00",
            url="https://example.invalid/track",
        )
        canonical = normalize_parcel(parcel)
        self.assertEqual(canonical["status"], "at_pickup_point")
        self.assertTrue(canonical["pickup"])
        self.assertEqual(canonical["planned_to"], "2026-01-12T00:00:00")

    def test_canonical_shape_is_complete(self) -> None:
        canonical = new_local_parcel(
            "ceska_posta", "DR1234567890C", first_seen="2026-01-01T00:00:00"
        )
        self.assertEqual(
            set(canonical),
            {
                "carrier",
                "barcode",
                "sender",
                "receiver",
                "status",
                "raw_status",
                "delivered",
                "delivered_at",
                "planned_from",
                "planned_to",
                "pickup",
                "pickup_point",
                "pickup_code",
                "url",
                "weight",
                "dimensions",
                "history",
                "raw",
                "first_seen",
                "error",
            },
        )
        self.assertEqual(canonical["status"], "unknown")


class StorageTests(unittest.TestCase):
    def test_keys_are_carrier_scoped(self) -> None:
        key = parcel_key("ceska_posta", "12345678")
        self.assertEqual(key, "ceska_posta:12345678")
        self.assertEqual(split_key(key), ("ceska_posta", "12345678"))
        self.assertNotEqual(key, parcel_key("dpd", "12345678"))

    def test_merge_preserves_mailbox_fields(self) -> None:
        cached = new_local_parcel(
            "ceska_posta",
            "DR1234567890C",
            first_seen="2026-01-01T00:00:00",
            pickup_code="12345678",
        )
        cached["status"] = "in_transit"
        incoming = normalize_parcel(
            ProviderParcel(
                carrier="ceska_posta",
                barcode="DR1234567890C",
                events=[{"id": "91", "date": "2026-01-02", "text": "Delivered"}],
            ),
            first_seen=None,
            pickup_code=None,
        )
        merged = merge_parcel(cached, incoming)
        self.assertEqual(merged["status"], "delivered")
        self.assertEqual(merged["first_seen"], "2026-01-01T00:00:00")
        self.assertEqual(merged["pickup_code"], "12345678")

    def test_failed_fetch_keeps_cached_state(self) -> None:
        cached = new_local_parcel(
            "ceska_posta", "DR1234567890C", first_seen="2026-01-01T00:00:00"
        )
        cached["status"] = "at_pickup_point"
        incoming = new_local_parcel(
            "ceska_posta", "DR1234567890C", first_seen=None
        )
        incoming["error"] = "network down"
        merged = merge_parcel(cached, incoming)
        self.assertEqual(merged["status"], "at_pickup_point")
        self.assertEqual(merged["error"], "network down")

    def test_status_counts(self) -> None:
        parcels = {
            "a": {"status": "delivered"},
            "b": {"status": "in_transit"},
            "c": {},
        }
        self.assertEqual(
            status_counts(parcels), {"delivered": 1, "in_transit": 1, "unknown": 1}
        )


if __name__ == "__main__":
    unittest.main()
