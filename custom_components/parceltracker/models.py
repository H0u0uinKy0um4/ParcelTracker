"""Shared data models.

``ParcelStatus`` deliberately uses the same vocabulary as the other
``ha-parcel-integrations`` carriers. Do not rename members: automations and
the optional Parcel Aggregator match on these exact strings.

This module imports nothing outside the standard library so it can be unit
tested without Home Assistant installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status shared across all HA parcel integrations."""

    REGISTERED = "registered"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    AT_PICKUP_POINT = "at_pickup_point"
    DELIVERED = "delivered"
    RETURNING = "returning"
    PROBLEM = "problem"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class ProviderParcel:
    """Normalised-but-still-carrier-specific result from a tracking provider.

    Providers translate their own status codes into canonical ``events``
    (``id``/``date``/``time``/``text``/``idIcon``) and set ``raw`` to the source
    payloads. :func:`parceltracker.parcels.normalize_parcel` turns this into the
    canonical dict exposed by the entities.
    """

    carrier: str
    barcode: str
    events: list[dict[str, Any]] = field(default_factory=list)
    sender: str | None = None
    receiver: str | None = None
    planned_from: str | None = None
    planned_to: str | None = None
    pickup_point: str | None = None
    stored_to: str | None = None
    weight: float | None = None
    dimensions: Any = None
    url: str | None = None
    raw: dict[str, Any] | None = None
    not_found: bool = False
    error: str | None = None
