"""Tests for the studylife-display buttons (display_button.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife.display_api import (
    DisplayApiError,
    DisplayApiNotFoundError,
)

from .conftest import get_entity_id, setup_display_integration


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str | None:
    return get_entity_id(hass, entry.entry_id, key, platform="button")


async def test_refresh_is_a_normal_control_and_reset_is_config(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    registry = er.async_get(hass)

    refresh = registry.async_get(_entity_id(hass, mock_display_config_entry, "refresh"))
    reset = registry.async_get(
        _entity_id(hass, mock_display_config_entry, "reset_settings")
    )
    assert refresh is not None and reset is not None
    assert refresh.entity_category is None
    assert refresh.translation_key == "display_refresh"
    assert reset.entity_category is not None
    assert reset.entity_category.value == "config"
    assert reset.translation_key == "display_reset_settings"


async def test_pressing_refresh_calls_the_api_and_polls_again(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = _entity_id(hass, mock_display_config_entry, "refresh")
    assert entity_id is not None

    mock_display_api_client.async_get_state.reset_mock()
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()

    mock_display_api_client.async_refresh.assert_awaited_once_with()
    mock_display_api_client.async_get_state.assert_awaited()


async def test_a_failed_refresh_outcome_raises(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = _entity_id(hass, mock_display_config_entry, "refresh")
    assert entity_id is not None
    mock_display_api_client.async_refresh.return_value = {"outcome": "failed"}

    with pytest.raises(HomeAssistantError, match="could not refresh the panel"):
        await hass.services.async_call(
            "button", "press", {"entity_id": entity_id}, blocking=True
        )


async def test_a_refresh_api_error_raises_home_assistant_error(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = _entity_id(hass, mock_display_config_entry, "refresh")
    assert entity_id is not None
    mock_display_api_client.async_refresh.side_effect = DisplayApiError("unreachable")

    with pytest.raises(HomeAssistantError, match="unreachable"):
        await hass.services.async_call(
            "button", "press", {"entity_id": entity_id}, blocking=True
        )


async def test_pressing_reset_calls_the_api_and_polls_again(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = _entity_id(hass, mock_display_config_entry, "reset_settings")
    assert entity_id is not None

    mock_display_api_client.async_get_state.reset_mock()
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()

    mock_display_api_client.async_reset_settings.assert_awaited_once_with()
    mock_display_api_client.async_get_state.assert_awaited()


async def test_a_reset_api_error_raises_home_assistant_error(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = _entity_id(hass, mock_display_config_entry, "reset_settings")
    assert entity_id is not None
    mock_display_api_client.async_reset_settings.side_effect = DisplayApiError("nope")

    with pytest.raises(HomeAssistantError, match="nope"):
        await hass.services.async_call(
            "button", "press", {"entity_id": entity_id}, blocking=True
        )


async def test_old_display_keeps_refresh_but_gets_no_reset_button(
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

    assert _entity_id(hass, mock_display_config_entry, "refresh") is not None
    assert _entity_id(hass, mock_display_config_entry, "reset_settings") is None
