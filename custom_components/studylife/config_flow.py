"""Config flow for StudyLife.

Two kinds of entry share this one flow, chosen from a menu on the very first step:
"account" (a StudyLife server, the original and only kind before displays existed) and
"display" (an optional studylife-display e-paper panel on the LAN, talking to its own
bearer-token JSON API - a completely separate service from the StudyLife server). Any
number of either kind may be added; each display becomes its own device (see
display_entity.py) - "add a display" can be run again for a second, third, ... panel.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY, CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import StudyLifeApiAuthError, StudyLifeApiClient, StudyLifeApiError
from .const import (
    CONF_ENTRY_TYPE,
    CONF_SCAN_INTERVAL,
    DEFAULT_DISPLAY_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENTRY_TYPE_ACCOUNT,
    ENTRY_TYPE_DISPLAY,
)
from .display_api import DisplayApiAuthError, DisplayApiClient, DisplayApiError

# API key is required since the server's phase-3 auth rework: every /api endpoint needs
# either a passkey session (browser only) or a per-user API key - there is nothing Home
# Assistant could reach without one, so an empty key would only ever produce a 401.
STEP_ACCOUNT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Required(CONF_API_KEY): str,
    }
)

# Reauth only ever needs the key - the URL is known and stays untouched.
STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): str,
    }
)

STEP_DISPLAY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Required(CONF_API_TOKEN): str,
        vol.Required(CONF_VERIFY_SSL, default=True): bool,
    }
)

# Display reauth only ever needs the token - same reasoning as the account's reauth.
STEP_DISPLAY_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_TOKEN): str,
    }
)


def _normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url


def _display_title(url: str) -> str:
    """ "StudyLife Display (studylife-display.local:8795)" - the host (and port, if not
    the scheme's default) distinguishes several displays in the flat device list without
    the user having to rename anything (see display_entity.py's DeviceInfo)."""
    parts = urlsplit(url)
    host = parts.hostname or url
    if parts.port and not (parts.scheme == "https" and parts.port == 443):
        host = f"{host}:{parts.port}"
    return f"StudyLife Display ({host})"


class StudyLifeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for StudyLife."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["account", "display"])

    # -- StudyLife account ----------------------------------------------------------

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            api_key = user_input[CONF_API_KEY]
            await self.async_set_unique_id(url)
            self._abort_if_unique_id_configured()

            client = StudyLifeApiClient(
                url, async_get_clientsession(self.hass), api_key
            )
            try:
                await client.async_test_connection()
            except StudyLifeApiAuthError:
                errors["base"] = "invalid_auth"
            except StudyLifeApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title="StudyLife",
                    data={
                        CONF_URL: url,
                        CONF_API_KEY: api_key,
                        CONF_ENTRY_TYPE: ENTRY_TYPE_ACCOUNT,
                    },
                )

        return self.async_show_form(
            step_id="account",
            data_schema=STEP_ACCOUNT_SCHEMA,
            errors=errors,
            description_placeholders={"example": "http://studylife.local:8080"},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Triggered by ConfigEntryAuthFailed from either coordinator.

        For an account entry: the per-user key is long-lived and never rotates by
        itself, so landing here means the user regenerated or revoked it in the
        StudyLife app (Setup page, "Home Assistant" card) - they need to generate a key
        there and paste the new value once. For a display entry: the display's
        DISPLAY_API_TOKEN was changed, or its JSON API was turned off.
        """
        if entry_data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DISPLAY:
            return await self.async_step_display_reauth_confirm()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            client = StudyLifeApiClient(
                entry.data[CONF_URL], async_get_clientsession(self.hass), api_key
            )
            try:
                await client.async_test_connection()
            except StudyLifeApiAuthError:
                errors["base"] = "invalid_auth"
            except StudyLifeApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, CONF_API_KEY: api_key}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Triggered by the "Reconfigure" entry in the integration tile's menu.

        Unlike reauth (key-only, URL assumed correct), this lets the user fix every
        field - a typo'd URL from initial setup, or the server/display having moved to
        a new host/port.
        """
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None
        if _entry_type(entry) == ENTRY_TYPE_DISPLAY:
            return await self.async_step_display_reconfigure_confirm()
        return await self.async_step_reconfigure_confirm()

    async def async_step_reconfigure_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None

        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            api_key = user_input[CONF_API_KEY]

            # URL doubles as the unique_id. Only enforce the collision check if it actually
            # CHANGED - re-submitting the entry's own current URL must never trip "already
            # configured" against itself, but pointing it at a URL some OTHER entry already
            # owns must still be rejected.
            await self.async_set_unique_id(url)
            if self.unique_id != entry.unique_id:
                self._abort_if_unique_id_configured()

            client = StudyLifeApiClient(
                url, async_get_clientsession(self.hass), api_key
            )
            try:
                await client.async_test_connection()
            except StudyLifeApiAuthError:
                errors["base"] = "invalid_auth"
            except StudyLifeApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        CONF_URL: url,
                        CONF_API_KEY: api_key,
                        CONF_ENTRY_TYPE: ENTRY_TYPE_ACCOUNT,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure_confirm",
            data_schema=self.add_suggested_values_to_schema(
                STEP_ACCOUNT_SCHEMA,
                {
                    CONF_URL: entry.data[CONF_URL],
                    CONF_API_KEY: entry.data[CONF_API_KEY],
                },
            ),
            errors=errors,
            description_placeholders={"example": "http://studylife.local:8080"},
        )

    # -- studylife-display ------------------------------------------------------------

    async def async_step_display(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            token = user_input[CONF_API_TOKEN]
            verify_ssl = user_input[CONF_VERIFY_SSL]
            await self.async_set_unique_id(url)
            self._abort_if_unique_id_configured()

            client = DisplayApiClient(
                url,
                async_get_clientsession(self.hass, verify_ssl=verify_ssl),
                token,
                verify_ssl=verify_ssl,
            )
            try:
                await client.async_test_connection()
            except DisplayApiAuthError:
                errors["base"] = "invalid_auth"
            except DisplayApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=_display_title(url),
                    data={
                        CONF_URL: url,
                        CONF_API_TOKEN: token,
                        CONF_VERIFY_SSL: verify_ssl,
                        CONF_ENTRY_TYPE: ENTRY_TYPE_DISPLAY,
                    },
                )

        return self.async_show_form(
            step_id="display",
            data_schema=STEP_DISPLAY_SCHEMA,
            errors=errors,
            description_placeholders={"example": "http://studylife-display.local:8795"},
        )

    async def async_step_display_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None

        if user_input is not None:
            token = user_input[CONF_API_TOKEN]
            verify_ssl = entry.data.get(CONF_VERIFY_SSL, True)
            client = DisplayApiClient(
                entry.data[CONF_URL],
                async_get_clientsession(self.hass, verify_ssl=verify_ssl),
                token,
                verify_ssl=verify_ssl,
            )
            try:
                await client.async_test_connection()
            except DisplayApiAuthError:
                errors["base"] = "invalid_auth"
            except DisplayApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, CONF_API_TOKEN: token}
                )

        return self.async_show_form(
            step_id="display_reauth_confirm",
            data_schema=STEP_DISPLAY_REAUTH_SCHEMA,
            errors=errors,
        )

    async def async_step_display_reconfigure_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None

        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            token = user_input[CONF_API_TOKEN]
            verify_ssl = user_input[CONF_VERIFY_SSL]

            await self.async_set_unique_id(url)
            if self.unique_id != entry.unique_id:
                self._abort_if_unique_id_configured()

            client = DisplayApiClient(
                url,
                async_get_clientsession(self.hass, verify_ssl=verify_ssl),
                token,
                verify_ssl=verify_ssl,
            )
            try:
                await client.async_test_connection()
            except DisplayApiAuthError:
                errors["base"] = "invalid_auth"
            except DisplayApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        CONF_URL: url,
                        CONF_API_TOKEN: token,
                        CONF_VERIFY_SSL: verify_ssl,
                        CONF_ENTRY_TYPE: ENTRY_TYPE_DISPLAY,
                    },
                )

        return self.async_show_form(
            step_id="display_reconfigure_confirm",
            data_schema=self.add_suggested_values_to_schema(
                STEP_DISPLAY_SCHEMA,
                {
                    CONF_URL: entry.data[CONF_URL],
                    CONF_API_TOKEN: entry.data[CONF_API_TOKEN],
                    CONF_VERIFY_SSL: entry.data.get(CONF_VERIFY_SSL, True),
                },
            ),
            errors=errors,
            description_placeholders={"example": "http://studylife-display.local:8795"},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> StudyLifeOptionsFlow:
        return StudyLifeOptionsFlow(config_entry)


def _entry_type(entry: ConfigEntry) -> str:
    return entry.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_ACCOUNT)


class StudyLifeOptionsFlow(OptionsFlow):
    """Handle StudyLife options (poll interval) - the same one field for both entry
    kinds, just a different default (see const.py's DEFAULT_DISPLAY_SCAN_INTERVAL)."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        default_interval = (
            DEFAULT_DISPLAY_SCAN_INTERVAL
            if _entry_type(self._config_entry) == ENTRY_TYPE_DISPLAY
            else DEFAULT_SCAN_INTERVAL
        )
        current = self._config_entry.options.get(CONF_SCAN_INTERVAL, default_interval)
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    int, vol.Range(min=10, max=3600)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
