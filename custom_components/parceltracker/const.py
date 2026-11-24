"""Constants for the ParcelTracker integration."""

from __future__ import annotations

DOMAIN = "parceltracker"

# Kept as strings so this module can be imported without Home Assistant.
# __init__.py converts them to Platform members at setup time.
PLATFORMS = ("sensor", "binary_sensor", "button")

# Config entry data (secrets) and options (everything else).
CONF_EMAIL = "email"
CONF_APP_PASSWORD = "app_password"
CONF_IMAP_HOST = "imap_host"
CONF_IMAP_PORT = "imap_port"
CONF_MAILBOX = "mailbox"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SENDER_ALLOWLIST = "sender_allowlist"
CONF_EXTRA_TRACKING_REGEX = "extra_tracking_regex"
CONF_MARK_SEEN = "mark_seen"
CONF_INCLUDE_HISTORY = "include_history"

DEFAULT_IMAP_HOST = "imap.gmail.com"
DEFAULT_IMAP_PORT = 993
DEFAULT_MAILBOX = "INBOX"
DEFAULT_SCAN_INTERVAL = 15
MIN_SCAN_INTERVAL = 5
DEFAULT_SENDER_ALLOWLIST = ""
DEFAULT_EXTRA_TRACKING_REGEX = ""
DEFAULT_MARK_SEEN = True
DEFAULT_INCLUDE_HISTORY = False

STORAGE_VERSION = 1
PARCELS_KEY = "parcels"
LAST_UID_KEY = "last_uid"

# Dynamic polling. Email polling never stops completely; it only slows down.
HOT_INTERVAL_MINUTES = 15
NORMAL_INTERVAL_MINUTES = 30
IDLE_INTERVAL_MINUTES = 60
QUIET_WINDOW_START_HOUR = 0
QUIET_WINDOW_END_HOUR = 6

HISTORY_MAX_EVENTS = 20

ATTR_PICKUP_CODE = "pickup_code"
ATTR_BARCODE = "barcode"
ATTR_CARRIER = "carrier"
ATTR_FIRST_SEEN = "first_seen"

EVENT_PARCEL_REGISTERED = f"{DOMAIN}_parcel_registered"
EVENT_PARCEL_STATUS_CHANGED = f"{DOMAIN}_parcel_status_changed"
EVENT_PARCEL_DELIVERED = f"{DOMAIN}_parcel_delivered"
