"""Data update coordinator for StudyLife.

Fetches /api/sessions, /api/settings, /api/notes and /api/metrics/{summary,achievements},
then maps them onto the same dataclasses the platforms (sensor.py, binary_sensor.py,
calendar.py, select.py) already consume - it does NOT re-derive the dashboard's metrics
itself. Every metric (streak, week/month quota, ECTS, average grade, forecast, course
hours, neglected course, weekly report, topics, month comparison, achievements) is computed
exactly once, server-side, by StudyLife.Shared (the same code the Blazor app runs) and
served pre-computed by GET /api/metrics/summary and GET /api/metrics/achievements - see
docs/api's metrics contract for the wire shape. This module's job is parsing that JSON into
the SAME dataclasses/entities this integration already exposed, not recomputing anything -
the sensors themselves are unaware this change happened.

(Earlier versions of this file duplicated Index.razor's client-side math as ~15 `_calc_*`
helpers, "a deliberately parallel implementation with identical semantics, manually kept in
sync" - two golden-fixture scenarios drifted from the real C# behavior for months with
nothing catching it. That whole layer is gone now that the server exposes the numbers
directly; see CHANGELOG/README for the resulting minimum StudyLife server version.)

Session StartTime/EndTime are naive local timestamps (no timezone info), just like the
server treats them (DateTime.Now, never UtcNow, for these fields). We compare against this
Home Assistant instance's local wall-clock time, which assumes StudyLife and Home Assistant
run in the same timezone - the same assumption GET /api/metrics/summary's server-side `now`
makes.

Multi-programme note: every study programme (the built-in one plus any custom ones, completed
or not) gets its own per-programme stats, ALL of them every poll cycle, regardless of which
one is active in the app - see the `programs` dict on StudyLifeData (one StudyLifeProgramData
per programme, keyed by `program_key()`). This now means one GET /api/metrics/summary call per
programme (0 = built-in, per that endpoint's convention, same as GET /api/courses) instead of
one client-side recomputation pass; the ACTIVE programme's own call is reused for the
top-level, cross-programme-shaped fields on StudyLifeData too (course hours, neglected course,
weekly report, topics, month comparison), so it is fetched exactly once, not twice. Course
catalogs (`GET /api/courses?program={id}`) and sessions/course goals (fetched once globally,
partitioned client-side by course id - course ids are globally unique across programmes, see
StudyProgramCatalog.CustomCourseIdOffset server-side) still work exactly as before: the
`courses`/`course_goals` fields on StudyLifeData stay scoped to the ACTIVE programme (they feed
the hub-device entities, calendars and services), and `study_programs`/`active_study_program`
expose the programme list itself.
"""
from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import homeassistant.util.dt as dt_util
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import StudyLifeApiAuthError, StudyLifeApiClient, StudyLifeApiError
from .const import DOMAIN, EVENT_WEEKLY_REPORT, SESSION_HISTORY_DAYS

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
    # `name`, e.g. a "streak" progress row picking out just the 4 streak tiers. Values
    # match AchievementCatalog's own category keys (see _ACHIEVEMENT_META below) - the
    # 5 that already existed for the server's push-notification reminders (hours,
    # streak, sessions, courses, allcourses) plus 8 new ones added for this endpoint
    # (earlybird, nightowl, weekend, marathon, perfectweek, notes, coursediversity,
    # programs), all single-sourced on AchievementCatalog server-side.
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
    the app); only the studied-hours numerator is per-programme. Sourced from
    ONE GET /api/metrics/summary?program={id} call - see _program_data_from_summary."""
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


def _parse_date(value: str) -> date:
    """Parse a date-only or datetime ISO string (the metrics endpoints send plain dates
    for e.g. neglectedCourse.lastStudied, upcomingCourseGoals[].targetDate) - taking just
    the first 10 chars is robust to either shape without needing a full datetime parse."""
    return date.fromisoformat(value[:10])


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


def _week_start(today: date) -> date:
    """Monday of the current week, same as Index.razor's dowOffset math. Still needed
    purely for `week_sessions` (the RAW list/count of this week's sessions, mirroring
    `today_sessions` - not a "metric": week_hours itself now comes straight off GET
    /api/metrics/summary's `hours.week`, see _async_update_data)."""
    return today - timedelta(days=today.weekday())


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


def _topics_by_course(course_goals: list[dict[str, Any]], courses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-course topic-completion breakdown feeding the topics_progress sensor's `courses`
    attribute - a HA-side-only convenience grouping, NOT a metric: the aggregate completed/
    total counts (`topics_completed`/`topics_total` on StudyLifeData) come from GET
    /api/metrics/summary's `topics` field, computed once server-side, same as everything
    else. This only reshapes course_goals/courses - already fetched locally for other
    reasons (average_grade's graded-courses list, the course picker, ...) into the grouping
    the sensor attribute has always exposed; the contract's wire format has no per-course
    breakdown field, so this stays a client-side view over locally-fetched raw data rather
    than something duplicating server math."""
    topics_by_id = {c["id"]: c.get("topics") or [] for c in courses}
    breakdown: list[dict[str, Any]] = []
    for g in course_goals:
        catalog_topics = topics_by_id.get(g["courseId"])
        if not catalog_topics:
            continue
        completed = {t for t in (g.get("completedTopics") or "").split(",") if t}
        done = sum(1 for t in catalog_topics if t in completed)
        if done > 0:
            breakdown.append({
                "course_id": g["courseId"],
                "course_name": g["courseName"],
                "topics_completed": done,
                "topics_total": len(catalog_topics),
            })
    return breakdown


# --- GET /api/metrics/summary parsing ---------------------------------------------------


def _parse_quota(raw: dict[str, Any]) -> QuotaInfo:
    return QuotaInfo(
        hours=raw["hours"],
        target_min=raw["targetMin"],
        target_max=raw["targetMax"],
        percent=raw["percent"],
        warning=raw["warning"],
        missing_hours=raw["missingHours"],
    )


def _parse_forecast(raw: dict[str, Any]) -> tuple[date | None, float | None]:
    if not raw.get("available"):
        return None, None
    forecast_date = _parse_date(raw["date"]) if raw.get("date") else None
    return forecast_date, raw.get("recentWeeklyHours")


def _parse_next_course_goal(raw: dict[str, Any]) -> NextCourseGoal:
    return NextCourseGoal(
        course_id=raw["courseId"],
        course_name=raw["courseName"],
        target_date=_parse_date(raw["targetDate"]),
        days_remaining=raw["daysLeft"],
    )


def _parse_course_hours(raw: list[dict[str, Any]]) -> list[CourseHours]:
    return [
        CourseHours(
            course_id=c["courseId"],
            course_name=c["courseName"],
            course_color=c["courseColor"],
            hours=c["hours"],
            sessions=c["sessionCount"],
        )
        for c in raw
    ]


def _parse_neglected_course(raw: dict[str, Any] | None) -> NeglectedCourse | None:
    if raw is None:
        return None
    return NeglectedCourse(
        course_id=raw["courseId"],
        course_name=raw["courseName"],
        last_studied=_parse_date(raw["lastStudied"]) if raw.get("lastStudied") else None,
        days_since=raw.get("daysSince"),
    )


def _parse_weekly_report(raw: dict[str, Any]) -> WeeklyReport:
    return WeeklyReport(
        week_id=raw["weekId"],
        hours=raw["hours"],
        delta_vs_previous_week_hours=raw["deltaVsPreviousWeek"],
        top_course=raw.get("topCourseName"),
        sessions_count=raw["sessionCount"],
    )


def _program_data_from_summary(
    program: StudyProgram,
    is_active: bool,
    courses: list[dict[str, Any]],
    course_goals: list[dict[str, Any]],
    raw_summary: dict[str, Any],
) -> StudyLifeProgramData:
    """Maps ONE GET /api/metrics/summary?program={id} response onto StudyLifeProgramData -
    the per-programme replacement for the old `_build_program_data` (which re-derived all of
    this from raw sessions/settings client-side). `courses`/`course_goals` still come from
    the existing per-programme catalog fetch/global-goals partition (unrelated to metrics,
    kept as-is - see the module docstring)."""
    forecast_date, forecast_recent_weekly_hours = _parse_forecast(raw_summary["forecast"])
    upcoming_course_goals = [_parse_next_course_goal(g) for g in raw_summary["upcomingCourseGoals"]]
    hours = raw_summary["hours"]
    return StudyLifeProgramData(
        program=program,
        is_active=is_active,
        courses=courses,
        course_goals=course_goals,
        ects_earned=raw_summary["ects"]["earned"],
        ects_total=raw_summary["ects"]["total"],
        average_grade=raw_summary["averageGrade"],
        streak_days=raw_summary["streak"]["current"],
        longest_streak_days=raw_summary["streak"]["longest"],
        week_hours=hours["week"],
        week_quota=_parse_quota(raw_summary["weekQuota"]),
        month_hours=hours["month"],
        month_quota=_parse_quota(raw_summary["monthQuota"]),
        total_hours=hours["total"],
        total_sessions=hours["totalSessions"],
        next_course_goal=upcoming_course_goals[0] if upcoming_course_goals else None,
        upcoming_course_goals=upcoming_course_goals,
        forecast_date=forecast_date,
        forecast_recent_weekly_hours=forecast_recent_weekly_hours,
    )


# --- GET /api/metrics/achievements parsing ----------------------------------------------

# Icon + English name template per AchievementCatalog category key (src/StudyLife.Shared/
# AchievementCatalog.cs in the studylife repo - "hours"/"streak"/"sessions"/"courses"/
# "allcourses" are its pre-existing push-notification keys, the other 8 were added
# alongside GET /api/metrics/achievements for the categories that don't push). The endpoint
# itself sends no name/icon (see the C# Achievement record) - this is the HA-side i18n/
# presentation layer _calc_achievements used to build inline, now just attached to the
# endpoint's {category, threshold, unlocked, current} tiers instead of computed from raw
# session history.
def _fmt_tier(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


_ACHIEVEMENT_META: dict[str, tuple[str, Callable[[float], str]]] = {
    "hours": ("⏱", lambda t: f"{_fmt_tier(t)}h studied"),
    "streak": ("🔥", lambda t: f"{_fmt_tier(t)}-day streak"),
    "sessions": ("✅", lambda t: f"{_fmt_tier(t)} sessions"),
    "courses": ("🎓", lambda t: f"{_fmt_tier(t)} courses completed"),
    "allcourses": ("🏆", lambda t: "All courses completed"),
    "earlybird": ("🌅", lambda t: f"{_fmt_tier(t)} early-bird sessions"),
    "nightowl": ("🦉", lambda t: f"{_fmt_tier(t)} night-owl sessions"),
    "weekend": ("🏖", lambda t: f"{_fmt_tier(t)} weekend sessions"),
    "marathon": ("🏃", lambda t: f"{_fmt_tier(t)}h marathon session"),
    "perfectweek": ("📅", lambda t: f"{_fmt_tier(t)} perfect weeks"),
    "notes": ("📝", lambda t: f"{_fmt_tier(t)} notes written"),
    "coursediversity": ("🎯", lambda t: f"{_fmt_tier(t)} courses in one week"),
    "programs": ("🏅", lambda t: f"{_fmt_tier(t)} study programme(s) completed"),
}


def _to_achievement(tier: dict[str, Any]) -> Achievement:
    category = tier["category"]
    icon, name_fn = _ACHIEVEMENT_META.get(
        category, ("🏅", lambda t: f"{_fmt_tier(t)} {category}")
    )
    threshold = tier["threshold"]
    return Achievement(
        icon=icon,
        name=name_fn(threshold),
        unlocked=tier["unlocked"],
        current=tier["current"],
        threshold=threshold,
        category=category,
    )


def _parse_achievements(raw: dict[str, Any]) -> tuple[list[Achievement], int]:
    achievements = [_to_achievement(t) for t in raw.get("tiers", [])]
    return achievements, raw["unlocked"]


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
            # Long-range history is now only needed for two things that genuinely need raw
            # session data rather than a pre-computed metric: `week_sessions` (the RAW list/
            # count for its own sensor, not the week_hours float - see _week_start) and
            # inactivity_warning/days_since_last_session (InactivityReminderService's own
            # "last session ever" lookup, which can reach further back than any metric needs).
            raw_history = await self._client.async_get_session_history(SESSION_HISTORY_DAYS, only_completed=False)
            settings = await self._client.async_get_settings()
            raw_notes = await self._client.async_get_notes()
            raw_course_goals = await self._client.async_get_course_goals()
            raw_timer_state = await self._client.async_get_timer_state()
            raw_study_programs = await self._client.async_get_study_programs()
            # One catalog + one metrics-summary request per programme (?program=0 for the
            # built-in one, per CoursesController's/MetricsController's shared convention).
            # Sessions/goals stay single global fetches and are partitioned client-side, so
            # these are the only per-programme requests - and each programme-specific URL has
            # its own ETag cache entry in the client (courses only; the metrics endpoint isn't
            # cached, see async_get_metrics_summary's docstring).
            raw_courses_by_key: dict[str, list[dict[str, Any]]] = {}
            raw_summary_by_key: dict[str, dict[str, Any]] = {}
            for raw_program in raw_study_programs:
                pid = raw_program.get("id")
                key = program_key(pid)
                resolved_pid = pid if pid is not None else 0
                raw_courses_by_key[key] = await self._client.async_get_courses(resolved_pid)
                raw_summary_by_key[key] = await self._client.async_get_metrics_summary(resolved_pid)

            # Resolved here (pure - no I/O, just parsing raw_study_programs/settings already
            # fetched above) rather than after the try block, so the achievements fetch right
            # below can use the POST-FALLBACK active id: a stale activeStudyProgramId (e.g. a
            # just-deleted custom programme) must fall back to the built-in programme the same
            # way active_study_program itself does, not 404 against the stale id.
            study_programs = [_to_study_program(p) for p in raw_study_programs]
            active_program_id = settings.get("activeStudyProgramId")
            active_study_program = next(
                (p for p in study_programs if p.id == active_program_id),
                # Defensive fallback (e.g. stale/unknown id): the built-in entry is always
                # first and always present, mirroring CoursesController's own fallback.
                study_programs[0] if study_programs else StudyProgram(None, "StudyLife", True, False),
            )
            raw_achievements = await self._client.async_get_metrics_achievements(
                active_study_program.id if active_study_program.id is not None else 0
            )
        except StudyLifeApiAuthError as err:
            # Genuine 401: both current and previous key rejected - normally only possible
            # when HA was offline longer than the 10-day grace window of a key rotation.
            # ConfigEntryAuthFailed makes HA show an actionable "re-authenticate" repair
            # that opens the reauth step in config_flow.py, instead of silently failing
            # every poll cycle.
            raise ConfigEntryAuthFailed(str(err)) from err
        except StudyLifeApiError as err:
            # Also covers StudyLifeApiEndpointMissingError (a 404 from either new metrics
            # endpoint, i.e. a too-old StudyLife server) - its message is already specific
            # and actionable, so it needs no special-casing here: UpdateFailed(str(err))
            # surfaces it verbatim as a Home Assistant repair/notification instead of a
            # generic, cryptic failure.
            raise UpdateFailed(str(err)) from err

        achievements, achievements_unlocked = _parse_achievements(raw_achievements)
        active_key = program_key(active_study_program.id)
        course_ids_by_key = {key: {c["id"] for c in courses} for key, courses in raw_courses_by_key.items()}

        # The ACTIVE programme's catalog, for the hub-device entities/calendars/services -
        # same list a parameterless GET /api/courses would resolve from the settings, just
        # reused from the per-programme fetch above instead of a redundant extra request.
        courses = raw_courses_by_key.get(active_key, [])

        # /api/coursegoals returns EVERY goal ever set, across ALL study programmes - course
        # ids stay globally unique (see StudyProgramCatalog.CustomCourseIdOffset server-side),
        # so a grade/target-date set while a different programme was active would otherwise
        # silently leak into average_grade/next_course_goal below. Filtering against the
        # active catalog here scopes every downstream consumer of `course_goals` in one
        # place; the per-programme datasets partition the same raw list per programme.
        active_course_ids = course_ids_by_key.get(active_key, set())
        course_goals = [g for g in raw_course_goals if g["courseId"] in active_course_ids]

        sessions = [_to_session(s) for s in raw_sessions]
        now = dt_util.now().replace(tzinfo=None)
        today = now.date()

        # Long-range history (all sessions, not just completed) - see the fetch comment
        # above for what still needs it. `sessions` stays the source for today/active/upcoming.
        history = [_to_session(s) for s in raw_history]

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

        # Raw list/count of this week's sessions (Mon-Sun, upper-bounded like the old
        # week_hours filter used to be) - NOT a metric, just a date-range view over `history`
        # for the week_sessions sensor. week_hours itself (the float) comes from the active
        # programme's metrics summary below.
        week_start = _week_start(today)
        week_end = week_start + timedelta(days=7)
        week_sessions = sorted(
            (s for s in history if week_start <= s.start.date() < week_end), key=lambda s: s.start
        )

        # Notes visible to the active programme: general notes without a courseId always
        # stay visible, course-bound ones only if the course belongs to the active study
        # programme - mirrors the scope in Notes.razor/Index.Insights.razor.cs.
        notes = [n for n in raw_notes if not n.get("courseId") or n["courseId"] in active_course_ids]
        latest_note = notes[0] if notes else None

        # Per-programme stats for EVERY programme (built-in + custom, completed or not) -
        # one GET /api/metrics/summary?program={id} call each, already fetched above inside
        # the try block. The active programme's own entry is reused below for every
        # top-level, cross-programme-shaped StudyLifeData field, so its numbers are
        # guaranteed identical between the hub device and its own programme device -
        # there is exactly one source of truth for them (the same HTTP response), not two
        # independently-computed copies like the old client-side version had.
        programs: dict[str, StudyLifeProgramData] = {}
        for program in study_programs:
            key = program_key(program.id)
            programs[key] = _program_data_from_summary(
                program=program,
                is_active=program.id == active_study_program.id,
                courses=raw_courses_by_key.get(key, []),
                course_goals=[g for g in raw_course_goals if g["courseId"] in course_ids_by_key.get(key, set())],
                raw_summary=raw_summary_by_key[key],
            )

        active_program_data = programs.get(active_key)
        if active_program_data is None:
            # Should not happen in practice - the server's GET /api/studyprograms always
            # returns at least the synthetic built-in entry (see the StudyProgram
            # dataclass docstring) - but a malformed/empty response must degrade to a
            # loud, comprehensible UpdateFailed instead of an unhandled KeyError crash.
            raise UpdateFailed(
                "StudyLife server returned no study programmes (GET /api/studyprograms "
                "was empty) - cannot determine the active programme's metrics"
            )
        active_summary = raw_summary_by_key[active_key]

        weekly_report = _parse_weekly_report(active_summary["weeklyReport"])
        neglected_course = _parse_neglected_course(active_summary["neglectedCourse"])
        course_hours = _parse_course_hours(active_summary["courseHours"])
        topics_completed = active_summary["topics"]["completed"]
        topics_total = active_summary["topics"]["total"]
        month_comparison = active_summary["monthComparison"]

        # previous_week_hours is, by construction, the same "last COMPLETED Mon-Sun week"
        # weekly_report already reports (both the old client-side calc and StudyMetrics'
        # weekly-report function use the exact same week boundary) - reusing it here avoids
        # asking the server for the same number twice under two different names.
        previous_week_hours = weekly_report.hours
        week_delta_hours = active_program_data.week_hours - previous_week_hours

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
            week_hours=round(active_program_data.week_hours, 2),
            previous_week_hours=round(previous_week_hours, 2),
            week_delta_hours=round(week_delta_hours, 2),
            streak_days=active_program_data.streak_days,
            week_quota=active_program_data.week_quota,
            month_quota=active_program_data.month_quota,
            course_goals=course_goals,
            average_grade=active_program_data.average_grade,
            course_hours=course_hours,
            total_hours=active_program_data.total_hours,
            total_sessions=active_program_data.total_sessions,
            next_course_goal=active_program_data.next_course_goal,
            upcoming_course_goals=active_program_data.upcoming_course_goals,
            neglected_course=neglected_course,
            courses=courses,
            timer_state=_to_timer_state(raw_timer_state),
            ects_earned=active_program_data.ects_earned,
            ects_total=active_program_data.ects_total,
            longest_streak_days=active_program_data.longest_streak_days,
            achievements=achievements,
            achievements_unlocked=achievements_unlocked,
            topics_completed=topics_completed,
            topics_total=topics_total,
            topics_by_course=_topics_by_course(course_goals, courses),
            days_since_last_session=days_since_last_session,
            inactivity_warning=inactivity_warning,
            forecast_date=active_program_data.forecast_date,
            forecast_recent_weekly_hours=active_program_data.forecast_recent_weekly_hours,
            month_hours_current=month_comparison["currentMonthHours"],
            month_hours_delta_vs_last_month=month_comparison["deltaVsPreviousMonth"],
            month_hours_delta_vs_last_year=month_comparison.get("deltaVsLastYear"),
            weekly_report=weekly_report,
            study_programs=study_programs,
            active_study_program=active_study_program,
            programs=programs,
        )
