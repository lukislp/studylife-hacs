"""Config flow for StudyLife.

Two kinds of entry share this one flow, chosen from a menu on the very first step:
"account" (a StudyLife server, the original and only kind before displays existed) and
"display" (an optional studylife-display e-paper panel on the LAN, talking to its own
bearer-token JSON API - a completely separate service from the StudyLife server). Any
number of either kind may be added; each display becomes its own device (see
display_entity.py) - "add a display" can be run again for a second, third, ... panel.
"""

from __future__ import annotations

import asyncio
import logging
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
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import (
    StudyLifeApiAuthError,
    StudyLifeApiClient,
    StudyLifeApiError,
    is_valid_instance_id,
)
from .const import (
    CONF_DISPLAY_ID,
    CONF_ENTRY_TYPE,
    CONF_INSTANCE_ID,
    CONF_SCAN_INTERVAL,
    DEFAULT_DISPLAY_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENTRY_TYPE_ACCOUNT,
    ENTRY_TYPE_DISPLAY,
)
from .display_api import DisplayApiAuthError, DisplayApiClient, DisplayApiError
from .display_coordinator import DisplayCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_TYPE_DISPLAY = "_studylife-display._tcp.local."
SERVICE_TYPE_SERVER = "_studylife._tcp.local."

# Budget for the one-off GET /api/instance asked of an existing server entry that has not
# stored its instance id yet, while a discovery is being evaluated.
INSTANCE_PROBE_TIMEOUT = 5

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


def _is_absolute_http_url(url: str) -> bool:
    parts = urlsplit(url)
    try:
        parts.port  # noqa: B018 - raises ValueError on an invalid port
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def _host_and_port(url: str) -> tuple[str, int]:
    """Lower-cased host and effective port of a URL, tolerating a missing scheme.

    A hand-added entry may hold "192.168.5.61:8795" (no scheme), which urlsplit reads as
    having no host - so normalise first. The scheme's default port (80/443) is made
    explicit so "https://h" and "https://h:443" compare equal.
    """
    parts = urlsplit(_normalize_url(url))
    default = 443 if parts.scheme == "https" else 80
    try:
        port = parts.port or default
    except ValueError:
        port = default
    return (parts.hostname or "").lower(), port


def _server_title_from_flow_name(name: str) -> str:
    """The DNS-SD instance name without the service type suffix."""
    return name.split("._studylife.", 1)[0]


def _display_title_from_flow_name(name: str) -> str:
    """The DNS-SD instance name without the service type suffix."""
    return name.split("._studylife-display.", 1)[0]


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

    _discovered_url: str = ""
    _discovered_tls: bool = False

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

            error = await self._async_test_account(url, api_key)
            if error is None:
                return self._create_account_entry(url, api_key)
            errors["base"] = error

        return self.async_show_form(
            step_id="account",
            data_schema=STEP_ACCOUNT_SCHEMA,
            errors=errors,
            description_placeholders={"example": "http://studylife.local:8080"},
        )

    async def _async_test_account(self, url: str, api_key: str) -> str | None:
        """Probe the server with the key; the error key for the form, None on success."""
        client = StudyLifeApiClient(url, async_get_clientsession(self.hass), api_key)
        try:
            await client.async_test_connection()
        except StudyLifeApiAuthError:
            return "invalid_auth"
        except StudyLifeApiError:
            return "cannot_connect"
        return None

    def _create_account_entry(self, url: str, api_key: str) -> ConfigFlowResult:
        return self.async_create_entry(
            title="StudyLife",
            data={
                CONF_URL: url,
                CONF_API_KEY: api_key,
                CONF_ENTRY_TYPE: ENTRY_TYPE_ACCOUNT,
            },
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

            error = await self._async_test_display(url, token, verify_ssl)
            if error is None:
                return self._create_display_entry(url, token, verify_ssl)
            errors["base"] = error

        return self.async_show_form(
            step_id="display",
            data_schema=STEP_DISPLAY_SCHEMA,
            errors=errors,
            description_placeholders={"example": "http://studylife-display.local:8795"},
        )

    async def _async_test_display(
        self, url: str, token: str, verify_ssl: bool
    ) -> str | None:
        """Probe the display's API; the error key for the form, None on success."""
        client = DisplayApiClient(
            url,
            async_get_clientsession(self.hass, verify_ssl=verify_ssl),
            token,
            verify_ssl=verify_ssl,
        )
        try:
            await client.async_test_connection()
        except DisplayApiAuthError:
            return "invalid_auth"
        except DisplayApiError:
            return "cannot_connect"
        return None

    def _create_display_entry(
        self, url: str, token: str, verify_ssl: bool
    ) -> ConfigFlowResult:
        return self.async_create_entry(
            title=_display_title(url),
            data={
                CONF_URL: url,
                CONF_API_TOKEN: token,
                CONF_VERIFY_SSL: verify_ssl,
                CONF_ENTRY_TYPE: ENTRY_TYPE_DISPLAY,
            },
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Dispatch on the announced service type: a studylife-display
        (_studylife-display._tcp) or a StudyLife server (_studylife._tcp)."""
        service_type = discovery_info.type.lower()
        if service_type == SERVICE_TYPE_SERVER:
            return await self._async_zeroconf_server(discovery_info)
        if service_type == SERVICE_TYPE_DISPLAY:
            return await self._async_zeroconf_display(discovery_info)
        return self.async_abort(reason="invalid_discovery")

    async def _async_zeroconf_display(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """A studylife-display announced itself via DNS-SD (_studylife-display._tcp)."""
        properties = discovery_info.properties
        tls = str(properties.get("tls", "false")).lower() == "true"
        host = discovery_info.hostname.rstrip(".") or str(discovery_info.ip_address)
        port = discovery_info.port or 8795
        url = _normalize_url(f"{'https' if tls else 'http'}://{host}:{port}")
        announced_id = str(properties.get("id") or "").strip() or None
        _LOGGER.debug(
            "Display discovery: name=%s hostname=%s port=%s ip_addresses=%s "
            "properties=%s url=%s",
            discovery_info.name,
            discovery_info.hostname,
            port,
            [str(ip) for ip in discovery_info.ip_addresses],
            dict(properties),
            url,
        )

        # Address-independent identity first: a display that publishes its stable id
        # (TXT "id") is the same display as an entry holding that id, whatever address
        # or name it announces now. The id is read from the entry data, or - for an
        # entry that has not stored it yet (not polled since the update) - from its
        # loaded coordinator. A changed address is deliberately NOT written into the
        # entry silently (the user can reconfigure it); we only avoid offering the
        # display a second time.
        if announced_id and self._matches_configured_display(announced_id):
            return self.async_abort(reason="already_configured")

        # Same unique_id scheme as the manual display step (the normalized URL), so a
        # manually added display is recognised; a changed address updates its URL.
        await self.async_set_unique_id(url)
        self._abort_if_unique_id_configured(updates={CONF_URL: url})

        # The same display may have been added by hand under another name (IP address
        # instead of mDNS host name): match on the host part too.
        known_hosts = {host.lower(), *(str(ip) for ip in discovery_info.ip_addresses)}
        for entry in self._async_current_entries(include_ignore=False):
            if _entry_type(entry) != ENTRY_TYPE_DISPLAY:
                continue
            # An entry added by hand may hold the address without a scheme
            # ("192.168.5.61:8795"), which urlsplit reads as no host at all - normalise
            # it the way the manual step does before taking the host part.
            stored_url = _normalize_url(str(entry.data.get(CONF_URL, "")))
            entry_host = (urlsplit(stored_url).hostname or "").lower()
            _LOGGER.debug(
                "Display discovery: entry %s stored host=%s matched by host=%s",
                entry.entry_id,
                entry_host,
                entry_host in known_hosts,
            )
            if entry_host in known_hosts:
                return self.async_abort(reason="already_configured")

        if str(properties.get("api", "true")).lower() == "false":
            return self.async_abort(reason="api_disabled")

        self._discovered_url = url
        self._discovered_tls = tls
        self.context["title_placeholders"] = {
            "name": _display_title_from_flow_name(discovery_info.name)
        }
        return await self.async_step_zeroconf_confirm()

    def _matches_configured_display(self, announced_id: str) -> bool:
        """True when a display entry already has this stable id (stored or live)."""
        loaded = self.hass.data.get(DOMAIN, {})
        for entry in self._async_current_entries(include_ignore=False):
            if _entry_type(entry) != ENTRY_TYPE_DISPLAY:
                continue
            coordinator = loaded.get(entry.entry_id)
            live_id = (
                coordinator.data.display_id
                if isinstance(coordinator, DisplayCoordinator) and coordinator.data
                else None
            )
            by_stored = entry.data.get(CONF_DISPLAY_ID) == announced_id
            by_live = live_id == announced_id
            _LOGGER.debug(
                "Display discovery: entry %s stored host=%s stored id=%s "
                "live id=%s matched by id=%s",
                entry.entry_id,
                urlsplit(_normalize_url(str(entry.data.get(CONF_URL, "")))).hostname,
                entry.data.get(CONF_DISPLAY_ID),
                live_id,
                by_stored or by_live,
            )
            if by_stored or by_live:
                return True
        return False

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        url = self._discovered_url

        if user_input is not None:
            token = user_input[CONF_API_TOKEN]
            verify_ssl = user_input[CONF_VERIFY_SSL]
            error = await self._async_test_display(url, token, verify_ssl)
            if error is None:
                return self._create_display_entry(url, token, verify_ssl)
            errors["base"] = error

        # A discovered https display most likely serves a self-signed certificate.
        schema = vol.Schema(
            {
                vol.Required(CONF_API_TOKEN): str,
                vol.Required(CONF_VERIFY_SSL, default=not self._discovered_tls): bool,
            }
        )
        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"url": url},
        )

    async def _async_zeroconf_server(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """A StudyLife server announced itself via DNS-SD (_studylife._tcp)."""
        properties = discovery_info.properties
        host = discovery_info.hostname.rstrip(".") or str(discovery_info.ip_address)
        port = discovery_info.port

        # The TXT "url" is what Home Assistant must use: the server is usually behind an
        # ingress/gateway, so it is NOT necessarily the announcing host. Only without it
        # fall back to the announcing host/port.
        txt_url = str(properties.get("url") or "").strip()
        if txt_url:
            if not _is_absolute_http_url(txt_url):
                return self.async_abort(reason="invalid_discovery")
            url = _normalize_url(txt_url)
        else:
            tls = str(properties.get("https", "false")).lower() == "true"
            netloc = f"{host}:{port}" if port else host
            url = _normalize_url(f"{'https' if tls else 'http'}://{netloc}")
        if not _is_absolute_http_url(url):
            return self.async_abort(reason="invalid_discovery")

        # Address-independent identity first: the server's stable instance id (TXT "id")
        # identifies an already configured server whatever URL it is configured under.
        announced_id = str(properties.get("id") or "").strip().lower()
        if is_valid_instance_id(announced_id):
            if await self._async_matches_configured_server(announced_id, url):
                return self.async_abort(reason="already_configured")
        else:
            announced_id = ""

        await self.async_set_unique_id(url)
        self._abort_if_unique_id_configured(updates={CONF_URL: url})

        # Existing account entries use unique_id=<URL as typed>, so the unique_id check
        # above is only a first filter. Duplicate rule (display entries and ignored
        # entries are skipped): an existing server is the same one when
        #   (a) its host AND effective port equal those of the discovered URL (default
        #       ports 80/443 count as no port; hosts compare case-insensitively; a URL
        #       stored without scheme is normalised first), or
        #   (b) its host is the announcing host name / one of its IPs AND its effective
        #       port equals the announced port (the same machine reached directly).
        # The same host on a DIFFERENT port is another server and is still offered.
        discovered = _host_and_port(url)
        announcing_hosts = {
            host.lower(),
            *(str(ip) for ip in discovery_info.ip_addresses),
        }
        for entry in self._async_current_entries(include_ignore=False):
            if _entry_type(entry) != ENTRY_TYPE_ACCOUNT:
                continue
            entry_host, entry_port = _host_and_port(str(entry.data.get(CONF_URL, "")))
            if (entry_host, entry_port) == discovered or (
                port and entry_host in announcing_hosts and entry_port == port
            ):
                return self.async_abort(reason="already_configured")

        self._discovered_url = url
        self.context["title_placeholders"] = {
            "name": _server_title_from_flow_name(discovery_info.name)
        }
        return await self.async_step_zeroconf_server_confirm()

    async def _async_matches_configured_server(
        self, announced_id: str, announced_url: str
    ) -> bool:
        """True when an account entry is the server with this stable instance id.

        Entries that have not stored their id yet (not set up since the update, or the
        fetch failed then) are asked once - concurrently, with a short timeout, errors
        ignored - and the answer is stored on the entry, so the first discovery after
        the update already recognises the server."""
        entries = [
            entry
            for entry in self._async_current_entries(include_ignore=False)
            if _entry_type(entry) == ENTRY_TYPE_ACCOUNT
        ]
        _LOGGER.debug(
            "Server discovery: announced id=%s url=%s, %d account entries",
            announced_id,
            announced_url,
            len(entries),
        )
        missing = [e for e in entries if not e.data.get(CONF_INSTANCE_ID)]
        if missing:
            await asyncio.gather(*(self._async_probe_instance_id(e) for e in missing))

        matched = False
        for entry in entries:
            stored_id = entry.data.get(CONF_INSTANCE_ID)
            by_id = stored_id == announced_id
            _LOGGER.debug(
                "Server discovery: entry %s stored host=%s stored id=%s matched by id=%s",
                entry.entry_id,
                urlsplit(_normalize_url(str(entry.data.get(CONF_URL, "")))).hostname,
                stored_id,
                by_id,
            )
            matched = matched or by_id
        return matched

    async def _async_probe_instance_id(self, entry: ConfigEntry) -> None:
        """One-off GET /api/instance against an entry's own URL; stores the id."""
        client = StudyLifeApiClient(
            _normalize_url(str(entry.data.get(CONF_URL, ""))),
            async_get_clientsession(self.hass),
            entry.data.get(CONF_API_KEY),
        )
        try:
            async with asyncio.timeout(INSTANCE_PROBE_TIMEOUT):
                info = await client.async_get_instance()
        except Exception:
            _LOGGER.debug(
                "Server discovery: probing entry %s failed",
                entry.entry_id,
                exc_info=True,
            )
            return
        instance_id = info.get("id") if info else None
        if instance_id:
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_INSTANCE_ID: instance_id}
            )

    async def async_step_zeroconf_server_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        url = self._discovered_url

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            error = await self._async_test_account(url, api_key)
            if error is None:
                return self._create_account_entry(url, api_key)
            errors["base"] = error

        return self.async_show_form(
            step_id="zeroconf_server_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"url": url},
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
