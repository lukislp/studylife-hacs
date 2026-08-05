"""Tests for the StudyLife sensor platform (custom_components/studylife/sensor.py).

Exercises the real integration setup path (see conftest.setup_integration) rather
than instantiating entities directly, so these tests cover the same wiring HA
itself drives: coordinator first refresh -> hub sensors -> dynamic per-programme
sensor discovery. The lower-level "program deleted" defensiveness of
StudyLifeProgramSensor is covered separately, without a full hass setup, since
it doesn't depend on any of that wiring.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest
from freezegun import freeze_time
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife.sensor import (
    PROGRAM_SENSOR_DESCRIPTIONS,
    StudyLifeProgramSensor,
)

from .conftest import (
    get_entity_id,
    make_course,
    make_raw_session,
    make_raw_study_program,
    setup_integration,
)

# 2026-01-08 12:00:00 UTC = 2026-01-08 04:00 US/Pacific (the test hass fixture's
# default tz - see pytest_homeassistant_custom_component.common). A Thursday, so
# "this week" runs Monday 2026-01-05 through Sunday 2026-01-11 - safely mid-week,
# no timezone-driven date-boundary ambiguity.
FROZEN_NOW = "2026-01-08 12:00:00"


def _sensor_id(
    hass: HomeAssistant, entry: MockConfigEntry, key: str, *, program_id: str | None = None
) -> str | None:
    return get_entity_id(hass, entry.entry_id, key, program_id=program_id, platform="sensor")


# ---------------------------------------------------------------------------
# 1. A straightforward hub sensor reflects coordinator data correctly
# ---------------------------------------------------------------------------


@freeze_time(FROZEN_NOW)
async def test_week_hours_and_streak_reflect_coordinator_data(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client
) -> None:
    """week_hours/streak read straight off StudyLifeData.week_hours/streak_days -
    verified against hand-computed expectations for two sessions on two
    consecutive days (today and yesterday, both within the current Mon-Sun week)."""
    history = [
        make_raw_session(id=1, start="2026-01-08T10:00:00", end="2026-01-08T11:00:00"),  # 1.0h, today
        make_raw_session(id=2, start="2026-01-07T10:00:00", end="2026-01-07T11:30:00"),  # 1.5h, yesterday
    ]
    mock_api_client.async_get_sessions.return_value = history
    mock_api_client.async_get_session_history.return_value = history

    await setup_integration(hass, mock_config_entry, mock_api_client)

    week_hours_state = hass.states.get(_sensor_id(hass, mock_config_entry, "week_hours"))
    streak_state = hass.states.get(_sensor_id(hass, mock_config_entry, "streak"))

    assert week_hours_state is not None
    assert week_hours_state.state == "2.5"
    assert streak_state is not None
    assert streak_state.state == "2"


@freeze_time(FROZEN_NOW)
async def test_week_hours_zero_with_no_sessions(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client
) -> None:
    """No sessions at all -> week_hours/streak both read 0, not an error/unknown."""
    await setup_integration(hass, mock_config_entry, mock_api_client)

    assert hass.states.get(_sensor_id(hass, mock_config_entry, "week_hours")).state == "0.0"
    assert hass.states.get(_sensor_id(hass, mock_config_entry, "streak")).state == "0"


# ---------------------------------------------------------------------------
# 2. timer_phase reflects TimerState.phase for a few states
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_timer_state", "expected_phase"),
    [
        ({"isRunning": False}, "idle"),
        ({"isRunning": True, "isBreak": True}, "break"),
        ({"isRunning": True, "isBreak": False}, "focus"),
    ],
    ids=["idle", "break", "focus"],
)
async def test_timer_phase_reflects_timer_state(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api_client,
    raw_timer_state: dict,
    expected_phase: str,
) -> None:
    mock_api_client.async_get_timer_state.return_value = raw_timer_state

    await setup_integration(hass, mock_config_entry, mock_api_client)

    state = hass.states.get(_sensor_id(hass, mock_config_entry, "timer_phase"))
    assert state is not None
    assert state.state == expected_phase


# ---------------------------------------------------------------------------
# 4. Per-programme sensors exist for every programme, on separate devices
# ---------------------------------------------------------------------------


async def test_per_programme_sensors_created_for_every_programme(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client
) -> None:
    """With 2 study programmes, both get their own full PROGRAM_SENSOR_DESCRIPTIONS
    set - verified here via ects_progress, which also cross-checks that each
    programme's course catalog stays properly scoped to itself (not merged)."""
    mock_api_client.async_get_study_programs.return_value = [
        make_raw_study_program(id=None, name="Bachelor", is_built_in=True),
        make_raw_study_program(id=7, name="Master", is_built_in=False),
    ]
    courses_by_pid = {
        0: [make_course(id=100, name="Algorithms", ects=5, semester=1)],
        7: [make_course(id=200, name="Databases", ects=8, semester=1)],
    }
    mock_api_client.async_get_courses.side_effect = lambda pid: courses_by_pid.get(pid, [])

    await setup_integration(hass, mock_config_entry, mock_api_client)

    builtin_id = _sensor_id(hass, mock_config_entry, "ects_progress", program_id="builtin")
    master_id = _sensor_id(hass, mock_config_entry, "ects_progress", program_id="7")
    assert builtin_id is not None
    assert master_id is not None
    assert builtin_id != master_id

    builtin_state = hass.states.get(builtin_id)
    master_state = hass.states.get(master_id)
    assert builtin_state.state == "0"  # no completed courses
    assert builtin_state.attributes["ects_total"] == 5
    assert master_state.state == "0"
    assert master_state.attributes["ects_total"] == 8


# ---------------------------------------------------------------------------
# 5. Dynamic discovery: a programme created later gets its entities without restart
# ---------------------------------------------------------------------------


async def test_programme_added_later_gets_entities_via_coordinator_listener(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client
) -> None:
    """Starts with just the built-in programme, then a second refresh (as would
    happen on the next poll cycle after the user creates a programme in the
    StudyLife web UI) reveals a new one - the sync_program_entities listener
    registered in async_setup_entry must pick it up immediately, no HA restart."""
    mock_api_client.async_get_study_programs.return_value = [
        make_raw_study_program(id=None, name="Bachelor", is_built_in=True),
    ]
    mock_api_client.async_get_courses.side_effect = lambda pid: (
        [make_course(id=100, name="Algorithms", ects=5, semester=1)] if pid == 0 else []
    )

    coordinator = await setup_integration(hass, mock_config_entry, mock_api_client)

    assert _sensor_id(hass, mock_config_entry, "ects_progress", program_id="builtin") is not None
    assert _sensor_id(hass, mock_config_entry, "ects_progress", program_id="9") is None

    # A programme created later via the StudyLife web UI shows up on the next poll.
    mock_api_client.async_get_study_programs.return_value = [
        make_raw_study_program(id=None, name="Bachelor", is_built_in=True),
        make_raw_study_program(id=9, name="New Programme", is_built_in=False),
    ]
    mock_api_client.async_get_courses.side_effect = lambda pid: (
        [make_course(id=100, name="Algorithms", ects=5, semester=1)]
        if pid == 0
        else [make_course(id=300, name="New Course", ects=6, semester=1)]
        if pid == 9
        else []
    )

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    new_entity_id = _sensor_id(hass, mock_config_entry, "ects_progress", program_id="9")
    assert new_entity_id is not None
    state = hass.states.get(new_entity_id)
    assert state is not None
    assert state.state == "0"
    assert state.attributes["ects_total"] == 6


# ---------------------------------------------------------------------------
# 6. A programme that disappears goes unavailable, not just None-valued
# ---------------------------------------------------------------------------


async def test_deleted_programme_entity_becomes_unavailable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client
) -> None:
    mock_api_client.async_get_study_programs.return_value = [
        make_raw_study_program(id=None, name="Bachelor", is_built_in=True),
        make_raw_study_program(id=7, name="Master", is_built_in=False),
    ]
    courses_by_pid = {
        0: [make_course(id=100, name="Algorithms", ects=5, semester=1)],
        7: [make_course(id=200, name="Databases", ects=8, semester=1)],
    }
    mock_api_client.async_get_courses.side_effect = lambda pid: courses_by_pid.get(pid, [])

    coordinator = await setup_integration(hass, mock_config_entry, mock_api_client)

    master_id = _sensor_id(hass, mock_config_entry, "ects_progress", program_id="7")
    assert master_id is not None
    assert hass.states.get(master_id).state != STATE_UNAVAILABLE

    # "Master" is deleted in StudyLife - the next poll no longer lists it.
    mock_api_client.async_get_study_programs.return_value = [
        make_raw_study_program(id=None, name="Bachelor", is_built_in=True),
    ]
    mock_api_client.async_get_courses.side_effect = lambda pid: courses_by_pid.get(0, []) if pid == 0 else []

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # Entity is NOT removed from the registry - it flips to unavailable instead.
    assert _sensor_id(hass, mock_config_entry, "ects_progress", program_id="7") == master_id
    state = hass.states.get(master_id)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


# ---------------------------------------------------------------------------
# 7. StudyLifeProgramSensor is defensive when program_data is None (unit-level,
#    no hass setup needed - this is exactly the state a deleted programme's
#    entity is in before entity.py's `available` override kicks in at the HA
#    state-machine level).
# ---------------------------------------------------------------------------


def test_program_sensor_returns_none_and_empty_attrs_when_program_data_missing() -> None:
    coordinator = Mock()
    coordinator.data.programs = {}  # the programme isn't (or no longer is) known
    entry = Mock()
    entry.entry_id = "entry123"
    entry.data = {}

    for description in PROGRAM_SENSOR_DESCRIPTIONS:
        entity = StudyLifeProgramSensor(coordinator, entry, description, "builtin", "Bachelor")
        assert entity.native_value is None, description.key
        assert entity.extra_state_attributes == {}, description.key
