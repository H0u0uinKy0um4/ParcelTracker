# AGENTS.md — ParcelTracker

Guidance for AI agents and contributors. Read this before changing code.

## 30-second context

- HACS custom integration, domain `parceltracker`, installed as
  `custom_components/parceltracker/`. No service, no Docker, no add-on.
- One config entry = one Gmail burner mailbox (email + app password).
- Carrier logic lives in `providers/`. One provider per carrier,
  registered with `@register_provider`.
- No third-party Python requirements: `manifest.json` has `"requirements": []`.
- Docs and translations are bilingual (EN + CS); keep both in sync.
- The canonical parcel shape and status vocabulary match
  `ha-parcel-integrations`, so `ha-parcel-aggregator` can read our parcels.
  Do not rename statuses or fields.

## Layout

| Module | Responsibility |
|---|---|
| `const.py` | Constants; string-only so pure modules import without HA |
| `models.py` | `ParcelStatus`, `ProviderParcel` |
| `parsers.py` | Pure email decoding and tracking-number extraction |
| `imap_client.py` | stdlib IMAP reader; runs in an HA executor |
| `providers/__init__.py` | Provider protocol and registry |
| `providers/ceska_posta.py` | Keyless ČP/Balíkovna JSON client |
| `parcels.py` | Canonical shape, status rank, history |
| `storage.py` | Pure key/merge helpers for HA `Store` |
| `coordinator.py` | Mailbox + provider polling, events, dynamic cadence |
| `sensor.py`, `binary_sensor.py`, `button.py` | Carrier-agnostic entities |

## Rules

1. **Never put raw subject/body, Message-ID or full email addresses into
   entity attributes, events or logs.** Only extracted numbers and carrier
   data are safe.
2. **Never log credentials**, even at debug level.
3. Provider code must be pure: inject the HTTP client as `fetch_json`. A
   failed fetch returns `ProviderParcel(error=...)`; never raise for one code.
4. Storage keys are `carrier:barcode`. Do not use barcode alone — more
   carriers are coming.
5. Statuses are the highest rank reached, not the last event. Keep the
   exclusion list for notification and designation codes.
6. Entities must not contain carrier-specific strings; use
   `translation_key` and canonical fields.
7. Tests import pure modules through `tests/bootstrap.py` and run without
   Home Assistant installed. Real messages in `exampleMails/*.eml` are parser
   fixtures — keep them redacted before publishing the repository.

## Adding a Czech carrier

1. Add `providers/<carrier>.py` with `CARRIER_ID`, `CARRIER_NAME`,
   `match_tracking_code()`, `tracking_url()` and `async_track()`.
2. Register it with `@register_provider` and import it from
   `providers/__init__.py`.
3. If the email format needs it, add label patterns to `parsers.py`.
4. Add a fixture and tests under `tests/`, a row in the README carrier table,
   and EN/CS translation keys if new entities are introduced.

Do not change `coordinator.py`, `sensor.py` or `storage.py` for a new carrier.

## Verification

```bash
python -m unittest discover -s tests -v
```
