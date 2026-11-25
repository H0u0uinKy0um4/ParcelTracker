"""Diagnostics support for ParcelTracker."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import ParcelTrackerRuntime
from .const import CONF_APP_PASSWORD, CONF_EMAIL, CONF_IMAP_HOST, DOMAIN

TO_REDACT = {CONF_APP_PASSWORD, CONF_EMAIL, CONF_IMAP_HOST}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return safe diagnostics for a config entry."""
    runtime: ParcelTrackerRuntime | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    coordinator = runtime.coordinator if runtime else None

    data = {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        }
    }
    if coordinator is None:
        return data

    data["coordinator"] = {
        "last_scan_at": coordinator.last_scan_at,
        "last_scan_scanned": coordinator.last_scan_scanned,
        "last_scan_matched": coordinator.last_scan_matched,
        "last_uid": coordinator._last_uid,
        "last_error": coordinator.last_error,
        "parcel_count": coordinator.parcel_count,
        "update_interval_seconds": (
            int(coordinator.update_interval.total_seconds())
            if coordinator.update_interval
            else None
        ),
    }
    return data
