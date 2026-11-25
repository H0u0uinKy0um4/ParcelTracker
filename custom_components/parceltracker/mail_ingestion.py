"""Mailbox ingestion behind local-substitutable seams."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable

from .imap_client import ImapClient, MailboxError, parse_allowlist
from .parcels import new_local_parcel
from .providers import detect_provider
from .storage import parcel_key

_LOGGER = logging.getLogger(__name__)

EventEmitter = Callable[[str, dict[str, Any]], None]


@dataclass(slots=True)
class MailIngestionResult:
    """Outcome of one mailbox polling pass."""

    last_uid: int
    scanned: int
    matched: int
    error: str | None = None


async def async_ingest_mail(
    *,
    parcels: dict[str, dict[str, Any]],
    last_uid: int,
    email: str,
    app_password: str,
    imap_host: str,
    imap_port: int,
    mailbox: str,
    mark_seen: bool,
    sender_allowlist: str | None,
    extra_patterns: tuple[str, ...] | list[str],
    run_in_executor: Callable[..., Any],
    fire_event: EventEmitter,
    now_iso: Callable[[], str],
    event_registered: str,
    logger: logging.Logger = _LOGGER,
) -> MailIngestionResult:
    """Read new forwarded messages and add newly discovered parcels."""
    client = ImapClient(
        email,
        app_password,
        imap_host,
        imap_port,
        mailbox,
    )

    try:
        result = await run_in_executor(
            client.fetch_new_messages,
            last_uid,
            50,
            bool(mark_seen),
            parse_allowlist(sender_allowlist),
            tuple(extra_patterns),
        )
    except MailboxError as err:
        logger.warning("ParcelTracker mailbox poll failed: %s", err)
        return MailIngestionResult(
            last_uid=last_uid, scanned=0, matched=0, error=err.__class__.__name__
        )

    scanned = result.scanned
    matched = len(result.messages)
    now = now_iso()
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

        provider = detect_provider(parsed.tracking_code)
        parcels[key] = new_local_parcel(
            parsed.carrier or (provider.CARRIER_ID if provider else "unknown"),
            parsed.tracking_code,
            first_seen=now,
            pickup_code=parsed.pickup_code,
            url=provider().tracking_url(parsed.tracking_code) if provider else None,
        )
        fire_event(
            event_registered,
            {"carrier": parcels[key]["carrier"], "barcode": parsed.tracking_code},
        )
        logger.info(
            "New parcel from email: carrier=%s barcode=%s",
            parcels[key]["carrier"],
            parsed.tracking_code,
        )
    return MailIngestionResult(
        last_uid=max(last_uid, result.last_uid), scanned=scanned, matched=matched
    )