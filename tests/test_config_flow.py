"""Tests for the StudyLife config flow (user, reauth, reconfigure, options)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife.api import StudyLifeApiAuthError, StudyLifeApiError
from custom_components.studylife.const import CONF_SCAN_INTERVAL, DOMAIN

from .conftest import TEST_API_KEY, TEST_URL

PATCH_TARGET = "custom_components.studylife.config_flow.StudyLifeApiClient.async_test_connection"


# ---------------------------------------------------------------------------
# User step
# ---------------------------------------------------------------------------


async def test_user_step_success(hass: HomeAssistant) -> None:
    """A successful connection test creates an entry with the right data/title."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(PATCH_TARGET, return_value=None):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: TEST_URL, CONF_API_KEY: TEST_API_KEY},
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "StudyLife"
    assert result2["data"] == {CONF_URL: TEST_URL, CONF_API_KEY: TEST_API_KEY}


async def test_user_step_invalid_auth(hass: HomeAssistant) -> None:
    """StudyLifeApiAuthError surfaces as an invalid_auth form error."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    with patch(PATCH_TARGET, side_effect=StudyLifeApiAuthError("nope")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: TEST_URL, CONF_API_KEY: TEST_API_KEY},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "user"
    assert result2["errors"]["base"] == "invalid_auth"


async def test_user_step_cannot_connect(hass: HomeAssistant) -> None:
    """StudyLifeApiError surfaces as a cannot_connect form error."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    with patch(PATCH_TARGET, side_effect=StudyLifeApiError("boom")):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: TEST_URL, CONF_API_KEY: TEST_API_KEY},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "user"
    assert result2["errors"]["base"] == "cannot_connect"


async def test_user_step_duplicate_url_aborts(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Submitting a URL that already has a config entry aborts as already_configured."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    with patch(PATCH_TARGET, return_value=None):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: TEST_URL, CONF_API_KEY: TEST_API_KEY},
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


# ---------------------------------------------------------------------------
# Reauth step
# ---------------------------------------------------------------------------


async def test_reauth_success(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
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


async def test_reauth_invalid_auth(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
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


async def test_reauth_cannot_connect(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
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


async def test_reconfigure_url_collision_aborts(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
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
