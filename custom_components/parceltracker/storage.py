"""Pure helpers for the persisted parcel store.

HA's ``Store`` handles the actual I/O; this module keeps keying and merge
semantics testable without Home Assistant. Keys are ``carrier:barcode`` so two
carriers can legitimately use the same barcode in the future.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .const import HISTORY_MAX_EVENTS
from .models import ParcelStatus
from .parcels import parse_iso


def parcel_key(carrier: str | None, barcode: str) -> str:
    """Return the stable storage/entity key for a parcel."""
    return f"{carrier or 'unknown'}:{barcode}"


def split_key(key: str) -> tuple[str, str]:
    """Split a storage key into ``(carrier, barcode)``."""
    carrier, separator, barcode = key.partition(":")
    return (carrier, barcode) if separator else ("unknown", carrier)


def prune_delivered(
    parcels: dict[str, dict[str, Any]],
    *,
    days: int,
    now: datetime | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Remove delivered parcels older than ``days`` days.

    ``delivered_at`` is preferred; older stored records fall back to
    ``first_seen``. ``days <= 0`` disables pruning. Returns the kept parcels and
    the removed keys so the caller can fire events and clean up entities.
    """
    if days <= 0:
        return dict(parcels), []

    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    cutoff = reference - timedelta(days=days)

    kept: dict[str, dict[str, Any]] = {}
    removed: list[str] = []
    for key, parcel in parcels.items():
        if not parcel.get("delivered"):
            kept[key] = parcel
            continue
        stamp = parse_iso(parcel.get("delivered_at")) or parse_iso(
            parcel.get("first_seen")
        )
        if stamp is None or stamp > cutoff:
            kept[key] = parcel
            continue
        removed.append(key)
    return kept, removed


def sanitize_stored(data: Any) -> dict[str, dict[str, Any]]:
    """Drop malformed entries from persisted data without raising."""
    if not isinstance(data, dict):
        return {}
    return {
        str(key): dict(value)
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, dict) and value.get("barcode")
    }


def merge_parcel(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
    *,
    max_events: int = HISTORY_MAX_EVENTS,
) -> dict[str, Any]:
    """Merge a fresh provider result into cached state.

    ``first_seen`` and the email-derived ``pickup_code`` are owned by the
    mailbox and never overwritten by tracking data. If the provider failed,
    cached status details win over the empty fresh result so a network hiccup
    does not blank out the entities.
    """
    if not existing:
        merged = dict(incoming)
    else:
        merged = dict(existing)
        merged.update(incoming)
        if incoming.get("error"):
            for field in (
                "status",
                "raw_status",
                "delivered",
                "delivered_at",
                "planned_from",
                "planned_to",
                "pickup",
                "pickup_point",
                "sender",
                "receiver",
                "weight",
                "dimensions",
                "history",
                "raw",
                "not_found",
            ):
                if field in existing:
                    merged[field] = existing[field]

    merged["first_seen"] = (existing or {}).get("first_seen") or incoming.get("first_seen")
    if not merged.get("pickup_code"):
        merged["pickup_code"] = (existing or {}).get("pickup_code")

    history = merged.get("history")
    if isinstance(history, list):
        merged["history"] = history[-max_events:]

    # Canonical fields must always exist, even for old stored entries.
    defaults = {
        "carrier": "unknown",
        "status": ParcelStatus.UNKNOWN.value,
        "delivered": False,
        "pickup": False,
        "history": [],
        "raw": None,
        "pickup_code": None,
        "first_seen": None,
        "error": None,
    }
    for field, value in defaults.items():
        merged.setdefault(field, value)
    return merged


def status_counts(parcels: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Count parcels by canonical status (used by dynamic polling)."""
    counts: dict[str, int] = {}
    for parcel in parcels.values():
        status = str(parcel.get("status") or ParcelStatus.UNKNOWN.value)
        counts[status] = counts.get(status, 0) + 1
    return counts
