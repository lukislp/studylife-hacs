"""Tests for the studylife-display number settings (display_number.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife import PLATFORMS_DISPLAY
from custom_components.studylife.display_api import (
    DisplayApiError,
    DisplayApiNotFoundError,
)

from .conftest import (
    get_entity_id,
    make_raw_display_settings,
    setup_display_integration,
)

NUMBER_KEYS = ["auto_recap_minutes", "redraw_after_minutes"]


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str | None:
    return get_entity_id(hass, entry.entry_id, key, platform="number")


def test_number_is_a_display_platform() -> None:
    assert Platform.NUMBER in PLATFORMS_DISPLAY


@pytest.mark.parametrize(
    ("key", "value", "maximum"),
    [("auto_recap_minutes", "10", 240), ("redraw_after_minutes", "60", 1440)],
)
async def test_number_is_a_config_entity_with_value_and_bounds(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
    key: str,
    value: str,
    maximum: int,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    entity_id = _entity_id(hass, mock_display_config_entry, key)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state.state == value
    assert state.attributes["min"] == 0
    assert state.attributes["max"] == maximum
    assert state.attributes["step"] == 1
    assert state.attributes["mode"] == "box"
    assert state.attributes["unit_of_measurement"] == "min"
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    assert entry.entity_category == EntityCategory.CONFIG
    assert entry.translation_key == f"display_{key}"


@pytest.mark.parametrize("missing", NUMBER_KEYS)
async def test_a_key_the_display_does_not_report_gets_no_entity(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
    missing: str,
) -> None:
    mock_display_api_client.async_get_settings.return_value = make_raw_display_settings(
        **{missing: None}
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    assert _entity_id(hass, mock_display_config_entry, missing) is None
    other = next(key for key in NUMBER_KEYS if key != missing)
    assert _entity_id(hass, mock_display_config_entry, other) is not None


async def test_zero_is_a_valid_value(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    mock_display_api_client.async_get_settings.return_value = make_raw_display_settings(
        auto_recap_minutes=0
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    entity_id = _entity_id(hass, mock_display_config_entry, "auto_recap_minutes")
    assert hass.states.get(entity_id).state == "0"


async def test_a_non_integer_value_is_unknown(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    mock_display_api_client.async_get_settings.return_value = make_raw_display_settings(
        auto_recap_minutes="soon"
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    entity_id = _entity_id(hass, mock_display_config_entry, "auto_recap_minutes")
    assert hass.states.get(entity_id).state == "unknown"


@pytest.mark.parametrize(
    ("key", "sent", "expected"),
    [
        ("auto_recap_minutes", 15, {"auto_recap_minutes": 15}),
        ("redraw_after_minutes", 90, {"redraw_after_minutes": 90}),
        # The UI delivers floats; the display wants a strict int.
        ("auto_recap_minutes", 15.0, {"auto_recap_minutes": 15}),
    ],
)
async def test_setting_a_value_sends_an_exact_int_and_refreshes(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
    key: str,
    sent: float,
    expected: dict[str, int],
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = _entity_id(hass, mock_display_config_entry, key)
    assert entity_id is not None

    mock_display_api_client.async_get_state.reset_mock()
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id, "value": sent},
        blocking=True,
    )
    await hass.async_block_till_done()

    mock_display_api_client.async_update_settings.assert_awaited_once_with(expected)
    (payload,) = mock_display_api_client.async_update_settings.await_args.args
    assert all(type(value) is int for value in payload.values())
    mock_display_api_client.async_get_state.assert_awaited()


async def test_a_rejected_value_surfaces_the_displays_reason(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = _entity_id(hass, mock_display_config_entry, "redraw_after_minutes")
    assert entity_id is not None
    mock_display_api_client.async_update_settings.side_effect = DisplayApiError(
        "redraw_after_minutes: must be 0..1440"
    )

    with pytest.raises(HomeAssistantError, match="must be 0..1440"):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": 1440},
            blocking=True,
        )


async def test_no_number_entities_on_a_display_without_the_settings_route(
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

    for key in NUMBER_KEYS:
        assert _entity_id(hass, mock_display_config_entry, key) is None
