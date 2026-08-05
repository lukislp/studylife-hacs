"""Tests for the StudyLife calendar entities (calendar.py).

Both entities are pure read-through views over `coordinator.data` (sessions /
course_goals) - no API calls of their own - so they're built directly against
a fake coordinator (a MagicMock whose `.data` stands in for `StudyLifeData`)
rather than a full config-entry setup. That keeps the date/boundary
assertions as direct as possible.

Session start/end are naive "local wall-clock" datetimes (see coordinator.py's
module docstring), so test data is built relative to `dt_util.now()` (itself
frozen via freezegun where "now" matters) rather than hard-coded clock times,
to stay correct regardless of the test machine's/HA test fixture's configured
timezone.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import homeassistant.util.dt as dt_util
from freezegun import freeze_time
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife.calendar import (
    StudyLifeCalendar,
    StudyLifeGoalsCalendar,
    _goal_to_event,
)

from .conftest import make_course_goal, make_session


def _build_sessions_calendar(
    hass: HomeAssistant, entry: MockConfigEntry, sessions: list[Any]
) -> StudyLifeCalendar:
    coordinator = MagicMock()
    coordinator.data = SimpleNamespace(sessions=sessions, course_goals=[])
    entity = StudyLifeCalendar(coordinator, entry)
    entity.hass = hass
    return entity


def _build_goals_calendar(
    hass: HomeAssistant, entry: MockConfigEntry, course_goals: list[dict[str, Any]]
) -> StudyLifeGoalsCalendar:
    coordinator = MagicMock()
    coordinator.data = SimpleNamespace(sessions=[], course_goals=course_goals)
    entity = StudyLifeGoalsCalendar(coordinator, entry)
    entity.hass = hass
    return entity


def _as_local_dt(d: date) -> datetime:
    """A tz-aware local midnight datetime for `d`, suitable as an
    async_get_events start_date/end_date argument."""
    return dt_util.as_local(datetime.combine(d, datetime.min.time()))


def _naive(*args: int) -> datetime:
    """Build a naive local-time datetime for session/window test data.

    Passes an explicit tzinfo to the constructor (satisfying ruff's DTZ001,
    which requires one) and then strips it again immediately - the resulting
    value is genuinely naive, matching StudySession.start/end's real "naive
    local wall-clock" contract (see coordinator.py's module docstring); this
    isn't `dt_util.now() +/- timedelta`-relative like the `.event` tests
    because these particular assertions are about fixed calendar-day window
    boundaries, not "now"."""
    return datetime(*args, tzinfo=timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# StudyLifeCalendar.event
# ---------------------------------------------------------------------------


async def test_sessions_event_returns_in_progress_not_past_session(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """An in-progress session (start in the past, end in the future) is the
    current event, even though a wholly-past session also exists."""
    with freeze_time("2026-08-05 12:00:00"):
        now = dt_util.now().replace(tzinfo=None)
        in_progress = make_session(
            id=1, course_name="In Progress",
            start=now - timedelta(minutes=30), end=now + timedelta(minutes=30),
        )
        past = make_session(
            id=2, course_name="Long Past",
            start=now - timedelta(hours=3), end=now - timedelta(hours=2),
        )
        entity = _build_sessions_calendar(hass, mock_config_entry, [past, in_progress])

        event = entity.event

        assert event is not None
        assert event.summary == "In Progress"


async def test_sessions_event_is_none_when_nothing_ends_in_future(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    with freeze_time("2026-08-05 12:00:00"):
        now = dt_util.now().replace(tzinfo=None)
        past = make_session(
            id=1, start=now - timedelta(hours=3), end=now - timedelta(hours=2),
        )
        entity = _build_sessions_calendar(hass, mock_config_entry, [past])

        assert entity.event is None


# ---------------------------------------------------------------------------
# StudyLifeCalendar.async_get_events
# ---------------------------------------------------------------------------


async def test_sessions_async_get_events_window_filtering(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Exercises the exact overlap condition:
    `as_local(session.end) >= start_date and as_local(session.start) <= end_date`.
    """
    window_start = _as_local_dt(date(2026, 1, 10))
    window_end = dt_util.as_local(_naive(2026, 1, 10, 23, 59, 59))

    fully_before = make_session(
        id=1, course_name="Fully Before",
        start=_naive(2026, 1, 9, 10, 0), end=_naive(2026, 1, 9, 11, 0),
    )
    fully_after = make_session(
        id=2, course_name="Fully After",
        start=_naive(2026, 1, 11, 10, 0), end=_naive(2026, 1, 11, 11, 0),
    )
    fully_inside = make_session(
        id=3, course_name="Fully Inside",
        start=_naive(2026, 1, 10, 9, 0), end=_naive(2026, 1, 10, 10, 0),
    )
    # Ends exactly at window_start (boundary: end >= start_date is inclusive).
    touches_start_boundary = make_session(
        id=4, course_name="Touches Start Boundary",
        start=_naive(2026, 1, 9, 23, 0), end=_naive(2026, 1, 10, 0, 0),
    )
    # Starts exactly at window_end (boundary: start <= end_date is inclusive).
    touches_end_boundary = make_session(
        id=5, course_name="Touches End Boundary",
        start=_naive(2026, 1, 10, 23, 59, 59), end=_naive(2026, 1, 11, 1, 0),
    )

    entity = _build_sessions_calendar(
        hass, mock_config_entry,
        [fully_before, fully_after, fully_inside, touches_start_boundary, touches_end_boundary],
    )

    events = await entity.async_get_events(hass, window_start, window_end)
    summaries = {e.summary for e in events}

    assert summaries == {"Fully Inside", "Touches Start Boundary", "Touches End Boundary"}


# ---------------------------------------------------------------------------
# StudyLifeGoalsCalendar - event construction / open-goal filtering
# ---------------------------------------------------------------------------


def test_goal_to_event_end_is_exclusive_target_date_plus_one() -> None:
    goal = make_course_goal(target_date="2026-01-15", course_name="Algorithms")

    event = _goal_to_event(goal)

    assert event.start == date(2026, 1, 15)
    assert event.end == date(2026, 1, 16)


async def test_goals_calendar_excludes_completed_and_dateless_goals(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    completed_goal = make_course_goal(
        course_id=1, course_name="Completed", target_date="2026-01-15",
        completed_at="2026-01-14T00:00:00",
    )
    dateless_goal = make_course_goal(course_id=2, course_name="No Deadline", target_date=None)
    open_goal = make_course_goal(course_id=3, course_name="Open Goal", target_date="2026-01-20")

    entity = _build_goals_calendar(
        hass, mock_config_entry, [completed_goal, dateless_goal, open_goal]
    )

    # Wide window comfortably covering everything above.
    events = await entity.async_get_events(
        hass, _as_local_dt(date(2026, 1, 1)), _as_local_dt(date(2026, 2, 1))
    )
    summaries = {e.summary for e in events}

    assert summaries == {"Open Goal"}


# ---------------------------------------------------------------------------
# StudyLifeGoalsCalendar.event
# ---------------------------------------------------------------------------


async def test_goals_event_due_exactly_today_is_still_current(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A goal whose target date is today has an exclusive end of tomorrow, so
    `event.end > today` still holds - it hasn't lapsed yet."""
    with freeze_time("2026-01-15 09:00:00"):
        today_goal = make_course_goal(course_id=1, course_name="Due Today", target_date="2026-01-15")
        entity = _build_goals_calendar(hass, mock_config_entry, [today_goal])

        event = entity.event

        assert event is not None
        assert event.summary == "Due Today"


async def test_goals_event_due_yesterday_is_not_current(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A goal due yesterday has an exclusive end of today, so `event.end > today`
    is False - it should NOT be the current event."""
    with freeze_time("2026-01-15 09:00:00"):
        yesterday_goal = make_course_goal(
            course_id=1, course_name="Due Yesterday", target_date="2026-01-14"
        )
        entity = _build_goals_calendar(hass, mock_config_entry, [yesterday_goal])

        assert entity.event is None


# ---------------------------------------------------------------------------
# StudyLifeGoalsCalendar.async_get_events - exact boundary logic
# ---------------------------------------------------------------------------


async def test_goals_async_get_events_window_starting_on_target_date_includes_goal(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Goal's exclusive end (1/11) > window_start (1/10) - included."""
    goal = make_course_goal(course_id=1, course_name="Goal", target_date="2026-01-10")
    entity = _build_goals_calendar(hass, mock_config_entry, [goal])

    events = await entity.async_get_events(
        hass, _as_local_dt(date(2026, 1, 10)), _as_local_dt(date(2026, 1, 10))
    )

    assert [e.summary for e in events] == ["Goal"]


async def test_goals_async_get_events_window_starting_day_after_target_excludes_goal(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Goal's exclusive end (1/11) > window_start (1/11) is False - excluded,
    exercising the exclusive-end boundary exactly."""
    goal = make_course_goal(course_id=1, course_name="Goal", target_date="2026-01-10")
    entity = _build_goals_calendar(hass, mock_config_entry, [goal])

    events = await entity.async_get_events(
        hass, _as_local_dt(date(2026, 1, 11)), _as_local_dt(date(2026, 1, 15))
    )

    assert events == []


async def test_goals_async_get_events_window_ending_before_target_excludes_goal(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Goal's start (1/10) <= window_end (1/9) is False - excluded."""
    goal = make_course_goal(course_id=1, course_name="Goal", target_date="2026-01-10")
    entity = _build_goals_calendar(hass, mock_config_entry, [goal])

    events = await entity.async_get_events(
        hass, _as_local_dt(date(2026, 1, 5)), _as_local_dt(date(2026, 1, 9))
    )

    assert events == []
