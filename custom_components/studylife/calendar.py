"""Calendar entities exposing StudyLife sessions and course-goal deadlines."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import homeassistant.util.dt as dt_util
from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import StudyLifeCoordinator, StudySession
from .entity import StudyLifeEntity


def _to_event(session: StudySession) -> CalendarEvent:
    return CalendarEvent(
        start=dt_util.as_local(session.start),
        end=dt_util.as_local(session.end),
        summary=session.course_name,
        description=session.topic or session.notes or "",
        uid=str(session.id),
    )


def _goal_to_event(goal: dict[str, Any]) -> CalendarEvent:
    """Open course goal (raw CourseGoalDto dict) -> all-day event on its target
    date. Per HA's all-day convention start/end are plain `date` objects and the
    end date is EXCLUSIVE, i.e. target day + 1 for a single-day event."""
    target = datetime.fromisoformat(goal["targetDate"]).date()
    return CalendarEvent(
        start=target,
        end=target + timedelta(days=1),
        summary=goal["courseName"],
        description=goal.get("tag") or "",
        uid=f"goal-{goal['courseId']}",
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: StudyLifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            StudyLifeCalendar(coordinator, entry),
            StudyLifeGoalsCalendar(coordinator, entry),
        ]
    )


class StudyLifeCalendar(StudyLifeEntity, CalendarEntity):
    """All planned/completed study sessions, as returned by /api/sessions."""

    _attr_translation_key = "sessions"

    def __init__(self, coordinator: StudyLifeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "sessions_calendar")

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        upcoming = sorted(self.data.sessions, key=lambda s: s.start)
        for session in upcoming:
            event = _to_event(session)
            if event.end >= now:
                return event
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [
            _to_event(session)
            for session in sorted(self.data.sessions, key=lambda s: s.start)
            if dt_util.as_local(session.end) >= start_date and dt_util.as_local(session.start) <= end_date
        ]


class StudyLifeGoalsCalendar(StudyLifeEntity, CalendarEntity):
    """Open course-goal deadlines as all-day events - every /api/coursegoals
    entry with a TargetDate that isn't completed yet, i.e. the same open-goal
    filter as the coordinator's `_calc_upcoming_course_goals`, but without the
    dashboard card's 5-goal cap."""

    _attr_translation_key = "goals"

    def __init__(self, coordinator: StudyLifeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "goals_calendar")

    def _goal_events(self) -> list[CalendarEvent]:
        return sorted(
            (
                _goal_to_event(goal)
                for goal in self.data.course_goals
                if goal.get("completedAt") is None and goal.get("targetDate")
            ),
            key=lambda event: event.start,
        )

    @property
    def event(self) -> CalendarEvent | None:
        today = dt_util.now().date()
        for event in self._goal_events():
            # Exclusive all-day end: the goal still counts on the target day itself.
            if event.end > today:
                return event
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        window_start = dt_util.as_local(start_date).date()
        window_end = dt_util.as_local(end_date).date()
        return [
            event
            for event in self._goal_events()
            # `event.end` is exclusive, so "> window_start" (not ">=") keeps a
            # goal whose target day is the window's first day.
            if event.end > window_start and event.start <= window_end
        ]
