"""Tests for delivered-parcel pruning (pure helper, no Home Assistant)."""

from __future__ import annotations

import bootstrap  # noqa: F401

from datetime import UTC, datetime
import unittest

from custom_components.parceltracker.storage import prune_delivered


NOW = datetime(2026, 2, 10, 12, 0, tzinfo=UTC)


class PruneDeliveredTests(unittest.TestCase):
    def test_old_delivered_parcel_is_removed(self) -> None:
        parcels = {
            "ceska_posta:A": {
                "delivered": True,
                "delivered_at": "2026-01-10T12:00:00Z",
                "first_seen": "2026-01-01T00:00:00Z",
            }
        }
        kept, removed = prune_delivered(parcels, days=30, now=NOW)
        self.assertEqual(kept, {})
        self.assertEqual(removed, ["ceska_posta:A"])

    def test_recent_delivered_parcel_is_kept(self) -> None:
        parcels = {
            "ceska_posta:A": {
                "delivered": True,
                "delivered_at": "2026-01-20T12:00:00Z",
            }
        }
        kept, removed = prune_delivered(parcels, days=30, now=NOW)
        self.assertEqual(kept, parcels)
        self.assertEqual(removed, [])

    def test_active_parcel_is_never_removed(self) -> None:
        parcels = {
            "ceska_posta:A": {
                "delivered": False,
                "delivered_at": None,
                "first_seen": "2020-01-01T00:00:00Z",
            }
        }
        kept, removed = prune_delivered(parcels, days=1, now=NOW)
        self.assertEqual(kept, parcels)
        self.assertEqual(removed, [])

    def test_first_seen_fallback(self) -> None:
        parcels = {
            "allegro:B": {
                "delivered": True,
                "delivered_at": None,
                "first_seen": "2026-01-01T00:00:00Z",
            }
        }
        kept, removed = prune_delivered(parcels, days=30, now=NOW)
        self.assertEqual(kept, {})
        self.assertEqual(removed, ["allegro:B"])

    def test_zero_days_disables_cleanup(self) -> None:
        parcels = {
            "packeta:C": {
                "delivered": True,
                "delivered_at": "2020-01-01T00:00:00Z",
            }
        }
        kept, removed = prune_delivered(parcels, days=0, now=NOW)
        self.assertEqual(kept, parcels)
        self.assertEqual(removed, [])

    def test_invalid_timestamp_is_kept(self) -> None:
        parcels = {
            "ceska_posta:D": {
                "delivered": True,
                "delivered_at": "not-a-date",
                "first_seen": "also-not-a-date",
            }
        }
        kept, removed = prune_delivered(parcels, days=1, now=NOW)
        self.assertEqual(kept, parcels)
        self.assertEqual(removed, [])

    def test_naive_timestamp_is_treated_as_utc(self) -> None:
        parcels = {
            "ceska_posta:E": {
                "delivered": True,
                "delivered_at": "2026-01-10T12:00:00",
            }
        }
        kept, removed = prune_delivered(parcels, days=30, now=NOW)
        self.assertEqual(kept, {})
        self.assertEqual(removed, ["ceska_posta:E"])


if __name__ == "__main__":
    unittest.main()
