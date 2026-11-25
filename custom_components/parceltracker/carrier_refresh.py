"""Carrier refresh orchestration behind a local-substitutable seam."""

from __future__ import annotations

import logging
from typing import Any, Callable

from .models import ParcelStatus
from .parcels import normalize_parcel
from .providers import JsonFetcher, provider_instance
from .storage import merge_parcel, split_key

_LOGGER = logging.getLogger(__name__)

EventEmitter = Callable[[str, dict[str, Any]], None]


async def async_refresh_parcels(
    *,
    parcels: dict[str, dict[str, Any]],
    fetch_json: JsonFetcher,
    include_history: bool,
    fire_event: EventEmitter,
    event_status_changed: str,
    event_delivered: str,
    logger: logging.Logger = _LOGGER,
) -> None:
    """Refresh non-delivered parcels through their carrier providers."""
    groups: dict[str, list[str]] = {}
    for key, parcel in parcels.items():
        if parcel.get("delivered"):
            continue
        carrier = str(parcel.get("carrier") or "unknown")
        groups.setdefault(carrier, []).append(key)

    for carrier, keys in groups.items():
        provider = provider_instance(carrier)
        if provider is None:
            logger.debug("No tracking provider for carrier %s", carrier)
            continue
        codes = [split_key(key)[1] for key in keys]
        try:
            results = await provider.async_track(codes, fetch_json)
        except Exception as err:  # noqa: BLE001 - a provider must not kill polling
            logger.warning("Provider %s failed: %s", carrier, err)
            continue

        for key, code in zip(keys, codes, strict=True):
            tracked = results.get(code)
            if tracked is None:
                continue
            old_status = parcels[key].get("status")
            fresh = normalize_parcel(
                tracked,
                include_history=include_history,
                first_seen=parcels[key].get("first_seen"),
                pickup_code=parcels[key].get("pickup_code"),
            )
            parcels[key] = merge_parcel(parcels[key], fresh)
            new_status = parcels[key].get("status")
            if new_status != old_status:
                fire_event(
                    event_status_changed,
                    {
                        "carrier": carrier,
                        "barcode": code,
                        "status": new_status,
                        "old_status": old_status,
                    },
                )
            if (
                parcels[key].get("delivered")
                and old_status != ParcelStatus.DELIVERED.value
            ):
                fire_event(event_delivered, {"carrier": carrier, "barcode": code})
