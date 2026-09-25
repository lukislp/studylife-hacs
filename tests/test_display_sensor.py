"""Tests for the studylife-display status sensor entity (display_sensor.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import get_entity_id, make_raw_display_state, setup_display_integration


async def test_state_is_the_current_frame_layout(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    entity_id = get_entity_id(
        hass, mock_display_config_entry.entry_id, "current_layout", platform="sensor"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "focus"
    assert state.attributes["status"] == "ok"
    assert state.attributes["layout_choice"] == "auto"
    assert state.attributes["resolved_layout"] == "focus"
    assert state.attributes["stale_minutes"] == 2


async def test_falls_back_to_resolved_layout_before_a_frame_exists(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        layout="week", current_frame=None
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    entity_id = get_entity_id(
        hass, mock_display_config_entry.entry_id, "current_layout", platform="sensor"
    )
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "week"
    assert state.attributes["shown_at"] is None


async def test_falls_back_to_frame_kind_for_a_screen_thats_not_a_layout(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        status="setup",
        setup=True,
        layout=None,
        current_frame={
            "shown_at": "2026-09-25T20:00:00+02:00",
            "layout": None,
            "kind": "setup",
        },
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    entity_id = get_entity_id(
        hass, mock_display_config_entry.entry_id, "current_layout", platform="sensor"
    )
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "setup"
