"""Tests for the studylife-display cycle-order text entity (display_text.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife.display_api import DisplayApiError
from custom_components.studylife.display_text import CYCLE_MAX_LENGTH, parse_cycle

from .conftest import (
    get_entity_id,
    make_raw_display_layouts,
    make_raw_display_layouts_legacy,
    make_raw_display_state,
    setup_display_integration,
)


def _cycle_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    return get_entity_id(hass, entry.entry_id, "cycle", platform="text")


async def test_value_is_the_cycle_joined_with_commas(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    entity_id = _cycle_entity_id(hass, mock_display_config_entry)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "classic, week, agenda, review"
    assert state.attributes["mode"] == "text"
    assert state.attributes["max"] == CYCLE_MAX_LENGTH
    # Lowercase keys separated by commas; anything else is refused by HA up front.
    assert state.attributes["pattern"]

    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    assert entry.entity_category == EntityCategory.CONFIG


async def test_setting_a_value_resends_the_current_choice_with_the_new_cycle(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        layout_choice="cycle"
    )
    mock_display_api_client.async_get_layouts.return_value = make_raw_display_layouts(
        choice="cycle"
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = _cycle_entity_id(hass, mock_display_config_entry)
    assert entity_id is not None

    mock_display_api_client.async_get_state.reset_mock()
    await hass.services.async_call(
        "text",
        "set_value",
        # Sloppy spacing is tolerated - the keys are stripped before sending.
        {"entity_id": entity_id, "value": "today,week , exam"},
        blocking=True,
    )
    await hass.async_block_till_done()

    mock_display_api_client.async_set_layout.assert_awaited_once_with(
        "cycle", cycle=["today", "week", "exam"]
    )
    # async_request_refresh triggers another poll cycle.
    mock_display_api_client.async_get_state.assert_awaited()


async def test_a_rejected_cycle_surfaces_the_displays_message(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    """The display answers 400 {"error": ...} for an unknown key and writes nothing -
    that reason must reach the caller as a HomeAssistantError, not a traceback."""
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = _cycle_entity_id(hass, mock_display_config_entry)
    assert entity_id is not None
    mock_display_api_client.async_set_layout.side_effect = DisplayApiError(
        "cycle: unknown layout 'nope'"
    )

    with pytest.raises(HomeAssistantError, match="unknown layout 'nope'"):
        await hass.services.async_call(
            "text",
            "set_value",
            {"entity_id": entity_id, "value": "classic, nope"},
            blocking=True,
        )


async def test_no_entity_for_a_legacy_display(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    mock_display_api_client.async_get_layouts.return_value = (
        make_raw_display_layouts_legacy()
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    assert _cycle_entity_id(hass, mock_display_config_entry) is None


async def test_no_entity_for_an_empty_cycle(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    mock_display_api_client.async_get_layouts.return_value = make_raw_display_layouts(
        cycle=[]
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    assert _cycle_entity_id(hass, mock_display_config_entry) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("classic", ["classic"]),
        ("classic, week", ["classic", "week"]),
        ("  classic ,week,, ", ["classic", "week"]),
        ("", []),
    ],
)
def test_parse_cycle(value: str, expected: list[str]) -> None:
    assert parse_cycle(value) == expected
