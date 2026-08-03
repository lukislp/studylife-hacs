"""Sensors mirroring the StudyLife dashboard (Client/Pages/Index.razor).

Two kinds:

- Hub sensors (SENSOR_DESCRIPTIONS, one static set on the per-config-entry hub
  device): app-global things - sessions, timer, notes, cross-programme study
  habit (week hours, streak, quotas over ALL sessions) and app state.
- Per-programme sensors (PROGRAM_SENSOR_DESCRIPTIONS, one set PER STUDY
  PROGRAMME on that programme's own device): progress metrics scoped to one
  programme - ECTS, average grade, forecast, quotas, goals, course count.
  Created dynamically: the initial set right after the first refresh, plus a
  coordinator listener that adds entity sets for programmes created later via
  the StudyLife web UI. Deleted programmes are NOT removed from the registry -
  their entities just flip to unavailable (see StudyLifeProgramEntity).
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .coordinator import (
    StudyLifeCoordinator,
    StudyLifeData,
    StudyLifeProgramData,
    StudySession,
)
from .entity import StudyLifeEntity, StudyLifeProgramEntity


def _as_local(value: datetime | None) -> datetime | None:
    return dt_util.as_local(value) if value is not None else None


def _session_attrs(session: StudySession | None) -> dict[str, Any]:
    if session is None:
        return {}
    return {
        "session_id": session.id,
        "course_id": session.course_id,
        "course_color": session.course_color,
        "topic": session.topic,
        "notes": session.notes,
        "start_time": session.start.isoformat(),
        "end_time": session.end.isoformat(),
        "timer_mode_id": session.timer_mode_id,
        "recurrence_group_id": session.recurrence_group_id,
    }


@dataclass(frozen=True, kw_only=True)
class StudyLifeSensorDescription(SensorEntityDescription):
    value_fn: Callable[[StudyLifeData], Any] = lambda data: None
    attrs_fn: Callable[[StudyLifeData], dict[str, Any]] = lambda data: {}


def _today_sessions_attrs(data: StudyLifeData) -> dict[str, Any]:
    return {
        "sessions": [
            {
                "session_id": s.id,
                "course_id": s.course_id,
                "course_name": s.course_name,
                "course_color": s.course_color,
                "topic": s.topic,
                "notes": s.notes,
                "start_time": s.start.isoformat(),
                "end_time": s.end.isoformat(),
                "is_completed": s.is_completed,
                "timer_mode_id": s.timer_mode_id,
                "recurrence_group_id": s.recurrence_group_id,
            }
            for s in data.today_sessions
        ]
    }


def _excerpt(content: str | None, limit: int = 120) -> str | None:
    if not content:
        return content
    return content[:limit].rstrip() + "…" if len(content) > limit else content


def _quota_attrs(quota) -> dict[str, Any]:
    return {
        "hours": quota.hours,
        "target_min_hours": quota.target_min,
        "target_max_hours": quota.target_max,
        "warning": quota.warning,
        "missing_hours": quota.missing_hours,
    }


def _resolve_courses(
    course_ids: list[int] | None,
    catalog: list[dict[str, Any]],
    tag_by_course_id: dict[int, str],
) -> list[dict[str, Any]]:
    """Map raw course IDs (from settings) to their catalog entry, so consumers
    get name/icon/color instead of a bare ID - mirrors the dashboard's course
    pills (Index.razor), now possible since /api/courses exists. Also attaches
    each course's priority tag (CourseGoalDto.Tag), shown as a badge on those
    same pills, e.g. "exam soon"."""
    if not course_ids:
        return []
    by_id = {c["id"]: c for c in catalog}
    return [
        {
            "id": c["id"],
            "name": c["name"],
            "code": c["code"],
            "icon": c["icon"],
            "color": c["color"],
            "tag": tag_by_course_id.get(c["id"]),
        }
        for cid in course_ids
        if (c := by_id.get(cid)) is not None
    ]


def _achievements_attrs(data: StudyLifeData) -> dict[str, Any]:
    """Splits the flat 44-entry achievement list (see coordinator._calc_achievements)
    into `unlocked`/`locked` rather than dumping one undifferentiated list - the
    two groups are what a dashboard actually wants to bind separately (a trophy
    wall for `unlocked`, a "what's next" list for `locked`). `locked` is sorted by
    current/threshold descending, so the FIRST entry is whichever badge is closest
    to being earned - `next_up` surfaces that single entry directly so a card can
    bind to it without a template needing to index into the list itself."""
    unlocked = [a for a in data.achievements if a.unlocked]
    locked = sorted(
        (a for a in data.achievements if not a.unlocked),
        key=lambda a: (a.current / a.threshold) if a.threshold else 0.0,
        reverse=True,
    )
    return {
        "total": len(data.achievements),
        "unlocked": [dataclasses.asdict(a) for a in unlocked],
        "locked": [dataclasses.asdict(a) for a in locked],
        "next_up": dataclasses.asdict(locked[0]) if locked else None,
    }


def _motivational_style_attrs(data: StudyLifeData) -> dict[str, Any]:
    tag_by_course_id = {g["courseId"]: g["tag"] for g in data.course_goals if g.get("tag")}
    return {
        "theme": data.settings.get("theme"),
        "auto_switch_focus": data.settings.get("autoSwitchFocus"),
        "auto_switch_minutes_before": data.settings.get("autoSwitchMinutesBefore"),
        "selected_course_ids": data.settings.get("selectedCourseIds"),
        "completed_course_ids": data.settings.get("completedCourseIds"),
        "selected_courses": _resolve_courses(data.settings.get("selectedCourseIds"), data.courses, tag_by_course_id),
        "completed_courses": _resolve_courses(data.settings.get("completedCourseIds"), data.courses, tag_by_course_id),
        "session_reminder_minutes": data.settings.get("sessionReminderMinutes"),
        "course_goal_reminder_days": data.settings.get("courseGoalReminderDays"),
        "inactivity_threshold_days": data.settings.get("inactivityThresholdDays"),
    }


SENSOR_DESCRIPTIONS: tuple[StudyLifeSensorDescription, ...] = (
    StudyLifeSensorDescription(
        key="active_session",
        translation_key="active_session",
        icon="mdi:book-open-page-variant",
        value_fn=lambda data: data.active_session.course_name if data.active_session else "none",
        attrs_fn=lambda data: _session_attrs(data.active_session),
    ),
    StudyLifeSensorDescription(
        key="next_session",
        translation_key="next_session",
        icon="mdi:clock-outline",
        value_fn=lambda data: data.upcoming_session.course_name if data.upcoming_session else "none",
        attrs_fn=lambda data: _session_attrs(data.upcoming_session),
    ),
    StudyLifeSensorDescription(
        key="next_session_start",
        translation_key="next_session_start",
        icon="mdi:calendar-arrow-right",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _as_local(data.upcoming_session.start if data.upcoming_session else None),
    ),
    StudyLifeSensorDescription(
        key="next_session_end",
        translation_key="next_session_end",
        icon="mdi:calendar-arrow-right",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _as_local(data.upcoming_session.end if data.upcoming_session else None),
    ),
    StudyLifeSensorDescription(
        key="active_session_end",
        translation_key="active_session_end",
        icon="mdi:calendar-check",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _as_local(data.active_session.end if data.active_session else None),
    ),
    StudyLifeSensorDescription(
        key="today_sessions",
        translation_key="today_sessions",
        icon="mdi:calendar-today",
        native_unit_of_measurement="sessions",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: len(data.today_sessions),
        attrs_fn=_today_sessions_attrs,
    ),
    StudyLifeSensorDescription(
        key="week_sessions",
        translation_key="week_sessions",
        icon="mdi:calendar-week",
        native_unit_of_measurement="sessions",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: len(data.week_sessions),
    ),
    StudyLifeSensorDescription(
        key="week_hours",
        translation_key="week_hours",
        icon="mdi:clock-time-eight-outline",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: data.week_hours,
        attrs_fn=lambda data: {
            "previous_week_hours": data.previous_week_hours,
            "delta_hours": data.week_delta_hours,
            "up": data.week_delta_hours >= 0,
        },
    ),
    StudyLifeSensorDescription(
        key="streak",
        translation_key="streak",
        icon="mdi:fire",
        native_unit_of_measurement="d",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.streak_days,
    ),
    StudyLifeSensorDescription(
        key="week_quota",
        translation_key="week_quota",
        icon="mdi:target",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.week_quota.percent,
        attrs_fn=lambda data: _quota_attrs(data.week_quota),
    ),
    StudyLifeSensorDescription(
        key="month_quota",
        translation_key="month_quota",
        icon="mdi:target",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.month_quota.percent,
        attrs_fn=lambda data: _quota_attrs(data.month_quota),
    ),
    # average_grade, ects_progress, next_course_goal(_date), courses and
    # study_forecast used to live here too - they're programme-scoped progress
    # metrics and moved to the per-programme devices (PROGRAM_SENSOR_DESCRIPTIONS
    # below), where every programme gets its own copy, active or not.
    StudyLifeSensorDescription(
        key="course_hours",
        translation_key="course_hours",
        icon="mdi:chart-bar",
        native_unit_of_measurement="courses",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: len(data.course_hours),
        attrs_fn=lambda data: {
            "total_hours": data.total_hours,
            "total_sessions": data.total_sessions,
            "courses": [
                {
                    "course_id": c.course_id,
                    "course_name": c.course_name,
                    "course_color": c.course_color,
                    "hours": c.hours,
                    "sessions": c.sessions,
                }
                for c in data.course_hours
            ],
        },
    ),
    StudyLifeSensorDescription(
        key="motivational_style",
        translation_key="motivational_style",
        icon="mdi:emoticon-outline",
        value_fn=lambda data: data.settings.get("motivationalStyle"),
        attrs_fn=_motivational_style_attrs,
    ),
    StudyLifeSensorDescription(
        key="timer_phase",
        translation_key="timer_phase",
        icon="mdi:timer-outline",
        value_fn=lambda data: data.timer_state.phase,
        attrs_fn=lambda data: {
            "session_id": data.timer_state.session_id,
            "timer_mode_id": data.timer_state.timer_mode_id,
            "current_round": data.timer_state.current_round,
        },
    ),
    StudyLifeSensorDescription(
        key="timer_phase_ends",
        translation_key="timer_phase_ends",
        icon="mdi:timer-sand",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _as_local(data.timer_state.phase_ends_at),
    ),
    StudyLifeSensorDescription(
        key="notes_count",
        translation_key="notes_count",
        icon="mdi:notebook-outline",
        native_unit_of_measurement="notes",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.notes_count,
        attrs_fn=lambda data: (
            {
                "latest_title": data.latest_note.get("title"),
                "latest_updated_at": data.latest_note.get("updatedAt"),
                "latest_course_id": data.latest_note.get("courseId"),
                "latest_excerpt": _excerpt(data.latest_note.get("content")),
                "latest_session_id": data.latest_note.get("sessionId"),
            }
            if data.latest_note
            else {}
        ),
    ),
    StudyLifeSensorDescription(
        key="neglected_course",
        translation_key="neglected_course",
        icon="mdi:scale-balance",
        value_fn=lambda data: data.neglected_course.course_name if data.neglected_course else "none",
        attrs_fn=lambda data: (
            {
                "course_id": data.neglected_course.course_id,
                "last_studied": data.neglected_course.last_studied.isoformat() if data.neglected_course.last_studied else None,
                "days_since": data.neglected_course.days_since,
            }
            if data.neglected_course
            else {}
        ),
    ),
    StudyLifeSensorDescription(
        key="achievements_unlocked",
        translation_key="achievements_unlocked",
        icon="mdi:trophy-outline",
        native_unit_of_measurement="badges",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.achievements_unlocked,
        attrs_fn=_achievements_attrs,
    ),
    StudyLifeSensorDescription(
        key="longest_streak",
        translation_key="longest_streak",
        icon="mdi:fire",
        native_unit_of_measurement="d",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.longest_streak_days,
    ),
    StudyLifeSensorDescription(
        key="topics_progress",
        translation_key="topics_progress",
        icon="mdi:checkbox-multiple-marked-outline",
        native_unit_of_measurement="topics",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.topics_completed,
        attrs_fn=lambda data: {
            "topics_total": data.topics_total,
            "percent": round(data.topics_completed / data.topics_total * 100, 1) if data.topics_total else 0,
            "courses": data.topics_by_course,
        },
    ),
    StudyLifeSensorDescription(
        key="month_comparison",
        translation_key="month_comparison",
        icon="mdi:calendar-sync-outline",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: round(data.month_hours_current, 2),
        attrs_fn=lambda data: {
            "delta_vs_last_month_hours": round(data.month_hours_delta_vs_last_month, 2),
            **(
                {"delta_vs_last_year_hours": round(data.month_hours_delta_vs_last_year, 2)}
                if data.month_hours_delta_vs_last_year is not None
                else {}
            ),
        },
    ),
    StudyLifeSensorDescription(
        # Which study programme (Studiengang) the StudyLife APP itself currently
        # treats as active/default. Every programme has its own HA device with its
        # own progress sensors regardless of this - only the hub's active-catalog
        # bits (course picker, motivational_style's resolved courses, the goals
        # calendar) follow it. binary_sensor <programme>_active mirrors it per device.
        key="active_program",
        translation_key="active_program",
        icon="mdi:school",
        value_fn=lambda data: data.active_study_program.name,
        attrs_fn=lambda data: {
            "program_id": data.active_study_program.id,
            "is_built_in": data.active_study_program.is_built_in,
            "programs_count": len(data.study_programs),
            "completed_programs_count": sum(1 for p in data.study_programs if p.is_completed),
            "programs": [dataclasses.asdict(p) for p in data.study_programs],
        },
    ),
    StudyLifeSensorDescription(
        # State = ISO week id of the last COMPLETED Mon-Sun week (e.g. "2026-W28"),
        # flips on Monday at the first coordinator refresh - automations trigger on
        # the state change, or on the studylife_weekly_report bus event the
        # coordinator fires alongside it (see _calc_weekly_report in coordinator.py).
        key="weekly_report",
        translation_key="weekly_report",
        icon="mdi:calendar-week-begin",
        value_fn=lambda data: data.weekly_report.week_id,
        attrs_fn=lambda data: {
            "hours": data.weekly_report.hours,
            "delta_vs_previous_week_hours": data.weekly_report.delta_vs_previous_week_hours,
            "top_course": data.weekly_report.top_course,
            "sessions_count": data.weekly_report.sessions_count,
        },
    ),
)


@dataclass(frozen=True, kw_only=True)
class StudyLifeProgramSensorDescription(SensorEntityDescription):
    """Like StudyLifeSensorDescription, but value/attrs derive from ONE
    programme's StudyLifeProgramData instead of the global StudyLifeData."""
    value_fn: Callable[[StudyLifeProgramData], Any] = lambda program: None
    attrs_fn: Callable[[StudyLifeProgramData], dict[str, Any]] = lambda program: {}


PROGRAM_SENSOR_DESCRIPTIONS: tuple[StudyLifeProgramSensorDescription, ...] = (
    StudyLifeProgramSensorDescription(
        key="ects_progress",
        translation_key="ects_progress",
        icon="mdi:progress-check",
        native_unit_of_measurement="ECTS",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda program: program.ects_earned,
        attrs_fn=lambda program: {
            "ects_total": program.ects_total,
            "percent": round(program.ects_earned / program.ects_total * 100, 1) if program.ects_total else 0,
        },
    ),
    StudyLifeProgramSensorDescription(
        key="average_grade",
        translation_key="average_grade",
        icon="mdi:school-outline",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda program: program.average_grade,
        attrs_fn=lambda program: {
            "weighted_by_ects": True,
            "graded_courses": [
                {"course_id": g["courseId"], "course_name": g["courseName"], "grade": g["grade"]}
                for g in program.course_goals
                if g.get("grade") is not None
            ],
        },
    ),
    StudyLifeProgramSensorDescription(
        key="streak",
        translation_key="streak",
        icon="mdi:fire",
        native_unit_of_measurement="d",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda program: program.streak_days,
    ),
    StudyLifeProgramSensorDescription(
        key="longest_streak",
        translation_key="longest_streak",
        icon="mdi:fire",
        native_unit_of_measurement="d",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda program: program.longest_streak_days,
    ),
    StudyLifeProgramSensorDescription(
        # Hours studied for THIS programme against the user's GLOBAL weekly goal
        # (there are no per-programme targets in the app) - the hub's week_quota
        # measures the same goal against ALL programmes' sessions combined.
        key="week_quota",
        translation_key="week_quota",
        icon="mdi:target",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda program: program.week_quota.percent,
        attrs_fn=lambda program: _quota_attrs(program.week_quota),
    ),
    StudyLifeProgramSensorDescription(
        key="month_quota",
        translation_key="month_quota",
        icon="mdi:target",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda program: program.month_quota.percent,
        attrs_fn=lambda program: _quota_attrs(program.month_quota),
    ),
    StudyLifeProgramSensorDescription(
        key="study_forecast",
        translation_key="study_forecast",
        icon="mdi:calendar-check-outline",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda program: program.forecast_date,
        attrs_fn=lambda program: (
            {"recent_weekly_hours": program.forecast_recent_weekly_hours}
            if program.forecast_date
            else {}
        ),
    ),
    StudyLifeProgramSensorDescription(
        key="next_course_goal",
        translation_key="next_course_goal",
        icon="mdi:flag-checkered",
        value_fn=lambda program: program.next_course_goal.course_name if program.next_course_goal else "none",
        attrs_fn=lambda program: {
            **(
                {
                    "course_id": program.next_course_goal.course_id,
                    "target_date": program.next_course_goal.target_date.isoformat(),
                    "days_remaining": program.next_course_goal.days_remaining,
                    "overdue": program.next_course_goal.days_remaining < 0,
                }
                if program.next_course_goal
                else {}
            ),
            "upcoming_goals": [
                {
                    "course_id": g.course_id,
                    "course_name": g.course_name,
                    "target_date": g.target_date.isoformat(),
                    "days_remaining": g.days_remaining,
                    "overdue": g.days_remaining < 0,
                }
                for g in program.upcoming_course_goals
            ],
        },
    ),
    StudyLifeProgramSensorDescription(
        key="next_course_goal_date",
        translation_key="next_course_goal_date",
        icon="mdi:calendar-alert",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda program: program.next_course_goal.target_date if program.next_course_goal else None,
    ),
    StudyLifeProgramSensorDescription(
        key="courses",
        translation_key="courses",
        icon="mdi:book-multiple-outline",
        native_unit_of_measurement="courses",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda program: len(program.courses),
        attrs_fn=lambda program: {
            "courses": program.courses,
            "total_hours": program.total_hours,
            "total_sessions": program.total_sessions,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: StudyLifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        StudyLifeSensor(coordinator, entry, description) for description in SENSOR_DESCRIPTIONS
    )

    # One full per-programme sensor set per study programme. The first refresh has
    # already happened (async_config_entry_first_refresh in __init__.py), so the
    # initial sync-call below sees the complete programme list; the coordinator
    # listener then picks up programmes created later via the StudyLife web UI and
    # adds their entity sets on the fly. Programmes that disappear are NOT removed
    # here - their entities go unavailable via StudyLifeProgramEntity.available.
    known_program_ids: set[str] = set()

    def _sync_program_entities() -> None:
        new_ids = [pid for pid in coordinator.data.programs if pid not in known_program_ids]
        if not new_ids:
            return
        entities: list[StudyLifeProgramSensor] = []
        for pid in new_ids:
            known_program_ids.add(pid)
            program_name = coordinator.data.programs[pid].program.name
            entities.extend(
                StudyLifeProgramSensor(coordinator, entry, description, pid, program_name)
                for description in PROGRAM_SENSOR_DESCRIPTIONS
            )
        async_add_entities(entities)

    _sync_program_entities()
    entry.async_on_unload(coordinator.async_add_listener(_sync_program_entities))


class StudyLifeSensor(StudyLifeEntity, SensorEntity):
    """A single StudyLife metric, derived from the polled dashboard data."""

    entity_description: StudyLifeSensorDescription

    def __init__(
        self,
        coordinator: StudyLifeCoordinator,
        entry: ConfigEntry,
        description: StudyLifeSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attrs_fn(self.data)


class StudyLifeProgramSensor(StudyLifeProgramEntity, SensorEntity):
    """One metric of one specific study programme, on that programme's device.
    Reports unavailable (and None/{} defensively, should HA still ask) once the
    programme has been deleted from StudyLife."""

    entity_description: StudyLifeProgramSensorDescription

    def __init__(
        self,
        coordinator: StudyLifeCoordinator,
        entry: ConfigEntry,
        description: StudyLifeProgramSensorDescription,
        program_id: str,
        program_name: str,
    ) -> None:
        super().__init__(coordinator, entry, description.key, program_id, program_name)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        program = self.program_data
        return self.entity_description.value_fn(program) if program is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        program = self.program_data
        return self.entity_description.attrs_fn(program) if program is not None else {}
