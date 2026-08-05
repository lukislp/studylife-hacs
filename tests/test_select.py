"""Tests for the StudyLife active-course select entity (select.py).

`StudyLifeActiveCourseSelect` is a pure local picker - it never calls the API
client itself, so these tests build the entity directly against a fake
coordinator (a MagicMock whose `.data` stands in for `StudyLifeData`) rather
than running a full config-entry setup. That keeps the options/restore/
attribute assertions direct while still exercising the real
`RestoreEntity`/`CoordinatorEntity` machinery (`entity.hass` +
`mock_restore_cache` are the real HA restore-state plumbing, not mocked out).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.studylife.select import StudyLifeActiveCourseSelect

from .conftest import make_course

ENTITY_ID = "select.studylife_active_course"


def _make_data(
    *,
    courses: list[dict[str, Any]],
    selected: list[int],
    completed: list[int] | None = None,
) -> SimpleNamespace:
    """A minimal stand-in for StudyLifeData exposing only what select.py reads."""
    return SimpleNamespace(
        settings={
            "selectedCourseIds": selected,
            "completedCourseIds": completed or [],
        },
        courses=courses,
    )


def _build_entity(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    data: SimpleNamespace,
    *,
    client: AsyncMock | None = None,
) -> StudyLifeActiveCourseSelect:
    coordinator = MagicMock()
    coordinator.data = data
    if client is not None:
        coordinator.client = client
    entity = StudyLifeActiveCourseSelect(coordinator, entry)
    entity.hass = hass
    entity.entity_id = ENTITY_ID
    return entity


# ---------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------


def test_options_excludes_unselected_and_completed_courses(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Only courses that are selected AND not yet completed show up as options."""
    courses = [
        make_course(id=1, name="Algorithms"),
        make_course(id=2, name="Databases"),
        make_course(id=3, name="Networks"),
    ]
    # 1: selected, active -> included
    # 2: selected, but completed -> excluded
    # 3: not selected at all -> excluded, even though not completed
    data = _make_data(courses=courses, selected=[1, 2], completed=[2])
    entity = _build_entity(hass, mock_config_entry, data)

    assert entity.options == ["Algorithms"]


# ---------------------------------------------------------------------------
# current_option
# ---------------------------------------------------------------------------


def test_current_option_defaults_to_first_option_with_no_prior_state(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """With nothing selected yet (fresh entity, no restore), current_option falls
    back to the first available option."""
    courses = [make_course(id=1, name="Algorithms"), make_course(id=2, name="Databases")]
    data = _make_data(courses=courses, selected=[1, 2])
    entity = _build_entity(hass, mock_config_entry, data)

    assert entity._selected is None
    assert entity.current_option == "Algorithms"


# ---------------------------------------------------------------------------
# restore behavior
# ---------------------------------------------------------------------------


async def test_restore_accepts_selection_still_in_options(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A restored last state that's still a valid option becomes current_option."""
    courses = [make_course(id=1, name="Algorithms"), make_course(id=2, name="Databases")]
    data = _make_data(courses=courses, selected=[1, 2])
    entity = _build_entity(hass, mock_config_entry, data)
    mock_restore_cache(hass, [State(ENTITY_ID, "Databases")])

    await entity.async_added_to_hass()

    assert entity.current_option == "Databases"


async def test_restore_rejects_selection_no_longer_in_options(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A restored last state for a course that's since been deselected/completed
    is NOT accepted - current_option falls back to options[0] instead of the
    stale value."""
    courses = [make_course(id=1, name="Algorithms"), make_course(id=2, name="Databases")]
    # The restored course ("Networks") isn't in the active set anymore.
    data = _make_data(courses=courses, selected=[1, 2])
    entity = _build_entity(hass, mock_config_entry, data)
    mock_restore_cache(hass, [State(ENTITY_ID, "Networks")])

    await entity.async_added_to_hass()

    assert entity._selected is None
    assert entity.current_option == "Algorithms"


# ---------------------------------------------------------------------------
# async_select_option
# ---------------------------------------------------------------------------


async def test_async_select_option_is_local_only(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client: AsyncMock
) -> None:
    """Selecting an option just updates local state - no API call, no coordinator
    refresh."""
    courses = [make_course(id=1, name="Algorithms"), make_course(id=2, name="Databases")]
    data = _make_data(courses=courses, selected=[1, 2])
    entity = _build_entity(hass, mock_config_entry, data, client=mock_api_client)
    coordinator = entity.coordinator
    # The entity was never added to a real platform (no full config-entry setup
    # in this test), so HA's name-resolution machinery inside
    # async_write_ha_state() isn't available - stub it out, since it's not what
    # this test is about; we only care that it's *called* (state marked dirty)
    # and that nothing else - API or coordinator - is touched.
    entity.async_write_ha_state = MagicMock()

    await entity.async_select_option("Databases")

    assert entity.current_option == "Databases"
    entity.async_write_ha_state.assert_called_once()
    assert mock_api_client.method_calls == []
    coordinator.async_request_refresh.assert_not_called()
    coordinator.async_refresh.assert_not_called()


# ---------------------------------------------------------------------------
# extra_state_attributes
# ---------------------------------------------------------------------------


def test_extra_state_attributes_for_current_selection(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    courses = [
        make_course(id=1, name="Algorithms", color="#ff0000"),
        make_course(id=2, name="Databases", color="#00ff00"),
    ]
    courses[0]["code"] = "CS101"
    data = _make_data(courses=courses, selected=[1, 2])
    entity = _build_entity(hass, mock_config_entry, data)

    attrs = entity.extra_state_attributes

    assert attrs["course_id"] == 1
    assert attrs["course_code"] == "CS101"
    assert attrs["course_color"] == "#ff0000"
    assert attrs["active_courses"] == courses
