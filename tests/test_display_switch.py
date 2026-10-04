"""Tests for the studylife-display update-check switch (display_switch.py)."""

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


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    return get_entity_id(hass, entry.entry_id, "update_check", platform="switch")


@pytest.mark.parametrize(("value", "state"), [(True, "on"), (False, "off")])
async def test_state_follows_the_setting(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
    value: bool,
    state: str,
) -> None:
    mock_display_api_client.async_get_settings.return_value = make_raw_display_settings(
        update_check=value
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    entity_id = _entity_id(hass, mock_display_config_entry)
    assert entity_id is not None
    assert hass.states.get(entity_id).state == state
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    assert entry.entity_category == EntityCategory.CONFIG
    assert entry.translation_key == "display_update_check"


@pytest.mark.parametrize(
    ("service", "expected"), [("turn_on", True), ("turn_off", False)]
)
async def test_turning_the_switch_sends_a_bool_and_refreshes(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
    service: str,
    expected: bool,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = _entity_id(hass, mock_display_config_entry)
    assert entity_id is not None

    mock_display_api_client.async_get_state.reset_mock()
    await hass.services.async_call(
        "switch", service, {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()

    mock_display_api_client.async_update_settings.assert_awaited_once_with(
        {"update_check": expected}
    )
    mock_display_api_client.async_get_state.assert_awaited()


async def test_a_rejected_change_surfaces_the_reason(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = _entity_id(hass, mock_display_config_entry)
    assert entity_id is not None
    mock_display_api_client.async_update_settings.side_effect = DisplayApiError(
        "update_check: read-only"
    )

    with pytest.raises(HomeAssistantError, match="read-only"):
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": entity_id}, blocking=True
        )


async def test_not_created_when_the_key_is_missing(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    document = make_raw_display_settings()
    del document["values"]["update_check"]
    mock_display_api_client.async_get_settings.return_value = document
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    assert _entity_id(hass, mock_display_config_entry) is None


async def test_not_created_on_a_display_without_the_settings_route(
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

    assert _entity_id(hass, mock_display_config_entry) is None
