"""Data update coordinator for StudyLife.

Fetches /api/sessions, /api/settings and /api/notes, then re-derives the same
metrics the Blazor dashboard computes client-side (Client/Pages/Index.razor):
active/upcoming session, today's sessions, week/month hours, streak and study
quota. That logic isn't exposed by the API itself, so it's duplicated here -
see docs/ARCHITECTURE.md for why, and keep both in sync if Index.razor changes.

Session StartTime/EndTime are naive local timestamps (no timezone info), just
like the server treats them (DateTime.Now, never UtcNow, for these fields).
We compare against this Home Assistant instance's local wall-clock time,
which assumes StudyLife and Home Assistant run in the same timezone.

Multi-programme note: every study programme (the built-in one plus any custom
ones, completed or not) gets its own per-programme stats, ALL of them every
poll cycle, regardless of which one is active in the app - see the `programs`
dict on StudyLifeData (one StudyLifeProgramData per programme, keyed by
`program_key()`). This works because course ids are globally unique across
programmes (StudyProgramCatalog.CustomCourseIdOffset server-side): sessions
(/api/sessions[/history]) and course goals (/api/coursegoals) are fetched ONCE
globally and partitioned client-side by each programme's course-id set, while
each programme's course catalog comes from GET /api/courses?program={id}
(0 = built-in, per CoursesController's convention). Only the per-programme
catalog is an extra request per programme; everything else is shared, and the
catalog responses are ETag-cached per URL anyway. The `courses`/`course_goals`
fields on StudyLifeData stay scoped to the ACTIVE programme (they feed the
hub-device entities, calendars and services), and `study_programs`/
`active_study_program` expose the programme list itself.
"""
from __future__ import annotations

import calendar
import dataclasses
import logging
import math
import re
from datetime import date, datetime, timedelta
from typing import Any

import homeassistant.util.dt as dt_util
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import StudyLifeApiAuthError, StudyLifeApiClient, StudyLifeApiError
from .const import (
    DOMAIN,
    EVENT_WEEKLY_REPORT,
    MONTH_QUOTA_MAX_HOURS,
    MONTH_QUOTA_MIN_HOURS,
    NEGLECT_HISTORY_DAYS,
    SESSION_HISTORY_DAYS,
    WEEK_QUOTA_MAX_HOURS,
    WEEK_QUOTA_MIN_HOURS,
)

_LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass
class StudySession:
    id: int
    course_id: int
    course_name: str
    course_color: str
    start: datetime
    end: datetime
    topic: str | None
    notes: str | None
    is_completed: bool
    timer_mode_id: int
    recurrence_group_id: str | None

    @property
    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60


@dataclasses.dataclass
class Achievement:
    icon: str
    name: str
    unlocked: bool
    current: float
    threshold: float
    # HA-side addition, no equivalent field on the C# Achievement record (which is
    # just icon/name/unlocked/current/threshold - see DashboardAchievementsCard.razor).
    # Lets a dashboard group/filter the flat 44-entry list without string-parsing
    # `name`, e.g. a "streak" progress row picking out just the 4 streak tiers.
    category: str


@dataclasses.dataclass
class QuotaInfo:
    hours: float
    target_min: float
    target_max: float
    percent: float
    warning: bool
    missing_hours: float


@dataclasses.dataclass
class CourseHours:
    course_id: int
    course_name: str
    course_color: str
    hours: float
    sessions: int


@dataclasses.dataclass
class NextCourseGoal:
    course_id: int
    course_name: str
    target_date: date
    days_remaining: int


@dataclasses.dataclass
class NeglectedCourse:
    course_id: int
    course_name: str
    last_studied: date | None
    days_since: int | None


@dataclasses.dataclass
class WeeklyReport:
    week_id: str
    hours: float
    delta_vs_previous_week_hours: float
    top_course: str | None
    sessions_count: int


@dataclasses.dataclass
class StudyProgram:
    """Mirrors StudyProgramSummaryDto (GET /api/studyprograms). id is None for the
    built-in, fixed catalog - there's exactly one such synthetic entry, always first."""
    id: int | None
    name: str
    is_built_in: bool
    is_completed: bool


def program_key(program_id: int | None) -> str:
    """Stable string key for a programme, used in coordinator data, unique_ids and
    device identifiers. The server represents the built-in programme as null (DTOs)
    or 0 (the `program` query param); HA identifiers need a stable hashable string,
    so both map to "builtin". Custom programmes use their DB id as a string."""
    return "builtin" if program_id in (None, 0) else str(program_id)


@dataclasses.dataclass
class StudyLifeProgramData:
    """Per-programme stats, computed for EVERY programme each poll cycle - the
    same shapes the single-programme fields on StudyLifeData use, but scoped to
    one programme's courses/goals/sessions. Week/month quota targets stay the
    user's GLOBAL weekly/monthly goals (there are no per-programme targets in
    the app); only the studied-hours numerator is per-programme."""
    program: StudyProgram
    is_active: bool
    courses: list[dict[str, Any]]
    course_goals: list[dict[str, Any]]
    ects_earned: int
    ects_total: int
    average_grade: float | None
    streak_days: int
    longest_streak_days: int
    week_hours: float
    week_quota: QuotaInfo
    month_hours: float
    month_quota: QuotaInfo
    total_hours: float
    total_sessions: int
    next_course_goal: NextCourseGoal | None
    upcoming_course_goals: list[NextCourseGoal]
    forecast_date: date | None
    forecast_recent_weekly_hours: float | None


@dataclasses.dataclass
class TimerState:
    session_id: int | None
    is_running: bool
    is_break: bool
    current_round: int
    timer_mode_id: int
    phase_ends_at: datetime | None

    @property
    def phase(self) -> str:
        if not self.is_running:
            return "idle"
        return "break" if self.is_break else "focus"


@dataclasses.dataclass
class StudyLifeData:
    sessions: list[StudySession]
    settings: dict[str, Any]
    notes_count: int
    latest_note: dict[str, Any] | None
    active_session: StudySession | None
    upcoming_session: StudySession | None
    today_sessions: list[StudySession]
    week_sessions: list[StudySession]
    week_hours: float
    previous_week_hours: float
    week_delta_hours: float
    streak_days: int
    week_quota: QuotaInfo
    month_quota: QuotaInfo
    course_goals: list[dict[str, Any]]
    average_grade: float | None
    course_hours: list[CourseHours]
    total_hours: float
    total_sessions: int
    next_course_goal: NextCourseGoal | None
    upcoming_course_goals: list[NextCourseGoal]
    neglected_course: NeglectedCourse | None
    courses: list[dict[str, Any]]
    timer_state: TimerState
    ects_earned: int
    ects_total: int
    longest_streak_days: int
    achievements: list[Achievement]
    achievements_unlocked: int
    topics_completed: int
    topics_total: int
    topics_by_course: list[dict[str, Any]]
    days_since_last_session: int | None
    inactivity_warning: bool
    forecast_date: date | None
    forecast_recent_weekly_hours: float | None
    month_hours_current: float
    month_hours_delta_vs_last_month: float
    month_hours_delta_vs_last_year: float | None
    weekly_report: WeeklyReport
    study_programs: list[StudyProgram]
    active_study_program: StudyProgram
    # One entry per study programme (built-in + custom, completed or not),
    # keyed by program_key() - feeds the one-device-per-programme entities.
    programs: dict[str, StudyLifeProgramData]


def _parse_dt(value: str) -> datetime:
    """Parse a naive local datetime string as returned by the StudyLife API."""
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=None)


def _setting_or_default(settings: dict[str, Any], key: str, default: float) -> float:
    """settings[key] if the field is present, else `default` - falls back only when
    the field is genuinely missing (None/absent, e.g. a not-yet-migrated server that
    doesn't send it at all), NOT when the user has explicitly set it to 0. A plain
    `settings.get(key) or default` looks equivalent but silently discards an
    explicit 0 (Python's `or` treats it as falsy), which would override a user's
    "no goal" setting with the built-in default and fire quota warnings they
    deliberately turned off."""
    value = settings.get(key)
    return float(value) if value is not None else default


def _to_session(raw: dict[str, Any]) -> StudySession:
    return StudySession(
        id=raw["id"],
        course_id=raw["courseId"],
        course_name=raw["courseName"],
        course_color=raw["courseColor"],
        start=_parse_dt(raw["startTime"]),
        end=_parse_dt(raw["endTime"]),
        topic=raw.get("topic"),
        notes=raw.get("notes"),
        is_completed=raw["isCompleted"],
        timer_mode_id=raw["timerModeId"],
        recurrence_group_id=raw.get("recurrenceGroupId"),
    )


def _calc_streak(sessions: list[StudySession], today: date) -> int:
    """Mirrors Index.razor's CalcStreak: counts back from today (or yesterday,
    if today has no session yet - the streak stays alive until the day is over
    instead of resetting to 0 every morning). Callers pass the already-filtered
    completed_history (is_completed OR end passed), so no re-filtering here -
    an earlier `and s.is_completed` re-check silently dropped every session
    that only counted as studied because its scheduled end had passed, which
    pinned the sensor to 0 for anyone not using the in-app focus timer."""
    study_dates = {s.start.date() for s in sessions}
    day = today if today in study_dates else today - timedelta(days=1)
    streak = 0
    while day in study_dates:
        streak += 1
        day -= timedelta(days=1)
    return streak


def _calc_longest_streak(sessions: list[StudySession]) -> int:
    """Longest-ever run of consecutive distinct study days - mirrors Index.razor's
    streak achievement badges / Stats.razor's Year-in-Review longest-streak slide.
    Unlike `_calc_streak` (current, must include today/yesterday), this looks at
    the whole history for the best run ever, so it only grows, never resets."""
    dates = sorted({s.start.date() for s in sessions})
    if not dates:
        return 0
    longest = current = 1
    for i in range(1, len(dates)):
        current = current + 1 if (dates[i] - dates[i - 1]).days == 1 else 1
        longest = max(longest, current)
    return longest


def _calc_achievements(
    completed_history: list[StudySession],
    settings: dict[str, Any],
    active_course_ids: set[int],
    notes_count: int,
    study_programs: list[StudyProgram],
    ects_total: int,
    ects_earned: int,
) -> list[Achievement]:
    """Mirrors Index.Achievements.razor.cs's BuildAchievements - ALL 13 permanent
    milestone categories (44 tiers total: 5 hours + 4 streak + 4 sessions + 4
    courses-completed + 1 all-courses-done + 3 early-bird + 3 night-owl + 3
    weekend-warrior + 3 marathon + 5 perfect-weeks + 3 notes + 3 course-diversity
    + 3 completed-programmes), not just the original 5. Icons/thresholds/formulas
    cross-checked against that file directly, not assumed.

    Uses the same SESSION_HISTORY_DAYS window as the other sensors here (not the
    dashboard's dedicated ~10-year AchievementHistoryDays fetch), trading a bit of
    long-term accuracy for avoiding an extra heavy request every poll cycle - fine
    for at-a-glance automations/dashboards, not meant to be pixel-perfect for
    someone many years into a degree.

    `completed_history` is the same "studied" set (is_completed OR end passed)
    every other achievement-adjacent sensor here already uses - matches the C#
    side's _allTimeHistory precondition (GetHistory defaults to
    onlyCompleted=true) without re-filtering.
    """
    total_hours = round(sum(s.duration_minutes for s in completed_history) / 60.0, 2)
    total_sessions = len(completed_history)
    longest_streak = _calc_longest_streak(completed_history)

    # Mirrors BuildAchievements' explicit intersection with the ACTIVE programme's
    # course ids: settings.CompletedCourseIds is a flat, cross-programme field, so
    # a raw len() would leak other programmes' completed-course tallies into a
    # freshly switched, empty one (this is the exact bug the C# comment calls out,
    # and what DASHBOARD_INTEGRATION.md used to document as a Python-side caveat -
    # fixed here to match).
    completed_course_ids = set(settings.get("completedCourseIds") or [])
    courses_completed = len(completed_course_ids & active_course_ids)
    all_courses_done = ects_total > 0 and ects_earned >= ects_total

    early_bird_count = sum(1 for s in completed_history if s.start.hour < 7)
    night_owl_count = sum(1 for s in completed_history if s.start.hour >= 22)
    # Python's date.weekday(): Monday=0 .. Sunday=6, so >=5 is Sat/Sun - same set
    # as C#'s DayOfWeek.Saturday/Sunday.
    weekend_count = sum(1 for s in completed_history if s.start.weekday() >= 5)
    longest_session_hours = max(
        (s.duration_minutes / 60.0 for s in completed_history), default=0.0
    )

    # Weekly groups keyed by each session's own week-Monday (StudyMetrics.WeekStartOf
    # in C#) - reuses `_week_start`, which is generic over any date, not just "today".
    weekly_groups: dict[date, list[StudySession]] = {}
    for s in completed_history:
        weekly_groups.setdefault(_week_start(s.start.date()), []).append(s)

    # C# gates on the RAW settings.WeeklyGoalMinHours > 0 (no fallback default) -
    # deliberately NOT the WEEK_QUOTA_MIN_HOURS-defaulted value used elsewhere in
    # this module for the week_quota sensor, to match BuildAchievements exactly.
    weekly_goal_min = float(settings.get("weeklyGoalMinHours") or 0)
    perfect_weeks = (
        sum(
            1
            for group in weekly_groups.values()
            if sum(s.duration_minutes for s in group) / 60.0 >= weekly_goal_min
        )
        if weekly_goal_min > 0
        else 0
    )
    max_course_diversity = max(
        (len({s.course_id for s in group}) for group in weekly_groups.values()),
        default=0,
    )

    programs_completed = sum(1 for p in study_programs if p.is_completed)

    achievements: list[Achievement] = []
    for t in (25, 100, 500, 1000, 2000):
        achievements.append(Achievement("⏱", f"{t}h studied", total_hours >= t, total_hours, t, "hours"))
    for t in (7, 30, 100, 365):
        achievements.append(Achievement("🔥", f"{t}-day streak", longest_streak >= t, longest_streak, t, "streak"))
    for t in (50, 200, 500, 1000):
        achievements.append(Achievement("✅", f"{t} sessions", total_sessions >= t, total_sessions, t, "sessions"))
    for t in (1, 10, 20, 30):
        achievements.append(Achievement("🎓", f"{t} courses completed", courses_completed >= t, courses_completed, t, "courses_completed"))
    achievements.append(Achievement("🏆", "All courses completed", all_courses_done, 1 if all_courses_done else 0, 1, "all_courses_done"))
    for t in (5, 25, 100):
        achievements.append(Achievement("🌅", f"{t} early-bird sessions", early_bird_count >= t, early_bird_count, t, "early_bird"))
    for t in (5, 25, 100):
        achievements.append(Achievement("🦉", f"{t} night-owl sessions", night_owl_count >= t, night_owl_count, t, "night_owl"))
    for t in (10, 50, 150):
        achievements.append(Achievement("🏖", f"{t} weekend sessions", weekend_count >= t, weekend_count, t, "weekend_warrior"))
    for t in (2, 4, 6):
        achievements.append(Achievement("🏃", f"{t}h marathon session", longest_session_hours >= t, longest_session_hours, t, "marathon_session"))
    for t in (1, 4, 12, 26, 52):
        achievements.append(Achievement("📅", f"{t} perfect weeks", perfect_weeks >= t, perfect_weeks, t, "perfect_weeks"))
    for t in (5, 25, 100):
        achievements.append(Achievement("📝", f"{t} notes written", notes_count >= t, notes_count, t, "notes_written"))
    for t in (2, 4, 6):
        achievements.append(Achievement("🎯", f"{t} courses in one week", max_course_diversity >= t, max_course_diversity, t, "course_diversity"))
    for t in (1, 2, 3):
        achievements.append(Achievement("🏅", f"{t} study programme(s) completed", programs_completed >= t, programs_completed, t, "completed_programmes"))

    return achievements


def _calc_topics_progress(
    course_goals: list[dict[str, Any]], courses: list[dict[str, Any]]
) -> tuple[int, int, list[dict[str, Any]]]:
    """Mirrors the Themen-Checkliste in Setup.razor: completed vs. total topics
    per course, derived from CourseGoalDto.completedTopics (comma-separated topic
    names) against the static catalog's Topics list."""
    topics_by_id = {c["id"]: c.get("topics") or [] for c in courses}
    breakdown: list[dict[str, Any]] = []
    total_completed = 0
    total_topics = 0
    for g in course_goals:
        catalog_topics = topics_by_id.get(g["courseId"])
        if not catalog_topics:
            continue
        completed = {t for t in (g.get("completedTopics") or "").split(",") if t}
        done = sum(1 for t in catalog_topics if t in completed)
        total_completed += done
        total_topics += len(catalog_topics)
        if done > 0:
            breakdown.append({
                "course_id": g["courseId"],
                "course_name": g["courseName"],
                "topics_completed": done,
                "topics_total": len(catalog_topics),
            })
    return total_completed, total_topics, breakdown


def _week_start(today: date) -> date:
    """Monday of the current week, same as Index.razor's dowOffset math."""
    return today - timedelta(days=today.weekday())


def _calc_weekly_report(history: list[StudySession], today: date) -> WeeklyReport:
    """Summary of the most recently COMPLETED Mon-Sun week - the HA-side
    counterpart of the server's Sunday-18:00 weekly web push
    (RunWeeklyReportAsync in BackgroundTaskService.cs), which HA automations
    can't react to. Same Monday week-start convention as `_week_start` and the
    same window filter as the previous-week-hours calc in `_async_update_data`
    (no completion filter - for a week that already ended, every session's end
    lies in the past, so this matches the server push's "IsCompleted OR
    EndTime <= now" studied-semantics anyway). The ISO week id (e.g.
    "2026-W28") flips on Monday, mirroring the server's ISOWeek-based dedup key."""
    week_start = _week_start(today)
    report_start = week_start - timedelta(days=7)
    report_sessions = [s for s in history if report_start <= s.start.date() < week_start]
    hours = sum(s.duration_minutes for s in report_sessions) / 60.0

    before_start = report_start - timedelta(days=7)
    before_hours = sum(
        s.duration_minutes for s in history
        if before_start <= s.start.date() < report_start
    ) / 60.0

    minutes_by_course: dict[str, float] = {}
    for s in report_sessions:
        minutes_by_course[s.course_name] = minutes_by_course.get(s.course_name, 0.0) + s.duration_minutes
    top_course = max(minutes_by_course, key=minutes_by_course.get) if minutes_by_course else None

    iso_year, iso_week, _ = report_start.isocalendar()
    return WeeklyReport(
        week_id=f"{iso_year}-W{iso_week:02d}",
        hours=round(hours, 2),
        delta_vs_previous_week_hours=round(hours - before_hours, 2),
        top_course=top_course,
        sessions_count=len(report_sessions),
    )


def _calc_week_quota(week_hours: float, week_min: float, week_max: float) -> QuotaInfo:
    max_bar = week_max * 1.15
    # A user-configured 0 target ("no goal") makes max_bar 0 too - guard against
    # dividing by it, same as _calc_month_quota already does for the same reason.
    percent = min(100.0, week_hours / max_bar * 100) if max_bar else 0.0
    warning = week_hours < week_min
    missing = max(0.0, week_min - week_hours)
    return QuotaInfo(
        hours=round(week_hours, 2),
        target_min=week_min,
        target_max=week_max,
        percent=round(percent, 1),
        warning=warning,
        missing_hours=round(missing, 2),
    )


def _calc_average_grade(
    course_goals: list[dict[str, Any]], courses: list[dict[str, Any]]
) -> float | None:
    """Mirrors Stats.razor: mean of every set Grade, weighted by each course's Ects.
    `course_goals` is expected to already be scoped to ONE study programme's
    course ids (see `_build_program_data` / the `course_goals` filtering in
    `_async_update_data`)."""
    ects_by_id = {c["id"]: c.get("ects", 5) for c in courses}
    weighted = [
        (g["grade"], ects_by_id.get(g["courseId"], 5))
        for g in course_goals
        if g.get("grade") is not None
    ]
    if not weighted:
        return None
    total_ects = sum(ects for _, ects in weighted)
    if total_ects <= 0:
        return round(sum(grade for grade, _ in weighted) / len(weighted), 2)
    return round(sum(grade * ects for grade, ects in weighted) / total_ects, 2)


def _calc_ects_progress(
    courses: list[dict[str, Any]], settings: dict[str, Any]
) -> tuple[int, int]:
    """Mirrors CourseCatalog.CalcTotalEcts / CalcEctsEarned in StudyLife.Shared:

    - ects_total  = sum of ungrouped course ECTS + sum of per-group quotas
                    (group quota is parsed from the group name, e.g.
                    "Wahlpflichtmodule A (5 ECTS)" → 5).  Result: 180 ECTS.
    - ects_earned = ungrouped completed ECTS
                    + per-group min(completed ECTS in group, group quota).
    """
    completed_ids = set(settings.get("completedCourseIds") or [])

    ungrouped = [c for c in courses if not c.get("group")]
    grouped_by_name: dict[str, list[dict[str, Any]]] = {}
    for c in courses:
        g = c.get("group")
        if g:
            grouped_by_name.setdefault(g, []).append(c)

    def _group_quota(group_name: str, members: list[dict[str, Any]]) -> int:
        m = re.search(r"\((\d+)\s*ECTS\)", group_name)
        return int(m.group(1)) if m else sum(c.get("ects", 5) for c in members)

    total = sum(c.get("ects", 5) for c in ungrouped) + sum(
        _group_quota(name, members) for name, members in grouped_by_name.items()
    )

    earned_ungrouped = sum(
        c.get("ects", 5) for c in ungrouped if c["id"] in completed_ids
    )
    earned_grouped = sum(
        min(
            sum(c.get("ects", 5) for c in members if c["id"] in completed_ids),
            _group_quota(name, members),
        )
        for name, members in grouped_by_name.items()
    )

    return earned_ungrouped + earned_grouped, total


def _calc_course_hours(sessions: list[StudySession], now: datetime) -> list[CourseHours]:
    """Mirrors Stats.razor: hours/session-count of *completed* sessions, grouped
    by course, sorted descending by hours. Course name/color come from the
    sessions themselves (no course catalog is available to the API/HA).
    "Completed" = timer-completed OR the scheduled end has already passed - not
    every session runs through the in-app timer (e.g. reading offline)."""
    by_course: dict[int, CourseHours] = {}
    for s in sessions:
        if not (s.is_completed or s.end <= now):
            continue
        entry = by_course.get(s.course_id)
        if entry is None:
            entry = CourseHours(
                course_id=s.course_id, course_name=s.course_name,
                course_color=s.course_color, hours=0.0, sessions=0,
            )
            by_course[s.course_id] = entry
        entry.hours += s.duration_minutes / 60.0
        entry.sessions += 1

    for entry in by_course.values():
        entry.hours = round(entry.hours, 2)

    return sorted(by_course.values(), key=lambda c: c.hours, reverse=True)


def _calc_upcoming_course_goals(
    course_goals: list[dict[str, Any]], today: date, limit: int = 5
) -> list[NextCourseGoal]:
    """Open (not-yet-completed, TargetDate set) course goals soonest-first -
    mirrors the dashboard's "Anstehende Kursziele" card (Index.razor), which
    also caps at 5. Complements the server-side push reminders with something
    HA automations can react to directly. `course_goals` is expected to already
    be scoped to ONE study programme (see `_build_program_data` /
    `_async_update_data`)."""
    open_goals = []
    for g in course_goals:
        if g.get("completedAt") is not None or not g.get("targetDate"):
            continue
        target_date = datetime.fromisoformat(g["targetDate"]).date()
        open_goals.append(
            NextCourseGoal(
                course_id=g["courseId"],
                course_name=g["courseName"],
                target_date=target_date,
                days_remaining=(target_date - today).days,
            )
        )
    open_goals.sort(key=lambda g: g.target_date)
    return open_goals[:limit]


def _to_study_program(raw: dict[str, Any]) -> StudyProgram:
    return StudyProgram(
        id=raw.get("id"),
        name=raw["name"],
        is_built_in=raw.get("isBuiltIn", False),
        is_completed=raw.get("isCompleted", False),
    )


def _to_timer_state(raw: dict[str, Any]) -> TimerState:
    return TimerState(
        session_id=raw.get("sessionId"),
        is_running=raw.get("isRunning", False),
        is_break=raw.get("isBreak", False),
        current_round=raw.get("currentRound", 0),
        timer_mode_id=raw.get("timerModeId", 0),
        phase_ends_at=_parse_dt(raw["phaseEndsAt"]) if raw.get("phaseEndsAt") else None,
    )


def _calc_neglected_course(
    settings: dict[str, Any],
    courses: list[dict[str, Any]],
    completed_history: list[StudySession],
    today: date,
) -> NeglectedCourse | None:
    """Active (selected, not completed) course studied longest ago (or never in
    the lookback window) - mirrors Index.razor's "Balance-Check" card. Returns
    None if there's fewer than 2 active courses (nothing to compare)."""
    selected_ids = set(settings.get("selectedCourseIds") or [])
    completed_ids = set(settings.get("completedCourseIds") or [])
    active_courses = [c for c in courses if c["id"] in selected_ids and c["id"] not in completed_ids]
    if len(active_courses) < 2:
        return None

    last_studied: dict[int, date] = {}
    for s in completed_history:
        d = s.start.date()
        if s.course_id not in last_studied or d > last_studied[s.course_id]:
            last_studied[s.course_id] = d

    ranked = sorted(active_courses, key=lambda c: last_studied.get(c["id"], date.min))
    pick = ranked[0]
    pick_last = last_studied.get(pick["id"])
    return NeglectedCourse(
        course_id=pick["id"],
        course_name=pick["name"],
        last_studied=pick_last,
        days_since=(today - pick_last).days if pick_last else None,
    )


def _calc_month_quota(
    month_hours: float, today: date, month_start: date, month_min: float, month_max: float
) -> QuotaInfo:
    """Prorates the absolute monthly goal (MonthlyGoalMinHours/MaxHours, independently
    configurable from the weekly goal - see Setup.razor's monthly-goal card) by how much
    of the month has elapsed, same as before: weeks_elapsed/total_weeks_in_month scales the
    full-month target down early in the month, so progress doesn't look misleadingly "behind"
    a full month's target on day 3. total_weeks_in_month uses the same ceil(days/7)
    convention as `weeks_elapsed` itself."""
    days_in_month = calendar.monthrange(month_start.year, month_start.month)[1]
    total_weeks_in_month = max(1, math.ceil(days_in_month / 7.0))
    weeks_elapsed = min(total_weeks_in_month, max(1, math.ceil((today - month_start).days / 7.0)))
    target_min = month_min * weeks_elapsed / total_weeks_in_month
    target_max = month_max * weeks_elapsed / total_weeks_in_month
    max_bar = target_max * 1.15
    percent = min(100.0, month_hours / max_bar * 100) if max_bar else 0.0
    warning = month_hours < target_min
    missing = max(0.0, target_min - month_hours)
    return QuotaInfo(
        hours=round(month_hours, 2),
        target_min=round(target_min, 2),
        target_max=round(target_max, 2),
        percent=round(percent, 1),
        warning=warning,
        missing_hours=round(missing, 2),
    )


def _calc_forecast(
    courses: list[dict[str, Any]],
    history: list[StudySession],
    ects_earned: int,
    ects_total: int,
    today: date,
    now: datetime,
    week_quota_min: float,
    week_quota_max: float,
) -> tuple[date | None, float | None]:
    """Mirrors Index.razor/Stats.razor's BuildForecast: a semester-based baseline (one
    semester = 6 months, ECTS spread evenly across the catalog's semesters) refined by
    the actual study pace of the last 8 weeks vs. the user's configurable weekly target
    (week_quota_min/max, WeeklyGoalMinHours/MaxHours server-side, default 25-30h).
    Deliberately NOT based on CourseGoalDto.completedAt (as an earlier
    version of both this and the C# code was) - a student who retroactively marks
    already-finished courses as done all at once would otherwise get an absurdly
    optimistic forecast (years of ECTS "earned" within days). Returns
    (forecast_date, recent_weekly_hours) - the latter is the actual measured pace
    feeding the adjustment, exposed so automations can see what's driving the date."""
    remaining_ects = ects_total - ects_earned
    if remaining_ects <= 0:
        return None, None

    total_semesters = max((c["semester"] for c in courses), default=0)
    if total_semesters <= 0:
        return None, None

    ects_per_semester = ects_total / total_semesters
    baseline_weeks_needed = remaining_ects / ects_per_semester * 26.0

    recent_weeks = 8
    recent_cutoff = today - timedelta(days=recent_weeks * 7)
    recent_hours = sum(
        s.duration_minutes / 60.0
        for s in history
        if s.start.date() >= recent_cutoff and (s.is_completed or s.end <= now)
    )
    recent_weekly_hours = recent_hours / recent_weeks

    reference_weekly_hours = (week_quota_min + week_quota_max) / 2.0
    pace_ratio = recent_weekly_hours / reference_weekly_hours if recent_weekly_hours > 0 else 1.0
    pace_ratio = min(3.0, max(0.25, pace_ratio))

    adjusted_weeks_needed = baseline_weeks_needed / pace_ratio
    forecast_date = today + timedelta(days=adjusted_weeks_needed * 7)
    return forecast_date, round(recent_weekly_hours, 2)


def _hours_in_month(sessions: list[StudySession], year: int, month: int) -> float:
    return sum(s.duration_minutes for s in sessions if s.start.year == year and s.start.month == month) / 60.0


def _calc_month_comparison(
    history: list[StudySession], today: date
) -> tuple[float, float, float | None]:
    """Mirrors Index.razor's BuildMonthComparison: this month's hours vs. the
    previous calendar month and vs. the same month last year. Reuses the
    coordinator's existing SESSION_HISTORY_DAYS (~400 days) history rather than a
    second long-range fetch - 400 days back always covers the full "same month
    last year" regardless of today's date (worst case ~396 days back), so a
    dedicated multi-year fetch every poll cycle isn't needed here (unlike
    achievements, which explicitly need the shorter window for the same reason -
    see that docstring). The year-over-year figure is only returned once history
    actually reaches back over the whole same month last year (exactly the C#
    `_monthCompHasYearData` gate) - otherwise a "0h" comparison would be
    misleading rather than informative."""
    this_month_hours = _hours_in_month(history, today.year, today.month)

    if today.month > 1:
        last_month_year, last_month_month = today.year, today.month - 1
    else:
        last_month_year, last_month_month = today.year - 1, 12
    last_month_hours = _hours_in_month(history, last_month_year, last_month_month)

    last_year_year, last_year_month = today.year - 1, today.month
    last_year_hours = _hours_in_month(history, last_year_year, last_year_month)

    earliest_session = min((s.start for s in history), default=None)
    last_year_month_start = date(last_year_year, last_year_month, 1)
    has_year_data = earliest_session is not None and earliest_session.date() <= last_year_month_start

    return (
        this_month_hours,
        this_month_hours - last_month_hours,
        (this_month_hours - last_year_hours) if has_year_data else None,
    )


def _build_program_data(
    program: StudyProgram,
    is_active: bool,
    courses: list[dict[str, Any]],
    all_course_goals: list[dict[str, Any]],
    history: list[StudySession],
    settings: dict[str, Any],
    now: datetime,
    today: date,
    week_start: date,
    month_start: date,
    week_quota_min: float,
    week_quota_max: float,
    month_quota_min: float,
    month_quota_max: float,
) -> StudyLifeProgramData:
    """One programme's stats, computed from the SHARED global fetches: sessions
    and course goals are partitioned by this programme's course-id set (course
    ids are globally unique across programmes), only `courses` comes from a
    programme-specific request. Reuses the exact same _calc_* helpers as the
    active-programme fields on StudyLifeData - same math, narrower inputs."""
    course_ids = {c["id"] for c in courses}
    course_goals = [g for g in all_course_goals if g["courseId"] in course_ids]
    prog_history = [s for s in history if s.course_id in course_ids]
    # Same studied-semantics as everywhere else: timer-completed OR end passed.
    prog_completed = [s for s in prog_history if s.is_completed or s.end <= now]

    week_hours = sum(
        s.duration_minutes for s in prog_history if s.start.date() >= week_start
    ) / 60.0
    month_hours = sum(
        s.duration_minutes for s in prog_history if s.start.date() >= month_start
    ) / 60.0

    ects_earned, ects_total = _calc_ects_progress(courses, settings)
    upcoming_course_goals = _calc_upcoming_course_goals(course_goals, today)
    forecast_date, forecast_recent_weekly_hours = _calc_forecast(
        courses, prog_history, ects_earned, ects_total, today, now,
        week_quota_min, week_quota_max,
    )

    return StudyLifeProgramData(
        program=program,
        is_active=is_active,
        courses=courses,
        course_goals=course_goals,
        ects_earned=ects_earned,
        ects_total=ects_total,
        average_grade=_calc_average_grade(course_goals, courses),
        streak_days=_calc_streak(prog_completed, today),
        longest_streak_days=_calc_longest_streak(prog_completed),
        week_hours=round(week_hours, 2),
        week_quota=_calc_week_quota(week_hours, week_quota_min, week_quota_max),
        month_hours=round(month_hours, 2),
        month_quota=_calc_month_quota(month_hours, today, month_start, month_quota_min, month_quota_max),
        total_hours=round(sum(s.duration_minutes for s in prog_completed) / 60.0, 2),
        total_sessions=len(prog_completed),
        next_course_goal=upcoming_course_goals[0] if upcoming_course_goals else None,
        upcoming_course_goals=upcoming_course_goals,
        forecast_date=forecast_date,
        forecast_recent_weekly_hours=forecast_recent_weekly_hours,
    )


class StudyLifeCoordinator(DataUpdateCoordinator[StudyLifeData]):
    """Coordinates polling of the StudyLife API."""

    def __init__(self, hass: HomeAssistant, client: StudyLifeApiClient, update_interval: timedelta) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=update_interval)
        self._client = client
        # Completed-week id seen in the previous refresh cycle; None until the
        # first refresh of this HA runtime (see the event-firing block below).
        self._last_report_week_id: str | None = None

    @property
    def client(self) -> StudyLifeApiClient:
        return self._client

    async def _async_update_data(self) -> StudyLifeData:
        try:
            raw_sessions = await self._client.async_get_sessions()
            raw_history = await self._client.async_get_session_history(SESSION_HISTORY_DAYS, only_completed=False)
            settings = await self._client.async_get_settings()
            raw_notes = await self._client.async_get_notes()
            raw_course_goals = await self._client.async_get_course_goals()
            raw_timer_state = await self._client.async_get_timer_state()
            raw_study_programs = await self._client.async_get_study_programs()
            # One catalog request per programme (?program=0 for the built-in one, per
            # CoursesController's convention). Sessions/goals stay single global fetches
            # and are partitioned client-side, so this is the only per-programme request -
            # and each programme-specific URL has its own ETag cache entry in the client.
            raw_courses_by_key: dict[str, list[dict[str, Any]]] = {}
            for raw_program in raw_study_programs:
                pid = raw_program.get("id")
                raw_courses_by_key[program_key(pid)] = await self._client.async_get_courses(
                    pid if pid is not None else 0
                )
        except StudyLifeApiAuthError as err:
            # Genuine 401: both current and previous key rejected - normally only possible
            # when HA was offline longer than the 10-day grace window of a key rotation.
            # ConfigEntryAuthFailed makes HA show an actionable "re-authenticate" repair
            # that opens the reauth step in config_flow.py, instead of silently failing
            # every poll cycle.
            raise ConfigEntryAuthFailed(str(err)) from err
        except StudyLifeApiError as err:
            raise UpdateFailed(str(err)) from err

        study_programs = [_to_study_program(p) for p in raw_study_programs]
        active_program_id = settings.get("activeStudyProgramId")
        active_study_program = next(
            (p for p in study_programs if p.id == active_program_id),
            # Defensive fallback (e.g. stale/unknown id): the built-in entry is always
            # first and always present, mirroring CoursesController's own fallback.
            study_programs[0] if study_programs else StudyProgram(None, "StudyLife", True, False),
        )

        # The ACTIVE programme's catalog, for the hub-device entities/calendars/services -
        # same list a parameterless GET /api/courses would resolve from the settings, just
        # reused from the per-programme fetch above instead of a redundant extra request.
        courses = raw_courses_by_key.get(program_key(active_study_program.id), [])

        # /api/coursegoals returns EVERY goal ever set, across ALL study programmes - course
        # ids stay globally unique (see StudyProgramCatalog.CustomCourseIdOffset server-side),
        # so a grade/target-date set while a different programme was active would otherwise
        # silently leak into average_grade/next_course_goal below. Filtering against the
        # active catalog here scopes every downstream consumer of `course_goals` in one
        # place; the per-programme datasets partition the same raw list per programme.
        active_course_ids = {c["id"] for c in courses}
        course_goals = [g for g in raw_course_goals if g["courseId"] in active_course_ids]

        sessions = [_to_session(s) for s in raw_sessions]
        now = dt_util.now().replace(tzinfo=None)
        today = now.date()

        # Long-range history (all sessions, not just completed) for anything that looks further
        # back than /api/sessions' ±7/90-day window: week/month hours, streak, week-over-week
        # delta, neglected-course. `sessions` stays the source for today/active/upcoming.
        history = [_to_session(s) for s in raw_history]
        # "Completed" = timer-completed OR the scheduled end has already passed - not every
        # session runs through the in-app timer (e.g. reading offline at the lake).
        completed_history = [s for s in history if s.is_completed or s.end <= now]

        today_sessions = sorted(
            (s for s in sessions if s.start.date() == today), key=lambda s: s.start
        )
        active_session = next(
            (s for s in sessions if not s.is_completed and s.start <= now <= s.end), None
        )
        upcoming_session = next(
            iter(
                sorted(
                    (s for s in sessions if not s.is_completed and s.start > now),
                    key=lambda s: s.start,
                )
            ),
            None,
        )

        week_start = _week_start(today)
        week_sessions = sorted(
            (s for s in history if s.start.date() >= week_start), key=lambda s: s.start
        )
        week_minutes = sum(s.duration_minutes for s in week_sessions)
        week_hours = week_minutes / 60.0

        previous_week_start = week_start - timedelta(days=7)
        previous_week_minutes = sum(
            s.duration_minutes for s in history
            if previous_week_start <= s.start.date() < week_start
        )
        previous_week_hours = previous_week_minutes / 60.0

        month_start = today.replace(day=1)
        month_sessions = [s for s in history if s.start.date() >= month_start]
        month_minutes = sum(s.duration_minutes for s in month_sessions)
        month_hours = month_minutes / 60.0

        streak_days = _calc_streak(completed_history, today)

        # Same filter logic as course_goals above (active_course_ids): general notes without
        # a courseId always stay visible, course-bound ones only if the course belongs to the
        # active study programme - mirrors exactly the scope recently fixed in
        # Notes.razor/Index.Insights.razor.cs.
        notes = [n for n in raw_notes if not n.get("courseId") or n["courseId"] in active_course_ids]
        latest_note = notes[0] if notes else None

        course_hours = _calc_course_hours(sessions, now)
        ects_earned, ects_total = _calc_ects_progress(courses, settings)
        upcoming_course_goals = _calc_upcoming_course_goals(course_goals, today)
        neglected_course = _calc_neglected_course(
            settings, courses,
            [s for s in completed_history if s.start.date() >= today - timedelta(days=NEGLECT_HISTORY_DAYS)],
            today,
        )

        # _setting_or_default (not `.get(...) or default`) keeps this working against a
        # not-yet-migrated server (older StudyLife versions don't send these fields at
        # all) WITHOUT overriding a user's explicit 0 ("no goal") with the built-in
        # default - see that helper's docstring for why `or` was wrong here.
        week_quota_min = _setting_or_default(settings, "weeklyGoalMinHours", WEEK_QUOTA_MIN_HOURS)
        week_quota_max = _setting_or_default(settings, "weeklyGoalMaxHours", WEEK_QUOTA_MAX_HOURS)
        month_quota_min = _setting_or_default(settings, "monthlyGoalMinHours", MONTH_QUOTA_MIN_HOURS)
        month_quota_max = _setting_or_default(settings, "monthlyGoalMaxHours", MONTH_QUOTA_MAX_HOURS)

        forecast_date, forecast_recent_weekly_hours = _calc_forecast(
            courses, history, ects_earned, ects_total, today, now, week_quota_min, week_quota_max
        )
        month_hours_current, month_hours_delta_vs_last_month, month_hours_delta_vs_last_year = (
            _calc_month_comparison(history, today)
        )

        longest_streak_days = _calc_longest_streak(completed_history)
        achievements = _calc_achievements(
            completed_history=completed_history,
            settings=settings,
            active_course_ids=active_course_ids,
            notes_count=len(notes),
            study_programs=study_programs,
            ects_total=ects_total,
            ects_earned=ects_earned,
        )
        achievements_unlocked = sum(1 for a in achievements if a.unlocked)
        topics_completed, topics_total, topics_by_course = _calc_topics_progress(course_goals, courses)

        # Mirrors InactivityReminderService.cs exactly: no past session ever -> always warn;
        # otherwise warn once the gap exceeds the user's configured threshold (default 5 days).
        last_past_session = max(
            (s for s in history if s.start <= now), key=lambda s: s.start, default=None
        )
        inactivity_threshold = settings.get("inactivityThresholdDays") or 5
        if last_past_session is None:
            days_since_last_session = None
            inactivity_warning = True
        else:
            days_since_last_session = (today - last_past_session.start.date()).days
            inactivity_warning = days_since_last_session > inactivity_threshold

        # Per-programme stats for EVERY programme (built-in + custom, completed or
        # not) - the active programme is recomputed here too, so its device shows
        # exactly the same numbers as the fields above without special-casing.
        programs: dict[str, StudyLifeProgramData] = {}
        for program in study_programs:
            key = program_key(program.id)
            programs[key] = _build_program_data(
                program=program,
                is_active=program.id == active_study_program.id,
                courses=raw_courses_by_key.get(key, []),
                all_course_goals=raw_course_goals,
                history=history,
                settings=settings,
                now=now,
                today=today,
                week_start=week_start,
                month_start=month_start,
                week_quota_min=week_quota_min,
                week_quota_max=week_quota_max,
                month_quota_min=month_quota_min,
                month_quota_max=month_quota_max,
            )

        weekly_report = _calc_weekly_report(history, today)
        # Fire the weekly-report bus event exactly once per week-rollover: only
        # when the completed-week id changed between two refreshes of the SAME
        # HA runtime. On the very first refresh after startup the previous id is
        # unknown (None), so it's only recorded without firing - otherwise every
        # HA restart would re-fire the event for a week that was already reported.
        if (
            self._last_report_week_id is not None
            and weekly_report.week_id != self._last_report_week_id
        ):
            self.hass.bus.async_fire(
                EVENT_WEEKLY_REPORT, dataclasses.asdict(weekly_report)
            )
        self._last_report_week_id = weekly_report.week_id

        return StudyLifeData(
            sessions=sessions,
            settings=settings,
            notes_count=len(notes),
            latest_note=latest_note,
            active_session=active_session,
            upcoming_session=upcoming_session,
            today_sessions=today_sessions,
            week_sessions=week_sessions,
            week_hours=round(week_hours, 2),
            previous_week_hours=round(previous_week_hours, 2),
            week_delta_hours=round(week_hours - previous_week_hours, 2),
            streak_days=streak_days,
            week_quota=_calc_week_quota(week_hours, week_quota_min, week_quota_max),
            month_quota=_calc_month_quota(month_hours, today, month_start, month_quota_min, month_quota_max),
            course_goals=course_goals,
            average_grade=_calc_average_grade(course_goals, courses),
            course_hours=course_hours,
            total_hours=round(sum(c.hours for c in course_hours), 2),
            total_sessions=sum(c.sessions for c in course_hours),
            next_course_goal=upcoming_course_goals[0] if upcoming_course_goals else None,
            upcoming_course_goals=upcoming_course_goals,
            neglected_course=neglected_course,
            courses=courses,
            timer_state=_to_timer_state(raw_timer_state),
            ects_earned=ects_earned,
            ects_total=ects_total,
            longest_streak_days=longest_streak_days,
            achievements=achievements,
            achievements_unlocked=achievements_unlocked,
            topics_completed=topics_completed,
            topics_total=topics_total,
            topics_by_course=topics_by_course,
            days_since_last_session=days_since_last_session,
            inactivity_warning=inactivity_warning,
            forecast_date=forecast_date,
            forecast_recent_weekly_hours=forecast_recent_weekly_hours,
            month_hours_current=month_hours_current,
            month_hours_delta_vs_last_month=month_hours_delta_vs_last_month,
            month_hours_delta_vs_last_year=month_hours_delta_vs_last_year,
            weekly_report=weekly_report,
            study_programs=study_programs,
            active_study_program=active_study_program,
            programs=programs,
        )
