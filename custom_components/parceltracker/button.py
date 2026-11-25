"""Refresh and per-parcel removal buttons."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
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
    """Add the refresh button and one remove button per parcel."""
    runtime: ParcelTrackerRuntime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator
    added: set[str] = set()

    async_add_entities([RefreshParcelsButton(coordinator, entry.entry_id)])

    @callback
    def _async_add_new_entities() -> None:
        new: list[RemoveParcelButton] = []
        valid: set[str] = set()
        for key in coordinator.data or {}:
            unique_id = parcel_unique_id(entry.entry_id, key, "remove")
            valid.add(unique_id)
            if unique_id in added:
                continue
            added.add(unique_id)
            new.append(RemoveParcelButton(coordinator, entry.entry_id, key))
        added.intersection_update(valid)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))
    _async_add_new_entities()


class RefreshParcelsButton(CoordinatorEntity[ParcelCoordinator], ButtonEntity):
    """Force an immediate mailbox and carrier refresh."""

    _attr_has_entity_name = True
    _attr_translation_key = "refresh"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: ParcelCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}:refresh"

    async def async_press(self) -> None:
        """Request a refresh of all parcels."""
        await self.coordinator.async_request_refresh()


class RemoveParcelButton(
    ParcelEntityMixin, CoordinatorEntity[ParcelCoordinator], ButtonEntity
):
    """Remove one parcel from the coordinator, store and entity registry."""

    _attr_has_entity_name = True
    _attr_translation_key = "remove"
    _attr_icon = "mdi:delete"

    def __init__(
        self, coordinator: ParcelCoordinator, entry_id: str, parcel_key: str
    ) -> None:
        super().__init__(coordinator)
        self.init_parcel_entity(coordinator, entry_id, parcel_key, "remove")

    @property
    def available(self) -> bool:
        return self.parcel_available

    async def async_press(self) -> None:
        """Delete this parcel and all of its entities."""
        await self.coordinator.async_remove_parcel(self._parcel_key)
