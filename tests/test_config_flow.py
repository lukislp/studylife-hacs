"""Tests for the StudyLife config flow (menu, account, display, reauth, reconfigure,
options)."""

from __future__ import annotations

from datetime import timedelta
from ipaddress import ip_address
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.const import CONF_API_KEY, CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife.api import StudyLifeApiAuthError, StudyLifeApiError
from custom_components.studylife.config_flow import _normalize_url
from custom_components.studylife.const import (
    CONF_ENTRY_TYPE,
    CONF_INSTANCE_ID,
    CONF_SCAN_INTERVAL,
    DOMAIN,
    ENTRY_TYPE_ACCOUNT,
    ENTRY_TYPE_DISPLAY,
)
from custom_components.studylife.display_api import DisplayApiAuthError, DisplayApiError
from custom_components.studylife.display_coordinator import (
    DisplayCoordinator,
    _parse_display_data,
)

from .conftest import (
    TEST_API_KEY,
    TEST_DISPLAY_TOKEN,
    TEST_DISPLAY_URL,
    TEST_URL,
    make_raw_display_layouts,
    make_raw_display_state,
)

PATCH_TARGET = (
    "custom_components.studylife.config_flow.StudyLifeApiClient.async_test_connection"
)
DISPLAY_PATCH_TARGET = (
    "custom_components.studylife.config_flow.DisplayApiClient.async_test_connection"
)


async def _start_account_flow(hass: HomeAssistant):
    """async_init lands on the menu; select "account" to reach the original form."""
    menu = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        menu["flow_id"], {"next_step_id": "account"}
    )


async def _start_display_flow(hass: HomeAssistant):
    menu = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        menu["flow_id"], {"next_step_id": "display"}
    )


# ---------------------------------------------------------------------------
# User step
# ---------------------------------------------------------------------------


async def test_user_step_shows_a_menu(hass: HomeAssistant) -> None:
    """The very first step lets you pick what to add: an account or a display."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "user"
    assert set(result["menu_options"]) == {"account", "display"}


async def test_account_step_success(hass: HomeAssistant) -> None:
    """A successful connection test creates an entry with the right data/title."""
    result = await _start_account_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "account"

    with patch(PATCH_TARGET, return_value=None):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: TEST_URL, CONF_API_KEY: TEST_API_KEY},
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "StudyLife"
    assert result2["data"] == {
        CONF_URL: TEST_URL,
        CONF_API_KEY: TEST_API_KEY,
        CONF_ENTRY_TYPE: ENTRY_TYPE_ACCOUNT,
    }


async def test_account_step_invalid_auth(hass: HomeAssistant) -> None:
    """StudyLifeApiAuthError surfaces as an invalid_auth form error."""
    result = await _start_account_flow(hass)

    with patch(PATCH_TARGET, side_effect=StudyLifeApiAuthError("nope")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: TEST_URL, CONF_API_KEY: TEST_API_KEY},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "account"
    assert result2["errors"]["base"] == "invalid_auth"


async def test_account_step_cannot_connect(hass: HomeAssistant) -> None:
    """StudyLifeApiError surfaces as a cannot_connect form error."""
    result = await _start_account_flow(hass)

    with patch(PATCH_TARGET, side_effect=StudyLifeApiError("boom")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: TEST_URL, CONF_API_KEY: TEST_API_KEY},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "account"
    assert result2["errors"]["base"] == "cannot_connect"


async def test_account_step_duplicate_url_aborts(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Submitting a URL that already has a config entry aborts as already_configured."""
    mock_config_entry.add_to_hass(hass)

    result = await _start_account_flow(hass)

    with patch(PATCH_TARGET, return_value=None):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: TEST_URL, CONF_API_KEY: TEST_API_KEY},
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


def test_normalize_url_prepends_http_for_bare_hostname() -> None:
    """A URL without a scheme (e.g. just typed as a hostname:port) defaults to http://."""
    assert _normalize_url("studylife.local:5000") == "http://studylife.local:5000"
    assert _normalize_url("https://studylife.example") == "https://studylife.example"
    assert _normalize_url("http://studylife.example/") == "http://studylife.example"


# ---------------------------------------------------------------------------
# Reauth step
# ---------------------------------------------------------------------------


async def test_reauth_success(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A successful reauth updates the entry's API key and reloads it."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    new_key = "new-api-key"
    with patch(PATCH_TARGET, return_value=None):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_KEY: new_key},
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_API_KEY] == new_key
    assert mock_config_entry.data[CONF_URL] == TEST_URL


async def test_reauth_invalid_auth(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A rejected key during reauth shows invalid_auth and doesn't touch the entry."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)

    with patch(PATCH_TARGET, side_effect=StudyLifeApiAuthError("nope")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_KEY: "bad-key"},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "reauth_confirm"
    assert result2["errors"]["base"] == "invalid_auth"
    assert mock_config_entry.data[CONF_API_KEY] == TEST_API_KEY


async def test_reauth_cannot_connect(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A connection failure during reauth shows cannot_connect and doesn't touch the entry."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)

    with patch(PATCH_TARGET, side_effect=StudyLifeApiError("boom")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_KEY: "bad-key"},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "reauth_confirm"
    assert result2["errors"]["base"] == "cannot_connect"
    assert mock_config_entry.data[CONF_API_KEY] == TEST_API_KEY


# ---------------------------------------------------------------------------
# Reconfigure step
# ---------------------------------------------------------------------------


async def test_reconfigure_api_key_only_no_duplicate_check(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Changing only the API key (same URL) succeeds without tripping the
    unique_id collision check against the entry's own current URL."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_confirm"

    new_key = "rotated-api-key"
    with patch(PATCH_TARGET, return_value=None):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: TEST_URL, CONF_API_KEY: new_key},
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_URL] == TEST_URL
    assert mock_config_entry.data[CONF_API_KEY] == new_key


async def test_reconfigure_new_url_not_taken_succeeds(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Changing the URL to one that isn't used by any other entry succeeds."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)

    new_url = "http://studylife.example:9000"
    with patch(PATCH_TARGET, return_value=None):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: new_url, CONF_API_KEY: TEST_API_KEY},
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_URL] == new_url


async def test_reconfigure_invalid_auth_shows_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A bad API key during reconfigure shows an inline error, entry untouched."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)

    with patch(PATCH_TARGET, side_effect=StudyLifeApiAuthError("nope")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: TEST_URL, CONF_API_KEY: "wrong-key"},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "invalid_auth"}
    assert mock_config_entry.data[CONF_API_KEY] == TEST_API_KEY


async def test_reconfigure_cannot_connect_shows_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """An unreachable server during reconfigure shows an inline error."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)

    with patch(PATCH_TARGET, side_effect=StudyLifeApiError("boom")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: "http://unreachable.example:9000", CONF_API_KEY: TEST_API_KEY},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_url_collision_aborts(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Pointing the entry at a URL some OTHER entry already owns is rejected."""
    mock_config_entry.add_to_hass(hass)

    other_url = "http://other-studylife.local:5000"
    other_entry = MockConfigEntry(
        domain=DOMAIN,
        title="StudyLife",
        data={CONF_URL: other_url, CONF_API_KEY: "other-key"},
        unique_id=other_url,
    )
    other_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)

    with patch(PATCH_TARGET, return_value=None):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: other_url, CONF_API_KEY: TEST_API_KEY},
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"
    # The original entry must be untouched.
    assert mock_config_entry.data[CONF_URL] == TEST_URL


# ---------------------------------------------------------------------------
# Display step
# ---------------------------------------------------------------------------


async def test_display_step_success(hass: HomeAssistant) -> None:
    result = await _start_display_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "display"

    with patch(DISPLAY_PATCH_TARGET, return_value=None):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_URL: TEST_DISPLAY_URL,
                CONF_API_TOKEN: TEST_DISPLAY_TOKEN,
                CONF_VERIFY_SSL: True,
            },
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "StudyLife Display (studylife-display.local:8795)"
    assert result2["data"] == {
        CONF_URL: TEST_DISPLAY_URL,
        CONF_API_TOKEN: TEST_DISPLAY_TOKEN,
        CONF_VERIFY_SSL: True,
        CONF_ENTRY_TYPE: ENTRY_TYPE_DISPLAY,
    }


async def test_display_step_invalid_auth(hass: HomeAssistant) -> None:
    result = await _start_display_flow(hass)

    with patch(DISPLAY_PATCH_TARGET, side_effect=DisplayApiAuthError("404")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_URL: TEST_DISPLAY_URL,
                CONF_API_TOKEN: "wrong",
                CONF_VERIFY_SSL: True,
            },
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "display"
    assert result2["errors"]["base"] == "invalid_auth"


async def test_display_step_cannot_connect(hass: HomeAssistant) -> None:
    result = await _start_display_flow(hass)

    with patch(DISPLAY_PATCH_TARGET, side_effect=DisplayApiError("boom")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_URL: TEST_DISPLAY_URL,
                CONF_API_TOKEN: TEST_DISPLAY_TOKEN,
                CONF_VERIFY_SSL: True,
            },
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"]["base"] == "cannot_connect"


async def test_display_step_duplicate_url_aborts(
    hass: HomeAssistant, mock_display_config_entry: MockConfigEntry
) -> None:
    mock_display_config_entry.add_to_hass(hass)

    result = await _start_display_flow(hass)

    with patch(DISPLAY_PATCH_TARGET, return_value=None):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_URL: TEST_DISPLAY_URL,
                CONF_API_TOKEN: TEST_DISPLAY_TOKEN,
                CONF_VERIFY_SSL: True,
            },
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_account_and_display_share_one_unique_id_namespace(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """unique_id is scoped to the whole "studylife" domain by Home Assistant itself,
    not per entry type - an account entry at a given URL blocks a display entry at the
    exact same URL too (and vice versa). Unlikely to matter in practice (a StudyLife
    server and a studylife-display panel are different services with different
    default ports), but worth pinning down rather than assuming otherwise."""
    mock_config_entry.add_to_hass(hass)

    result = await _start_display_flow(hass)
    with patch(DISPLAY_PATCH_TARGET, return_value=None):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_URL: TEST_URL,
                CONF_API_TOKEN: TEST_DISPLAY_TOKEN,
                CONF_VERIFY_SSL: True,
            },
        )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


# ---------------------------------------------------------------------------
# Display reauth step
# ---------------------------------------------------------------------------


async def test_display_reauth_success(
    hass: HomeAssistant, mock_display_config_entry: MockConfigEntry
) -> None:
    mock_display_config_entry.add_to_hass(hass)

    result = await mock_display_config_entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "display_reauth_confirm"

    new_token = "new-display-token"
    with patch(DISPLAY_PATCH_TARGET, return_value=None):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_TOKEN: new_token}
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"
    assert mock_display_config_entry.data[CONF_API_TOKEN] == new_token
    assert mock_display_config_entry.data[CONF_URL] == TEST_DISPLAY_URL


async def test_display_reauth_invalid_auth(
    hass: HomeAssistant, mock_display_config_entry: MockConfigEntry
) -> None:
    mock_display_config_entry.add_to_hass(hass)

    result = await mock_display_config_entry.start_reauth_flow(hass)

    with patch(DISPLAY_PATCH_TARGET, side_effect=DisplayApiAuthError("401")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_TOKEN: "bad-token"}
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "display_reauth_confirm"
    assert result2["errors"]["base"] == "invalid_auth"
    assert mock_display_config_entry.data[CONF_API_TOKEN] == TEST_DISPLAY_TOKEN


# ---------------------------------------------------------------------------
# Display reconfigure step
# ---------------------------------------------------------------------------


async def test_display_reconfigure_success(
    hass: HomeAssistant, mock_display_config_entry: MockConfigEntry
) -> None:
    mock_display_config_entry.add_to_hass(hass)

    result = await mock_display_config_entry.start_reconfigure_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "display_reconfigure_confirm"

    new_token = "rotated-display-token"
    with patch(DISPLAY_PATCH_TARGET, return_value=None):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_URL: TEST_DISPLAY_URL,
                CONF_API_TOKEN: new_token,
                CONF_VERIFY_SSL: False,
            },
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reconfigure_successful"
    assert mock_display_config_entry.data[CONF_API_TOKEN] == new_token
    assert mock_display_config_entry.data[CONF_VERIFY_SSL] is False


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


async def test_options_flow_valid_scan_interval(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A scan_interval within [10, 3600] is accepted and stored."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 60},
    )
    await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["data"] == {CONF_SCAN_INTERVAL: 60}


@pytest.mark.parametrize("bad_value", [5, 3601])
async def test_options_flow_scan_interval_out_of_range_rejected(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, bad_value: int
) -> None:
    """Values outside [10, 3600] are rejected by the voluptuous schema itself -
    async_configure raises since there's no form re-display path for schema
    validation errors in this flow."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_SCAN_INTERVAL: bad_value},
        )


async def test_options_flow_display_entry_defaults_to_the_slower_interval(
    hass: HomeAssistant, mock_display_config_entry: MockConfigEntry
) -> None:
    """A display entry's options form defaults to DEFAULT_DISPLAY_SCAN_INTERVAL (60s),
    not the account default (30s) - polling faster than studylife-display's own
    5-minute refresh would only re-read the same cached state."""
    mock_display_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(
        mock_display_config_entry.entry_id
    )
    assert result["data_schema"]({})[CONF_SCAN_INTERVAL] == 60


# ---------------------------------------------------------------------------
# Zeroconf discovery of a studylife-display
# ---------------------------------------------------------------------------


def _zeroconf_info(
    *,
    ip: str = "192.168.1.50",
    hostname: str = "studylife-display.local.",
    port: int = 8795,
    tls: str = "false",
    api: str = "true",
    display_id: str | None = None,
    ips: list[str] | None = None,
) -> ZeroconfServiceInfo:
    properties = {"version": "1.12.0", "tls": tls, "api": api, "path": "/"}
    if display_id is not None:
        properties["id"] = display_id
    return ZeroconfServiceInfo(
        ip_address=ip_address(ip),
        ip_addresses=[ip_address(addr) for addr in (ips or [ip])],
        port=port,
        hostname=hostname,
        type="_studylife-display._tcp.local.",
        name="StudyLife Display (studylife-display)._studylife-display._tcp.local.",
        properties=properties,
    )


async def _start_zeroconf_flow(hass: HomeAssistant, info: ZeroconfServiceInfo):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=info
    )


async def test_zeroconf_shows_confirm_form_with_url(hass: HomeAssistant) -> None:
    result = await _start_zeroconf_flow(hass, _zeroconf_info())

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"
    assert result["description_placeholders"] == {"url": TEST_DISPLAY_URL}
    schema = result["data_schema"].schema
    verify = next(k for k in schema if k == CONF_VERIFY_SSL)
    assert verify.default() is True


async def test_zeroconf_confirm_creates_display_entry(hass: HomeAssistant) -> None:
    result = await _start_zeroconf_flow(hass, _zeroconf_info())

    with patch(DISPLAY_PATCH_TARGET, return_value=None):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_TOKEN: TEST_DISPLAY_TOKEN, CONF_VERIFY_SSL: True},
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "StudyLife Display (studylife-display.local:8795)"
    assert result2["data"] == {
        CONF_URL: TEST_DISPLAY_URL,
        CONF_API_TOKEN: TEST_DISPLAY_TOKEN,
        CONF_VERIFY_SSL: True,
        CONF_ENTRY_TYPE: ENTRY_TYPE_DISPLAY,
    }
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == TEST_DISPLAY_URL


async def test_zeroconf_wrong_token_shows_invalid_auth(hass: HomeAssistant) -> None:
    result = await _start_zeroconf_flow(hass, _zeroconf_info())

    with patch(DISPLAY_PATCH_TARGET, side_effect=DisplayApiAuthError("nope")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_TOKEN: "wrong", CONF_VERIFY_SSL: True},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "zeroconf_confirm"
    assert result2["errors"] == {"base": "invalid_auth"}


async def test_zeroconf_unreachable_shows_cannot_connect(hass: HomeAssistant) -> None:
    result = await _start_zeroconf_flow(hass, _zeroconf_info())

    with patch(DISPLAY_PATCH_TARGET, side_effect=DisplayApiError("down")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_TOKEN: TEST_DISPLAY_TOKEN, CONF_VERIFY_SSL: True},
        )

    assert result2["errors"] == {"base": "cannot_connect"}


async def test_zeroconf_tls_defaults_verify_ssl_off(hass: HomeAssistant) -> None:
    result = await _start_zeroconf_flow(hass, _zeroconf_info(tls="true", port=8443))

    assert result["type"] == FlowResultType.FORM
    assert result["description_placeholders"] == {
        "url": "https://studylife-display.local:8443"
    }
    schema = result["data_schema"].schema
    verify = next(k for k in schema if k == CONF_VERIFY_SSL)
    assert verify.default() is False


@pytest.mark.parametrize("stored", ["192.168.1.50:8795", "http://192.168.1.50:8795"])
async def test_zeroconf_entry_stored_by_ip_without_scheme_aborts(
    hass: HomeAssistant, stored: str
) -> None:
    """A display added by hand as "192.168.1.50:8795" (no scheme, as older flows stored it)
    is recognised when it announces itself under its host name over https."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_URL: stored,
            CONF_API_TOKEN: TEST_DISPLAY_TOKEN,
            CONF_VERIFY_SSL: False,
            CONF_ENTRY_TYPE: ENTRY_TYPE_DISPLAY,
        },
        unique_id=stored,
    )
    entry.add_to_hass(hass)

    result = await _start_zeroconf_flow(hass, _zeroconf_info(tls="true"))

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf_already_configured_aborts(
    hass: HomeAssistant, mock_display_config_entry: MockConfigEntry
) -> None:
    mock_display_config_entry.add_to_hass(hass)

    result = await _start_zeroconf_flow(hass, _zeroconf_info())

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf_same_host_other_port_aborts(
    hass: HomeAssistant, mock_display_config_entry: MockConfigEntry
) -> None:
    """Same host name, new port: the unique_id (URL) differs, but the host matches an
    existing display, so no duplicate is offered."""
    mock_display_config_entry.add_to_hass(hass)

    result = await _start_zeroconf_flow(hass, _zeroconf_info(port=9000))

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf_same_unique_id_updates_url(hass: HomeAssistant) -> None:
    """An entry whose unique_id matches the discovered URL gets its stored URL refreshed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_URL: "http://192.168.1.99:8795",
            CONF_API_TOKEN: TEST_DISPLAY_TOKEN,
            CONF_VERIFY_SSL: True,
            CONF_ENTRY_TYPE: ENTRY_TYPE_DISPLAY,
        },
        unique_id=TEST_DISPLAY_URL,
    )
    entry.add_to_hass(hass)

    with patch("custom_components.studylife.async_setup_entry", return_value=True):
        result = await _start_zeroconf_flow(hass, _zeroconf_info())
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_URL] == TEST_DISPLAY_URL


async def test_zeroconf_ip_match_with_manually_added_display_aborts(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_URL: "http://192.168.1.50:8795",
            CONF_API_TOKEN: TEST_DISPLAY_TOKEN,
            CONF_VERIFY_SSL: True,
            CONF_ENTRY_TYPE: ENTRY_TYPE_DISPLAY,
        },
        unique_id="http://192.168.1.50:8795",
    )
    entry.add_to_hass(hass)

    result = await _start_zeroconf_flow(hass, _zeroconf_info())

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf_api_disabled_aborts(hass: HomeAssistant) -> None:
    result = await _start_zeroconf_flow(hass, _zeroconf_info(api="false"))

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "api_disabled"


async def test_zeroconf_falls_back_to_ip_without_hostname(hass: HomeAssistant) -> None:
    result = await _start_zeroconf_flow(hass, _zeroconf_info(hostname="."))

    assert result["description_placeholders"] == {"url": "http://192.168.1.50:8795"}


# --- discovery by the display's stable id ----------------------------------

DISPLAY_ID = "0123456789abcdef"


def _display_entry(url: str = "https://10.0.0.5:8795", **extra: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_URL: url,
            CONF_API_TOKEN: TEST_DISPLAY_TOKEN,
            CONF_VERIFY_SSL: False,
            CONF_ENTRY_TYPE: ENTRY_TYPE_DISPLAY,
            **extra,
        },
        unique_id=url,
    )


# ---------------------------------------------------------------------------
# Zeroconf discovery of a StudyLife server (_studylife._tcp)
# ---------------------------------------------------------------------------

SERVER_URL = "https://studylife.example.org"


def _server_info(
    *,
    ip: str = "192.168.1.60",
    hostname: str = "studylife-host.local.",
    port: int = 8080,
    props: dict[str, str] | None = None,
) -> ZeroconfServiceInfo:
    """A server announcement; by default the TXT url points at an ingress, not at the
    announcing host."""
    if props is None:
        props = {
            "version": "1.2.3",
            "url": SERVER_URL,
            "https": "true",
            "path": "/",
        }
    return ZeroconfServiceInfo(
        ip_address=ip_address(ip),
        ip_addresses=[ip_address(ip)],
        port=port,
        hostname=hostname,
        type="_studylife._tcp.local.",
        name="StudyLife._studylife._tcp.local.",
        properties=props,
    )


def _account_entry(url: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: url, CONF_API_KEY: TEST_API_KEY},
        unique_id=url,
    )


async def test_zeroconf_stored_display_id_aborts_despite_different_host(
    hass: HomeAssistant,
) -> None:
    _display_entry(display_id=DISPLAY_ID).add_to_hass(hass)

    result = await _start_zeroconf_flow(
        hass, _zeroconf_info(display_id=DISPLAY_ID, ips=["192.168.1.50", "fe80::1"])
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf_loaded_coordinator_id_aborts_without_stored_id(
    hass: HomeAssistant,
) -> None:
    entry = _display_entry()
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = DisplayCoordinator(
        hass, AsyncMock(), timedelta(seconds=60)
    )
    hass.data[DOMAIN][entry.entry_id].data = _parse_display_data(
        make_raw_display_state(display_id=DISPLAY_ID), make_raw_display_layouts()
    )

    result = await _start_zeroconf_flow(hass, _zeroconf_info(display_id=DISPLAY_ID))

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf_different_id_and_host_offers_the_display(
    hass: HomeAssistant,
) -> None:
    _display_entry(display_id="ffffffffffffffff").add_to_hass(hass)

    result = await _start_zeroconf_flow(hass, _zeroconf_info(display_id=DISPLAY_ID))

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"


async def test_zeroconf_without_id_falls_back_to_host_checks(
    hass: HomeAssistant,
) -> None:
    """An old display (no TXT id) is still recognised by host, and offered otherwise."""
    _display_entry("http://192.168.1.50:8795").add_to_hass(hass)
    result = await _start_zeroconf_flow(hass, _zeroconf_info())
    assert result["type"] == FlowResultType.ABORT

    other = await _start_zeroconf_flow(
        hass, _zeroconf_info(hostname="other.local.", ip="192.168.9.9")
    )
    assert other["type"] == FlowResultType.FORM


async def test_zeroconf_entry_without_stored_id_or_coordinator_falls_back_to_host(
    hass: HomeAssistant,
) -> None:
    _display_entry("http://192.168.1.50:8795").add_to_hass(hass)

    result = await _start_zeroconf_flow(hass, _zeroconf_info(display_id=DISPLAY_ID))

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_server_zeroconf_shows_confirm_form_with_txt_url(
    hass: HomeAssistant,
) -> None:
    result = await _start_zeroconf_flow(hass, _server_info())

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "zeroconf_server_confirm"
    assert result["description_placeholders"] == {"url": SERVER_URL}
    assert list(result["data_schema"].schema) == [CONF_API_KEY]
    flow = hass.config_entries.flow.async_get(result["flow_id"])
    assert flow["context"]["title_placeholders"] == {"name": "StudyLife"}


async def test_server_zeroconf_trailing_slash_in_txt_url_is_stripped(
    hass: HomeAssistant,
) -> None:
    info = _server_info(props={"url": f"{SERVER_URL}/"})
    result = await _start_zeroconf_flow(hass, info)

    assert result["description_placeholders"] == {"url": SERVER_URL}


@pytest.mark.parametrize(
    ("props", "expected"),
    [
        ({"https": "false"}, "http://studylife-host.local:8080"),
        ({"https": "true"}, "https://studylife-host.local:8080"),
        ({}, "http://studylife-host.local:8080"),
    ],
)
async def test_server_zeroconf_falls_back_to_host_and_port(
    hass: HomeAssistant, props: dict[str, str], expected: str
) -> None:
    result = await _start_zeroconf_flow(hass, _server_info(props=props))

    assert result["step_id"] == "zeroconf_server_confirm"
    assert result["description_placeholders"] == {"url": expected}


@pytest.mark.parametrize(
    "bad", ["not a url", "ftp://studylife.local", "http://", "//x"]
)
async def test_server_zeroconf_invalid_txt_url_aborts(
    hass: HomeAssistant, bad: str
) -> None:
    result = await _start_zeroconf_flow(hass, _server_info(props={"url": bad}))

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery"


async def test_server_zeroconf_confirm_creates_account_entry(
    hass: HomeAssistant,
) -> None:
    result = await _start_zeroconf_flow(hass, _server_info())

    with patch(PATCH_TARGET, return_value=None):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: TEST_API_KEY}
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "StudyLife"
    assert result2["data"] == {
        CONF_URL: SERVER_URL,
        CONF_API_KEY: TEST_API_KEY,
        CONF_ENTRY_TYPE: ENTRY_TYPE_ACCOUNT,
    }
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == SERVER_URL


async def test_server_zeroconf_wrong_key_shows_invalid_auth(
    hass: HomeAssistant,
) -> None:
    result = await _start_zeroconf_flow(hass, _server_info())

    with patch(PATCH_TARGET, side_effect=StudyLifeApiAuthError("nope")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "wrong"}
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "zeroconf_server_confirm"
    assert result2["errors"] == {"base": "invalid_auth"}


async def test_server_zeroconf_unreachable_shows_cannot_connect(
    hass: HomeAssistant,
) -> None:
    result = await _start_zeroconf_flow(hass, _server_info())

    with patch(PATCH_TARGET, side_effect=StudyLifeApiError("down")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: TEST_API_KEY}
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "cannot_connect"}


@pytest.mark.parametrize(
    "stored",
    [
        "https://studylife.example.org",  # identical
        "https://studylife.example.org/",  # trailing slash
        "https://studylife.example.org:443",  # explicit default port
        "https://STUDYLIFE.example.org",  # host case
    ],
)
async def test_server_zeroconf_same_server_aborts(
    hass: HomeAssistant, stored: str
) -> None:
    _account_entry(stored).add_to_hass(hass)

    result = await _start_zeroconf_flow(hass, _server_info())

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_server_zeroconf_entry_stored_without_scheme_aborts(
    hass: HomeAssistant,
) -> None:
    """Hand-added as "192.168.5.61:8795" (no scheme): recognised via the TXT-less
    fallback URL built from the announcing IP and port."""
    _account_entry("192.168.5.61:8795").add_to_hass(hass)

    info = _server_info(ip="192.168.5.61", hostname=".", port=8795, props={})
    result = await _start_zeroconf_flow(hass, info)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_server_zeroconf_default_port_without_scheme_aborts(
    hass: HomeAssistant,
) -> None:
    """A schemeless "studylife.example.org" is http on port 80."""
    _account_entry("studylife.example.org").add_to_hass(hass)

    info = _server_info(props={"url": "http://studylife.example.org"})
    result = await _start_zeroconf_flow(hass, info)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_server_zeroconf_announcing_host_and_port_match_aborts(
    hass: HomeAssistant,
) -> None:
    """Behind an ingress: the entry points at the announcing machine directly (same IP
    and port) while the TXT url names the gateway - still the same server."""
    _account_entry("http://192.168.1.60:8080").add_to_hass(hass)

    result = await _start_zeroconf_flow(hass, _server_info())

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_server_zeroconf_announcing_host_other_port_is_offered(
    hass: HomeAssistant,
) -> None:
    """Same host, different port is a different server: it is offered."""
    _account_entry("http://192.168.1.60:9999").add_to_hass(hass)

    result = await _start_zeroconf_flow(hass, _server_info())

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "zeroconf_server_confirm"


async def test_server_zeroconf_same_host_other_port_in_url_is_offered(
    hass: HomeAssistant,
) -> None:
    _account_entry("http://studylife.local:5000").add_to_hass(hass)

    info = _server_info(props={"url": "http://studylife.local:5001"})
    result = await _start_zeroconf_flow(hass, info)

    assert result["type"] == FlowResultType.FORM


async def test_server_zeroconf_display_entry_does_not_block(
    hass: HomeAssistant, mock_display_config_entry: MockConfigEntry
) -> None:
    mock_display_config_entry.add_to_hass(hass)

    info = _server_info(
        ip="192.168.1.50",
        hostname="studylife-display.local.",
        port=8080,
        props={"url": "http://studylife-display.local:8080"},
    )
    result = await _start_zeroconf_flow(hass, info)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "zeroconf_server_confirm"


async def test_server_zeroconf_same_unique_id_updates_url(hass: HomeAssistant) -> None:
    entry = _account_entry("http://old.example:1")
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, unique_id=SERVER_URL)

    with patch("custom_components.studylife.async_setup_entry", return_value=True):
        result = await _start_zeroconf_flow(hass, _server_info())
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_URL] == SERVER_URL


async def test_server_zeroconf_ignored_entry_is_skipped(hass: HomeAssistant) -> None:
    """An ignored entry (source "ignore", no URL) must not break the host comparison."""
    MockConfigEntry(
        domain=DOMAIN, source="ignore", data={}, unique_id="something-else"
    ).add_to_hass(hass)

    result = await _start_zeroconf_flow(hass, _server_info())

    assert result["type"] == FlowResultType.FORM


# ---------------------------------------------------------------------------
# Server discovery by stable instance id
# ---------------------------------------------------------------------------

SERVER_ID = "0123456789abcdef0123456789abcdef"
OTHER_SERVER_ID = "fedcba9876543210fedcba9876543210"
CONFIGURED_URL = "https://studylife.lukas2311-homelab.com"
ANNOUNCED_URL = "https://studylife.heim.lan"
INSTANCE_PATCH_TARGET = (
    "custom_components.studylife.config_flow.StudyLifeApiClient.async_get_instance"
)


def _id_info(server_id: str | None = SERVER_ID) -> ZeroconfServiceInfo:
    props = {"version": "1.2.3", "url": ANNOUNCED_URL, "https": "true", "path": "/"}
    if server_id is not None:
        props["id"] = server_id
    return _server_info(ip="10.9.8.7", hostname="other-host.local.", props=props)


def _entry_with_id(server_id: str | None) -> MockConfigEntry:
    data = {CONF_URL: CONFIGURED_URL, CONF_API_KEY: TEST_API_KEY}
    if server_id is not None:
        data[CONF_INSTANCE_ID] = server_id
    return MockConfigEntry(domain=DOMAIN, data=data, unique_id=CONFIGURED_URL)


async def test_zeroconf_server_stored_id_aborts_despite_different_url(
    hass: HomeAssistant,
) -> None:
    _entry_with_id(SERVER_ID).add_to_hass(hass)

    with patch(INSTANCE_PATCH_TARGET, new_callable=AsyncMock) as probe:
        result = await _start_zeroconf_flow(hass, _id_info())

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    probe.assert_not_called()


async def test_zeroconf_server_entry_without_id_is_recognised_by_probe_and_stored(
    hass: HomeAssistant,
) -> None:
    entry = _entry_with_id(None)
    entry.add_to_hass(hass)

    with patch(
        INSTANCE_PATCH_TARGET,
        new_callable=AsyncMock,
        return_value={"id": SERVER_ID, "version": "1.2.3"},
    ) as probe:
        result = await _start_zeroconf_flow(hass, _id_info())

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    probe.assert_awaited_once()
    assert entry.data[CONF_INSTANCE_ID] == SERVER_ID


async def test_zeroconf_server_probe_stores_id_even_when_it_differs(
    hass: HomeAssistant,
) -> None:
    entry = _entry_with_id(None)
    entry.add_to_hass(hass)

    with patch(
        INSTANCE_PATCH_TARGET,
        new_callable=AsyncMock,
        return_value={"id": OTHER_SERVER_ID},
    ):
        result = await _start_zeroconf_flow(hass, _id_info())

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "zeroconf_server_confirm"
    assert entry.data[CONF_INSTANCE_ID] == OTHER_SERVER_ID


async def test_zeroconf_server_different_stored_id_offers_the_server(
    hass: HomeAssistant,
) -> None:
    _entry_with_id(OTHER_SERVER_ID).add_to_hass(hass)

    result = await _start_zeroconf_flow(hass, _id_info())

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "zeroconf_server_confirm"


async def test_zeroconf_server_probe_failure_falls_back_gracefully(
    hass: HomeAssistant,
) -> None:
    entry = _entry_with_id(None)
    entry.add_to_hass(hass)

    with patch(
        INSTANCE_PATCH_TARGET,
        new_callable=AsyncMock,
        side_effect=RuntimeError("unreachable"),
    ):
        result = await _start_zeroconf_flow(hass, _id_info())

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "zeroconf_server_confirm"
    assert CONF_INSTANCE_ID not in entry.data


async def test_zeroconf_server_probe_returning_none_falls_back_to_host_rule(
    hass: HomeAssistant,
) -> None:
    # Same host as announced, no id obtainable: the old duplicate rule still applies.
    _account_entry(ANNOUNCED_URL).add_to_hass(hass)

    with patch(INSTANCE_PATCH_TARGET, new_callable=AsyncMock, return_value=None):
        result = await _start_zeroconf_flow(hass, _id_info())

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf_server_without_txt_id_uses_duplicate_rules_and_never_probes(
    hass: HomeAssistant,
) -> None:
    _entry_with_id(None).add_to_hass(hass)

    with patch(INSTANCE_PATCH_TARGET, new_callable=AsyncMock) as probe:
        result = await _start_zeroconf_flow(hass, _id_info(None))

    assert result["type"] == FlowResultType.FORM
    probe.assert_not_called()


async def test_zeroconf_server_invalid_txt_id_is_ignored(hass: HomeAssistant) -> None:
    _entry_with_id(SERVER_ID).add_to_hass(hass)

    result = await _start_zeroconf_flow(hass, _id_info("not-a-valid-id"))

    assert result["type"] == FlowResultType.FORM


async def test_zeroconf_server_id_ignores_display_entries(hass: HomeAssistant) -> None:
    _display_entry().add_to_hass(hass)

    with patch(INSTANCE_PATCH_TARGET, new_callable=AsyncMock) as probe:
        result = await _start_zeroconf_flow(hass, _id_info())

    assert result["type"] == FlowResultType.FORM
    probe.assert_not_called()
