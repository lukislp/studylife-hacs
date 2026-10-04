"""Tests for the studylife-display select entities (display_select.py): the layout
choice and the two duo halves."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife.display_api import DisplayApiError

from .conftest import (
    get_entity_id,
    make_raw_display_layout_option,
    make_raw_display_layouts,
    make_raw_display_layouts_legacy,
    make_raw_display_state,
    setup_display_integration,
)


def _select_state(hass: HomeAssistant, entry: MockConfigEntry, key: str):
    entity_id = get_entity_id(hass, entry.entry_id, key, platform="select")
    assert entity_id is not None, key
    state = hass.states.get(entity_id)
    assert state is not None, key
    return state


# --------------------------------------------------------------------------
# Layout choice
# --------------------------------------------------------------------------


async def test_options_are_the_pseudo_choices_then_every_layout_key(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    state = _select_state(hass, mock_display_config_entry, "layout")
    # Order matters: pseudo choices first (as the display's own UI lists them), then
    # the real layouts in the display's order.
    assert state.attributes["options"] == ["auto", "classic", "focus", "exam"]


async def test_every_pseudo_choice_the_display_offers_is_listed(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    """The pseudo choices come straight off the wire - a display offering a second one
    gets it listed ahead of the real layouts without the integration knowing its key."""
    mock_display_api_client.async_get_layouts.return_value = make_raw_display_layouts(
        pseudo=[
            make_raw_display_layout_option(
                key="auto", name_de="Automatisch", name_en="Automatic"
            ),
            make_raw_display_layout_option(
                key="other", name_de="Anderes", name_en="Other"
            ),
        ]
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    state = _select_state(hass, mock_display_config_entry, "layout")
    assert state.attributes["options"] == ["auto", "other", "classic", "focus", "exam"]


async def test_legacy_display_falls_back_to_auto_plus_layout_keys(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    """A display before the duo release sends only choice/resolved/options -
    the layout select must look exactly as it did before, and no duo entities appear."""
    mock_display_api_client.async_get_layouts.return_value = (
        make_raw_display_layouts_legacy()
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    state = _select_state(hass, mock_display_config_entry, "layout")
    assert state.attributes["options"] == ["auto", "classic", "focus", "exam"]
    for key in ("duo_left", "duo_right"):
        assert (
            get_entity_id(
                hass, mock_display_config_entry.entry_id, key, platform="select"
            )
            is None
        ), key


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

    assert _select_state(hass, mock_display_config_entry, "layout").state == "classic"


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

    # A plain layout change sends no duo pair - it stays as it is.
    mock_display_api_client.async_set_layout.assert_awaited_once_with("exam")
    # async_request_refresh triggers another poll.
    mock_display_api_client.async_get_state.assert_awaited()


# --------------------------------------------------------------------------
# Duo halves
# --------------------------------------------------------------------------


async def test_duo_selects_offer_the_panes_and_show_the_current_pair(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    mock_display_api_client.async_get_layouts.return_value = make_raw_display_layouts(
        duo=["focus", "exam"]
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    registry = er.async_get(hass)

    left = _select_state(hass, mock_display_config_entry, "duo_left")
    right = _select_state(hass, mock_display_config_entry, "duo_right")
    # panes = every real layout key (the fixture's default), never the pseudo choices.
    assert left.attributes["options"] == ["classic", "focus", "exam"]
    assert right.attributes["options"] == ["classic", "focus", "exam"]
    assert left.state == "focus"
    assert right.state == "exam"
    for state in (left, right):
        entry = registry.async_get(state.entity_id)
        assert entry is not None, state.entity_id
        assert entry.entity_category == EntityCategory.CONFIG, state.entity_id


@pytest.mark.parametrize(
    ("key", "option", "expected_duo"),
    [
        ("duo_left", "classic", ["classic", "exam"]),
        ("duo_right", "classic", ["focus", "classic"]),
    ],
)
async def test_selecting_a_duo_half_resends_the_current_choice_with_the_new_pair(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
    key: str,
    option: str,
    expected_duo: list[str],
) -> None:
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        layout_choice="classic"
    )
    mock_display_api_client.async_get_layouts.return_value = make_raw_display_layouts(
        choice="classic", duo=["focus", "exam"]
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = get_entity_id(
        hass, mock_display_config_entry.entry_id, key, platform="select"
    )
    assert entity_id is not None

    mock_display_api_client.async_get_state.reset_mock()
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id, "option": option},
        blocking=True,
    )
    await hass.async_block_till_done()

    # The layout CHOICE is re-sent unchanged ("classic" here, not "duo") - editing the
    # pair must never switch the display's layout by itself.
    mock_display_api_client.async_set_layout.assert_awaited_once_with(
        "classic", duo=expected_duo
    )
    mock_display_api_client.async_get_state.assert_awaited()


async def test_duo_rejected_by_the_display_surfaces_as_home_assistant_error(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    entity_id = get_entity_id(
        hass, mock_display_config_entry.entry_id, "duo_left", platform="select"
    )
    assert entity_id is not None
    mock_display_api_client.async_set_layout.side_effect = DisplayApiError(
        "duo: unknown layout 'classic'"
    )

    with pytest.raises(HomeAssistantError, match="unknown layout"):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": entity_id, "option": "classic"},
            blocking=True,
        )


async def test_no_duo_entities_when_the_display_reports_no_panes(
    hass: HomeAssistant,
    mock_display_config_entry: MockConfigEntry,
    mock_display_api_client: AsyncMock,
) -> None:
    # Extended payload otherwise, but an empty panes list - same outcome as a legacy
    # display: no duo selects at all rather than two unavailable ones.
    mock_display_api_client.async_get_layouts.return_value = make_raw_display_layouts(
        panes=[]
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    for key in ("duo_left", "duo_right"):
        assert (
            get_entity_id(
                hass, mock_display_config_entry.entry_id, key, platform="select"
            )
            is None
        ), key
