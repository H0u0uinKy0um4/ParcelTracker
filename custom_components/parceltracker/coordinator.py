"""Coordinator: mailbox polling + carrier refresh + canonical parcel store."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from aiohttp import ClientError, ClientSession

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_APP_PASSWORD,
    CONF_EMAIL,
    CONF_EXTRA_TRACKING_REGEX,
    CONF_IMAP_HOST,
    CONF_IMAP_PORT,
    CONF_INCLUDE_HISTORY,
    CONF_MAILBOX,
    CONF_MARK_SEEN,
    CONF_SCAN_INTERVAL,
    CONF_SENDER_ALLOWLIST,
    DEFAULT_IMAP_HOST,
    DEFAULT_IMAP_PORT,
    DEFAULT_MAILBOX,
    DEFAULT_SCAN_INTERVAL,
    EVENT_PARCEL_DELIVERED,
    EVENT_PARCEL_REGISTERED,
    EVENT_PARCEL_STATUS_CHANGED,
    HOT_INTERVAL_MINUTES,
    IDLE_INTERVAL_MINUTES,
    NORMAL_INTERVAL_MINUTES,
    PARCELS_KEY,
    QUIET_WINDOW_END_HOUR,
    QUIET_WINDOW_START_HOUR,
    STORAGE_VERSION,
    DOMAIN,
)
from .imap_client import GmailImapClient, MailboxError, parse_allowlist
from .models import ParcelStatus
from .parcels import new_local_parcel, normalize_parcel
from .providers import provider_instance
from .storage import merge_parcel, parcel_key, sanitize_stored, split_key

_LOGGER = logging.getLogger(__name__)


class TrackingFetchError(Exception):
    """A carrier endpoint returned an unexpected response."""


class ParcelCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Owns the persisted parcels for one burner mailbox."""

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

    async def _fetch_json(self, url: str) -> Any:
        """GET a keyless carrier endpoint and decode JSON regardless of MIME."""
        try:
            async with self._session.get(url) as response:
                if response.status != 200:
                    raise TrackingFetchError(f"HTTP {response.status}")
                return await response.json(content_type=None)
        except (ClientError, ValueError) as err:
            raise TrackingFetchError(str(err)) from err

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Poll the mailbox, refresh active parcels and persist the result."""
        stored = await self._store.async_load()
        parcels = sanitize_stored(
            stored.get(PARCELS_KEY) if isinstance(stored, dict) else stored
        )

        last_uid = int(stored.get("last_uid", 0)) if isinstance(stored, dict) else 0
        last_uid = await self._async_read_mail(parcels, last_uid)
        await self._async_refresh_parcels(parcels)

        await self._store.async_save({PARCELS_KEY: parcels, "last_uid": last_uid})
        self.update_interval = self._next_interval(parcels)
        return parcels

    async def _async_read_mail(
        self, parcels: dict[str, dict[str, Any]], last_uid: int
    ) -> int:
        """Read new forwarded messages and add newly discovered parcels."""
        entry = self.config_entry
        options = entry.options
        client = GmailImapClient(
            str(entry.data.get(CONF_EMAIL, "")),
            str(entry.data.get(CONF_APP_PASSWORD, "")),
            str(options.get(CONF_IMAP_HOST, DEFAULT_IMAP_HOST)),
            int(options.get(CONF_IMAP_PORT, DEFAULT_IMAP_PORT)),
            str(options.get(CONF_MAILBOX, DEFAULT_MAILBOX)),
        )
        extra_regex = str(options.get(CONF_EXTRA_TRACKING_REGEX, ""))
        extra_patterns = [
            line.strip() for line in extra_regex.splitlines() if line.strip()
        ]

        try:
            result = await self.hass.async_add_executor_job(
                client.fetch_new_messages,
                last_uid,
                50,
                bool(options.get(CONF_MARK_SEEN, True)),
                parse_allowlist(options.get(CONF_SENDER_ALLOWLIST)),
                tuple(extra_patterns),
            )
        except MailboxError as err:
            _LOGGER.warning("ParcelTracker mailbox poll failed: %s", err)
            return last_uid

        now = dt_util.utcnow().isoformat()
        for fetched in result.messages:
            parsed = fetched.parsed
            if not parsed.tracking_code:
                continue
            key = parcel_key(parsed.carrier, parsed.tracking_code)
            existing = parcels.get(key)
            if existing:
                if parsed.pickup_code and not existing.get("pickup_code"):
                    existing["pickup_code"] = parsed.pickup_code
                continue

            from .providers import detect_provider

            provider = detect_provider(parsed.tracking_code)
            parcels[key] = new_local_parcel(
                parsed.carrier or (provider.CARRIER_ID if provider else "unknown"),
                parsed.tracking_code,
                first_seen=now,
                pickup_code=parsed.pickup_code,
                url=provider().tracking_url(parsed.tracking_code) if provider else None,
            )
            self.hass.bus.async_fire(
                EVENT_PARCEL_REGISTERED,
                {"carrier": parcels[key]["carrier"], "barcode": parsed.tracking_code},
            )
            _LOGGER.info(
                "New parcel from email: carrier=%s barcode=%s",
                parcels[key]["carrier"],
                parsed.tracking_code,
            )
        return max(last_uid, result.last_uid)

    async def _async_refresh_parcels(
        self, parcels: dict[str, dict[str, Any]]
    ) -> None:
        """Refresh every non-delivered parcel through its carrier provider."""
        groups: dict[str, list[str]] = {}
        for key, parcel in parcels.items():
            if parcel.get("delivered"):
                continue
            carrier = str(parcel.get("carrier") or "unknown")
            groups.setdefault(carrier, []).append(key)

        include_history = bool(
            self.config_entry.options.get(CONF_INCLUDE_HISTORY, False)
        )
        for carrier, keys in groups.items():
            provider = provider_instance(carrier)
            if provider is None:
                _LOGGER.debug("No tracking provider for carrier %s", carrier)
                continue
            codes = [split_key(key)[1] for key in keys]
            try:
                results = await provider.async_track(codes, self._fetch_json)
            except Exception as err:  # noqa: BLE001 - a provider must not kill polling
                _LOGGER.warning("Provider %s failed: %s", carrier, err)
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
                    self.hass.bus.async_fire(
                        EVENT_PARCEL_STATUS_CHANGED,
                        {
                            "carrier": carrier,
                            "barcode": code,
                            "status": new_status,
                            "old_status": old_status,
                        },
                    )
                if parcels[key].get("delivered") and old_status != ParcelStatus.DELIVERED.value:
                    self.hass.bus.async_fire(
                        EVENT_PARCEL_DELIVERED,
                        {"carrier": carrier, "barcode": code},
                    )

    def _next_interval(self, parcels: dict[str, dict[str, Any]]) -> timedelta:
        """Pick the next polling cadence; email is always checked eventually."""
        now = dt_util.now()
        if QUIET_WINDOW_START_HOUR <= now.hour < QUIET_WINDOW_END_HOUR:
            return timedelta(minutes=IDLE_INTERVAL_MINUTES)
        if any(parcel.get("status") == ParcelStatus.OUT_FOR_DELIVERY.value for parcel in parcels.values()):
            return timedelta(minutes=HOT_INTERVAL_MINUTES)
        if any(not parcel.get("delivered") for parcel in parcels.values()):
            return timedelta(minutes=NORMAL_INTERVAL_MINUTES)
        return timedelta(minutes=IDLE_INTERVAL_MINUTES)

    @property
    def parcel_count(self) -> int:
        """Number of persisted parcels (for diagnostics/sensors)."""
        return len(self.data or {})
