"""Parcel lifecycle and polling-cadence helpers."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from .const import (
    HOT_INTERVAL_MINUTES,
    IDLE_INTERVAL_MINUTES,
    NORMAL_INTERVAL_MINUTES,
    QUIET_WINDOW_END_HOUR,
    QUIET_WINDOW_START_HOUR,
)
from .models import ParcelStatus


def next_poll_interval(parcels: dict[str, dict[str, Any]], now) -> timedelta:
    """Pick the next polling cadence; email is always checked eventually."""
    if QUIET_WINDOW_START_HOUR <= now.hour < QUIET_WINDOW_END_HOUR:
        return timedelta(minutes=IDLE_INTERVAL_MINUTES)
    if any(
        parcel.get("status") == ParcelStatus.OUT_FOR_DELIVERY.value
        for parcel in parcels.values()
    ):
        return timedelta(minutes=HOT_INTERVAL_MINUTES)
    if any(not parcel.get("delivered") for parcel in parcels.values()):
        return timedelta(minutes=NORMAL_INTERVAL_MINUTES)
    return timedelta(minutes=IDLE_INTERVAL_MINUTES)