"""Shared per-parcel entity lifecycle and identity helpers.

Platforms declare parcel-specific behavior; this module owns the common
identity, lookup, availability, and track/untrack lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .storage import split_key

if TYPE_CHECKING:
    from homeassistant.helpers.entity import Entity

    from .coordinator import ParcelCoordinator


@dataclass(frozen=True, slots=True)
class ParcelEntityDescriptor:
    """Registry entry for a per-parcel entity kind."""

    domain: str
    suffix: str


# Single source of truth for per-parcel entity cleanup/identity.
PARCEL_ENTITY_DESCRIPTORS: tuple[ParcelEntityDescriptor, ...] = (
    ParcelEntityDescriptor("sensor", "status"),
    ParcelEntityDescriptor("sensor", "last_event"),
    ParcelEntityDescriptor("sensor", "pickup_code"),
    ParcelEntityDescriptor("binary_sensor", "delivered"),
    ParcelEntityDescriptor("button", "remove"),
)


def parcel_unique_id(entry_id: str, parcel_key: str, suffix: str) -> str:
    """Return the stable unique ID for a per-parcel entity."""
    return f"{entry_id}:{parcel_key}:{suffix}"


def parcel_device_info(parcel_key: str) -> DeviceInfo:
    """Return the shared HA device for a parcel."""
    barcode = split_key(parcel_key)[1]
    return DeviceInfo(
        identifiers={(DOMAIN, parcel_key)},
        name=f"Parcel {barcode}",
        manufacturer="ParcelTracker",
    )


def get_parcel(
    coordinator: ParcelCoordinator, parcel_key: str
) -> dict[str, Any] | None:
    """Return the canonical parcel dict for a key, if present."""
    return (coordinator.data or {}).get(parcel_key)


def is_parcel_available(coordinator: ParcelCoordinator, parcel_key: str) -> bool:
    """A parcel entity is available only when coordinator has the parcel."""
    return coordinator.last_update_success and get_parcel(coordinator, parcel_key) is not None


class ParcelEntityMixin:
    """Common lifecycle for dynamic per-parcel entities."""

    coordinator: ParcelCoordinator
    _parcel_key: str

    def init_parcel_entity(
        self, coordinator: ParcelCoordinator, entry_id: str, parcel_key: str, suffix: str
    ) -> None:
        """Initialise shared parcel entity identity."""
        self._parcel_key = parcel_key
        self._attr_unique_id = parcel_unique_id(entry_id, parcel_key, suffix)
        self._attr_device_info = parcel_device_info(parcel_key)

    @property
    def parcel(self) -> dict[str, Any] | None:
        """Current canonical parcel dict."""
        return get_parcel(self.coordinator, self._parcel_key)

    @property
    def parcel_available(self) -> bool:
        """Availability based on coordinator success and parcel presence."""
        return is_parcel_available(self.coordinator, self._parcel_key)

    async def async_added_to_hass(self) -> None:
        """Register for immediate removal when the parcel is deleted."""
        await super().async_added_to_hass()  # type: ignore[misc]
        self.coordinator.track_entity(self._parcel_key, self)  # type: ignore[arg-type]

    async def async_will_remove_from_hass(self) -> None:
        """Unregister before Home Assistant removes this entity."""
        self.coordinator.untrack_entity(self._parcel_key, self)  # type: ignore[arg-type]
        await super().async_will_remove_from_hass()  # type: ignore[misc]
