"""Tests for StudyLifeCoordinator (custom_components/studylife/coordinator.py)."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, call

import pytest
from freezegun import freeze_time
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.studylife.api import (
    StudyLifeApiAuthError,
    StudyLifeApiEndpointMissingError,
    StudyLifeApiError,
)
from custom_components.studylife.const import EVENT_WEEKLY_REPORT
from custom_components.studylife.coordinator import StudyLifeCoordinator, StudyLifeData

from .conftest import (
    make_course,
    make_raw_metrics_summary,
    make_raw_session,
    make_raw_study_program,
)


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
    assert coordinator.data.timer_state is not None
    assert coordinator.data.timer_state.session_id == 1
    assert coordinator.data.timer_state.is_running is True


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


async def test_metrics_endpoint_missing_maps_to_update_failed_with_clear_message(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """A 404 from GET /api/metrics/summary (a too-old StudyLife server - see
    api.py's StudyLifeApiEndpointMissingError) must fail the refresh LOUDLY with a
    specific, actionable message, not crash cryptically or silently degrade - the
    exact deploy-order failure mode docs/api's metrics contract calls out."""
    mock_api_client.async_get_metrics_summary.side_effect = StudyLifeApiEndpointMissingError(
        "GET http://x/api/metrics/summary returned 404 - the StudyLife server does not "
        "expose this endpoint yet. Update the StudyLife server, then reload this integration."
    )

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert "does not expose this endpoint yet" in str(coordinator.last_exception)
    assert "Update the StudyLife server" in str(coordinator.last_exception)


async def test_metrics_achievements_endpoint_missing_also_maps_to_update_failed(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    mock_api_client.async_get_metrics_achievements.side_effect = StudyLifeApiEndpointMissingError(
        "GET http://x/api/metrics/achievements returned 404 - update the StudyLife server."
    )

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert isinstance(coordinator.last_exception, UpdateFailed)


async def test_no_study_programs_at_all_fails_loudly_instead_of_crashing(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """Should not happen against a real server (GET /api/studyprograms always returns at
    least the synthetic built-in entry), but a malformed/empty response must still
    degrade to a comprehensible UpdateFailed, not an unhandled KeyError."""
    mock_api_client.async_get_study_programs.return_value = []

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert "no study programmes" in str(coordinator.last_exception)


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

    # Nothing about the (static) mocked response changed, so the second refresh
    # computes the exact same week id as the first.
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert captured == []


async def test_weekly_report_event_fired_when_week_changes(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """A differing `weeklyReport.weekId` between two refreshes' GET /api/metrics/summary
    responses fires EVENT_WEEKLY_REPORT with the new report's data as the payload. Unlike
    the old version of this test (which monkeypatched the now-deleted `_calc_weekly_report`
    helper directly), the coordinator no longer computes this itself - it only PARSES
    whatever weeklyReport the mocked endpoint returns, so driving two different mocked
    responses across two refreshes is the natural way to exercise this."""
    captured = await _capture_weekly_report_events(hass)

    summary_week_1 = make_raw_metrics_summary(
        weekly_report={
            "weekId": "2026-W10", "hours": 5.0, "deltaVsPreviousWeek": 1.0,
            "topCourseName": "Algorithms", "sessionCount": 3,
        }
    )
    summary_week_2 = make_raw_metrics_summary(
        weekly_report={
            "weekId": "2026-W11", "hours": 7.5, "deltaVsPreviousWeek": 2.5,
            "topCourseName": "Databases", "sessionCount": 4,
        }
    )
    mock_api_client.async_get_metrics_summary.side_effect = [summary_week_1, summary_week_2]

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert captured == []  # first refresh: still no previous id to compare against

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(captured) == 1
    assert captured[0].data["week_id"] == "2026-W11"
    assert captured[0].data["hours"] == 7.5
    assert captured[0].data["top_course"] == "Databases"
    assert coordinator._last_report_week_id == "2026-W11"
    assert coordinator.data.weekly_report.week_id == "2026-W11"


# ---------------------------------------------------------------------------
# Per-programme fan-out: GET /api/courses AND GET /api/metrics/summary, once each
# per study programme (0 = built-in, per both endpoints' shared convention).
# ---------------------------------------------------------------------------


async def test_multiple_study_programs_fetch_courses_and_summary_per_program(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    mock_api_client.async_get_study_programs.return_value = [
        {"id": None, "name": "Built-in", "isBuiltIn": True, "isCompleted": False},
        {"id": 5, "name": "Custom Program", "isBuiltIn": False, "isCompleted": False},
    ]

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert mock_api_client.async_get_courses.call_count == 2
    assert mock_api_client.async_get_courses.call_args_list == [call(0), call(5)]
    assert mock_api_client.async_get_metrics_summary.call_count == 2
    assert mock_api_client.async_get_metrics_summary.call_args_list == [call(0), call(5)]
    assert set(coordinator.data.programs.keys()) == {"builtin", "5"}


async def test_achievements_fetched_once_for_the_active_program_id(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """GET /api/metrics/achievements is scoped to ONE programme (unlike the summary,
    which is fetched per programme) - the ACTIVE one, resolved the same way
    active_study_program itself is."""
    mock_api_client.async_get_settings.return_value = {"activeStudyProgramId": 7}
    mock_api_client.async_get_study_programs.return_value = [
        make_raw_study_program(id=None, name="Built-in", is_built_in=True),
        make_raw_study_program(id=7, name="Custom", is_built_in=False),
    ]

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    mock_api_client.async_get_metrics_achievements.assert_awaited_once_with(7)


async def test_achievements_fetched_for_builtin_when_no_active_id_set(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    mock_api_client.async_get_metrics_achievements.assert_awaited_once_with(0)


async def test_stale_active_program_id_falls_back_to_builtin_for_achievements(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """activeStudyProgramId pointing at a programme no longer present in
    async_get_study_programs (e.g. just deleted from another client) must fall back to
    the built-in programme - same defensive behaviour active_study_program itself always
    had - and the achievements fetch must follow that SAME fallback (id 0), not the
    stale id (which would otherwise 404)."""
    mock_api_client.async_get_settings.return_value = {"activeStudyProgramId": 999}
    mock_api_client.async_get_study_programs.return_value = [
        make_raw_study_program(id=None, name="Built-in", is_built_in=True),
    ]

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.active_study_program.id is None
    mock_api_client.async_get_metrics_achievements.assert_awaited_once_with(0)


# ---------------------------------------------------------------------------
# Parsing/mapping at the coordinator (integration) level: values come straight from the
# mocked GET /api/metrics/summary response, not recomputed locally - complements the
# pure-function unit tests in test_coordinator_calc.py.
# ---------------------------------------------------------------------------


async def test_top_level_metrics_come_from_active_programs_summary_response(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    mock_api_client.async_get_metrics_summary.return_value = make_raw_metrics_summary(
        week_hours=4.0,
        streak_current=5,
        streak_longest=12,
        ects_earned=30,
        ects_total=180,
        average_grade=2.23,
        weekly_report={
            "weekId": "2026-W28", "hours": 3.0, "deltaVsPreviousWeek": 2.0,
            "topCourseName": "A", "sessionCount": 2,
        },
    )

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.week_hours == 4.0
    assert coordinator.data.streak_days == 5
    assert coordinator.data.longest_streak_days == 12
    assert coordinator.data.ects_earned == 30
    assert coordinator.data.ects_total == 180
    assert coordinator.data.average_grade == 2.23
    # previous_week_hours/week_delta_hours are DERIVED from weeklyReport.hours (the last
    # COMPLETED Mon-Sun week, by construction the same week boundary), not a second,
    # independently-fetched/computed number - see coordinator.py's comment on this.
    assert coordinator.data.previous_week_hours == 3.0
    assert coordinator.data.week_delta_hours == 1.0  # 4.0 - 3.0


async def test_program_device_and_hub_device_share_the_same_active_program_numbers(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """The active programme's own device (coordinator.data.programs[key]) and the hub
    device's cross-programme fields are sourced from the exact same HTTP response now
    (not two independently-computed copies), so they must always agree."""
    mock_api_client.async_get_metrics_summary.return_value = make_raw_metrics_summary(
        week_hours=4.0, ects_earned=30, ects_total=180
    )

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    builtin = coordinator.data.programs["builtin"]
    assert builtin.week_hours == coordinator.data.week_hours
    assert builtin.ects_earned == coordinator.data.ects_earned == 30
    assert builtin.ects_total == coordinator.data.ects_total == 180


async def test_neglected_course_course_hours_topics_and_month_comparison_from_summary(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """The fields with NO equivalent on StudyLifeProgramData (course_hours,
    neglected_course, topics_completed/total, month_hours_current/delta) are populated
    from the active programme's summary response too, not left at empty defaults."""
    mock_api_client.async_get_metrics_summary.return_value = make_raw_metrics_summary(
        neglected_course={"courseId": 12, "courseName": "Databases", "lastStudied": "2026-07-01", "daysSince": 56},
        course_hours=[{"courseId": 1, "courseName": "A", "courseColor": "#fff", "hours": 40.0, "sessionCount": 22}],
        topics_completed=34,
        topics_total=60,
        current_month_hours=12.5,
        previous_month_hours=15.5,
        delta_vs_previous_month=-3.0,
    )

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.neglected_course.course_id == 12
    assert coordinator.data.neglected_course.days_since == 56
    assert len(coordinator.data.course_hours) == 1
    assert coordinator.data.course_hours[0].course_name == "A"
    assert coordinator.data.topics_completed == 34
    assert coordinator.data.topics_total == 60
    assert coordinator.data.month_hours_current == 12.5
    assert coordinator.data.month_hours_delta_vs_last_month == -3.0


async def test_topics_by_course_breakdown_still_derived_locally_from_course_goals(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """topics_completed/topics_total are authoritative from the endpoint (see the test
    above), but the topics_progress sensor's per-course `courses` breakdown attribute
    (topics_by_course) has no equivalent field in the metrics contract's wire format -
    it's still built locally from course_goals/courses, which the coordinator fetches
    for other reasons anyway (see coordinator.py's _topics_by_course docstring)."""
    mock_api_client.async_get_courses.return_value = [
        make_course(id=100, name="Algorithms", topics=["A", "B", "C"])
    ]
    mock_api_client.async_get_course_goals.return_value = [
        {"courseId": 100, "courseName": "Algorithms", "grade": None, "targetDate": None,
         "completedAt": None, "completedTopics": "A,B"}
    ]

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.topics_by_course == [
        {"course_id": 100, "course_name": "Algorithms", "topics_completed": 2, "topics_total": 3}
    ]


# ---------------------------------------------------------------------------
# week_sessions: a RAW list/count view over `history`, not a metric - week_hours itself
# comes from the endpoint now, but the list of this-week's session objects is still
# filtered locally (mirrors today_sessions/active_session's "genuinely needs raw data").
# ---------------------------------------------------------------------------

# 2026-01-08 12:00:00 UTC = 2026-01-08 04:00 US/Pacific (the test hass fixture's default
# tz - see pytest_homeassistant_custom_component.common). A Thursday, so "this week" runs
# Monday 2026-01-05 through Sunday 2026-01-11.
FROZEN_NOW = "2026-01-08 12:00:00"


@freeze_time(FROZEN_NOW)
async def test_week_sessions_excludes_future_dated_session_beyond_this_week(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """/api/sessions/history has no upper date bound server-side, so a session scheduled
    almost 4 weeks out (well past this Mon-Sun week) must be EXCLUDED from the
    week_sessions list, not silently inflate it - unlike week_hours (a metric, now purely
    server-sourced), this list is still built locally from `history`."""
    history = [
        make_raw_session(id=1, start="2026-01-06T09:00:00", end="2026-01-06T11:00:00"),  # this week
        make_raw_session(id=2, start="2026-02-02T09:00:00", end="2026-02-02T13:00:00"),  # ~4 weeks out
    ]
    mock_api_client.async_get_session_history.return_value = history

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert len(coordinator.data.week_sessions) == 1
    assert coordinator.data.week_sessions[0].id == 1


@freeze_time(FROZEN_NOW)
async def test_week_hours_reflects_whatever_the_summary_endpoint_says_regardless_of_history(
    hass: HomeAssistant, mock_api_client: AsyncMock
) -> None:
    """Confirms week_hours is no longer locally derived from `history` AT ALL: even
    though `history` here has no sessions in the current week, week_hours must still
    equal the mocked endpoint's number, not 0.0 - there is exactly one source of truth
    for it now (the server), and this test would fail if any local recomputation crept
    back in."""
    mock_api_client.async_get_session_history.return_value = [
        make_raw_session(id=1, start="2025-01-06T09:00:00", end="2025-01-06T11:00:00"),  # a year old
    ]
    mock_api_client.async_get_metrics_summary.return_value = make_raw_metrics_summary(week_hours=99.5)

    coordinator = StudyLifeCoordinator(hass, mock_api_client, timedelta(seconds=30))
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.week_hours == 99.5
    assert coordinator.data.week_sessions == []
