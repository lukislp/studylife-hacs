"""Tests for the StudyLife binary_sensor platform
(custom_components/studylife/binary_sensor.py).

Same approach as test_sensor.py: drives the real integration setup path (see
conftest.setup_integration) so these tests cover the same wiring HA itself
uses, including dynamic per-programme entity discovery/removal-avoidance. The
lower-level "program deleted" defensiveness of StudyLifeProgramBinarySensor is
covered separately, without a full hass setup, since it doesn't depend on any
of that wiring.
"""
from __future__ import annotations

from unittest.mock import Mock

from freezegun import freeze_time
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife.binary_sensor import (
    PROGRAM_BINARY_SENSOR_DESCRIPTIONS,
    StudyLifeProgramBinarySensor,
)

from .conftest import (
    get_entity_id,
    make_course,
    make_raw_session,
    make_raw_study_program,
    setup_integration,
)

# Same frozen instant as test_sensor.py: a Thursday, so "this week" is Monday
# 2026-01-05 through Sunday 2026-01-11, and the same date sits comfortably
# inside January for the month-quota tests too.
FROZEN_NOW = "2026-01-08 12:00:00"


def _binary_sensor_id(
    hass: HomeAssistant, entry: MockConfigEntry, key: str, *, program_id: str | None = None
) -> str | None:
    return get_entity_id(
        hass, entry.entry_id, key, program_id=program_id, platform="binary_sensor"
    )


# ---------------------------------------------------------------------------
# 3. week_quota_warning / month_quota_warning reflect QuotaInfo.warning
# ---------------------------------------------------------------------------


async def test_week_quota_warning_on_when_under_weekly_target(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client
) -> None:
    """No sessions logged at all -> 0h is under any positive weekly goal (the
    default fallback is 25h, see const.WEEK_QUOTA_MIN_HOURS) -> QuotaInfo.warning
    is True."""
    coordinator = await setup_integration(hass, mock_config_entry, mock_api_client)

    assert coordinator.data.week_quota.warning is True
    state = hass.states.get(_binary_sensor_id(hass, mock_config_entry, "week_quota_warning"))
    assert state is not None
    assert state.state == STATE_ON


@freeze_time(FROZEN_NOW)
async def test_week_quota_warning_off_when_weekly_target_met(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client
) -> None:
    """2h logged today clears a 1h weekly goal -> warning False.

    Note: a goal of literally 0h can't be used for this - coordinator.py reads
    it as `settings.get("weeklyGoalMinHours") or WEEK_QUOTA_MIN_HOURS`, and
    Python's `or` treats 0 as falsy, so a 0h goal silently falls back to the
    25h default instead of being honored as "always met"."""
    mock_api_client.async_get_settings.return_value = {"weeklyGoalMinHours": 1}
    history = [make_raw_session(id=1, start="2026-01-08T10:00:00", end="2026-01-08T12:00:00")]
    mock_api_client.async_get_sessions.return_value = history
    mock_api_client.async_get_session_history.return_value = history

    coordinator = await setup_integration(hass, mock_config_entry, mock_api_client)

    assert coordinator.data.week_quota.warning is False
    state = hass.states.get(_binary_sensor_id(hass, mock_config_entry, "week_quota_warning"))
    assert state is not None
    assert state.state == STATE_OFF


async def test_month_quota_warning_on_when_under_monthly_target(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client
) -> None:
    coordinator = await setup_integration(hass, mock_config_entry, mock_api_client)

    assert coordinator.data.month_quota.warning is True
    state = hass.states.get(_binary_sensor_id(hass, mock_config_entry, "month_quota_warning"))
    assert state is not None
    assert state.state == STATE_ON


@freeze_time(FROZEN_NOW)
async def test_month_quota_warning_off_when_monthly_target_met(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client
) -> None:
    """2h logged this month clears a 1h monthly goal (prorated down to a
    fraction of an hour this early in the month) -> warning False. Same 0h
    caveat as the weekly test above applies to monthlyGoalMinHours."""
    mock_api_client.async_get_settings.return_value = {"monthlyGoalMinHours": 1}
    history = [make_raw_session(id=1, start="2026-01-08T10:00:00", end="2026-01-08T12:00:00")]
    mock_api_client.async_get_sessions.return_value = history
    mock_api_client.async_get_session_history.return_value = history

    coordinator = await setup_integration(hass, mock_config_entry, mock_api_client)

    assert coordinator.data.month_quota.warning is False
    state = hass.states.get(_binary_sensor_id(hass, mock_config_entry, "month_quota_warning"))
    assert state is not None
    assert state.state == STATE_OFF


# ---------------------------------------------------------------------------
# 4. Per-programme binary sensors exist for every programme, on separate devices
# ---------------------------------------------------------------------------


async def test_per_programme_binary_sensors_created_for_every_programme(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client
) -> None:
    """With 2 study programmes, both get program_completed/program_active - and
    with distinct completed/active flags per programme, confirms each entity
    reads its OWN programme's data rather than sharing state."""
    mock_api_client.async_get_settings.return_value = {"activeStudyProgramId": 7}
    mock_api_client.async_get_study_programs.return_value = [
        make_raw_study_program(id=None, name="Bachelor", is_built_in=True, is_completed=False),
        make_raw_study_program(id=7, name="Master", is_built_in=False, is_completed=True),
    ]
    courses_by_pid = {
        0: [make_course(id=100, name="Algorithms")],
        7: [make_course(id=200, name="Databases")],
    }
    mock_api_client.async_get_courses.side_effect = lambda pid: courses_by_pid.get(pid, [])

    await setup_integration(hass, mock_config_entry, mock_api_client)

    builtin_completed_id = _binary_sensor_id(
        hass, mock_config_entry, "program_completed", program_id="builtin"
    )
    master_completed_id = _binary_sensor_id(
        hass, mock_config_entry, "program_completed", program_id="7"
    )
    builtin_active_id = _binary_sensor_id(
        hass, mock_config_entry, "program_active", program_id="builtin"
    )
    master_active_id = _binary_sensor_id(
        hass, mock_config_entry, "program_active", program_id="7"
    )
    for entity_id in (builtin_completed_id, master_completed_id, builtin_active_id, master_active_id):
        assert entity_id is not None

    assert hass.states.get(builtin_completed_id).state == STATE_OFF
    assert hass.states.get(master_completed_id).state == STATE_ON
    # activeStudyProgramId=7 -> Master is the app-active programme, not Bachelor.
    assert hass.states.get(builtin_active_id).state == STATE_OFF
    assert hass.states.get(master_active_id).state == STATE_ON


# ---------------------------------------------------------------------------
# 5. Dynamic discovery: a programme created later gets its entities without restart
# ---------------------------------------------------------------------------


async def test_programme_added_later_gets_binary_sensors_via_coordinator_listener(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client
) -> None:
    mock_api_client.async_get_study_programs.return_value = [
        make_raw_study_program(id=None, name="Bachelor", is_built_in=True),
    ]
    mock_api_client.async_get_courses.return_value = []

    coordinator = await setup_integration(hass, mock_config_entry, mock_api_client)

    assert (
        _binary_sensor_id(hass, mock_config_entry, "program_active", program_id="builtin")
        is not None
    )
    assert _binary_sensor_id(hass, mock_config_entry, "program_active", program_id="9") is None

    mock_api_client.async_get_study_programs.return_value = [
        make_raw_study_program(id=None, name="Bachelor", is_built_in=True),
        make_raw_study_program(id=9, name="New Programme", is_built_in=False, is_completed=True),
    ]

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    new_entity_id = _binary_sensor_id(hass, mock_config_entry, "program_completed", program_id="9")
    assert new_entity_id is not None
    state = hass.states.get(new_entity_id)
    assert state is not None
    assert state.state == STATE_ON


# ---------------------------------------------------------------------------
# 6. A programme that disappears goes unavailable, not just None/off-valued
# ---------------------------------------------------------------------------


async def test_deleted_programme_binary_sensor_becomes_unavailable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client
) -> None:
    mock_api_client.async_get_study_programs.return_value = [
        make_raw_study_program(id=None, name="Bachelor", is_built_in=True),
        make_raw_study_program(id=7, name="Master", is_built_in=False, is_completed=True),
    ]
    mock_api_client.async_get_courses.return_value = []

    coordinator = await setup_integration(hass, mock_config_entry, mock_api_client)

    master_id = _binary_sensor_id(hass, mock_config_entry, "program_completed", program_id="7")
    assert master_id is not None
    assert hass.states.get(master_id).state == STATE_ON

    # "Master" is deleted in StudyLife - the next poll no longer lists it.
    mock_api_client.async_get_study_programs.return_value = [
        make_raw_study_program(id=None, name="Bachelor", is_built_in=True),
    ]

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # Entity is NOT removed from the registry - it flips to unavailable instead
    # of e.g. silently reporting "off" (which would misreport a completed
    # programme as suddenly incomplete).
    assert (
        _binary_sensor_id(hass, mock_config_entry, "program_completed", program_id="7")
        == master_id
    )
    state = hass.states.get(master_id)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


# ---------------------------------------------------------------------------
# 7. StudyLifeProgramBinarySensor is defensive when program_data is None
#    (unit-level, no hass setup needed).
# ---------------------------------------------------------------------------


def test_program_binary_sensor_returns_none_and_empty_attrs_when_program_data_missing() -> None:
    coordinator = Mock()
    coordinator.data.programs = {}  # the programme isn't (or no longer is) known
    entry = Mock()
    entry.entry_id = "entry123"
    entry.data = {}

    for description in PROGRAM_BINARY_SENSOR_DESCRIPTIONS:
        entity = StudyLifeProgramBinarySensor(coordinator, entry, description, "builtin", "Bachelor")
        assert entity.is_on is None, description.key
        assert entity.extra_state_attributes == {}, description.key
