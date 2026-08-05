"""Tests for StudyLifeCoordinator (custom_components/studylife/coordinator.py)."""
from __future__ import annotations

import dataclasses
from datetime import timedelta
from unittest.mock import AsyncMock, call

import pytest
from freezegun import freeze_time
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.studylife import coordinator as coordinator_module
from custom_components.studylife.api import StudyLifeApiAuthError, StudyLifeApiError
from custom_components.studylife.const import (
    EVENT_WEEKLY_REPORT,
    MONTH_QUOTA_MAX_HOURS,
    MONTH_QUOTA_MIN_HOURS,
    WEEK_QUOTA_MAX_HOURS,
    WEEK_QUOTA_MIN_HOURS,
)
from custom_components.studylife.coordinator import (
    StudyLifeCoordinator,
    StudyLifeData,
    WeeklyReport,
)

from .conftest import make_raw_session


async def _capture_weekly_report_events(hass: HomeAssistant) -> list:
    """Register a listener for EVENT_WEEKLY_REPORT and return the list it appends to."""
    captured: list = []

    async def _listener(event) -> None:
        captured.append(event)

    hass.bus.async_listen(EVENT_WEEKLY_REPORT, _listener)
    return captured


async def test_refresh_populates_data(hass: HomeAssistant, mock_api_client: AsyncMock) -> None:
    """A successful refresh should produce a fully-populated StudyLifeData."""
    mock_api_client.async_get_sessions.return_value = [
        make_raw_session(id=1, course_id=100, is_completed=True),
        make_raw_session(id=2, course_id=100, is_completed=True),
    ]
    mock_api_client.async_get_timer_state.return_value = {
        "sessionId": 1,
        "isRunning": True,
        "isBreak": False,
        "currentRound": 1,
        "timerModeId": 1,
        "phaseEndsAt": "2026-08-05T10:30:00",
    }

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert isinstance(coordinator.data, StudyLifeData)
    assert len(coordinator.data.sessions) == 2
    # Both raw sessions are is_completed=True, so both count towards course_hours
    # regardless of the real current date the test happens to run on.
    assert coordinator.data.total_sessions == 2
    assert coordinator.data.timer_state is not None
    assert coordinator.data.timer_state.session_id == 1
    assert coordinator.data.timer_state.is_running is True


async def test_explicit_zero_goal_is_not_overridden_by_default(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """Regression test: settings.get(key) or DEFAULT treats an explicit 0 ("no goal")
    as falsy and silently substitutes the built-in default (25h/100h), which would
    fire quota warnings the user deliberately turned off by setting 0. The fix
    (_setting_or_default) must only fall back when the field is genuinely absent."""
    mock_api_client.async_get_settings.return_value = {
        "weeklyGoalMinHours": 0,
        "weeklyGoalMaxHours": 0,
        "monthlyGoalMinHours": 0,
        "monthlyGoalMaxHours": 0,
    }

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.week_quota.target_min == 0
    assert coordinator.data.week_quota.target_max == 0
    assert coordinator.data.week_quota.warning is False
    assert coordinator.data.month_quota.target_min == 0
    assert coordinator.data.month_quota.target_max == 0
    assert coordinator.data.month_quota.warning is False


@freeze_time("2026-01-31")  # deterministic date; since the month-quota proration was
# removed (full goal applies on every day of the month, in lockstep with the app's
# dashboard card), the specific day no longer matters for the equality assertions -
# the freeze just keeps the test independent of the wall clock.
async def test_missing_goal_settings_fall_back_to_defaults(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """A not-yet-migrated server that doesn't send these fields at all (None, not 0)
    should still fall back to the built-in defaults - only an explicit 0 is exempt."""
    mock_api_client.async_get_settings.return_value = {}

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.week_quota.target_min == WEEK_QUOTA_MIN_HOURS
    assert coordinator.data.week_quota.target_max == WEEK_QUOTA_MAX_HOURS
    assert coordinator.data.month_quota.target_min == MONTH_QUOTA_MIN_HOURS
    assert coordinator.data.month_quota.target_max == MONTH_QUOTA_MAX_HOURS


async def test_auth_error_maps_to_config_entry_auth_failed(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """StudyLifeApiAuthError from any fetch call should surface as ConfigEntryAuthFailed.

    DataUpdateCoordinator.async_refresh() swallows ConfigEntryAuthFailed internally
    (it triggers HA's reauth flow rather than propagating), recording it on
    last_exception/last_update_success instead of raising - see
    DataUpdateCoordinator._async_refresh.
    """
    mock_api_client.async_get_sessions.side_effect = StudyLifeApiAuthError("401 rejected")

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert isinstance(coordinator.last_exception, ConfigEntryAuthFailed)
    assert "401 rejected" in str(coordinator.last_exception)

    # Calling _async_update_data() directly confirms the exception really is raised
    # (not just recorded) by the coordinator's own mapping logic.
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_api_error_maps_to_update_failed(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """StudyLifeApiError from any fetch call should surface as UpdateFailed."""
    mock_api_client.async_get_settings.side_effect = StudyLifeApiError("server unreachable")

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert "server unreachable" in str(coordinator.last_exception)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_weekly_report_event_not_fired_on_first_refresh(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """No previous week id exists yet on the first-ever refresh, so nothing fires."""
    captured = await _capture_weekly_report_events(hass)

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    assert coordinator._last_report_week_id is None

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert captured == []
    # The week id is now recorded for comparison on the *next* refresh.
    assert coordinator._last_report_week_id is not None


async def test_weekly_report_event_not_fired_when_week_unchanged(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """Two refreshes computing the same completed-week id should never fire."""
    captured = await _capture_weekly_report_events(hass)

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()  # primes _last_report_week_id
    await hass.async_block_till_done()
    assert captured == []

    # Nothing about the (empty, static) input data changed, so the second refresh
    # computes the exact same week id as the first.
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert captured == []


async def test_weekly_report_event_fired_when_week_changes(
    hass: HomeAssistant, mock_api_client: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A differing computed week id between two refreshes fires EVENT_WEEKLY_REPORT
    with the new report's data as the payload.

    Constructing real session data that shifts which Mon-Sun week is "most recently
    completed" is fiddly, so `_calc_weekly_report` is monkeypatched directly to
    return two distinct reports across the two refreshes - the coordinator only
    cares about the returned `week_id` differing, not how it was computed.
    """
    captured = await _capture_weekly_report_events(hass)

    report_week_1 = WeeklyReport(
        week_id="2026-W10",
        hours=5.0,
        delta_vs_previous_week_hours=1.0,
        top_course="Algorithms",
        sessions_count=3,
    )
    report_week_2 = WeeklyReport(
        week_id="2026-W11",
        hours=7.5,
        delta_vs_previous_week_hours=2.5,
        top_course="Databases",
        sessions_count=4,
    )
    call_results = iter([report_week_1, report_week_2])
    monkeypatch.setattr(
        coordinator_module,
        "_calc_weekly_report",
        lambda history, today: next(call_results),
    )

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert captured == []  # first refresh: still no previous id to compare against

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(captured) == 1
    assert captured[0].data == dataclasses.asdict(report_week_2)
    assert coordinator._last_report_week_id == "2026-W11"


async def test_multiple_study_programs_fetch_courses_per_program(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """One async_get_courses(program_id) call is expected per study programme
    returned by async_get_study_programs - the built-in one (id None -> 0) plus
    each custom one (its own id)."""
    mock_api_client.async_get_study_programs.return_value = [
        {"id": None, "name": "Built-in", "isBuiltIn": True, "isCompleted": False},
        {"id": 5, "name": "Custom Program", "isBuiltIn": False, "isCompleted": False},
    ]

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert mock_api_client.async_get_courses.call_count == 2
    assert mock_api_client.async_get_courses.call_args_list == [call(0), call(5)]
    assert set(coordinator.data.programs.keys()) == {"builtin", "5"}
