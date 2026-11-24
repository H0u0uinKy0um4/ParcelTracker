"""Carrier provider registry.

Adding a Czech carrier later must not require touching the IMAP client,
coordinator, entities or aggregator mapping. A new carrier is a new module in
this package that registers itself with :func:`register_provider`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol, runtime_checkable

from ..models import ProviderParcel

#: Injected HTTP helper: ``await fetch_json(url) -> decoded JSON``.
#: Home Assistant supplies a thin aiohttp wrapper; tests supply a fixture.
JsonFetcher = Callable[[str], Awaitable[Any]]


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


# Importing registers providers. Keep this at the bottom to avoid cycles.
from . import ceska_posta  # noqa: E402,F401
