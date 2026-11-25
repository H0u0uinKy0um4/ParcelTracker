"""Carrier provider registry.

Adding a Czech carrier later must not require touching the IMAP client,
coordinator, entities or aggregator mapping. A new carrier is a new module in
this package that registers itself with :func:`register_provider`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
import logging
import re
from typing import Any, Protocol, runtime_checkable

from ..models import ProviderParcel

_LOGGER = logging.getLogger(__name__)

#: Injected HTTP helper: ``await fetch_json(url, method="GET") -> decoded JSON``.
#: The optional keyword lets a keyless carrier endpoint that requires POST
#: (Packeta) use the same helper; the default stays GET for all other carriers.
#: Home Assistant supplies a thin aiohttp wrapper; tests supply a fixture.
JsonFetcher = Callable[..., Awaitable[Any]]


@runtime_checkable
class TrackingProvider(Protocol):
    """Interface every carrier implementation satisfies."""

    CARRIER_ID: str
    CARRIER_NAME: str

    @classmethod
    def match_tracking_code(cls, code: str) -> bool:
        """Whether this provider recognises the tracking code's shape."""
        ...

    def tracking_url(self, code: str) -> str | None:
        """Public tracking deep-link for the parcel."""
        ...

    async def async_track(
        self, codes: Sequence[str], fetch_json: JsonFetcher
    ) -> dict[str, ProviderParcel]:
        """Fetch and normalise parcels for the given codes.

        Implementations must never raise for a single code's network failure:
        return a :class:`ProviderParcel` with ``error`` set instead so the
        coordinator can keep the cached state.
        """
        ...


_PROVIDERS: dict[str, type[TrackingProvider]] = {}


def register_provider(provider: type[TrackingProvider]) -> type[TrackingProvider]:
    """Register a provider class by its ``CARRIER_ID``."""
    carrier_id = getattr(provider, "CARRIER_ID", None)
    if not carrier_id:
        raise ValueError("Provider must define CARRIER_ID")
    if carrier_id in _PROVIDERS and _PROVIDERS[carrier_id] is not provider:
        raise ValueError(f"Duplicate carrier id: {carrier_id}")
    _PROVIDERS[carrier_id] = provider
    return provider


def get_provider(carrier_id: str) -> type[TrackingProvider] | None:
    """Return a registered provider class, or ``None``."""
    return _PROVIDERS.get(carrier_id)


def registered_providers() -> tuple[type[TrackingProvider], ...]:
    """Return providers in registration order (Česká pošta is first)."""
    return tuple(_PROVIDERS.values())


@dataclass(frozen=True, slots=True)
class RecognizedCode:
    """A tracking code recognized in text by a provider seam."""

    carrier: str
    code: str
    numeric: bool = False


def _compiled_extra(
    extra_patterns: Sequence[str | re.Pattern[str]] = (),
) -> tuple[re.Pattern[str], ...]:
    compiled: list[re.Pattern[str]] = []
    for pattern in extra_patterns or ():
        try:
            compiled.append(
                pattern if isinstance(pattern, re.Pattern) else re.compile(pattern)
            )
        except re.error:
            continue
    return tuple(compiled)


def extract_codes_from_text(
    text: str,
    extra_patterns: Sequence[str | re.Pattern[str]] = (),
) -> list[RecognizedCode]:
    """Extract recognized carrier codes from text in registry precedence order.

    Providers may expose ``extract_codes_from_text(text)`` returning
    ``list[str]`` or ``list[RecognizedCode]``. Providers without it fall back
    to no text extraction (they may still match via ``match_tracking_code``).
    """
    upper = text.upper()
    found: list[RecognizedCode] = []
    seen: set[str] = set()

    def add(carrier: str, code: str, *, numeric: bool = False) -> None:
        normalized = (code or "").strip().upper()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        found.append(RecognizedCode(carrier=carrier, code=normalized, numeric=numeric))

    for provider in registered_providers():
        extractor = getattr(provider, "extract_codes_from_text", None)
        if extractor is None:
            continue
        try:
            matches = extractor(upper)
        except Exception:  # a broken extractor must not stop other carriers
            continue
        for match in matches or ():
            if isinstance(match, RecognizedCode):
                add(match.carrier or provider.CARRIER_ID, match.code, numeric=match.numeric)
            elif isinstance(match, tuple) and len(match) == 2:
                code, numeric = match
                add(provider.CARRIER_ID, str(code), numeric=bool(numeric))
            else:
                add(provider.CARRIER_ID, str(match), numeric=False)

    for pattern in _compiled_extra(extra_patterns):
        for match in pattern.finditer(text):
            value = match.group(1) if match.groups() else match.group(0)
            normalized = (value or "").strip().upper()
            if not normalized or normalized in seen:
                continue
            provider = detect_provider(normalized)
            if provider is None:
                # Unknown extra-pattern match: keep as-is only if it looks code-like.
                if 6 <= len(normalized) <= 15 and normalized.isalnum():
                    seen.add(normalized)
                    found.append(
                        RecognizedCode(carrier="unknown", code=normalized, numeric=normalized.isdigit())
                    )
                continue
            seen.add(normalized)
            found.append(
                RecognizedCode(
                    carrier=provider.CARRIER_ID,
                    code=normalized,
                    numeric=normalized.isdigit(),
                )
            )

    return found


def detect_provider(code: str | None) -> type[TrackingProvider] | None:
    """Guess the carrier from a tracking code using provider matchers."""
    if not code:
        return None
    normalized = code.strip().upper()
    for provider in registered_providers():
        try:
            if provider.match_tracking_code(normalized):
                return provider
        except Exception:  # a broken matcher must not stop other carriers
            continue
    return None


def provider_instance(carrier_id: str) -> TrackingProvider | None:
    """Create an instance of a registered provider."""
    provider = get_provider(carrier_id)
    return provider() if provider else None


def dedupe_codes(codes: Sequence[str] | None) -> list[str]:
    """Return unique, non-empty tracking codes in original order."""
    return list(dict.fromkeys(code for code in (codes or ()) if code))


def error_parcel(
    *,
    carrier: str,
    code: str,
    url: str | None = None,
    error: str,
    raw: dict[str, Any] | None = None,
    not_found: bool = False,
) -> ProviderParcel:
    """Build a ProviderParcel representing a per-code tracking failure."""
    return ProviderParcel(
        carrier=carrier,
        barcode=code,
        url=url,
        error=error,
        raw=raw,
        not_found=not_found,
    )


async def track_per_code(
    *,
    carrier: str,
    codes: Sequence[str] | None,
    fetch_one: Callable[[str], Awaitable[ProviderParcel]],
    tracking_url: Callable[[str], str | None] | None = None,
    logger: logging.Logger | None = None,
    label: str | None = None,
    request_error: str = "request failed",
) -> dict[str, ProviderParcel]:
    """Track codes concurrently, converting per-code failures to error parcels.

    Implementations must never raise for a single code's network/parse failure:
    this helper enforces that invariant for per-code carriers.
    """
    unique = dedupe_codes(codes)
    if not unique:
        return {}

    log = logger or _LOGGER
    name = label or carrier
    results = await asyncio.gather(
        *(fetch_one(code) for code in unique), return_exceptions=True
    )
    tracked: dict[str, ProviderParcel] = {}
    for code, parcel in zip(unique, results, strict=True):
        if isinstance(parcel, BaseException):
            log.warning("%s tracking failed for %s: %s", name, code, parcel)
            tracked[code] = error_parcel(
                carrier=carrier,
                code=code,
                url=tracking_url(code) if tracking_url else None,
                error=f"{request_error}: {parcel}",
            )
        else:
            tracked[code] = parcel
    return tracked


# Importing registers providers. Keep this at the bottom to avoid cycles.
# Registration order is also detection precedence. Packeta's ``Z`` + ten
# digits also fits the generic postal shape, so it must be matched first;
# Allegro waybills contain mixed letters and cannot collide with ČP codes.
from . import packeta  # noqa: E402,F401
from . import ceska_posta  # noqa: E402,F401
from . import allegro  # noqa: E402,F401
