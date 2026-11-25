"""Packeta / Zásilkovna provider using the keyless web JSON endpoint.

The public tracking website exposes ``POST
https://tracking.packeta.com/api/getPacketById/{code}/en`` without an API key.
It is not the documented Packeta API: the payload has no numeric status codes,
so event states are derived from the tracking descriptions. An unknown packet
answers HTTP 404, which surfaces as ``ProviderParcel.error``.

The helper is injected; Packeta is the one carrier that needs the optional
``method="POST"`` keyword on ``fetch_json``.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

from ..models import ProviderParcel
from . import JsonFetcher, register_provider, track_per_code

_LOGGER = logging.getLogger(__name__)

CARRIER_ID = "packeta"
CARRIER_NAME = "Packeta / Zásilkovna"

API_URL = "https://tracking.packeta.com/api/getPacketById/{code}/en"
TRACKING_URL = "https://tracking.packeta.com/cs/?id={code}"
_CODE_RE = re.compile(r"^Z\d{10}$")

# The web payload has no status codes; map its English/Czech descriptions to
# the canonical event ids. Most specific first.
_STATUS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("91", re.compile(r"with you|delivered|doručen|vydána|dostarczon", re.IGNORECASE)),
    (
        "P2",
        re.compile(
            r"ready for pickup|připravena k vyzvednutí|připraven k vyzvednutí"
            r"|připravena k odběru|gotowy do odbioru",
            re.IGNORECASE,
        ),
    ),
    (
        "53",
        re.compile(
            r"on its way to you|almost there|na cestě k vám|w drodze do ciebie",
            re.IGNORECASE,
        ),
    ),
    ("RETURNING", re.compile(r"posted back|returned|vrácen|odeslána zpět|zwrot", re.IGNORECASE)),
    (
        "-L",
        re.compile(
            r"aware of your parcel|waiting for the sender|received data"
            r"|převzali vaši|přijata k přepravě",
            re.IGNORECASE,
        ),
    ),
    (
        "21",
        re.compile(
            r"on its way to the depot|received for transport|arrived at the depot"
            r"|dispatched|accepted|depo|přeprava",
            re.IGNORECASE,
        ),
    ),
)


def _status_id(description: str) -> str:
    """Return a canonical event id for a Packeta tracking description."""
    for status_id, pattern in _STATUS_PATTERNS:
        if pattern.search(description):
            return status_id
    return "INFO"


def _iso_timestamp(value: Any) -> str | None:
    """Convert ``2026-10-12 15:43:02`` to an ISO timestamp."""
    if not value:
        return None
    return str(value).strip().replace(" ", "T", 1)


def _events(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Build canonical events, oldest first, and include the current state."""
    details = item.get("trackingDetails")
    if not isinstance(details, list):
        details = []
    events: list[dict[str, Any]] = []
    for entry in details:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or "")
        events.append(
            {
                "id": _status_id(text),
                "timestamp": _iso_timestamp(entry.get("time")),
                "text": text or None,
            }
        )
    events.sort(key=lambda event: str(event.get("timestamp") or ""))

    current = str(item.get("packetStatus") or "")
    current_id = _status_id(current)
    if current and current_id != "INFO" and not any(
        event["id"] == current_id for event in events
    ):
        # Home-delivery events can stop at status 6 until the carrier reports
        # back. The headline status still tells us the final state.
        events.append({"id": current_id, "timestamp": None, "text": current})
    return events


@register_provider
class PacketaProvider:
    """TrackingProvider implementation for Packeta / Zásilkovna barcodes."""

    CARRIER_ID = CARRIER_ID
    CARRIER_NAME = CARRIER_NAME

    @classmethod
    def match_tracking_code(cls, code: str) -> bool:
        """Match the ``Z`` + ten digits barcode shape."""
        return bool(_CODE_RE.fullmatch((code or "").strip().upper()))

    @classmethod
    def extract_codes_from_text(cls, text: str) -> list[str]:
        """Extract Packeta barcode candidates from text."""
        found: list[str] = []
        for match in re.finditer(r"\bZ[\s\-]*\d(?:[\s\-]*\d){9}\b", text or "", re.IGNORECASE):
            code = re.sub(r"[\s\-]+", "", match.group(0)).upper()
            if cls.match_tracking_code(code):
                found.append(code)
        return found

    def tracking_url(self, code: str) -> str | None:
        """Return the public Packeta tracking deep-link."""
        if not code:
            return None
        return TRACKING_URL.format(code=quote(code, safe=""))

    async def async_track(
        self, codes: list[str] | tuple[str, ...], fetch_json: JsonFetcher
    ) -> dict[str, ProviderParcel]:
        """Fetch every barcode with POST and return ``ProviderParcel`` objects."""
        return await track_per_code(
            carrier=CARRIER_ID,
            codes=codes,
            fetch_one=lambda code: self._async_one(code, fetch_json),
            tracking_url=self.tracking_url,
            logger=_LOGGER,
            label="Packeta",
            request_error="Packeta request failed",
        )

    async def _async_one(self, code: str, fetch_json: JsonFetcher) -> ProviderParcel:
        """Fetch and normalise one barcode."""
        payload = await fetch_json(
            API_URL.format(code=quote(code, safe="")), method="POST"
        )
        raw = payload if isinstance(payload, dict) else {}
        item = raw.get("item")
        item = item if isinstance(item, dict) else {}
        error: str | None = None
        if not isinstance(payload, dict) or not item:
            error = "unexpected Packeta body (no packet item)"
            _LOGGER.warning("Packeta tracking failed for %s: %s", code, error)

        branch = item.get("branchAddress")
        return ProviderParcel(
            carrier=CARRIER_ID,
            barcode=code,
            events=_events(item),
            sender=item.get("sender") or None,
            pickup_point=str(branch) if branch else None,
            stored_to=_iso_timestamp(item.get("storedUntil")),
            url=self.tracking_url(code),
            raw=raw,
            not_found=not item,
            error=error,
        )
