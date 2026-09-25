"""Tests for the studylife-display layout select entity (display_select.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import (
    get_entity_id,
    make_raw_display_layouts,
    make_raw_display_state,
    setup_display_integration,
)


async def test_options_include_auto_plus_every_layout_key(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    entity_id = get_entity_id(
        hass, mock_display_config_entry.entry_id, "layout", platform="select"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert set(state.attributes["options"]) == {"auto", "classic", "focus", "exam"}


async def test_current_option_is_the_persisted_choice(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    # "classic" is one of make_raw_display_layouts()' default options - HA's SelectEntity
    # reports "unknown" for a current_option that isn't in `options`, so choice and the
    # options list have to agree here exactly like the real display keeps them in sync.
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        layout_choice="classic"
    )
    mock_display_api_client.async_get_layouts.return_value = make_raw_display_layouts(
        choice="classic"
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    entity_id = get_entity_id(
        hass, mock_display_config_entry.entry_id, "layout", platform="select"
    )
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "classic"


async def test_selecting_an_option_calls_set_layout_and_refreshes(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = get_entity_id(
        hass, mock_display_config_entry.entry_id, "layout", platform="select"
    )
    assert entity_id is not None

    mock_display_api_client.async_get_state.reset_mock()
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id, "option": "exam"},
        blocking=True,
    )
    await hass.async_block_till_done()

    mock_display_api_client.async_set_layout.assert_awaited_once_with("exam")
    # async_request_refresh triggers another poll cycle.
    mock_display_api_client.async_get_state.assert_awaited()
