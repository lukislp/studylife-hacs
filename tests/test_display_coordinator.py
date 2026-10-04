"""Tests for DisplayCoordinator (custom_components/studylife/display_coordinator.py)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.studylife.display_api import (
    DisplayApiAuthError,
    DisplayApiError,
    DisplayApiNotFoundError,
)
from custom_components.studylife.display_coordinator import (
    FALLBACK_PSEUDO_OPTIONS,
    DisplayCoordinator,
)

from .conftest import (
    make_raw_display_layouts,
    make_raw_display_layouts_legacy,
    make_raw_display_settings,
    make_raw_display_state,
)


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
    # The extended /api/layouts keys (duo release).
    assert [o.key for o in data.pseudo_options] == ["auto"]
    assert data.pseudo_options[0].name == {"de": "Automatisch", "en": "Automatic"}
    assert data.duo == ["focus", "agenda"]
    assert data.panes == ["classic", "focus", "exam"]


async def test_legacy_layouts_payload_gets_the_documented_fallbacks(
    coordinator: DisplayCoordinator, mock_display_api_client: AsyncMock
) -> None:
    """A display before the duo release sends only choice/resolved/options."""
    mock_display_api_client.async_get_layouts.return_value = (
        make_raw_display_layouts_legacy()
    )
    data = await coordinator._async_update_data()

    assert [o.key for o in data.layout_options] == ["classic", "focus", "exam"]
    assert data.pseudo_options == FALLBACK_PSEUDO_OPTIONS
    assert data.pseudo_options[0].key == "auto"
    assert data.pseudo_options[0].name == {"de": "Automatisch", "en": "Automatic"}
    assert data.duo == []
    assert data.panes == []


async def test_null_or_malformed_extended_keys_fall_back_too(
    coordinator: DisplayCoordinator, mock_display_api_client: AsyncMock
) -> None:
    # JSON null / a non-list where a list is expected must not blow up the poll.
    layouts = make_raw_display_layouts()
    layouts.update(
        {
            "pseudo": [],
            "duo": "focus",
            "panes": None,
        }
    )
    mock_display_api_client.async_get_layouts.return_value = layouts
    data = await coordinator._async_update_data()

    assert [o.key for o in data.pseudo_options] == ["auto"]
    assert data.duo == []
    assert data.panes == []


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


async def test_parses_settings_values_and_sources(
    coordinator: DisplayCoordinator, mock_display_api_client: AsyncMock
) -> None:
    mock_display_api_client.async_get_settings.return_value = make_raw_display_settings(
        language="en", sources={"language": True, "rotation": False}
    )
    data = await coordinator._async_update_data()

    assert data.settings["language"] == "en"
    assert data.settings["rotation"] == 0
    assert data.settings["update_check"] is True
    assert data.settings_sources["language"] is True
    assert data.settings_sources["rotation"] is False


async def test_settings_route_404_gives_empty_settings_not_a_failure(
    coordinator: DisplayCoordinator, mock_display_api_client: AsyncMock
) -> None:
    """A display before 1.11 has no /api/settings - it keeps working without them."""
    mock_display_api_client.async_get_settings.side_effect = DisplayApiNotFoundError(
        "404"
    )
    data = await coordinator._async_update_data()

    assert data.settings == {}
    assert data.settings_sources == {}
    assert data.status == "ok"


async def test_malformed_settings_document_gives_empty_settings(
    coordinator: DisplayCoordinator, mock_display_api_client: AsyncMock
) -> None:
    mock_display_api_client.async_get_settings.return_value = {"values": None}
    data = await coordinator._async_update_data()

    assert data.settings == {}
    assert data.settings_sources == {}


async def test_settings_auth_error_other_than_404_still_raises(
    coordinator: DisplayCoordinator, mock_display_api_client: AsyncMock
) -> None:
    mock_display_api_client.async_get_settings.side_effect = DisplayApiAuthError("401")
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_settings_fetch_failure_raises_update_failed(
    coordinator: DisplayCoordinator, mock_display_api_client: AsyncMock
) -> None:
    mock_display_api_client.async_get_settings.side_effect = DisplayApiError("boom")
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_parses_display_id(
    coordinator: DisplayCoordinator, mock_display_api_client: AsyncMock
) -> None:
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        display_id="0123456789abcdef"
    )

    data = await coordinator._async_update_data()

    assert data.display_id == "0123456789abcdef"


@pytest.mark.parametrize("raw", [None, "", "   ", 42, ["x"], {"a": 1}])
async def test_missing_or_malformed_display_id_is_none(
    coordinator: DisplayCoordinator, mock_display_api_client: AsyncMock, raw: object
) -> None:
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        display_id=raw
    )

    assert (await coordinator._async_update_data()).display_id is None


async def test_old_display_without_id_key_has_no_display_id(
    coordinator: DisplayCoordinator,
) -> None:
    assert (await coordinator._async_update_data()).display_id is None
