"""Canonical parcel shape and status derivation.

The vocabulary and field names match the ``ha-parcel-integrations`` family so
``ha-parcel-aggregator`` can consume these parcels without a translation
layer. Everything here is a pure function: no Home Assistant, no I/O.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .const import HISTORY_MAX_EVENTS
from .models import ParcelStatus, ProviderParcel

# Rank ladder, not a flat map: the status is the highest rank *reached*.
# BG* designation records can appear after the terminal event, so deriving
# from the last event would report a delivered parcel as still at pickup.
STATUS_RANK: dict[str, tuple[int, ParcelStatus]] = {
    "-L": (10, ParcelStatus.REGISTERED),
    "-M": (10, ParcelStatus.REGISTERED),
    "21": (20, ParcelStatus.IN_TRANSIT),
    "-F": (20, ParcelStatus.IN_TRANSIT),
    "-I": (20, ParcelStatus.IN_TRANSIT),
    "-B": (30, ParcelStatus.IN_TRANSIT),
    "-K": (30, ParcelStatus.IN_TRANSIT),
    "5K": (30, ParcelStatus.IN_TRANSIT),
    "51": (40, ParcelStatus.IN_TRANSIT),
    "53": (50, ParcelStatus.OUT_FOR_DELIVERY),
    "P2": (60, ParcelStatus.AT_PICKUP_POINT),
    "82": (60, ParcelStatus.AT_PICKUP_POINT),
    "91": (90, ParcelStatus.DELIVERED),
}

NOTIFICATION_CODES = frozenset({"42", "43"})
DESIGNATION_CODES = frozenset({"BG1", "BG4", "BG5"})
NOT_FOUND_CODES = frozenset({"-3", "-4"})
EXCLUDED_CODES = NOTIFICATION_CODES | DESIGNATION_CODES | NOT_FOUND_CODES


def derive_status(events: list[dict[str, Any]]) -> ParcelStatus:
    """Return the highest canonical status reached by the event list."""
    ranked = [
        STATUS_RANK[str(event.get("id"))]
        for event in events
        if event.get("id") in STATUS_RANK and event.get("id") not in EXCLUDED_CODES
    ]
    return max(ranked)[1] if ranked else ParcelStatus.UNKNOWN


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO timestamp; naive values are treated as UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _event_timestamp(event: dict[str, Any]) -> str | None:
    date = event.get("date")
    if not date:
        return None
    time = event.get("time")
    return f"{date}T{time}:00" if time else f"{date}T00:00:00"


def build_history(
    events: list[dict[str, Any]] | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict[str, Any]]:
    """Build the canonical history list, sorted by timestamp and capped."""
    parseable: list[tuple[datetime, dict[str, Any]]] = []
    unparseable: list[dict[str, Any]] = []
    for event in events or []:
        code = event.get("id")
        if not code or code in NOTIFICATION_CODES | DESIGNATION_CODES:
            continue
        timestamp = _event_timestamp(event)
        if not timestamp:
            continue
        entry = {
            "timestamp": timestamp,
            "status": STATUS_RANK[str(code)][1] if str(code) in STATUS_RANK else None,
            "raw_status": event.get("text") or code,
        }
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def _last_status_text(events: list[dict[str, Any]]) -> str | None:
    """Most recent non-notification event text, used as ``raw_status``."""
    for event in reversed(events):
        if event.get("id") in NOTIFICATION_CODES | DESIGNATION_CODES:
            continue
        if event.get("text"):
            return str(event["text"])
    return None


def _terminal_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((event for event in events if event.get("id") == "91"), None)


def _out_for_delivery_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((event for event in reversed(events) if event.get("id") == "53"), None)


def new_local_parcel(
    carrier: str,
    barcode: str,
    *,
    first_seen: str,
    pickup_code: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """Build the placeholder parcel created from a newly parsed email."""
    return {
        "carrier": carrier or "unknown",
        "barcode": barcode,
        "sender": None,
        "receiver": None,
        "status": ParcelStatus.UNKNOWN.value,
        "raw_status": None,
        "delivered": False,
        "delivered_at": None,
        "planned_from": None,
        "planned_to": None,
        "pickup": False,
        "pickup_point": None,
        "pickup_code": pickup_code,
        "url": url,
        "weight": None,
        "dimensions": None,
        "history": [],
        "raw": None,
        "first_seen": first_seen,
        "error": None,
    }


def normalize_parcel(
    provider_parcel: ProviderParcel,
    *,
    include_history: bool = False,
    first_seen: str | None = None,
    pickup_code: str | None = None,
) -> dict[str, Any]:
    """Turn a provider result into the canonical, aggregator-friendly dict."""
    events = provider_parcel.events or []
    status = derive_status(events)
    delivered = status is ParcelStatus.DELIVERED
    terminal = _terminal_event(events) if delivered else None

    planned_from = provider_parcel.planned_from
    planned_to = provider_parcel.planned_to
    if status is ParcelStatus.AT_PICKUP_POINT and not planned_to:
        planned_to = provider_parcel.stored_to

    if status is ParcelStatus.OUT_FOR_DELIVERY and not planned_from and not planned_to:
        ofd = _out_for_delivery_event(events)
        if ofd and ofd.get("date"):
            planned_from = f"{ofd['date']}T00:00:00"
            planned_to = f"{ofd['date']}T23:59:59"

    raw = provider_parcel.raw or {}
    result = {
        "carrier": provider_parcel.carrier,
        "barcode": provider_parcel.barcode,
        "sender": provider_parcel.sender,
        "receiver": provider_parcel.receiver,
        "status": status.value,
        "raw_status": _last_status_text(events),
        "delivered": delivered,
        "delivered_at": _event_timestamp(terminal) if terminal else None,
        "planned_from": None if delivered else planned_from,
        "planned_to": None if delivered else planned_to,
        "pickup": status is ParcelStatus.AT_PICKUP_POINT,
        "pickup_point": provider_parcel.pickup_point,
        "pickup_code": pickup_code,
        "url": provider_parcel.url
        or (raw.get("id") and f"https://www.balikovna.cz/cs/sledovat-balik/-/balik/{raw['id']}"),
        "weight": provider_parcel.weight,
        "dimensions": provider_parcel.dimensions,
        "history": build_history(events) if include_history else [],
        "raw": raw,
        "first_seen": first_seen,
        "error": provider_parcel.error,
        "not_found": provider_parcel.not_found,
    }
    return result
