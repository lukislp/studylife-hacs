"""Tests for the studylife-display current-frame camera entity (camera.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.camera import async_get_image
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife.display_api import DisplayApiError

from .conftest import get_entity_id, setup_display_integration


async def test_camera_entity_is_created_for_a_display_entry(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    entity_id = get_entity_id(
        hass, mock_display_config_entry.entry_id, "current_frame", platform="camera"
    )
    assert entity_id is not None
    assert hass.states.get(entity_id) is not None


async def test_camera_image_is_the_current_png(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = get_entity_id(
        hass, mock_display_config_entry.entry_id, "current_frame", platform="camera"
    )
    assert entity_id is not None

    image = await async_get_image(hass, entity_id)
    assert image.content == b"\x89PNG-fake-bytes"


async def test_camera_image_raises_when_the_display_api_errors(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    """async_camera_image returning None (see camera.py) makes Home Assistant's own
    async_get_image raise HomeAssistantError - the entity itself never raises."""
    mock_display_api_client.async_get_current_png.side_effect = DisplayApiError("boom")
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = get_entity_id(
        hass, mock_display_config_entry.entry_id, "current_frame", platform="camera"
    )
    assert entity_id is not None

    with pytest.raises(Exception):  # noqa: B017 - HomeAssistantError, no image available
        await async_get_image(hass, entity_id)
