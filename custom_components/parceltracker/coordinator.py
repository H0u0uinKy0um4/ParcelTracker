"""Coordinator: mailbox polling + carrier refresh + canonical parcel store."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from aiohttp import ClientError, ClientSession

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_APP_PASSWORD,
    CONF_CLEANUP_DAYS,
    CONF_EMAIL,
    CONF_EXTRA_TRACKING_REGEX,
    CONF_IMAP_HOST,
    CONF_IMAP_PORT,
    CONF_INCLUDE_HISTORY,
    CONF_MAILBOX,
    CONF_MARK_SEEN,
    CONF_SCAN_INTERVAL,
    CONF_SENDER_ALLOWLIST,
    DEFAULT_CLEANUP_DAYS,
    DEFAULT_IMAP_HOST,
    DEFAULT_IMAP_PORT,
    DEFAULT_MAILBOX,
    DEFAULT_SCAN_INTERVAL,
    EVENT_PARCEL_DELIVERED,
    EVENT_PARCEL_REGISTERED,
    EVENT_PARCEL_REMOVED,
    EVENT_PARCEL_STATUS_CHANGED,
    LAST_UID_KEY,
    PARCELS_KEY,
    STORAGE_VERSION,
    DOMAIN,
)
from .carrier_refresh import async_refresh_parcels
from .mail_ingestion import async_ingest_mail
from .parcel_entities import PARCEL_ENTITY_DESCRIPTORS, parcel_unique_id
from .parcel_lifecycle import next_poll_interval
from .storage import prune_delivered, sanitize_stored, split_key

_LOGGER = logging.getLogger(__name__)

# Per-parcel entity cleanup uses the shared descriptor registry.


class TrackingFetchError(Exception):
    """A carrier endpoint returned an unexpected response."""


class ParcelCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Owns the persisted parcels for one mailbox."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        store: Store[dict[str, Any]],
        session: ClientSession | None = None,
    ) -> None:
        """Initialise the coordinator and its HTTP helper."""
        initial_interval = int(
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=max(1, initial_interval)),
        )
        self._store = store
        self._session = session or async_get_clientsession(hass)
        self._last_uid = 0
        self._parcel_entities: dict[str, list[Entity]] = {}
        self.last_scan_at: str | None = None
        self.last_scan_scanned: int = 0
        self.last_scan_matched: int = 0
        self.last_error: str | None = None

    async def _fetch_json(self, url: str, *, method: str = "GET") -> Any:
        """Fetch a keyless carrier endpoint and decode JSON regardless of MIME.

        ``method`` is generic (not carrier-specific): most endpoints use GET,
        while Packeta's public web endpoint only accepts POST.
        """
        try:
            async with self._session.request(method.upper(), url) as response:
                if response.status != 200:
                    raise TrackingFetchError(f"HTTP {response.status}")
                return await response.json(content_type=None)
        except (ClientError, ValueError) as err:
            raise TrackingFetchError(str(err)) from err

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Poll the mailbox, refresh active parcels and persist the result."""
        try:
            stored = await self._store.async_load()
            parcels = sanitize_stored(
                stored.get(PARCELS_KEY) if isinstance(stored, dict) else stored
            )

            last_uid = (
                int(stored.get(LAST_UID_KEY, 0)) if isinstance(stored, dict) else 0
            )
            last_uid = await self._async_read_mail(parcels, last_uid)
            await self._async_refresh_parcels(parcels)

            self._last_uid = last_uid
            cleanup_days = int(
                self.config_entry.options.get(CONF_CLEANUP_DAYS, DEFAULT_CLEANUP_DAYS)
            )
            if cleanup_days > 0:
                parcels, removed = prune_delivered(parcels, days=cleanup_days)
                for key in removed:
                    await self._async_on_parcel_removed(key)

            await self._store.async_save({PARCELS_KEY: parcels, LAST_UID_KEY: last_uid})
            self.update_interval = self._next_interval(parcels)
            self.last_scan_at = dt_util.utcnow().isoformat()
            self.last_error = None
            return parcels
        except Exception as err:  # noqa: BLE001 - coordinator reports the error
            self.last_error = err.__class__.__name__
            raise

    async def _async_read_mail(
        self, parcels: dict[str, dict[str, Any]], last_uid: int
    ) -> int:
        """Read new forwarded messages and add newly discovered parcels."""
        entry = self.config_entry
        options = entry.options
        extra_regex = str(options.get(CONF_EXTRA_TRACKING_REGEX, ""))
        extra_patterns = tuple(
            line.strip() for line in extra_regex.splitlines() if line.strip()
        )

        result = await async_ingest_mail(
            parcels=parcels,
            last_uid=last_uid,
            email=str(entry.data.get(CONF_EMAIL, "")),
            app_password=str(entry.data.get(CONF_APP_PASSWORD, "")),
            imap_host=str(options.get(CONF_IMAP_HOST, DEFAULT_IMAP_HOST)),
            imap_port=int(options.get(CONF_IMAP_PORT, DEFAULT_IMAP_PORT)),
            mailbox=str(options.get(CONF_MAILBOX, DEFAULT_MAILBOX)),
            mark_seen=bool(options.get(CONF_MARK_SEEN, True)),
            sender_allowlist=options.get(CONF_SENDER_ALLOWLIST),
            extra_patterns=extra_patterns,
            run_in_executor=self.hass.async_add_executor_job,
            fire_event=self.hass.bus.async_fire,
            now_iso=lambda: dt_util.utcnow().isoformat(),
            event_registered=EVENT_PARCEL_REGISTERED,
            logger=_LOGGER,
        )
        if result.error:
            self.last_error = result.error
            return last_uid
        self.last_scan_scanned = result.scanned
        self.last_scan_matched = result.matched
        return result.last_uid

    async def _async_refresh_parcels(
        self, parcels: dict[str, dict[str, Any]]
    ) -> None:
        """Refresh every non-delivered parcel through its carrier provider."""
        await async_refresh_parcels(
            parcels=parcels,
            fetch_json=self._fetch_json,
            include_history=bool(
                self.config_entry.options.get(CONF_INCLUDE_HISTORY, False)
            ),
            fire_event=self.hass.bus.async_fire,
            event_status_changed=EVENT_PARCEL_STATUS_CHANGED,
            event_delivered=EVENT_PARCEL_DELIVERED,
            logger=_LOGGER,
        )

    def _next_interval(self, parcels: dict[str, dict[str, Any]]) -> timedelta:
        """Pick the next polling cadence; email is always checked eventually."""
        return next_poll_interval(parcels, dt_util.now())

    async def async_remove_parcel(self, key: str) -> bool:
        """Delete one parcel by storage key, including its HA entities."""
        parcels = dict(self.data or {})
        if key not in parcels:
            return False
        parcels.pop(key)

        await self._store.async_save(
            {PARCELS_KEY: parcels, LAST_UID_KEY: self._last_uid}
        )
        self.async_set_updated_data(parcels)
        await self._async_on_parcel_removed(key)
        return True

    async def async_clear_delivered(self) -> int:
        """Remove all delivered parcels from storage and HA registries."""
        parcels = dict(self.data or {})
        delivered_keys = [
            key for key, parcel in parcels.items() if parcel.get("delivered")
        ]
        if not delivered_keys:
            return 0

        for key in delivered_keys:
            parcels.pop(key, None)

        await self._store.async_save(
            {PARCELS_KEY: parcels, LAST_UID_KEY: self._last_uid}
        )
        self.async_set_updated_data(parcels)
        for key in delivered_keys:
            await self._async_on_parcel_removed(key)
        return len(delivered_keys)

    async def _async_on_parcel_removed(self, key: str) -> None:
        """Fire the removal event and drop registry/entity leftovers."""
        carrier, barcode = split_key(key)
        self.hass.bus.async_fire(
            EVENT_PARCEL_REMOVED, {"carrier": carrier, "barcode": barcode}
        )
        _LOGGER.info(
            "Removed parcel: carrier=%s barcode=%s", carrier, barcode
        )
        await self._async_remove_parcel_entities(key)

    async def _async_remove_parcel_entities(self, key: str) -> None:
        """Remove entity and device registry entries for a deleted parcel."""
        entry_id = self.config_entry.entry_id
        entity_reg = er.async_get(self.hass)
        for descriptor in PARCEL_ENTITY_DESCRIPTORS:
            unique_id = parcel_unique_id(entry_id, key, descriptor.suffix)
            entity_id = entity_reg.async_get_entity_id(descriptor.domain, DOMAIN, unique_id)
            if entity_id:
                entity_reg.async_remove(entity_id)

        device_reg = dr.async_get(self.hass)
        identifier = (DOMAIN, key)
        if hasattr(device_reg, "async_get_device_by_identifier"):
            device = device_reg.async_get_device_by_identifier(identifier, entry_id)
        else:  # older HA versions
            device = device_reg.async_get_device(identifiers={identifier})
        if device:
            device_reg.async_remove_device(device.id)

        for entity in tuple(self._parcel_entities.get(key, ())):
            await entity.async_remove(force_remove=True)

    @callback
    def track_entity(self, key: str, entity: Entity) -> None:
        """Remember a dynamic entity so deletion can remove it immediately."""
        self._parcel_entities.setdefault(key, []).append(entity)

    @callback
    def untrack_entity(self, key: str, entity: Entity) -> None:
        """Forget a dynamic entity that is being removed."""
        entities = self._parcel_entities.get(key)
        if not entities:
            return
        if entity in entities:
            entities.remove(entity)
        if not entities:
            self._parcel_entities.pop(key, None)

    @property
    def parcel_count(self) -> int:
        """Number of persisted parcels (for diagnostics/sensors)."""
        return len(self.data or {})
