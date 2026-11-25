"""Delivered binary sensor for each tracked parcel."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ParcelTrackerRuntime
from .const import DOMAIN
from .coordinator import ParcelCoordinator
from .parcel_entities import ParcelEntityMixin, parcel_unique_id


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
        valid: set[str] = set()
        for key in coordinator.data or {}:
            unique_id = parcel_unique_id(entry.entry_id, key, "delivered")
            valid.add(unique_id)
            if unique_id in added:
                continue
            added.add(unique_id)
            new.append(ParcelDeliveredSensor(coordinator, entry.entry_id, key))
        added.intersection_update(valid)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))
    _async_add_new_entities()


class ParcelDeliveredSensor(
    ParcelEntityMixin, CoordinatorEntity[ParcelCoordinator], BinarySensorEntity
):
    """True once the carrier reports a terminal delivery event."""

    _attr_has_entity_name = True
    _attr_translation_key = "delivered"
    _attr_icon = "mdi:package-check"

    def __init__(
        self, coordinator: ParcelCoordinator, entry_id: str, parcel_key: str
    ) -> None:
        super().__init__(coordinator)
        self.init_parcel_entity(coordinator, entry_id, parcel_key, "delivered")

    @property
    def available(self) -> bool:
        return self.parcel_available

    @property
    def is_on(self) -> bool:
        return bool((self.parcel or {}).get("delivered"))
