"""Delivered binary sensor for each tracked parcel."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ParcelTrackerRuntime
from .const import DOMAIN
from .coordinator import ParcelCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create delivered sensors for known and future parcels."""
    runtime: ParcelTrackerRuntime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator
    added: set[str] = set()

    @callback
    def _async_add_new_entities() -> None:
        new: list[ParcelDeliveredSensor] = []
        for key in (coordinator.data or {}):
            unique_id = f"{entry.entry_id}:{key}:delivered"
            if unique_id in added:
                continue
            added.add(unique_id)
            new.append(ParcelDeliveredSensor(coordinator, entry.entry_id, key))
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))
    _async_add_new_entities()


class ParcelDeliveredSensor(CoordinatorEntity[ParcelCoordinator], BinarySensorEntity):
    """True once the carrier reports a terminal delivery event."""

    _attr_has_entity_name = True
    _attr_translation_key = "delivered"
    _attr_icon = "mdi:package-check"

    def __init__(
        self, coordinator: ParcelCoordinator, entry_id: str, parcel_key: str
    ) -> None:
        super().__init__(coordinator)
        self._parcel_key = parcel_key
        self._attr_unique_id = f"{entry_id}:{parcel_key}:delivered"
        barcode = parcel_key.rpartition(":")[2]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, parcel_key)},
            name=f"Parcel {barcode}",
            manufacturer="ParcelTracker",
        )

    @property
    def _parcel(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get(self._parcel_key)

    @property
    def available(self) -> bool:
        return super().available and self._parcel is not None

    @property
    def is_on(self) -> bool:
        return bool((self._parcel or {}).get("delivered"))
