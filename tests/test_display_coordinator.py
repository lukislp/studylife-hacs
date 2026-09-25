"""Tests for DisplayCoordinator (custom_components/studylife/display_coordinator.py)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.studylife.display_api import DisplayApiAuthError, DisplayApiError
from custom_components.studylife.display_coordinator import DisplayCoordinator

from .conftest import make_raw_display_layouts, make_raw_display_state


@pytest.fixture
def coordinator(
    hass: HomeAssistant, mock_display_api_client: AsyncMock
) -> DisplayCoordinator:
    return DisplayCoordinator(hass, mock_display_api_client, timedelta(seconds=60))


async def test_parses_state_and_layouts_into_display_data(
    coordinator: DisplayCoordinator, mock_display_api_client: AsyncMock
) -> None:
    data = await coordinator._async_update_data()

    assert data.status == "ok"
    assert data.version == "1.10.0"
    assert data.layout_choice == "auto"
    assert data.resolved_layout == "focus"
    assert data.current_frame_kind == "dashboard"
    assert data.current_frame_layout == "focus"
    assert data.current_frame_shown_at is not None
    assert [o.key for o in data.layout_options] == ["classic", "focus", "exam"]
    assert data.layout_options[1].name == {"de": "Fokus", "en": "Focus"}


async def test_current_frame_none_before_the_first_frame(
    coordinator: DisplayCoordinator, mock_display_api_client: AsyncMock
) -> None:
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        status="setup",
        setup=True,
        layout=None,
        layout_choice="auto",
        current_frame=None,
    )
    data = await coordinator._async_update_data()

    assert data.setup is True
    assert data.current_frame_kind is None
    assert data.current_frame_layout is None
    assert data.current_frame_shown_at is None


async def test_error_kind_frame_has_no_layout(
    coordinator: DisplayCoordinator, mock_display_api_client: AsyncMock
) -> None:
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        status="error",
        layout=None,
        current_frame={
            "shown_at": "2026-09-25T20:00:00+02:00",
            "layout": None,
            "kind": "error",
        },
    )
    mock_display_api_client.async_get_layouts.return_value = make_raw_display_layouts(
        resolved="classic"
    )
    data = await coordinator._async_update_data()

    assert data.current_frame_kind == "error"
    assert data.current_frame_layout is None


async def test_auth_error_raises_config_entry_auth_failed(
    coordinator: DisplayCoordinator, mock_display_api_client: AsyncMock
) -> None:
    mock_display_api_client.async_get_state.side_effect = DisplayApiAuthError("401")
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_generic_error_raises_update_failed(
    coordinator: DisplayCoordinator, mock_display_api_client: AsyncMock
) -> None:
    mock_display_api_client.async_get_state.side_effect = DisplayApiError("boom")
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_layouts_fetch_failure_also_raises_update_failed(
    coordinator: DisplayCoordinator, mock_display_api_client: AsyncMock
) -> None:
    mock_display_api_client.async_get_layouts.side_effect = DisplayApiError("boom")
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
