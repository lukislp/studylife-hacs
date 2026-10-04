"""Tests for the studylife-display diagnostic binary sensors (display_binary_sensor.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import get_entity_id, make_raw_display_state, setup_display_integration


async def test_both_binary_sensors_are_diagnostic(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    registry = er.async_get(hass)

    for key in ("quiet_hours_active", "sessions_problem"):
        entity_id = get_entity_id(
            hass, mock_display_config_entry.entry_id, key, platform="binary_sensor"
        )
        assert entity_id is not None, key
        entry = registry.async_get(entity_id)
        assert entry is not None
        assert entry.entity_category == EntityCategory.DIAGNOSTIC


async def test_healthy_display_reads_off_on_both(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    quiet = hass.states.get(
        get_entity_id(
            hass,
            mock_display_config_entry.entry_id,
            "quiet_hours_active",
            platform="binary_sensor",
        )
    )
    problem = hass.states.get(
        get_entity_id(
            hass,
            mock_display_config_entry.entry_id,
            "sessions_problem",
            platform="binary_sensor",
        )
    )
    assert quiet is not None and quiet.state == STATE_OFF
    assert problem is not None and problem.state == STATE_OFF
    assert problem.attributes["device_class"] == "problem"


async def test_quiet_hours_and_failing_sessions_read_on(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        quiet_hours_active=True, sessions_ok=False
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    quiet = hass.states.get(
        get_entity_id(
            hass,
            mock_display_config_entry.entry_id,
            "quiet_hours_active",
            platform="binary_sensor",
        )
    )
    problem = hass.states.get(
        get_entity_id(
            hass,
            mock_display_config_entry.entry_id,
            "sessions_problem",
            platform="binary_sensor",
        )
    )
    assert quiet is not None and quiet.state == STATE_ON
    # sessions_ok=false on the wire -> the PROBLEM entity is on.
    assert problem is not None and problem.state == STATE_ON
