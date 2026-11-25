"""The ParcelTracker integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_BARCODE,
    ATTR_CARRIER,
    DOMAIN,
    PLATFORMS,
    SERVICE_CLEAR_DELIVERED,
    SERVICE_REMOVE_PARCEL,
    SERVICE_SCAN_NOW,
    STORAGE_VERSION,
)
from .coordinator import ParcelCoordinator
from .storage import parcel_key

_LOGGER = logging.getLogger(__name__)

REMOVE_PARCEL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CARRIER): str,
        vol.Required(ATTR_BARCODE): str,
    }
)


@dataclass(slots=True)
class ParcelTrackerRuntime:
    """Objects shared by the platforms of one config entry."""

    coordinator: ParcelCoordinator
    store: Store


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration-wide services once."""

    async def _async_remove_parcel(call: ServiceCall) -> None:
        carrier = str(call.data[ATTR_CARRIER]).strip()
        barcode = str(call.data[ATTR_BARCODE]).strip()
        if not carrier or not barcode:
            return

        key = parcel_key(carrier, barcode)
        removed = False
        for runtime in list(hass.data.get(DOMAIN, {}).values()):
            if await runtime.coordinator.async_remove_parcel(key):
                removed = True
        if not removed:
            _LOGGER.debug(
                "Parcel removal requested but not found: carrier=%s barcode=%s",
                carrier,
                barcode,
            )

    async def _async_scan_now(call: ServiceCall) -> None:
        for runtime in list(hass.data.get(DOMAIN, {}).values()):
            await runtime.coordinator.async_request_refresh()

    async def _async_clear_delivered(call: ServiceCall) -> None:
        for runtime in list(hass.data.get(DOMAIN, {}).values()):
            await runtime.coordinator.async_clear_delivered()

    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_PARCEL,
        _async_remove_parcel,
        schema=REMOVE_PARCEL_SCHEMA,
    )
    hass.services.async_register(DOMAIN, SERVICE_SCAN_NOW, _async_scan_now)
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_DELIVERED, _async_clear_delivered
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ParcelTracker from a config entry."""
    store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.parcels.{entry.entry_id}")
    coordinator = ParcelCoordinator(hass, entry, store)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ParcelTrackerRuntime(
        coordinator=coordinator, store=store
    )
    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform(platform) for platform in PLATFORMS]
    )
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, [Platform(platform) for platform in PLATFORMS]
    )
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
