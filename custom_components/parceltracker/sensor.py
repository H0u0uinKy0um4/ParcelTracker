"""Per-parcel sensors exposing the canonical aggregator-compatible fields."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ParcelTrackerRuntime
from .const import CONF_INCLUDE_HISTORY, DOMAIN
from .coordinator import ParcelCoordinator
from .parcel_entities import ParcelEntityMixin, parcel_unique_id


@dataclass(frozen=True, kw_only=True)
class ParcelSensorDescription(SensorEntityDescription):
    """A sensor reading one canonical field of a parcel."""

    field: str


SENSORS: tuple[ParcelSensorDescription, ...] = (
    ParcelSensorDescription(
        key="status",
        translation_key="status",
        field="status",
        icon="mdi:truck-delivery",
    ),
    ParcelSensorDescription(
        key="last_event",
        translation_key="last_event",
        field="raw_status",
        icon="mdi:clipboard-text-outline",
    ),
    ParcelSensorDescription(
        key="pickup_code",
        translation_key="pickup_code",
        field="pickup_code",
        icon="mdi:ticket-outline",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create sensors for every known parcel and for parcels found later."""
    runtime: ParcelTrackerRuntime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator
    added: set[str] = set()

    @callback
    def _async_add_new_entities() -> None:
        new: list[ParcelSensor] = []
        valid: set[str] = set()
        for key, parcel in (coordinator.data or {}).items():
            for description in SENSORS:
                if description.key == "pickup_code" and not parcel.get("pickup_code"):
                    continue
                unique_id = parcel_unique_id(entry.entry_id, key, description.key)
                valid.add(unique_id)
                if unique_id in added:
                    continue
                added.add(unique_id)
                new.append(ParcelSensor(coordinator, entry.entry_id, key, description))
        added.intersection_update(valid)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))
    _async_add_new_entities()


class ParcelSensor(
    ParcelEntityMixin, CoordinatorEntity[ParcelCoordinator], SensorEntity
):
    """A single canonical field of one parcel."""

    entity_description: ParcelSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ParcelCoordinator,
        entry_id: str,
        parcel_key: str,
        description: ParcelSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self.init_parcel_entity(coordinator, entry_id, parcel_key, description.key)

    @property
    def available(self) -> bool:
        return self.parcel_available

    @property
    def native_value(self) -> str | None:
        parcel = self.parcel
        if parcel is None:
            return None
        value = parcel.get(self.entity_description.field)
        return str(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the canonical parcel shape without raw email content."""
        parcel = self.parcel
        if parcel is None:
            return {}
        include_history = bool(
            self.coordinator.config_entry.options.get(CONF_INCLUDE_HISTORY, False)
        )
        attributes = dict(parcel)
        attributes.pop("raw", None)
        if not include_history:
            attributes.pop("history", None)
        attributes.pop("error", None)
        attributes.pop("not_found", None)
        return attributes
