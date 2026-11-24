"""Manual refresh button."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ParcelTrackerRuntime
from .const import DOMAIN
from .coordinator import ParcelCoordinator
from homeassistant.helpers.update_coordinator import CoordinatorEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add one refresh button per mailbox entry."""
    runtime: ParcelTrackerRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RefreshParcelsButton(runtime.coordinator, entry.entry_id)])


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
