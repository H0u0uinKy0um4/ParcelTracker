"""Allegro One provider using the keyless public edge JSON endpoint.

``GET https://edge.allegro.pl/ad/tracking?packageNo={code}`` needs no API key
and answers with a plain JSON object. The endpoint is undocumented/internal,
so it may change without notice; a failed fetch is reported as
``ProviderParcel.error`` and never breaks mailbox polling.

This provider is pure: the HTTP client is injected as ``fetch_json``.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

from ..models import ProviderParcel
from . import JsonFetcher, register_provider, track_per_code

_LOGGER = logging.getLogger(__name__)

CARRIER_ID = "allegro"
CARRIER_NAME = "Allegro One"

TRACKING_URL = "https://edge.allegro.pl/ad/tracking?packageNo={code}"
_CODE_RE = re.compile(r"^A\d[A-Z0-9]{8,11}$")

# Map the carrier's event descriptions to the canonical event ids used by
# :mod:`parceltracker.parcels`. Multilingual patterns cover the languages the
# endpoint returns for Czech, Slovak and Polish accounts. Most specific first.
_STATUS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("91", re.compile(r"delivered|doručen|dostarczon", re.IGNORECASE)),
    (
        "P2",
        re.compile(
            r"awaiting pick-?up|ready for pick-?up|připraven k vyzvednutí"
            r"|pripraven k vyzvednuti|oczekuje na odbiór|do odbioru",
            re.IGNORECASE,
        ),
    ),
    (
        "53",
        re.compile(
            r"released for delivery|out for delivery|na cestě k vám|w drodze do ciebie",
            re.IGNORECASE,
        ),
    ),
    ("RETURNING", re.compile(r"return|vrácen|zwrot", re.IGNORECASE)),
    (
        "-L",
        re.compile(
            r"prepared by the sender|data received|převzata od|przyjęta od",
            re.IGNORECASE,
        ),
    ),
    (
        "21",
        re.compile(
            r"accepted|dispatched|picked up|sorting|branch|přeprava|w transporcie",
            re.IGNORECASE,
        ),
    ),
)


def _status_id(description: str) -> str:
    """Return a canonical event id for an Allegro event description."""
    for status_id, pattern in _STATUS_PATTERNS:
        if pattern.search(description):
            return status_id
    return "INFO"


def _events(items: list[Any]) -> list[dict[str, Any]]:
    """Build canonical events, sorted oldest first (the API may reverse them)."""
    events: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "")
        events.append(
            {
                "id": _status_id(description),
                "timestamp": item.get("eventTimestamp"),
                "text": description or None,
            }
        )
    events.sort(key=lambda event: str(event.get("timestamp") or ""))
    return events


@register_provider
class AllegroProvider:
    """TrackingProvider implementation for Allegro One waybills."""

    CARRIER_ID = CARRIER_ID
    CARRIER_NAME = CARRIER_NAME

    @classmethod
    def match_tracking_code(cls, code: str) -> bool:
        """Match Allegro One alphanumeric waybills (e.g. ``A0069G83L2``)."""
        return bool(_CODE_RE.fullmatch((code or "").strip().upper()))

    @classmethod
    def extract_codes_from_text(cls, text: str) -> list[str]:
        """Extract Allegro One waybill candidates from text."""
        return [
            m.group(0).upper()
            for m in re.finditer(r"\bA\d[A-Z0-9]{8,11}\b", text or "", re.IGNORECASE)
        ]

    def tracking_url(self, code: str) -> str | None:
        """Return the keyless tracking endpoint as a code-specific deep-link."""
        if not code:
            return None
        return TRACKING_URL.format(code=quote(code, safe=""))

    async def async_track(
        self, codes: list[str] | tuple[str, ...], fetch_json: JsonFetcher
    ) -> dict[str, ProviderParcel]:
        """Fetch events for every code and return ``ProviderParcel`` objects."""
        return await track_per_code(
            carrier=CARRIER_ID,
            codes=codes,
            fetch_one=lambda code: self._async_one(code, fetch_json),
            tracking_url=self.tracking_url,
            logger=_LOGGER,
            label="Allegro",
            request_error="Allegro request failed",
        )

    async def _async_one(self, code: str, fetch_json: JsonFetcher) -> ProviderParcel:
        """Fetch and normalise one waybill."""
        payload = await fetch_json(self.tracking_url(code) or "")
        raw = payload if isinstance(payload, dict) else {}
        error: str | None = None
        if not isinstance(payload, dict):
            error = "unexpected Allegro body (not a JSON object)"
        items = raw.get("status")
        if not isinstance(items, list):
            if error is None:
                error = "unexpected Allegro body (no status list)"
            items = []
        if error:
            _LOGGER.warning("Allegro tracking failed for %s: %s", code, error)
        return ProviderParcel(
            carrier=CARRIER_ID,
            barcode=code,
            events=_events(items),
            url=self.tracking_url(code),
            raw=raw,
            error=error,
        )
