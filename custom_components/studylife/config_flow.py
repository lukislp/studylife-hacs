"""Config flow for StudyLife."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import StudyLifeApiAuthError, StudyLifeApiClient, StudyLifeApiError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

# API key is required since the server's phase-3 auth rework: every /api endpoint needs
# either a passkey session (browser only) or a per-user API key - there is nothing Home
# Assistant could reach without one, so an empty key would only ever produce a 401.
STEP_USER_SCHEMA = vol.Schema(
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


def _normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url


class StudyLifeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for StudyLife."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            api_key = user_input[CONF_API_KEY]
            await self.async_set_unique_id(url)
            self._abort_if_unique_id_configured()

            client = StudyLifeApiClient(url, async_get_clientsession(self.hass), api_key)
            try:
                await client.async_test_connection()
            except StudyLifeApiAuthError:
                errors["base"] = "invalid_auth"
            except StudyLifeApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title="StudyLife", data={CONF_URL: url, CONF_API_KEY: api_key}
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
            description_placeholders={"example": "http://studylife.local:8080"},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Triggered by ConfigEntryAuthFailed from the coordinator.

        The per-user key is long-lived and never rotates by itself, so landing here
        means the user regenerated or revoked it in the StudyLife app (Setup page,
        "Home Assistant" card) - they need to generate a key there and paste the new
        value once.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
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

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Triggered by the "Reconfigure" entry in the integration tile's menu.

        Unlike reauth (key-only, URL assumed correct), this lets the user fix BOTH fields -
        a typo'd URL from initial setup, or the server having moved to a new host/port.
        """
        return await self.async_step_reconfigure_confirm()

    async def async_step_reconfigure_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
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

            client = StudyLifeApiClient(url, async_get_clientsession(self.hass), api_key)
            try:
                await client.async_test_connection()
            except StudyLifeApiAuthError:
                errors["base"] = "invalid_auth"
            except StudyLifeApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data={CONF_URL: url, CONF_API_KEY: api_key}
                )

        return self.async_show_form(
            step_id="reconfigure_confirm",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, {CONF_URL: entry.data[CONF_URL], CONF_API_KEY: entry.data[CONF_API_KEY]}
            ),
            errors=errors,
            description_placeholders={"example": "http://studylife.local:8080"},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> StudyLifeOptionsFlow:
        return StudyLifeOptionsFlow(config_entry)


class StudyLifeOptionsFlow(OptionsFlow):
    """Handle StudyLife options (poll interval)."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self._config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        schema = vol.Schema(
            {vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(int, vol.Range(min=10, max=3600))}
        )
        return self.async_show_form(step_id="init", data_schema=schema)
