"""Tests for the studylife-display sensor entities (display_sensor.py): the primary
current-layout sensor and the diagnostic sensors that replaced its attributes."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.const import STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife.display_sensor import (
    DISPLAY_DIAGNOSTIC_SENSOR_DESCRIPTIONS,
)

from .conftest import (
    get_entity_id,
    make_raw_display_layouts,
    make_raw_display_layouts_legacy,
    make_raw_display_state,
    setup_display_integration,
)


def _state(hass: HomeAssistant, entry: MockConfigEntry, key: str):
    entity_id = get_entity_id(hass, entry.entry_id, key, platform="sensor")
    assert entity_id is not None, key
    state = hass.states.get(entity_id)
    assert state is not None, key
    return state


async def test_state_is_the_current_frame_layout(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    state = _state(hass, mock_display_config_entry, "current_layout")
    assert state.state == "focus"
    # The health fields are their own diagnostic entities now, not attributes here.
    assert "status" not in state.attributes
    assert "layout_choice" not in state.attributes
    assert "stale_minutes" not in state.attributes


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

    assert _state(hass, mock_display_config_entry, "current_layout").state == "week"
    # No frame yet -> no shown-at and no frame kind either.
    assert _state(hass, mock_display_config_entry, "shown_at").state == STATE_UNKNOWN
    assert _state(hass, mock_display_config_entry, "frame_kind").state == STATE_UNKNOWN


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

    assert _state(hass, mock_display_config_entry, "current_layout").state == "setup"
    assert _state(hass, mock_display_config_entry, "frame_kind").state == "setup"
    assert _state(hass, mock_display_config_entry, "status").state == "setup"


async def test_every_health_field_is_its_own_diagnostic_sensor(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    registry = er.async_get(hass)

    expected = {
        "status",
        "resolved_layout",
        "frame_kind",
        "shown_at",
        "stale_minutes",
        "last_error",
        "next_in_cycle",
        "version",
    }
    assert {d.key for d in DISPLAY_DIAGNOSTIC_SENSOR_DESCRIPTIONS} == expected
    for key in expected:
        entity_id = get_entity_id(
            hass, mock_display_config_entry.entry_id, key, platform="sensor"
        )
        assert entity_id is not None, key
        entry = registry.async_get(entity_id)
        assert entry is not None, key
        assert entry.entity_category == EntityCategory.DIAGNOSTIC, key

    # The primary sensor itself stays out of the diagnostic group.
    primary = registry.async_get(
        get_entity_id(
            hass,
            mock_display_config_entry.entry_id,
            "current_layout",
            platform="sensor",
        )
    )
    assert primary is not None and primary.entity_category is None


async def test_diagnostic_values_mirror_the_state_payload(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    status = _state(hass, mock_display_config_entry, "status")
    assert status.state == "ok"
    assert status.attributes["options"] == ["ok", "degraded", "error", "setup"]
    assert _state(hass, mock_display_config_entry, "resolved_layout").state == "focus"
    assert _state(hass, mock_display_config_entry, "frame_kind").state == "dashboard"
    assert _state(hass, mock_display_config_entry, "version").state == "1.10.0"

    stale = _state(hass, mock_display_config_entry, "stale_minutes")
    assert stale.state == "2"
    assert stale.attributes["unit_of_measurement"] == "min"
    assert stale.attributes["device_class"] == "duration"

    shown_at = _state(hass, mock_display_config_entry, "shown_at")
    assert shown_at.attributes["device_class"] == "timestamp"
    # 2026-09-25T20:30:34.711983+02:00 from the fixture, rendered in UTC by HA.
    assert shown_at.state == "2026-09-25T18:30:34+00:00"

    last_error = _state(hass, mock_display_config_entry, "last_error")
    assert last_error.state == "none"
    assert "message" not in last_error.attributes


async def test_last_error_exposes_kind_as_state_and_details_as_attributes(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        status="degraded",
        last_error={
            "kind": "transient",
            "status": 502,
            "message": "Bad Gateway",
            "at": "2026-09-25T20:25:00+02:00",
        },
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    last_error = _state(hass, mock_display_config_entry, "last_error")
    assert last_error.state == "transient"
    assert last_error.attributes["message"] == "Bad Gateway"
    assert last_error.attributes["http_status"] == 502
    assert last_error.attributes["at"] == "2026-09-25T20:25:00+02:00"
    assert _state(hass, mock_display_config_entry, "status").state == "degraded"


async def test_unknown_last_error_kind_falls_back_to_transient(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    # A kind a newer studylife-display might add - must not break the ENUM sensor.
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        last_error={
            "kind": "brand_new_kind",
            "status": None,
            "message": "?",
            "at": "2026-09-25T20:25:00+02:00",
        },
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    assert _state(hass, mock_display_config_entry, "last_error").state == "transient"


async def test_next_in_cycle_mirrors_the_layouts_payload(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    mock_display_api_client.async_get_layouts.return_value = make_raw_display_layouts(
        next_in_cycle="week"
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    state = _state(hass, mock_display_config_entry, "next_in_cycle")
    assert state.state == "week"
    assert state.attributes["icon"] == "mdi:skip-next"


async def test_next_in_cycle_is_unknown_on_a_legacy_display(
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

    state = _state(hass, mock_display_config_entry, "next_in_cycle")
    assert state.state == STATE_UNKNOWN
