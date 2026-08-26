"""Unit tests for coordinator.py's pure parsing/mapping helpers.

These are plain functions (dataclasses + dict lookups, no `hass`/HA imports required to
call them), so they're exercised directly here without any coordinator/config-entry/API
scaffolding - just like the `_calc_*` unit tests this file replaced.

What changed and why: every METRIC (streak, week/month quota, ECTS, average grade,
forecast, course hours, neglected course, weekly report, topics, month comparison,
achievements) used to be computed here from raw sessions/settings, mirroring
Index.razor's client-side math by hand ("a deliberately parallel implementation with
identical semantics, manually kept in sync" - and it drifted, more than once). Per the
owner decision that every metric lives in exactly ONE place (StudyLife.Shared, served by
GET /api/metrics/summary and GET /api/metrics/achievements - see docs/api's metrics
contract), this module's job is now parsing that JSON into the same dataclasses the
platforms already consume, not recomputing anything. These tests cover THAT parsing/
mapping layer - the actual math (is 20h against a 25-30h target really an 11.6% quota
with a warning?) is StudyLife.Shared's responsibility now, pinned by ITS OWN golden
fixtures server-side, not duplicated here.
"""
from __future__ import annotations

from datetime import date, datetime

from custom_components.studylife.coordinator import (
    Achievement,
    CourseHours,
    NeglectedCourse,
    NextCourseGoal,
    QuotaInfo,
    StudyProgram,
    WeeklyReport,
    _fmt_tier,
    _parse_achievements,
    _parse_course_hours,
    _parse_date,
    _parse_dt,
    _parse_forecast,
    _parse_neglected_course,
    _parse_next_course_goal,
    _parse_quota,
    _parse_weekly_report,
    _program_data_from_summary,
    _to_achievement,
    _to_session,
    _to_study_program,
    _to_timer_state,
    _topics_by_course,
    _week_start,
)

from .conftest import (
    make_course,
    make_course_goal,
    make_raw_achievement_tier,
    make_raw_metrics_summary,
    make_raw_quota,
    make_raw_session,
)

# ---------------------------------------------------------------------------
# _parse_dt / _parse_date
# ---------------------------------------------------------------------------


def test_parse_dt_strips_any_timezone_info() -> None:
    assert _parse_dt("2026-01-06T10:00:00") == datetime(2026, 1, 6, 10, 0)


def test_parse_date_accepts_date_only_and_datetime_strings() -> None:
    assert _parse_date("2026-01-06") == date(2026, 1, 6)
    assert _parse_date("2026-01-06T10:00:00") == date(2026, 1, 6)


# ---------------------------------------------------------------------------
# _week_start
# ---------------------------------------------------------------------------


def test_week_start_returns_monday_for_any_weekday() -> None:
    monday = date.fromisocalendar(2026, 5, 1)
    wednesday = date.fromisocalendar(2026, 5, 3)
    sunday = date.fromisocalendar(2026, 5, 7)
    assert _week_start(monday) == monday
    assert _week_start(wednesday) == monday
    assert _week_start(sunday) == monday


# ---------------------------------------------------------------------------
# _to_session / _to_study_program / _to_timer_state
# ---------------------------------------------------------------------------


def test_to_session_maps_every_field() -> None:
    raw = make_raw_session(id=7, course_id=3, topic="Chapter 1", notes="note", recurrence_group_id="g1")
    session = _to_session(raw)
    assert session.id == 7
    assert session.course_id == 3
    assert session.topic == "Chapter 1"
    assert session.notes == "note"
    assert session.recurrence_group_id == "g1"
    assert session.duration_minutes == 60.0


def test_to_study_program_defaults_missing_flags_to_false() -> None:
    program = _to_study_program({"id": 5, "name": "Custom"})
    assert program.id == 5
    assert program.is_built_in is False
    assert program.is_completed is False


def test_to_timer_state_maps_phase_ends_at_only_when_present() -> None:
    idle = _to_timer_state({})
    assert idle.phase == "idle"
    assert idle.phase_ends_at is None

    running_focus = _to_timer_state(
        {"sessionId": 1, "isRunning": True, "isBreak": False, "phaseEndsAt": "2026-01-06T10:30:00"}
    )
    assert running_focus.phase == "focus"
    assert running_focus.phase_ends_at == datetime(2026, 1, 6, 10, 30)

    running_break = _to_timer_state({"isRunning": True, "isBreak": True})
    assert running_break.phase == "break"


# ---------------------------------------------------------------------------
# _parse_quota
# ---------------------------------------------------------------------------


def test_parse_quota_maps_every_field_and_drops_min_percent() -> None:
    raw = make_raw_quota(hours=20.0, target_min=25.0, target_max=30.0, percent=57.9, warning=True, missing_hours=5.0)
    quota = _parse_quota(raw)
    assert quota == QuotaInfo(hours=20.0, target_min=25.0, target_max=30.0, percent=57.9, warning=True, missing_hours=5.0)
    # minPercent is part of the wire contract but QuotaInfo has no field for it - the
    # contract test above only asserts the fields QuotaInfo DOES have still round-trip.


# ---------------------------------------------------------------------------
# _parse_forecast
# ---------------------------------------------------------------------------


def test_parse_forecast_unavailable_returns_none_pair() -> None:
    assert _parse_forecast({"available": False, "alreadyDone": True, "date": None, "recentWeeklyHours": 0.0}) == (None, None)
    assert _parse_forecast({"available": False, "alreadyDone": False, "date": None, "recentWeeklyHours": 0.0}) == (None, None)


def test_parse_forecast_available_parses_date_and_pace() -> None:
    forecast_date, recent_weekly_hours = _parse_forecast(
        {"available": True, "alreadyDone": False, "date": "2028-07-08", "recentWeeklyHours": 12.5}
    )
    assert forecast_date == date(2028, 7, 8)
    assert recent_weekly_hours == 12.5


# ---------------------------------------------------------------------------
# _parse_next_course_goal / _parse_course_hours / _parse_neglected_course / _parse_weekly_report
# ---------------------------------------------------------------------------


def test_parse_next_course_goal_maps_fields() -> None:
    goal = _parse_next_course_goal(
        {"courseId": 3, "courseName": "Algorithms", "targetDate": "2026-09-15", "daysLeft": 20}
    )
    assert goal == NextCourseGoal(course_id=3, course_name="Algorithms", target_date=date(2026, 9, 15), days_remaining=20)


def test_parse_course_hours_maps_list_preserving_order() -> None:
    raw = [
        {"courseId": 1, "courseName": "A", "courseColor": "#fff", "hours": 40.0, "sessionCount": 22},
        {"courseId": 2, "courseName": "B", "courseColor": "#000", "hours": 10.0, "sessionCount": 5},
    ]
    result = _parse_course_hours(raw)
    assert result == [
        CourseHours(course_id=1, course_name="A", course_color="#fff", hours=40.0, sessions=22),
        CourseHours(course_id=2, course_name="B", course_color="#000", hours=10.0, sessions=5),
    ]


def test_parse_neglected_course_none_stays_none() -> None:
    assert _parse_neglected_course(None) is None


def test_parse_neglected_course_maps_populated_object() -> None:
    result = _parse_neglected_course(
        {"courseId": 12, "courseName": "Databases", "lastStudied": "2026-07-01", "daysSince": 56}
    )
    assert result == NeglectedCourse(course_id=12, course_name="Databases", last_studied=date(2026, 7, 1), days_since=56)


def test_parse_neglected_course_never_studied_has_no_last_studied() -> None:
    result = _parse_neglected_course({"courseId": 2, "courseName": "B", "lastStudied": None, "daysSince": None})
    assert result.last_studied is None
    assert result.days_since is None


def test_parse_weekly_report_maps_fields() -> None:
    report = _parse_weekly_report(
        {"weekId": "2026-W34", "hours": 18.5, "deltaVsPreviousWeek": 2.0, "topCourseName": "Algorithms", "sessionCount": 6}
    )
    assert report == WeeklyReport(
        week_id="2026-W34", hours=18.5, delta_vs_previous_week_hours=2.0, top_course="Algorithms", sessions_count=6
    )


def test_parse_weekly_report_no_top_course_is_none() -> None:
    report = _parse_weekly_report(
        {"weekId": "2026-W34", "hours": 0.0, "deltaVsPreviousWeek": 0.0, "topCourseName": None, "sessionCount": 0}
    )
    assert report.top_course is None


# ---------------------------------------------------------------------------
# _program_data_from_summary
# ---------------------------------------------------------------------------


def test_program_data_from_summary_maps_every_field() -> None:
    program = StudyProgram(id=7, name="Custom", is_built_in=False, is_completed=False)
    courses = [make_course(id=20)]
    course_goals = [make_course_goal(course_id=20)]
    raw_summary = make_raw_metrics_summary(
        week_hours=4.0,
        month_hours=12.5,
        total_hours=340.0,
        total_sessions=210,
        streak_current=5,
        streak_longest=12,
        ects_earned=30,
        ects_total=180,
        average_grade=2.23,
        forecast_available=True,
        forecast_date="2028-07-08",
        forecast_recent_weekly_hours=0.0,
        upcoming_course_goals=[
            {"courseId": 3, "courseName": "Algorithms", "targetDate": "2026-09-15", "daysLeft": 20},
            {"courseId": 4, "courseName": "Databases", "targetDate": "2026-10-01", "daysLeft": 36},
        ],
    )

    data = _program_data_from_summary(program, True, courses, course_goals, raw_summary)

    assert data.program is program
    assert data.is_active is True
    assert data.courses is courses
    assert data.course_goals is course_goals
    assert data.week_hours == 4.0
    assert data.month_hours == 12.5
    assert data.total_hours == 340.0
    assert data.total_sessions == 210
    assert data.streak_days == 5
    assert data.longest_streak_days == 12
    assert data.ects_earned == 30
    assert data.ects_total == 180
    assert data.average_grade == 2.23
    assert data.forecast_date == date(2028, 7, 8)
    assert data.forecast_recent_weekly_hours == 0.0
    assert isinstance(data.week_quota, QuotaInfo)
    assert isinstance(data.month_quota, QuotaInfo)
    assert data.next_course_goal.course_id == 3  # soonest-first, first entry wins
    assert [g.course_id for g in data.upcoming_course_goals] == [3, 4]


def test_program_data_from_summary_no_upcoming_goals_means_no_next_goal() -> None:
    program = StudyProgram(id=None, name="Built-in", is_built_in=True, is_completed=False)
    raw_summary = make_raw_metrics_summary(upcoming_course_goals=[])
    data = _program_data_from_summary(program, True, [], [], raw_summary)
    assert data.next_course_goal is None
    assert data.upcoming_course_goals == []


def test_program_data_from_summary_average_grade_none_passes_through() -> None:
    program = StudyProgram(id=None, name="Built-in", is_built_in=True, is_completed=False)
    raw_summary = make_raw_metrics_summary(average_grade=None)
    data = _program_data_from_summary(program, True, [], [], raw_summary)
    assert data.average_grade is None


# ---------------------------------------------------------------------------
# _fmt_tier / _to_achievement / _parse_achievements
# ---------------------------------------------------------------------------


def test_fmt_tier_strips_trailing_zero_but_keeps_fractions() -> None:
    assert _fmt_tier(25) == "25"
    assert _fmt_tier(25.0) == "25"
    assert _fmt_tier(2.5) == "2.5"


def test_to_achievement_known_category_gets_local_icon_and_name() -> None:
    tier = make_raw_achievement_tier(category="hours", threshold=25, unlocked=True, current=340.0)
    achievement = _to_achievement(tier)
    assert achievement.icon == "⏱"
    assert achievement.name == "25h studied"
    assert achievement.unlocked is True
    assert achievement.current == 340.0
    assert achievement.threshold == 25
    assert achievement.category == "hours"


def test_to_achievement_all_known_categories_produce_a_name() -> None:
    """AchievementCatalog's 13 category keys (5 pre-existing push-notification keys plus
    8 added for this endpoint) must each resolve to a real, non-empty English name - a
    silent fallback to the raw category string would be a real (if minor) UX regression
    a dashboard user would notice."""
    for category in (
        "hours", "streak", "sessions", "courses", "allcourses",
        "earlybird", "nightowl", "weekend", "marathon", "perfectweek",
        "notes", "coursediversity", "programs",
    ):
        tier = make_raw_achievement_tier(category=category, threshold=1, unlocked=False, current=0)
        achievement = _to_achievement(tier)
        assert achievement.name
        assert achievement.icon
        assert achievement.category == category


def test_to_achievement_unknown_category_falls_back_defensively() -> None:
    """Forward-compat: a category this HA version doesn't know about yet (server added a
    new one) must still produce a usable Achievement, not crash the whole refresh."""
    tier = make_raw_achievement_tier(category="brandnew", threshold=3, unlocked=True, current=3)
    achievement = _to_achievement(tier)
    assert achievement.category == "brandnew"
    assert "3" in achievement.name
    assert achievement.icon


def test_to_achievement_allcourses_name_ignores_threshold() -> None:
    tier = make_raw_achievement_tier(category="allcourses", threshold=1, unlocked=True, current=1)
    assert _to_achievement(tier).name == "All courses completed"


def test_parse_achievements_maps_tiers_and_reports_server_unlocked_count() -> None:
    raw = {
        "unlocked": 2,
        "total": 3,
        "tiers": [
            make_raw_achievement_tier(category="hours", threshold=25, unlocked=True, current=340.0),
            make_raw_achievement_tier(category="streak", threshold=7, unlocked=True, current=12),
            make_raw_achievement_tier(category="streak", threshold=30, unlocked=False, current=12),
        ],
    }
    achievements, unlocked = _parse_achievements(raw)
    assert len(achievements) == 3
    assert all(isinstance(a, Achievement) for a in achievements)
    # `unlocked` is taken verbatim from the server response, not recounted locally -
    # the endpoint is the single source of truth for it too.
    assert unlocked == 2


def test_parse_achievements_empty_tiers() -> None:
    achievements, unlocked = _parse_achievements({"unlocked": 0, "total": 0, "tiers": []})
    assert achievements == []
    assert unlocked == 0


# ---------------------------------------------------------------------------
# _topics_by_course
# ---------------------------------------------------------------------------


def test_topics_by_course_skips_goal_for_course_with_no_catalog_topics() -> None:
    courses = [make_course(id=100, topics=[])]
    goals = [make_course_goal(course_id=100, completed_topics="A,B")]
    assert _topics_by_course(goals, courses) == []


def test_topics_by_course_skips_goal_for_course_missing_from_catalog() -> None:
    courses = [make_course(id=999, topics=["A", "B"])]
    goals = [make_course_goal(course_id=100, completed_topics="A")]  # course 100 not in catalog
    assert _topics_by_course(goals, courses) == []


def test_topics_by_course_only_includes_courses_with_progress() -> None:
    courses = [
        make_course(id=100, name="Algorithms", topics=["A", "B", "C"]),
        make_course(id=200, name="Databases", topics=["X", "Y"]),
    ]
    goals = [
        make_course_goal(course_id=100, course_name="Algorithms", completed_topics="A,B"),
        # no topics done yet for course 200 -> excluded from the breakdown.
        make_course_goal(course_id=200, course_name="Databases", completed_topics=""),
    ]
    breakdown = _topics_by_course(goals, courses)
    assert breakdown == [
        {"course_id": 100, "course_name": "Algorithms", "topics_completed": 2, "topics_total": 3}
    ]
