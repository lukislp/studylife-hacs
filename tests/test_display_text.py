"""Tests for the studylife-display text settings (display_text.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife.display_api import (
    DisplayApiError,
    DisplayApiNotFoundError,
)

from .conftest import (
    get_entity_id,
    make_raw_display_settings,
    setup_display_integration,
)

TEXT_KEYS = [
    "quiet_hours",
    "clear_at",
    "auto_review",
    "auto_agenda",
    "auto_tomorrow",
    "auto_quiet",
]


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str | None:
    return get_entity_id(hass, entry.entry_id, key, platform="text")


async def test_every_text_setting_is_a_config_entity_with_its_value(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    registry = er.async_get(hass)
    expected = {
        "quiet_hours": "23-7",
        "clear_at": "04:00",
        "auto_review": "sun 18-24",
        "auto_agenda": "06-12",
        "auto_tomorrow": "18-23",
        "auto_quiet": "",
    }

    for key in TEXT_KEYS:
        entity_id = _entity_id(hass, mock_display_config_entry, key)
        assert entity_id is not None, key
        state = hass.states.get(entity_id)
        assert state is not None and state.state == expected[key], key
        # Empty is valid (rule off), so the lower bound is 0 and nothing is patterned.
        assert state.attributes["min"] == 0
        assert state.attributes["max"] == 40
        assert state.attributes["mode"] == "text"
        assert state.attributes["pattern"] is None
        entry = registry.async_get(entity_id)
        assert entry is not None
        assert entry.entity_category == EntityCategory.CONFIG
        assert entry.translation_key == f"display_{key}"


@pytest.mark.parametrize("key", TEXT_KEYS)
async def test_setting_a_value_sends_it_and_refreshes(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
    key: str,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = _entity_id(hass, mock_display_config_entry, key)
    assert entity_id is not None

    mock_display_api_client.async_get_state.reset_mock()
    await hass.services.async_call(
        "text", "set_value", {"entity_id": entity_id, "value": "22-6"}, blocking=True
    )
    await hass.async_block_till_done()

    mock_display_api_client.async_update_settings.assert_awaited_once_with(
        {key: "22-6"}
    )
    mock_display_api_client.async_get_state.assert_awaited()


async def test_an_empty_string_is_sent_to_switch_the_rule_off(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = _entity_id(hass, mock_display_config_entry, "quiet_hours")
    assert entity_id is not None

    await hass.services.async_call(
        "text", "set_value", {"entity_id": entity_id, "value": ""}, blocking=True
    )

    mock_display_api_client.async_update_settings.assert_awaited_once_with(
        {"quiet_hours": ""}
    )


async def test_a_value_the_display_rejects_surfaces_its_reason(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = _entity_id(hass, mock_display_config_entry, "clear_at")
    assert entity_id is not None
    mock_display_api_client.async_update_settings.side_effect = DisplayApiError(
        "clear_at: expected HH:MM"
    )

    with pytest.raises(HomeAssistantError, match="expected HH:MM"):
        await hass.services.async_call(
            "text",
            "set_value",
            {"entity_id": entity_id, "value": "late"},
            blocking=True,
        )


@pytest.mark.parametrize("missing", ["auto_tomorrow", "auto_quiet"])
async def test_a_key_the_display_does_not_report_gets_no_entity(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
    missing: str,
) -> None:
    """Displays before the tomorrow/quiet rules lack those keys in `values`."""
    document = make_raw_display_settings()
    del document["values"][missing]
    mock_display_api_client.async_get_settings.return_value = document
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    assert _entity_id(hass, mock_display_config_entry, missing) is None
    assert _entity_id(hass, mock_display_config_entry, "quiet_hours") is not None


async def test_no_text_entities_on_a_display_without_the_settings_route(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    mock_display_api_client.async_get_settings.side_effect = DisplayApiNotFoundError(
        "404"
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    for key in TEXT_KEYS:
        assert _entity_id(hass, mock_display_config_entry, key) is None
