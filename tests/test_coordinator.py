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

from .conftest import make_course, make_raw_session, make_raw_study_program


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


# ---------------------------------------------------------------------------
# D4 fix #1: week_hours upper bound (`_async_update_data` AND `_build_program_data`
# both filter sessions to `week_start <= start < week_start + 7 days`, matching
# Index.razor.cs exactly - a far-future-dated session must not inflate "this week").
# ---------------------------------------------------------------------------

# 2026-01-08 12:00:00 UTC = 2026-01-08 04:00 US/Pacific (the test hass fixture's
# default tz - see pytest_homeassistant_custom_component.common). A Thursday, so
# "this week" runs Monday 2026-01-05 through Sunday 2026-01-11 - same convention as
# test_sensor.py's FROZEN_NOW, reused here for the same reason.
FROZEN_NOW = "2026-01-08 12:00:00"


@freeze_time(FROZEN_NOW)
async def test_week_hours_excludes_future_dated_session_beyond_this_week(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """Regression test for audit finding D4: /api/sessions/history has no upper date
    bound server-side, so a session scheduled almost 4 weeks out (well past this
    Mon-Sun week) must be EXCLUDED from week_hours/week_sessions, not silently
    inflate them. Mirrors the golden-fixture scenario
    "week_quota_future_dated_session_drift" (tests/test_metrics_golden_fixtures.py)."""
    history = [
        make_raw_session(id=1, start="2026-01-06T09:00:00", end="2026-01-06T11:00:00"),  # 2h, this week
        make_raw_session(id=2, start="2026-02-02T09:00:00", end="2026-02-02T13:00:00"),  # 4h, ~4 weeks out
    ]
    mock_api_client.async_get_session_history.return_value = history

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.week_hours == 2.0  # NOT 6.0 (2 + 4)
    assert len(coordinator.data.week_sessions) == 1
    assert coordinator.data.week_sessions[0].id == 1


@freeze_time(FROZEN_NOW)
async def test_program_data_week_hours_also_excludes_future_dated_session(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """Same fix, the OTHER call site: _build_program_data's per-programme week_hours
    (feeding coordinator.data.programs[...]/the per-programme device sensors) must
    apply the identical upper bound, not just the top-level field."""
    history = [
        make_raw_session(id=1, course_id=100, start="2026-01-06T09:00:00", end="2026-01-06T11:00:00"),
        make_raw_session(id=2, course_id=100, start="2026-02-02T09:00:00", end="2026-02-02T13:00:00"),
    ]
    mock_api_client.async_get_session_history.return_value = history
    mock_api_client.async_get_study_programs.return_value = [
        make_raw_study_program(id=None, name="Built-in", is_built_in=True),
    ]
    mock_api_client.async_get_courses.return_value = [make_course(id=100)]

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.programs["builtin"].week_hours == 2.0


@freeze_time(FROZEN_NOW)
async def test_month_hours_still_includes_far_future_dated_session_intentionally(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """NOT a bug, and NOT touched by the D4 fix: Index.razor.cs's own month filter
    (`monthSessions = history.Where(s => s.StartTime.Date >= monthStart)`) has no
    upper bound either - checked and confirmed the real app's intended behavior, not
    a second instance of the week_hours drift. A session dated well beyond the
    current month must still count towards month_hours, exactly like before this
    fix - this regression test exists so a future well-meaning "consistency" change
    doesn't silently add an upper bound here and break parity with the C# truth
    (the golden fixtures would catch it too, but the intent should be explicit here)."""
    history = [
        make_raw_session(id=1, start="2026-01-06T09:00:00", end="2026-01-06T11:00:00"),  # 2h, this month
        make_raw_session(id=2, start="2026-02-02T09:00:00", end="2026-02-02T13:00:00"),  # 4h, next month
    ]
    mock_api_client.async_get_session_history.return_value = history

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.month_quota.hours == 6.0  # 2 + 4, deliberately unbounded


# ---------------------------------------------------------------------------
# D4 fix #2: active custom study programme's elective-group ECTS quotas come from
# GET /api/studyprograms/{id} (StudyProgramDetailDto.GroupEctsQuotas) instead of
# regex-parsing "(N ECTS)" out of the group's display name.
# ---------------------------------------------------------------------------


async def test_active_custom_program_uses_study_program_detail_for_group_quotas(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """The exact bug this fix targets: an elective group named plain "Electives" (no
    "(N ECTS)" substring) whose real, DB-configured quota (5) only the detail
    endpoint can supply. Mirrors the golden-fixture scenario
    "custom_program_group_quota_not_embedded_in_name"."""
    mock_api_client.async_get_settings.return_value = {
        "activeStudyProgramId": 7,
        "completedCourseIds": [20, 21],
    }
    mock_api_client.async_get_study_programs.return_value = [
        make_raw_study_program(id=None, name="Built-in", is_built_in=True),
        make_raw_study_program(id=7, name="Custom", is_built_in=False),
    ]
    custom_courses = [
        make_course(id=20, ects=3, group="Electives"),
        make_course(id=21, ects=4, group="Electives"),
    ]
    mock_api_client.async_get_courses.side_effect = (
        lambda program_id: custom_courses if program_id == 7 else []
    )
    mock_api_client.async_get_study_program.return_value = {
        "id": 7,
        "name": "Custom",
        "groupEctsQuotas": {"Electives": 5},
    }

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    mock_api_client.async_get_study_program.assert_awaited_once_with(7)
    # NOT ectsTotal=7/ectsEarned=7 (the uncapped regex-fallback sum).
    assert coordinator.data.ects_total == 5
    assert coordinator.data.ects_earned == 5
    assert coordinator.data.programs["7"].ects_total == 5
    assert coordinator.data.programs["7"].ects_earned == 5


async def test_active_builtin_program_does_not_fetch_study_program_detail(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """No activeStudyProgramId (built-in programme active, the default mock) - the
    built-in entry has no DB row and no /api/studyprograms/{id} route at all, so the
    coordinator must not call the new endpoint in this case."""
    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    mock_api_client.async_get_study_program.assert_not_awaited()


async def test_stale_active_program_id_skips_detail_fetch_and_falls_back(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """activeStudyProgramId pointing at a programme no longer present in
    async_get_study_programs (e.g. just deleted from another client) must NOT
    attempt the detail fetch - that would 404 and fail the WHOLE refresh - and must
    still fall back to the built-in programme, exactly like active_study_program's
    own pre-existing stale-id defensiveness."""
    mock_api_client.async_get_settings.return_value = {"activeStudyProgramId": 999}
    mock_api_client.async_get_study_programs.return_value = [
        make_raw_study_program(id=None, name="Built-in", is_built_in=True),
    ]

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    mock_api_client.async_get_study_program.assert_not_awaited()
    assert coordinator.data.active_study_program.id is None


async def test_study_program_detail_error_maps_to_update_failed(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """StudyLifeApiError from the new async_get_study_program call follows the exact
    same error-handling pattern as every other required fetch inside this same try
    block - see test_api_error_maps_to_update_failed above."""
    mock_api_client.async_get_settings.return_value = {"activeStudyProgramId": 7}
    mock_api_client.async_get_study_programs.return_value = [
        make_raw_study_program(id=None, name="Built-in", is_built_in=True),
        make_raw_study_program(id=7, name="Custom", is_built_in=False),
    ]
    mock_api_client.async_get_study_program.side_effect = StudyLifeApiError("server unreachable")

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert "server unreachable" in str(coordinator.last_exception)
