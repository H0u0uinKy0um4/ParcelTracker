"""PPL CZ provider using the keyless public TrackAndTrace JSON endpoint.

The public PPL shipment-tracking widget talks to ``api.dhl.com/ecs/ppl/webapi``
with a static client-side Apigee key published in the widget configuration.
This is transport compatibility material, not a user credential; it must never
be logged, persisted in diagnostics/attributes/events, or exposed outside the
HTTP request.

The endpoint answers HTTP 400 for an unknown code with
``detail == "service.TrackAndTrace.ShipmentNotFound"``. Match on that detail,
not on the status alone.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

from ..models import ProviderParcel
from . import JsonFetcher, register_provider, track_per_code

_LOGGER = logging.getLogger(__name__)

CARRIER_ID = "ppl"
CARRIER_NAME = "PPL CZ"

TRACKING_URL = "https://www.ppl.cz/vyhledat-zasilku?shipmentId={code}"
_API_URL = "https://api.dhl.com/ecs/ppl/webapi/TrackAndTrace/{code}"
# Public client-side widget key from https://www.ppl.cz/shipmenttrackingwidget/configuration.js.
# Transport-compatibility only; never log or persist.
_API_KEY = "7HH634Q79Zpge4xEGeFAHXAnUMRxv0XQ"
_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "dhl-api-key": _API_KEY,
}

# Plain numeric PPL codes overlap with Česká pošta. Only claim them in strong
# PPL context to avoid stealing CP parcels.
_PPL_CONTEXT_RE = re.compile(
    r"\bppl\.cz\b|\bmojeppl\b|\bppl\b|dhl\.com/ecs/ppl\b",
    re.IGNORECASE,
)
_NUMERIC_RE = re.compile(r"^\d{8,14}$")
_TEXT_NUMERIC_RE = re.compile(r"\b\d{8,14}\b")

# Canonical event ids are those understood by parcels.STATUS_RANK.
_STATUS_TO_EVENT_ID: dict[str, str] = {
    "REGISTERED": "-L",
    "IN_TRANSIT": "21",
    "OUT_FOR_DELIVERY": "53",
    "AT_PICKUP_POINT": "P2",
    "DELIVERED": "91",
    "RETURNING": "RETURNING",
    "PROBLEM": "PROBLEM",
}

_STATUS_MAP: dict[str, str] = {
    "WaitingForShipment": "REGISTERED",
    "Active": "REGISTERED",
    "PickedUpFromSender": "IN_TRANSIT",
    "ShipmentInTransport": "IN_TRANSIT",
    "ShipmentInTransport.TakeOverFromSender": "IN_TRANSIT",
    "PreparingForDelivery": "IN_TRANSIT",
    "LoadingForDelivery": "OUT_FOR_DELIVERY",
    "OutForDelivery": "OUT_FOR_DELIVERY",
    "DeliveredToPickupPoint": "AT_PICKUP_POINT",
    "Delivered": "DELIVERED",
    "Delivered.Parcelshop": "AT_PICKUP_POINT",
    "Delivered.BackToSender": "RETURNING",
    "BackToSender": "RETURNING",
    "Canceled": "PROBLEM",
    "Rejected": "PROBLEM",
    "NotDelivered": "PROBLEM",
}


def has_ppl_context(text: str) -> bool:
    """Return True when text strongly indicates PPL as the carrier."""
    return bool(_PPL_CONTEXT_RE.search(text or ""))


def _lookup_status(code: str | None) -> str | None:
    if not code:
        return None
    value = str(code).strip()
    if not value:
        return None
    mapped = _STATUS_MAP.get(value)
    if mapped is not None:
        return mapped
    if ".BackToSender" in value or value.endswith("BackToSender"):
        return "RETURNING"
    return None


def _event_id(code: str | None) -> str:
    status = _lookup_status(code)
    return _STATUS_TO_EVENT_ID.get(status or "", "INFO")


def _events(raw: dict[str, Any]) -> list[dict[str, Any]]:
    events_raw = raw.get("events")
    if not isinstance(events_raw, list):
        return []
    events: list[dict[str, Any]] = []
    for item in events_raw:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        timestamp = item.get("eventDate") or item.get("timestamp")
        text = item.get("eventText") or item.get("text") or code
        events.append(
            {
                "id": _event_id(str(code) if code is not None else None),
                "timestamp": timestamp,
                "text": str(text) if text is not None else None,
                "raw_code": code,
            }
        )
    events.sort(key=lambda event: str(event.get("timestamp") or ""))
    return events


def _is_not_found(err: Exception) -> bool:
    status = getattr(err, "status", None)
    body = getattr(err, "body", None)
    if status != 400 or not isinstance(body, dict):
        return False
    detail = body.get("detail")
    return detail == "service.TrackAndTrace.ShipmentNotFound"


def _raw_error(raw: dict[str, Any]) -> str | None:
    for key in ("error", "errorMessage", "message"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


@register_provider
class PplProvider:
    """TrackingProvider implementation for PPL CZ TrackAndTrace."""

    CARRIER_ID = CARRIER_ID
    CARRIER_NAME = CARRIER_NAME

    @classmethod
    def match_tracking_code(cls, code: str) -> bool:
        """Match PPL codes only when shape is unambiguous.

        Plain numeric codes overlap with Česká pošta, so numeric-only codes are
        not claimed here; context-aware extraction handles them.
        """
        normalized = (code or "").strip().upper()
        if not normalized:
            return False
        # No unambiguous PPL-only shape currently known. Numeric-only codes
        # overlap with CP and are handled via context-aware extraction.
        return False

    @classmethod
    def extract_codes_from_text(cls, text: str) -> list[tuple[str, bool]]:
        """Extract PPL numeric codes only in strong PPL context."""
        source = text or ""
        if not has_ppl_context(source):
            return []
        found: list[tuple[str, bool]] = []
        seen: set[str] = set()
        for match in _TEXT_NUMERIC_RE.finditer(source):
            code = match.group(0)
            if code in seen:
                continue
            seen.add(code)
            found.append((code, True))
        return found

    def tracking_url(self, code: str) -> str | None:
        """Return the public PPL tracking deep-link."""
        if not code:
            return None
        return TRACKING_URL.format(code=quote(code, safe=""))

    async def async_track(
        self, codes: list[str] | tuple[str, ...], fetch_json: JsonFetcher
    ) -> dict[str, ProviderParcel]:
        """Fetch TrackAndTrace payloads for every code."""
        return await track_per_code(
            carrier=CARRIER_ID,
            codes=codes,
            fetch_one=lambda code: self._async_one(code, fetch_json),
            tracking_url=self.tracking_url,
            logger=_LOGGER,
            label="PPL",
            request_error="PPL request failed",
        )

    async def _async_one(self, code: str, fetch_json: JsonFetcher) -> ProviderParcel:
        url = _API_URL.format(code=quote(code, safe=""))
        try:
            payload = await fetch_json(url, method="POST", headers=_HEADERS, json={})
        except Exception as err:  # noqa: BLE001 - provider reports per-code failures
            if _is_not_found(err):
                return ProviderParcel(
                    carrier=CARRIER_ID,
                    barcode=code,
                    url=self.tracking_url(code),
                    raw={"detail": "service.TrackAndTrace.ShipmentNotFound"},
                    not_found=True,
                )
            raise

        if not isinstance(payload, dict):
            return ProviderParcel(
                carrier=CARRIER_ID,
                barcode=code,
                url=self.tracking_url(code),
                raw={"payload": payload},
                error="unexpected PPL body (not a JSON object)",
            )

        raw = dict(payload)
        error = _raw_error(raw)
        events = _events(raw)
        return ProviderParcel(
            carrier=CARRIER_ID,
            barcode=code,
            events=events,
            sender=_string(raw.get("senderName") or raw.get("sender")),
            receiver=_string(raw.get("recipientName") or raw.get("receiver")),
            pickup_point=_string(
                raw.get("pickupPointName")
                or raw.get("accessPointName")
                or raw.get("pickupPoint")
            ),
            planned_from=_string(raw.get("deliveryDateFrom") or raw.get("plannedFrom")),
            planned_to=_string(raw.get("deliveryDateTo") or raw.get("plannedTo")),
            weight=_string(raw.get("weight")),
            url=self.tracking_url(code),
            raw=raw,
            not_found=False,
            error=error,
        )


def _string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        for key in ("name", "title", "value", "address"):
            nested = value.get(key)
            if nested not in (None, ""):
                return str(nested)
        return None
    return str(value)
