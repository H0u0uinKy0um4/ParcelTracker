"""Config and options flow for ParcelTracker.

The email address and password/app password live in the config entry data
(HA's ``.storage``); all non-secret settings live in options. Credentials are
never written to attributes, logs or diagnostics. Any IMAP provider is
supported via the configurable host and port.
"""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_APP_PASSWORD,
    CONF_EXTRA_TRACKING_REGEX,
    CONF_IMAP_HOST,
    CONF_IMAP_PORT,
    CONF_INCLUDE_HISTORY,
    CONF_MAILBOX,
    CONF_MARK_SEEN,
    CONF_SCAN_INTERVAL,
    CONF_SENDER_ALLOWLIST,
    DEFAULT_EXTRA_TRACKING_REGEX,
    DEFAULT_IMAP_HOST,
    DEFAULT_IMAP_PORT,
    DEFAULT_INCLUDE_HISTORY,
    DEFAULT_MAILBOX,
    DEFAULT_MARK_SEEN,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SENDER_ALLOWLIST,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .imap_client import ImapClient, MailboxError

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _credentials_schema(values: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_EMAIL, default=values.get(CONF_EMAIL, "")): str,
            vol.Required(
                CONF_APP_PASSWORD, default=values.get(CONF_APP_PASSWORD, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }
    )


def _general_schema(values: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_IMAP_HOST, default=values.get(CONF_IMAP_HOST, DEFAULT_IMAP_HOST)
            ): str,
            vol.Required(
                CONF_IMAP_PORT, default=values.get(CONF_IMAP_PORT, DEFAULT_IMAP_PORT)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(
                CONF_MAILBOX, default=values.get(CONF_MAILBOX, DEFAULT_MAILBOX)
            ): str,
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=values.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=240)),
            vol.Optional(
                CONF_SENDER_ALLOWLIST,
                default=values.get(CONF_SENDER_ALLOWLIST, DEFAULT_SENDER_ALLOWLIST),
            ): str,
            vol.Optional(
                CONF_EXTRA_TRACKING_REGEX,
                default=values.get(
                    CONF_EXTRA_TRACKING_REGEX, DEFAULT_EXTRA_TRACKING_REGEX
                ),
            ): str,
            vol.Optional(
                CONF_MARK_SEEN,
                default=values.get(CONF_MARK_SEEN, DEFAULT_MARK_SEEN),
            ): bool,
            vol.Optional(
                CONF_INCLUDE_HISTORY,
                default=values.get(CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY),
            ): bool,
        }
    )


class ParcelTrackerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial burner-mailbox setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Collect credentials and mailbox settings, then verify IMAP login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email_address = str(user_input[CONF_EMAIL]).strip().casefold()
            host = str(user_input.get(CONF_IMAP_HOST, DEFAULT_IMAP_HOST)).strip()
            if not _EMAIL_RE.fullmatch(email_address):
                errors[CONF_EMAIL] = "invalid_email"
            elif not host:
                errors[CONF_IMAP_HOST] = "invalid_host"
            else:
                client = ImapClient(
                    email_address,
                    user_input[CONF_APP_PASSWORD],
                    host,
                    int(user_input.get(CONF_IMAP_PORT, DEFAULT_IMAP_PORT)),
                    user_input.get(CONF_MAILBOX, DEFAULT_MAILBOX),
                )
                try:
                    await self.hass.async_add_executor_job(client.validate_login)
                except MailboxError as err:
                    text = str(err).upper()
                    errors["base"] = (
                        "invalid_auth"
                        if "AUTH" in text or "LOGIN" in text or "PASSWORD" in text
                        else "cannot_connect"
                    )
                else:
                    await self.async_set_unique_id(email_address)
                    self._abort_if_unique_id_configured()

                    data = {
                        CONF_EMAIL: email_address,
                        CONF_APP_PASSWORD: user_input[CONF_APP_PASSWORD],
                    }
                    options = {
                        key: value
                        for key, value in user_input.items()
                        if key not in (CONF_EMAIL, CONF_APP_PASSWORD)
                    }
                    options[CONF_IMAP_HOST] = host
                    return self.async_create_entry(
                        title=email_address, data=data, options=options
                    )

        defaults = {
            CONF_IMAP_HOST: DEFAULT_IMAP_HOST,
            CONF_IMAP_PORT: DEFAULT_IMAP_PORT,
            CONF_MAILBOX: DEFAULT_MAILBOX,
            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            CONF_SENDER_ALLOWLIST: DEFAULT_SENDER_ALLOWLIST,
            CONF_EXTRA_TRACKING_REGEX: DEFAULT_EXTRA_TRACKING_REGEX,
            CONF_MARK_SEEN: DEFAULT_MARK_SEEN,
            CONF_INCLUDE_HISTORY: DEFAULT_INCLUDE_HISTORY,
        }
        schema = vol.Schema(
            {
                **_credentials_schema(user_input or {}).schema,
                **_general_schema(defaults | (user_input or {})).schema,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class ParcelTrackerOptionsFlow(config_entries.OptionsFlow):
    """Options for an existing mailbox entry."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Offer general settings or credential updates."""
        return self.async_show_menu(
            step_id="init", menu_options=["general", "credentials"]
        )

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Update non-secret settings."""
        if user_input is not None:
            host = str(user_input.get(CONF_IMAP_HOST, DEFAULT_IMAP_HOST)).strip()
            if not host:
                return self.async_show_form(
                    step_id="general",
                    data_schema=_general_schema(user_input),
                    errors={CONF_IMAP_HOST: "invalid_host"},
                )
            user_input[CONF_IMAP_HOST] = host
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="general", data_schema=_general_schema(dict(self._entry.options))
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Update the mailbox credentials without re-creating the entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email_address = str(user_input[CONF_EMAIL]).strip().casefold()
            if not _EMAIL_RE.fullmatch(email_address):
                errors[CONF_EMAIL] = "invalid_email"
            else:
                client = ImapClient(
                    email_address,
                    user_input[CONF_APP_PASSWORD],
                    str(self._entry.options.get(CONF_IMAP_HOST, DEFAULT_IMAP_HOST)).strip(),
                    int(
                        self._entry.options.get(
                            CONF_IMAP_PORT, DEFAULT_IMAP_PORT
                        )
                    ),
                    self._entry.options.get(CONF_MAILBOX, DEFAULT_MAILBOX),
                )
                try:
                    await self.hass.async_add_executor_job(client.validate_login)
                except MailboxError as err:
                    text = str(err).upper()
                    errors["base"] = (
                        "invalid_auth"
                        if "AUTH" in text or "LOGIN" in text or "PASSWORD" in text
                        else "cannot_connect"
                    )
                else:
                    self.hass.config_entries.async_update_entry(
                        self._entry,
                        data={
                            CONF_EMAIL: email_address,
                            CONF_APP_PASSWORD: user_input[CONF_APP_PASSWORD],
                        },
                    )
                    return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="credentials",
            data_schema=_credentials_schema(
                {
                    CONF_EMAIL: self._entry.data.get(CONF_EMAIL, ""),
                    CONF_APP_PASSWORD: "",
                }
            ),
            errors=errors,
        )


@callback
def async_get_options_flow(
    config_entry: config_entries.ConfigEntry,
) -> ParcelTrackerOptionsFlow:
    """Return the options flow handler for an existing entry."""
    return ParcelTrackerOptionsFlow(config_entry)
