"""Česká pošta / Balíkovna provider using the keyless public JSON surfaces.

Two endpoints are used, both unauthenticated:

* ParcelHistory (``b2c.cpost.cz``) — event history and weight, batched by ten
  codes joined with ``;``.
* Balíkovna package detail — sender, pickup point and storage deadline, one
  code per call (a semicolon silently drops everything after the first code).

Both answer HTTP 200 for an unknown code: "not found" is a state inside the
body, not an HTTP error. Fetch failures are reported through
``ProviderParcel.error`` so the coordinator can keep cached data.

This provider is pure: the HTTP client is injected as ``fetch_json``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import quote

from ..models import ProviderParcel
from . import JsonFetcher, register_provider

_LOGGER = logging.getLogger(__name__)

CARRIER_ID = "ceska_posta"
CARRIER_NAME = "Česká pošta / Balíkovna"

BACKBONE_URL = (
    "https://b2c.cpost.cz/services/ParcelHistory/getDataAsJson"
    "?idParcel={ids}&language=en"
)
PACKAGE_URL = "https://www.balikovna.cz/o/cpb/package/{code}?lang=en_US"
TRACKING_URL = "https://www.balikovna.cz/cs/sledovat-balik/-/balik/{code}"
BATCH_SIZE = 10

_CODE_RE = re.compile(r"^[A-Z]{1,2}\d{9,11}[A-Z]{0,2}$")
_NUMERIC_RE = re.compile(r"^\d{8,13}$")
_NOT_FOUND_CODES = frozenset({"-3", "-4"})


def _events_from_raw(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge backbone events with the times from Balíkovna's own events."""
    backbone = raw.get("backbone") or {}
    enrichment = raw.get("enrichment") or {}
    backbone_events = (backbone.get("states") or {}).get("state") or []
    enrichment_events = enrichment.get("packageStates") or []

    if not isinstance(backbone_events, list):
        backbone_events = []
    if not isinstance(enrichment_events, list):
        enrichment_events = []

    times: dict[tuple[Any, Any], str] = {}
    for event in enrichment_events:
        if isinstance(event, dict) and event.get("time"):
            times.setdefault((event.get("stateId"), event.get("date")), event["time"])

    if backbone_events:
        merged: list[dict[str, Any]] = []
        for event in backbone_events:
            if not isinstance(event, dict):
                continue
            code, date = event.get("id"), event.get("date")
            merged.append(
                {
                    "id": code,
                    "date": date,
                    "time": times.get((code, date)),
                    "text": event.get("text"),
                    "idIcon": event.get("idIcon"),
                }
            )
        return merged

    return [
        {
            "id": event.get("stateId"),
            "date": event.get("date"),
            "time": event.get("time"),
            "text": event.get("text"),
            "idIcon": event.get("iconId"),
        }
        for event in enrichment_events
        if isinstance(event, dict)
    ]


def _is_not_found(raw: dict[str, Any]) -> bool:
    backbone = raw.get("backbone") or {}
    events = (backbone.get("states") or {}).get("state") or []
    backbone_missing = (
        len(events) == 1
        and isinstance(events[0], dict)
        and events[0].get("id") in _NOT_FOUND_CODES
    )
    enrichment = raw.get("enrichment") or {}
    return backbone_missing or enrichment.get("sender") == "-"


def _pickup_point(enrichment: dict[str, Any]) -> str | None:
    place = enrichment.get("pickupPlace") or {}
    if not isinstance(place, dict):
        return None
    name = place.get("name")
    if not name:
        return None
    address = place.get("address")
    return f"{name}, {address}" if address else str(name)


@register_provider
class CeskaPostaProvider:
    """TrackingProvider implementation for Česká pošta / Balíkovna."""

    CARRIER_ID = CARRIER_ID
    CARRIER_NAME = CARRIER_NAME

    @classmethod
    def match_tracking_code(cls, code: str) -> bool:
        """Match the postal-code shapes used by Česká pošta and Balíkovna."""
        normalized = (code or "").strip().upper()
        return bool(_CODE_RE.fullmatch(normalized) or _NUMERIC_RE.fullmatch(normalized))

    def tracking_url(self, code: str) -> str | None:
        """Return the Balíkovna tracking deep-link."""
        if not code:
            return None
        return TRACKING_URL.format(code=quote(code, safe=""))

    async def async_track(
        self, codes: list[str] | tuple[str, ...], fetch_json: JsonFetcher
    ) -> dict[str, ProviderParcel]:
        """Fetch both JSON surfaces for every code and return ProviderParcels."""
        codes = list(dict.fromkeys(code for code in codes if code))
        if not codes:
            return {}

        backbone = await self._async_backbone(codes, fetch_json)
        enrichment_results = await asyncio.gather(
            *(self._async_enrichment(code, fetch_json) for code in codes),
            return_exceptions=True,
        )

        result: dict[str, ProviderParcel] = {}
        for code, enrichment in zip(codes, enrichment_results, strict=True):
            error: str | None = None
            if isinstance(enrichment, BaseException):
                error = f"Balíkovna request failed: {enrichment}"
                enrichment = None

            spine = backbone.get(code)
            if isinstance(spine, BaseException):
                error = error or f"ParcelHistory request failed: {spine}"
                spine = None

            raw: dict[str, Any] = {
                "id": code,
                "backbone": spine,
                "enrichment": enrichment,
            }
            if error:
                raw["error"] = error
                _LOGGER.warning("Ceska Posta tracking failed for %s: %s", code, error)

            attributes = (spine or {}).get("attributes") or {}
            enriched = enrichment or {}
            sender = enriched.get("sender")
            if sender in (None, "-"):
                sender = None
            weight = attributes.get("weight")
            if not weight:
                weight = None

            result[code] = ProviderParcel(
                carrier=CARRIER_ID,
                barcode=code,
                events=_events_from_raw(raw),
                sender=sender,
                receiver=enriched.get("recipient") or None,
                planned_from=_timestamp(attributes.get("dorucovaniOd")),
                planned_to=_timestamp(attributes.get("dorucovaniDo")),
                pickup_point=_pickup_point(enriched),
                stored_to=_timestamp(enriched.get("storedTo")),
                weight=weight,
                url=self.tracking_url(code),
                raw=raw,
                not_found=_is_not_found(raw),
                error=error,
            )
        return result

    async def _async_backbone(
        self, codes: list[str], fetch_json: JsonFetcher
    ) -> dict[str, Any]:
        """Fetch the ParcelHistory backbone in chunks of ten."""
        results: dict[str, Any] = {}
        for start in range(0, len(codes), BATCH_SIZE):
            chunk = codes[start : start + BATCH_SIZE]
            url = BACKBONE_URL.format(ids=";".join(quote(code, safe="") for code in chunk))
            try:
                payload = await fetch_json(url)
            except Exception as err:  # noqa: BLE001 - network errors vary by client
                _LOGGER.warning("ParcelHistory chunk failed: %s", err)
                results.update({code: err for code in chunk})
                continue
            if not isinstance(payload, list):
                error = ValueError("unexpected ParcelHistory body (not a JSON array)")
                results.update({code: error for code in chunk})
                continue
            # Zip by request order: the endpoint echoes ids, but a short
            # response must never shift every later code's mapping.
            results.update(
                {
                    code: obj
                    for code, obj in zip(chunk, payload, strict=False)
                    if isinstance(obj, dict)
                }
            )
        return results

    async def _async_enrichment(
        self, code: str, fetch_json: JsonFetcher
    ) -> dict[str, Any] | None:
        """Fetch the Balíkovna detail payload for one code."""
        payload = await fetch_json(PACKAGE_URL.format(code=quote(code, safe="")))
        if not isinstance(payload, dict):
            raise ValueError("unexpected Balikovna body (not a JSON object)")
        return payload


def _timestamp(value: Any) -> str | None:
    """Return a string timestamp for an API field, or ``None``."""
    if value in (None, ""):
        return None
    return str(value)
