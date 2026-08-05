"""Tests for the StudyLife integration's setup/unload entry points
(custom_components/studylife/__init__.py)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife.api import StudyLifeApiError
from custom_components.studylife.const import CONF_SCAN_INTERVAL, DOMAIN
from custom_components.studylife.services import (
    SERVICE_CREATE_SESSION,
    SERVICE_DELETE_SESSION,
    SERVICE_GENERATE_EXAM_PLAN,
    SERVICE_SET_ACTIVE_PROGRAM,
    SERVICE_SET_COURSE_GOAL,
    SERVICE_UPDATE_SESSION,
)

from .conftest import TEST_API_KEY, TEST_URL

# custom_components/studylife/__init__.py imports StudyLifeApiClient into its own
# module namespace (`from .api import StudyLifeApiClient`), so that's what needs
# patching to make async_setup_entry use our AsyncMock instead of a real client -
# same pattern test_config_flow.py uses for config_flow.py's own copy of the name.
PATCH_TARGET = "custom_components.studylife.StudyLifeApiClient"

ALL_SERVICES = (
    SERVICE_CREATE_SESSION,
    SERVICE_UPDATE_SESSION,
    SERVICE_DELETE_SESSION,
    SERVICE_SET_COURSE_GOAL,
    SERVICE_GENERATE_EXAM_PLAN,
    SERVICE_SET_ACTIVE_PROGRAM,
)


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_setup_entry_success(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client: AsyncMock
) -> None:
    """A successful first refresh stores the coordinator, loads the entry and
    forwards to every platform."""
    mock_config_entry.add_to_hass(hass)

    with patch(PATCH_TARGET, return_value=mock_api_client):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
    assert coordinator.client is mock_api_client
    assert coordinator.last_update_success is True

    # select.py always creates exactly one entity on the hub device regardless of
    # data content - a reliable signal that PLATFORMS actually got forwarded and
    # ran, not just that the coordinator refreshed.
    assert hass.states.get("select.studylife_active_course") is not None

    for service in ALL_SERVICES:
        assert hass.services.has_service(DOMAIN, service)


async def test_setup_entry_first_refresh_failure_retries_setup(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client: AsyncMock
) -> None:
    """When the coordinator's first refresh fails, async_config_entry_first_refresh()
    itself raises ConfigEntryNotReady (standard DataUpdateCoordinator behavior) -
    async_setup_entry does not catch it, so HA's own ConfigEntries.async_setup
    catches it for us and schedules a retry instead of loading the entry."""
    mock_config_entry.add_to_hass(hass)
    mock_api_client.async_get_sessions.side_effect = StudyLifeApiError("boom")

    with patch(PATCH_TARGET, return_value=mock_api_client):
        assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert DOMAIN not in hass.data or mock_config_entry.entry_id not in hass.data[DOMAIN]


# ---------------------------------------------------------------------------
# _async_update_listener
# ---------------------------------------------------------------------------


async def test_update_listener_skips_reload_when_nothing_relevant_changed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client: AsyncMock
) -> None:
    """An options update that resolves to the SAME url/key/scan_interval the
    running coordinator's client already uses must not trigger a reload."""
    mock_config_entry.add_to_hass(hass)

    with patch(PATCH_TARGET, return_value=mock_api_client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)) as mock_reload:
        # Explicit scan_interval equal to the default the coordinator was already
        # built with - a real change to entry.options (from {} to a populated
        # dict), so HA still fires the update listener, but nothing the
        # coordinator's client cares about actually changed.
        hass.config_entries.async_update_entry(
            mock_config_entry, options={CONF_SCAN_INTERVAL: 30}
        )
        await hass.async_block_till_done()

    mock_reload.assert_not_called()


async def test_update_listener_reloads_when_scan_interval_changed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client: AsyncMock
) -> None:
    """An options update that changes the effective scan_interval must trigger
    hass.config_entries.async_reload."""
    mock_config_entry.add_to_hass(hass)

    with patch(PATCH_TARGET, return_value=mock_api_client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)) as mock_reload:
        hass.config_entries.async_update_entry(
            mock_config_entry, options={CONF_SCAN_INTERVAL: 60}
        )
        await hass.async_block_till_done()

    mock_reload.assert_called_once_with(mock_config_entry.entry_id)


async def test_update_listener_reloads_when_api_key_changed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client: AsyncMock
) -> None:
    """A data update (e.g. a rotated API key via reconfigure) that changes the
    key must also trigger a reload, not just scan_interval/options changes."""
    mock_config_entry.add_to_hass(hass)

    with patch(PATCH_TARGET, return_value=mock_api_client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)) as mock_reload:
        hass.config_entries.async_update_entry(
            mock_config_entry,
            data={**mock_config_entry.data, CONF_API_KEY: "rotated-key"},
        )
        await hass.async_block_till_done()

    mock_reload.assert_called_once_with(mock_config_entry.entry_id)


# ---------------------------------------------------------------------------
# async_unload_entry
# ---------------------------------------------------------------------------


async def test_unload_entry_removes_services_when_last_entry_unloaded(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client: AsyncMock
) -> None:
    """Unloading the only loaded entry pops it from hass.data[DOMAIN] and, since
    that leaves the domain's entry map empty, removes all 6 registered services."""
    mock_config_entry.add_to_hass(hass)

    with patch(PATCH_TARGET, return_value=mock_api_client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    for service in ALL_SERVICES:
        assert hass.services.has_service(DOMAIN, service)

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.entry_id not in hass.data.get(DOMAIN, {})
    assert not hass.data[DOMAIN]

    for service in ALL_SERVICES:
        assert not hass.services.has_service(DOMAIN, service)


async def test_unload_one_of_two_entries_keeps_services_registered(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """With two entries loaded, unloading only one must NOT remove the shared
    services - the other entry's platforms/services still depend on them."""
    entry1 = MockConfigEntry(
        domain=DOMAIN,
        title="StudyLife",
        data={CONF_URL: TEST_URL, CONF_API_KEY: TEST_API_KEY},
        unique_id=TEST_URL,
    )
    other_url = "http://other-studylife.local:5000"
    entry2 = MockConfigEntry(
        domain=DOMAIN,
        title="StudyLife (other)",
        data={CONF_URL: other_url, CONF_API_KEY: "other-key"},
        unique_id=other_url,
    )
    entry1.add_to_hass(hass)
    entry2.add_to_hass(hass)

    # Both entries are already added to hass before either is set up, so the
    # first async_setup call bootstraps the "studylife" component as a whole -
    # which itself sets up every pending config entry for the domain, entry2
    # included. A second explicit async_setup(entry2.entry_id) call would then
    # fail with OperationNotAllowed (entry2 already LOADED by that point).
    with patch(PATCH_TARGET, return_value=mock_api_client):
        await hass.config_entries.async_setup(entry1.entry_id)
        await hass.async_block_till_done()

    assert entry1.state is ConfigEntryState.LOADED
    assert entry2.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry1.entry_id)
    await hass.async_block_till_done()

    assert entry1.entry_id not in hass.data[DOMAIN]
    assert entry2.entry_id in hass.data[DOMAIN]

    for service in ALL_SERVICES:
        assert hass.services.has_service(DOMAIN, service)
