"""Unit tests for the pure `_calc_*` helper functions in coordinator.py.

These are plain functions (dataclasses + arithmetic, no `hass`/HA imports
required to call them), so they're exercised directly here without any
coordinator/config-entry/API scaffolding - `today`/`now` are passed in
explicitly by every function under test, so there's no real clock to freeze.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from custom_components.studylife.coordinator import (
    Achievement,
    StudyProgram,
    _calc_achievements,
    _calc_average_grade,
    _calc_course_hours,
    _calc_ects_progress,
    _calc_forecast,
    _calc_longest_streak,
    _calc_month_comparison,
    _calc_month_quota,
    _calc_neglected_course,
    _calc_streak,
    _calc_topics_progress,
    _calc_upcoming_course_goals,
    _calc_week_quota,
    _calc_weekly_report,
    _week_start,
)

from .conftest import make_course, make_course_goal, make_session


def _find(achievements: list[Achievement], category: str, threshold: float) -> Achievement:
    for a in achievements:
        if a.category == category and a.threshold == threshold:
            return a
    raise AssertionError(f"no achievement found for category={category!r} threshold={threshold!r}")


# ---------------------------------------------------------------------------
# _calc_streak
# ---------------------------------------------------------------------------


def test_calc_streak_no_sessions_is_zero() -> None:
    assert _calc_streak([], date(2026, 1, 10)) == 0


def test_calc_streak_counts_back_from_today() -> None:
    today = date(2026, 1, 10)
    sessions = [
        make_session(start=datetime.combine(today - timedelta(days=2), time(9, 0))),
        make_session(start=datetime.combine(today - timedelta(days=1), time(9, 0))),
        make_session(start=datetime.combine(today, time(9, 0))),
    ]
    assert _calc_streak(sessions, today) == 3


def test_calc_streak_stays_alive_when_today_has_no_session_yet() -> None:
    """Today has no session but yesterday (and the day before) does - the
    streak must still count back from yesterday instead of resetting to 0."""
    today = date(2026, 1, 10)
    sessions = [
        make_session(start=datetime.combine(today - timedelta(days=3), time(9, 0))),
        make_session(start=datetime.combine(today - timedelta(days=2), time(9, 0))),
        make_session(start=datetime.combine(today - timedelta(days=1), time(9, 0))),
    ]
    assert _calc_streak(sessions, today) == 3


def test_calc_streak_gap_breaks_the_streak() -> None:
    today = date(2026, 1, 10)
    sessions = [
        make_session(start=datetime.combine(today - timedelta(days=5), time(9, 0))),
        # gap at today-4 .. today-2
        make_session(start=datetime.combine(today - timedelta(days=1), time(9, 0))),
        make_session(start=datetime.combine(today, time(9, 0))),
    ]
    assert _calc_streak(sessions, today) == 2


# ---------------------------------------------------------------------------
# _calc_longest_streak
# ---------------------------------------------------------------------------


def test_calc_longest_streak_empty_is_zero() -> None:
    assert _calc_longest_streak([]) == 0


def test_calc_longest_streak_single_session_is_one() -> None:
    assert _calc_longest_streak([make_session()]) == 1


def test_calc_longest_streak_consecutive_run() -> None:
    base = date(2026, 1, 1)
    sessions = [
        make_session(id=i, start=datetime.combine(base + timedelta(days=i), time(9, 0)))
        for i in range(4)
    ]
    assert _calc_longest_streak(sessions) == 4


def test_calc_longest_streak_gap_resets_current_but_keeps_best() -> None:
    """A bigger run earlier in history must still win over a smaller, more
    recent run - `longest` tracks the best-ever run, not the last one."""
    base = date(2026, 1, 1)
    # 5-day run, then a gap, then a 2-day run.
    dates = [base + timedelta(days=i) for i in range(5)] + [
        base + timedelta(days=20),
        base + timedelta(days=21),
    ]
    sessions = [
        make_session(id=i, start=datetime.combine(d, time(9, 0))) for i, d in enumerate(dates)
    ]
    assert _calc_longest_streak(sessions) == 5


def test_calc_longest_streak_later_bigger_run_overtakes_earlier_one() -> None:
    """Best-ever tracking must also work in the other direction: a bigger run
    that comes AFTER a smaller one must still update `longest`."""
    base = date(2026, 1, 1)
    dates = [base, base + timedelta(days=1)]  # run of 2
    dates += [base + timedelta(days=10 + i) for i in range(5)]  # run of 5
    sessions = [
        make_session(id=i, start=datetime.combine(d, time(9, 0))) for i, d in enumerate(dates)
    ]
    assert _calc_longest_streak(sessions) == 5


# ---------------------------------------------------------------------------
# _calc_achievements
# ---------------------------------------------------------------------------


def _hours_session(hours: float, start: datetime = datetime(2026, 1, 1, 0, 0)) -> list:
    return [make_session(start=start, end=start + timedelta(hours=hours))]


def test_calc_achievements_hours_tier_crosses_at_right_threshold() -> None:
    # 24h59m -> rounds to 24.98, still under 25.
    under = _calc_achievements(
        [make_session(start=datetime(2026, 1, 1, 0, 0), end=datetime(2026, 1, 1, 0, 0) + timedelta(hours=24, minutes=59))],
        {}, set(), 0, [], 0, 0,
    )
    assert _find(under, "hours", 25).unlocked is False

    at = _calc_achievements(_hours_session(25), {}, set(), 0, [], 0, 0)
    tier25 = _find(at, "hours", 25)
    assert tier25.unlocked is True
    assert tier25.current == 25.0
    assert _find(at, "hours", 100).unlocked is False


def test_calc_achievements_streak_tier_uses_longest_not_current() -> None:
    """_calc_achievements has no `today` parameter at all, so it cannot know a
    "current, still-alive" streak - it must (and does) use `_calc_longest_streak`
    over the whole history instead. A 7-day run deep in the past, with no
    trailing activity, still has to unlock the 7-day streak tier."""
    base = date(2020, 1, 1)
    sessions = [
        make_session(id=i, start=datetime.combine(base + timedelta(days=i), time(9, 0)))
        for i in range(7)
    ]
    achievements = _calc_achievements(sessions, {}, set(), 0, [], 0, 0)
    tier7 = _find(achievements, "streak", 7)
    assert tier7.current == 7
    assert tier7.unlocked is True
    assert _find(achievements, "streak", 30).unlocked is False


def test_calc_achievements_courses_completed_excludes_other_programme() -> None:
    """settings.completedCourseIds is a flat, cross-programme field - a course
    id completed while a DIFFERENT programme was active must not inflate this
    programme's courses_completed tally. Documented bug-fix in the source."""
    active_course_ids = {100, 101}
    settings = {"completedCourseIds": [100, 200]}  # 200 belongs to another programme
    achievements = _calc_achievements([], settings, active_course_ids, 0, [], 0, 0)
    tier1 = _find(achievements, "courses_completed", 1)
    assert tier1.current == 1  # only the intersection {100}, NOT len([100, 200]) == 2
    assert tier1.unlocked is True
    assert _find(achievements, "courses_completed", 10).unlocked is False


def test_calc_achievements_perfect_weeks_zero_when_weekly_goal_missing() -> None:
    week1_monday = date.fromisocalendar(2026, 2, 1)
    sessions = [
        make_session(start=datetime.combine(week1_monday, time(9, 0)), end=datetime.combine(week1_monday, time(21, 0))),
    ]
    for settings in ({}, {"weeklyGoalMinHours": 0}, {"weeklyGoalMinHours": None}):
        achievements = _calc_achievements(sessions, settings, set(), 0, [], 0, 0)
        tier1 = _find(achievements, "perfect_weeks", 1)
        assert tier1.current == 0
        assert tier1.unlocked is False


def test_calc_achievements_perfect_weeks_counts_weeks_meeting_goal() -> None:
    week1_monday = date.fromisocalendar(2026, 2, 1)
    week2_monday = date.fromisocalendar(2026, 5, 1)
    sessions = [
        # week 1: 12h total, meets the 10h goal.
        make_session(id=1, start=datetime.combine(week1_monday, time(9, 0)), end=datetime.combine(week1_monday, time(15, 0))),
        make_session(id=2, start=datetime.combine(week1_monday + timedelta(days=1), time(9, 0)), end=datetime.combine(week1_monday + timedelta(days=1), time(15, 0))),
        # week 2: 3h total, does NOT meet the goal.
        make_session(id=3, start=datetime.combine(week2_monday, time(9, 0)), end=datetime.combine(week2_monday, time(12, 0))),
    ]
    settings = {"weeklyGoalMinHours": 10}
    achievements = _calc_achievements(sessions, settings, set(), 0, [], 0, 0)
    tier1 = _find(achievements, "perfect_weeks", 1)
    assert tier1.current == 1
    assert tier1.unlocked is True
    assert _find(achievements, "perfect_weeks", 4).unlocked is False


def test_calc_achievements_early_bird_night_owl_weekend_warrior_counts() -> None:
    monday = date.fromisocalendar(2026, 3, 1)  # not a weekend day
    saturday = date.fromisocalendar(2026, 3, 6)
    sessions = []
    sid = 0
    for _ in range(3):  # early bird: hour < 7
        sessions.append(make_session(id=sid, start=datetime.combine(monday, time(6, 0))))
        sid += 1
    for _ in range(2):  # night owl: hour >= 22
        sessions.append(make_session(id=sid, start=datetime.combine(monday, time(23, 0))))
        sid += 1
    for _ in range(4):  # weekend: Sat/Sun, neither early nor late
        sessions.append(make_session(id=sid, start=datetime.combine(saturday, time(12, 0))))
        sid += 1

    achievements = _calc_achievements(sessions, {}, set(), 0, [], 0, 0)
    assert _find(achievements, "early_bird", 5).current == 3
    assert _find(achievements, "early_bird", 5).unlocked is False
    assert _find(achievements, "night_owl", 5).current == 2
    assert _find(achievements, "weekend_warrior", 10).current == 4


def test_calc_achievements_marathon_session_uses_longest_single_session() -> None:
    base = datetime(2026, 1, 1, 0, 0)
    sessions = [
        make_session(id=1, start=base, end=base + timedelta(hours=1)),
        make_session(id=2, start=base, end=base + timedelta(hours=5)),
        make_session(id=3, start=base, end=base + timedelta(hours=3)),
    ]
    achievements = _calc_achievements(sessions, {}, set(), 0, [], 0, 0)
    tier4 = _find(achievements, "marathon_session", 4)
    assert tier4.current == 5.0
    assert tier4.unlocked is True
    assert _find(achievements, "marathon_session", 6).unlocked is False


def test_calc_achievements_course_diversity_uses_busiest_week() -> None:
    week1_monday = date.fromisocalendar(2026, 2, 1)
    week2_monday = date.fromisocalendar(2026, 5, 1)
    sessions = [
        make_session(id=1, course_id=1, start=datetime.combine(week1_monday, time(9, 0))),
        make_session(id=2, course_id=2, start=datetime.combine(week1_monday, time(10, 0))),
        make_session(id=3, course_id=1, start=datetime.combine(week2_monday, time(9, 0))),
        make_session(id=4, course_id=2, start=datetime.combine(week2_monday, time(10, 0))),
        make_session(id=5, course_id=3, start=datetime.combine(week2_monday, time(11, 0))),
    ]
    achievements = _calc_achievements(sessions, {}, set(), 0, [], 0, 0)
    tier2 = _find(achievements, "course_diversity", 2)
    assert tier2.current == 3  # week2's 3 distinct courses, not week1's 2
    assert tier2.unlocked is True
    assert _find(achievements, "course_diversity", 4).unlocked is False


def test_calc_achievements_notes_written_uses_notes_count_param() -> None:
    achievements = _calc_achievements([], {}, set(), notes_count=5, study_programs=[], ects_total=0, ects_earned=0)
    tier5 = _find(achievements, "notes_written", 5)
    assert tier5.current == 5
    assert tier5.unlocked is True
    assert _find(achievements, "notes_written", 25).unlocked is False


def test_calc_achievements_completed_programmes_counts_is_completed() -> None:
    programs = [
        StudyProgram(id=None, name="Main", is_built_in=True, is_completed=False),
        StudyProgram(id=5, name="Other", is_built_in=False, is_completed=True),
    ]
    achievements = _calc_achievements([], {}, set(), 0, programs, 0, 0)
    tier1 = _find(achievements, "completed_programmes", 1)
    assert tier1.current == 1
    assert tier1.unlocked is True
    assert _find(achievements, "completed_programmes", 2).unlocked is False


def test_calc_achievements_all_courses_done_requires_positive_total() -> None:
    zero_total = _calc_achievements([], {}, set(), 0, [], ects_total=0, ects_earned=0)
    assert _find(zero_total, "all_courses_done", 1).unlocked is False

    not_quite = _calc_achievements([], {}, set(), 0, [], ects_total=180, ects_earned=179)
    assert _find(not_quite, "all_courses_done", 1).unlocked is False

    done = _calc_achievements([], {}, set(), 0, [], ects_total=180, ects_earned=180)
    tier = _find(done, "all_courses_done", 1)
    assert tier.unlocked is True
    assert tier.current == 1


def test_calc_achievements_sessions_tier_uses_total_session_count() -> None:
    sessions = [make_session(id=i) for i in range(50)]
    achievements = _calc_achievements(sessions, {}, set(), 0, [], 0, 0)
    tier50 = _find(achievements, "sessions", 50)
    assert tier50.current == 50
    assert tier50.unlocked is True
    assert _find(achievements, "sessions", 200).unlocked is False


# ---------------------------------------------------------------------------
# _calc_topics_progress
# ---------------------------------------------------------------------------


def test_calc_topics_progress_skips_goal_for_course_with_no_catalog_topics() -> None:
    courses = [make_course(id=100, topics=[])]  # no topics in the catalog
    goals = [make_course_goal(course_id=100, completed_topics="A,B")]
    completed, total, breakdown = _calc_topics_progress(goals, courses)
    assert (completed, total, breakdown) == (0, 0, [])


def test_calc_topics_progress_skips_goal_for_course_missing_from_catalog() -> None:
    courses = [make_course(id=999, topics=["A", "B"])]
    goals = [make_course_goal(course_id=100, completed_topics="A")]  # course 100 not in catalog
    completed, total, breakdown = _calc_topics_progress(goals, courses)
    assert (completed, total, breakdown) == (0, 0, [])


def test_calc_topics_progress_counts_intersection_and_breakdown() -> None:
    courses = [
        make_course(id=100, name="Algorithms", topics=["A", "B", "C"]),
        make_course(id=200, name="Databases", topics=["X", "Y"]),
    ]
    goals = [
        make_course_goal(course_id=100, course_name="Algorithms", completed_topics="A,B"),
        # no topics done yet for course 200 -> excluded from breakdown, still counts toward total.
        make_course_goal(course_id=200, course_name="Databases", completed_topics=""),
    ]
    completed, total, breakdown = _calc_topics_progress(goals, courses)
    assert completed == 2
    assert total == 5  # 3 + 2
    assert breakdown == [
        {"course_id": 100, "course_name": "Algorithms", "topics_completed": 2, "topics_total": 3}
    ]


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
# _calc_weekly_report
# ---------------------------------------------------------------------------


def test_calc_weekly_report_picks_most_recently_completed_week() -> None:
    today = date.fromisocalendar(2026, 29, 3)  # Wednesday of the CURRENT week (29)
    week29_monday = _week_start(today)
    week28_monday = week29_monday - timedelta(days=7)  # the reported week
    week27_monday = week28_monday - timedelta(days=7)  # the week before that

    sessions = [
        # reported week (28): course A 2h, course B 1h -> top course A, 3h total.
        make_session(id=1, course_id=1, course_name="A", start=datetime.combine(week28_monday, time(9, 0)), end=datetime.combine(week28_monday, time(11, 0))),
        make_session(id=2, course_id=2, course_name="B", start=datetime.combine(week28_monday + timedelta(days=1), time(9, 0)), end=datetime.combine(week28_monday + timedelta(days=1), time(10, 0))),
        # week before (27): 1h -> delta should be 3-1=2.
        make_session(id=3, course_id=1, course_name="A", start=datetime.combine(week27_monday, time(9, 0)), end=datetime.combine(week27_monday, time(10, 0))),
        # current week (29): must be excluded entirely.
        make_session(id=4, course_id=1, course_name="A", start=datetime.combine(week29_monday, time(9, 0)), end=datetime.combine(week29_monday, time(20, 0))),
    ]

    report = _calc_weekly_report(sessions, today)
    assert report.week_id == "2026-W28"
    assert report.hours == 3.0
    assert report.delta_vs_previous_week_hours == 2.0
    assert report.top_course == "A"
    assert report.sessions_count == 2


def test_calc_weekly_report_empty_week_has_no_top_course() -> None:
    today = date.fromisocalendar(2026, 29, 3)
    report = _calc_weekly_report([], today)
    assert report.hours == 0.0
    assert report.top_course is None
    assert report.sessions_count == 0


# ---------------------------------------------------------------------------
# _calc_week_quota
# ---------------------------------------------------------------------------


def test_calc_week_quota_basic_math() -> None:
    quota = _calc_week_quota(week_hours=20, week_min=25, week_max=30)
    assert quota.target_min == 25
    assert quota.target_max == 30
    assert quota.percent == round(20 / (30 * 1.15) * 100, 1)
    assert quota.warning is True
    assert quota.missing_hours == 5.0


def test_calc_week_quota_percent_caps_at_100_and_no_warning() -> None:
    quota = _calc_week_quota(week_hours=100, week_min=25, week_max=30)
    assert quota.percent == 100.0
    assert quota.warning is False
    assert quota.missing_hours == 0.0


# ---------------------------------------------------------------------------
# _calc_average_grade
# ---------------------------------------------------------------------------


def test_calc_average_grade_none_when_no_goals() -> None:
    assert _calc_average_grade([], []) is None


def test_calc_average_grade_none_when_no_grade_set() -> None:
    courses = [make_course(id=100)]
    goals = [make_course_goal(course_id=100, grade=None)]
    assert _calc_average_grade(goals, courses) is None


def test_calc_average_grade_ects_weighted_mean() -> None:
    courses = [make_course(id=100, ects=5), make_course(id=200, ects=10)]
    goals = [
        make_course_goal(course_id=100, grade=1.0),
        make_course_goal(course_id=200, grade=2.0),
    ]
    # (1.0*5 + 2.0*10) / 15 = 25/15 = 1.6666...
    assert _calc_average_grade(goals, courses) == round(25 / 15, 2)


def test_calc_average_grade_falls_back_to_unweighted_mean_when_total_ects_zero() -> None:
    courses = [make_course(id=100, ects=0), make_course(id=200, ects=0)]
    goals = [
        make_course_goal(course_id=100, grade=1.0),
        make_course_goal(course_id=200, grade=2.0),
    ]
    assert _calc_average_grade(goals, courses) == 1.5


# ---------------------------------------------------------------------------
# _calc_ects_progress
# ---------------------------------------------------------------------------


def test_calc_ects_progress_ungrouped_courses() -> None:
    courses = [make_course(id=1, ects=5), make_course(id=2, ects=3)]
    settings = {"completedCourseIds": [1]}
    earned, total = _calc_ects_progress(courses, settings)
    assert total == 8
    assert earned == 5


def test_calc_ects_progress_grouped_quota_parsed_from_name() -> None:
    courses = [
        make_course(id=10, ects=3, group="Wahlpflicht (5 ECTS)"),
        make_course(id=11, ects=4, group="Wahlpflicht (5 ECTS)"),
    ]
    settings = {"completedCourseIds": [10, 11]}
    earned, total = _calc_ects_progress(courses, settings)
    assert total == 5  # regex-parsed quota, not sum(3+4)=7
    assert earned == 5  # capped at the quota even though members sum to 7


def test_calc_ects_progress_grouped_earned_caps_at_quota_when_over() -> None:
    courses = [
        make_course(id=10, ects=3, group="Wahlpflicht (5 ECTS)"),
        make_course(id=11, ects=4, group="Wahlpflicht (5 ECTS)"),
    ]
    settings = {"completedCourseIds": [10]}  # only 3 of 5 done, under quota
    earned, total = _calc_ects_progress(courses, settings)
    assert total == 5
    assert earned == 3


def test_calc_ects_progress_grouped_falls_back_to_sum_without_pattern() -> None:
    courses = [
        make_course(id=20, ects=3, group="Electives"),
        make_course(id=21, ects=4, group="Electives"),
    ]
    settings = {"completedCourseIds": [20]}
    earned, total = _calc_ects_progress(courses, settings)
    assert total == 7  # fallback: sum of member ects, no "(N ECTS)" in the name
    assert earned == 3

    settings_all_done = {"completedCourseIds": [20, 21]}
    earned2, total2 = _calc_ects_progress(courses, settings_all_done)
    assert total2 == 7
    assert earned2 == 7  # sum == quota here, so no capping is exercised


def test_calc_ects_progress_mixes_ungrouped_and_grouped() -> None:
    courses = [
        make_course(id=1, ects=5, group=None),
        make_course(id=10, ects=3, group="Wahlpflicht (5 ECTS)"),
        make_course(id=11, ects=4, group="Wahlpflicht (5 ECTS)"),
    ]
    settings = {"completedCourseIds": [1, 10, 11]}
    earned, total = _calc_ects_progress(courses, settings)
    assert total == 5 + 5  # ungrouped 5 + group quota 5
    assert earned == 5 + 5  # ungrouped earned 5 + capped group earned 5


# ---------------------------------------------------------------------------
# _calc_course_hours
# ---------------------------------------------------------------------------


def test_calc_course_hours_only_counts_completed_or_ended_sessions() -> None:
    now = datetime(2026, 1, 2, 0, 0)
    sessions = [
        make_session(id=1, course_id=100, course_name="A", is_completed=True, start=datetime(2026, 1, 1, 9, 0), end=datetime(2026, 1, 1, 10, 0)),
        make_session(id=2, course_id=100, course_name="A", is_completed=False, start=datetime(2026, 1, 1, 9, 0), end=datetime(2026, 1, 1, 9, 30)),  # not completed but end<=now
        make_session(id=3, course_id=200, course_name="B", is_completed=False, start=datetime(2026, 1, 1, 9, 0), end=datetime(2099, 1, 1, 9, 0)),  # not completed, end in the future -> excluded
    ]
    result = _calc_course_hours(sessions, now)
    assert len(result) == 1
    assert result[0].course_id == 100
    assert result[0].hours == 1.5
    assert result[0].sessions == 2


def test_calc_course_hours_sorted_descending_by_hours() -> None:
    now = datetime(2026, 1, 2, 0, 0)
    sessions = [
        make_session(id=1, course_id=100, course_name="A", is_completed=True, start=datetime(2026, 1, 1, 9, 0), end=datetime(2026, 1, 1, 10, 0)),
        make_session(id=2, course_id=200, course_name="B", is_completed=True, start=datetime(2026, 1, 1, 9, 0), end=datetime(2026, 1, 1, 14, 0)),
    ]
    result = _calc_course_hours(sessions, now)
    assert [c.course_id for c in result] == [200, 100]


# ---------------------------------------------------------------------------
# _calc_upcoming_course_goals
# ---------------------------------------------------------------------------


def test_calc_upcoming_course_goals_skips_no_target_date_and_completed() -> None:
    today = date(2026, 1, 1)
    goals = [
        make_course_goal(course_id=1, target_date=None),
        make_course_goal(course_id=2, target_date="2026-02-01", completed_at="2026-01-15T00:00:00"),
        make_course_goal(course_id=3, target_date="2026-02-10"),
    ]
    result = _calc_upcoming_course_goals(goals, today)
    assert len(result) == 1
    assert result[0].course_id == 3


def test_calc_upcoming_course_goals_sorted_soonest_first_and_capped() -> None:
    today = date(2026, 1, 1)
    goals = [
        make_course_goal(course_id=100 + i, target_date=f"2026-03-{10 + i:02d}")
        for i in range(7)
    ]
    result = _calc_upcoming_course_goals(goals, today)
    assert len(result) == 5
    assert [g.target_date for g in result] == sorted(g.target_date for g in result)
    assert result[0].target_date == date(2026, 3, 10)


def test_calc_upcoming_course_goals_respects_custom_limit() -> None:
    today = date(2026, 1, 1)
    goals = [
        make_course_goal(course_id=100 + i, target_date=f"2026-03-{10 + i:02d}")
        for i in range(3)
    ]
    result = _calc_upcoming_course_goals(goals, today, limit=2)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# _calc_neglected_course
# ---------------------------------------------------------------------------


def test_calc_neglected_course_none_when_fewer_than_two_active() -> None:
    settings = {"selectedCourseIds": [1], "completedCourseIds": []}
    courses = [make_course(id=1)]
    assert _calc_neglected_course(settings, courses, [], date(2026, 1, 1)) is None

    assert _calc_neglected_course({}, courses, [], date(2026, 1, 1)) is None


def test_calc_neglected_course_picks_never_studied_course() -> None:
    courses = [make_course(id=1, name="A"), make_course(id=2, name="B")]
    settings = {"selectedCourseIds": [1, 2], "completedCourseIds": []}
    history = [make_session(course_id=1, start=datetime(2026, 1, 1, 9, 0), end=datetime(2026, 1, 1, 10, 0))]
    result = _calc_neglected_course(settings, courses, history, date(2026, 1, 10))
    assert result.course_id == 2
    assert result.last_studied is None
    assert result.days_since is None


def test_calc_neglected_course_picks_studied_longest_ago() -> None:
    courses = [make_course(id=1, name="A"), make_course(id=2, name="B")]
    settings = {"selectedCourseIds": [1, 2], "completedCourseIds": []}
    history = [
        make_session(id=1, course_id=1, start=datetime(2026, 1, 1, 9, 0), end=datetime(2026, 1, 1, 10, 0)),
        make_session(id=2, course_id=2, start=datetime(2026, 1, 5, 9, 0), end=datetime(2026, 1, 5, 10, 0)),
    ]
    today = date(2026, 1, 10)
    result = _calc_neglected_course(settings, courses, history, today)
    assert result.course_id == 1
    assert result.last_studied == date(2026, 1, 1)
    assert result.days_since == 9


def test_calc_neglected_course_excludes_completed_courses_from_active_set() -> None:
    courses = [make_course(id=1, name="A"), make_course(id=2, name="B"), make_course(id=3, name="C")]
    # course 3 is selected but already completed -> not "active", leaves only 1 and 2.
    settings = {"selectedCourseIds": [1, 2, 3], "completedCourseIds": [3]}
    history = [
        make_session(id=1, course_id=1, start=datetime(2026, 1, 1, 9, 0), end=datetime(2026, 1, 1, 10, 0)),
        make_session(id=2, course_id=2, start=datetime(2026, 1, 5, 9, 0), end=datetime(2026, 1, 5, 10, 0)),
    ]
    result = _calc_neglected_course(settings, courses, history, date(2026, 1, 10))
    assert result.course_id == 1


# ---------------------------------------------------------------------------
# _calc_month_quota
# ---------------------------------------------------------------------------


def test_calc_month_quota_full_target_early_in_month() -> None:
    """The full monthly goal applies from day one - the former elapsed-weeks proration
    was removed in lockstep with the app's dashboard card (the prorated target read as
    a bug next to the configured goal in the settings)."""
    month_start = date(2026, 2, 1)  # Feb 2026
    today = date(2026, 2, 2)  # 1 day elapsed - target no longer scales with this
    quota = _calc_month_quota(month_hours=1, today=today, month_start=month_start, month_min=40, month_max=60)
    assert quota.target_min == 40.0
    assert quota.target_max == 60.0


def test_calc_month_quota_full_target_at_end_of_month() -> None:
    month_start = date(2026, 2, 1)
    today = date(2026, 2, 28)  # last day - same full target as on day one
    quota = _calc_month_quota(month_hours=1, today=today, month_start=month_start, month_min=40, month_max=60)
    assert quota.target_min == 40.0
    assert quota.target_max == 60.0


def test_calc_month_quota_percent_capped_and_warning() -> None:
    month_start = date(2026, 2, 1)
    today = date(2026, 2, 28)
    quota = _calc_month_quota(month_hours=1000, today=today, month_start=month_start, month_min=40, month_max=60)
    assert quota.percent == 100.0
    assert quota.warning is False

    low_quota = _calc_month_quota(month_hours=1, today=today, month_start=month_start, month_min=40, month_max=60)
    assert low_quota.warning is True
    assert low_quota.missing_hours == 39.0


# ---------------------------------------------------------------------------
# _calc_forecast
# ---------------------------------------------------------------------------


def test_calc_forecast_none_when_ects_already_complete() -> None:
    courses = [make_course(id=1, semester=2)]
    result = _calc_forecast(courses, [], ects_earned=180, ects_total=180, today=date(2026, 1, 1), now=datetime(2026, 1, 1), week_quota_min=25, week_quota_max=30)
    assert result == (None, None)

    result_over = _calc_forecast(courses, [], ects_earned=190, ects_total=180, today=date(2026, 1, 1), now=datetime(2026, 1, 1), week_quota_min=25, week_quota_max=30)
    assert result_over == (None, None)


def test_calc_forecast_none_when_no_course_has_a_semester_set() -> None:
    courses = [make_course(id=1, semester=0), make_course(id=2, semester=0)]
    result = _calc_forecast(courses, [], ects_earned=0, ects_total=180, today=date(2026, 1, 1), now=datetime(2026, 1, 1), week_quota_min=25, week_quota_max=30)
    assert result == (None, None)

    result_empty_courses = _calc_forecast([], [], ects_earned=0, ects_total=180, today=date(2026, 1, 1), now=datetime(2026, 1, 1), week_quota_min=25, week_quota_max=30)
    assert result_empty_courses == (None, None)


def test_calc_forecast_normal_pace_matches_expected_baseline() -> None:
    courses = [make_course(id=1, semester=2), make_course(id=2, semester=4)]
    today = date(2026, 3, 1)
    now = datetime(2026, 3, 1, 12, 0)
    week_quota_min, week_quota_max = 25, 30  # reference weekly hours = 27.5

    # remaining_ects=90, ects_per_semester=180/4=45 -> baseline_weeks_needed = 90/45*26 = 52
    # recent pace exactly matches the reference (27.5h/week over 8 weeks = 220h) -> pace_ratio=1.0
    history = [
        make_session(
            id=1,
            start=datetime(2026, 2, 19, 0, 0),
            end=datetime(2026, 2, 19, 0, 0) + timedelta(hours=220),
            is_completed=True,
        )
    ]
    forecast_date, recent_weekly_hours = _calc_forecast(
        courses, history, ects_earned=90, ects_total=180, today=today, now=now,
        week_quota_min=week_quota_min, week_quota_max=week_quota_max,
    )
    assert recent_weekly_hours == 27.5
    assert forecast_date == today + timedelta(days=52 * 7)


def test_calc_forecast_pace_ratio_clamped_at_max_so_date_is_not_unrealistically_soon() -> None:
    courses = [make_course(id=1, semester=2), make_course(id=2, semester=4)]
    today = date(2026, 3, 1)
    now = datetime(2026, 3, 1, 12, 0)
    week_quota_min, week_quota_max = 25, 30  # reference = 27.5h/week

    # 700h over the last 8 weeks -> 87.5h/week -> raw pace_ratio ~3.18, must clamp to 3.0.
    history = [
        make_session(id=1, start=datetime(2026, 2, 19, 0, 0), end=datetime(2026, 2, 19, 0, 0) + timedelta(hours=700), is_completed=True)
    ]
    forecast_date, recent_weekly_hours = _calc_forecast(
        courses, history, ects_earned=90, ects_total=180, today=today, now=now,
        week_quota_min=week_quota_min, week_quota_max=week_quota_max,
    )
    assert recent_weekly_hours == 87.5

    baseline_weeks_needed = 52.0
    clamped_date = today + timedelta(days=(baseline_weeks_needed / 3.0) * 7)
    raw_ratio = 87.5 / 27.5
    naive_unclamped_date = today + timedelta(days=(baseline_weeks_needed / raw_ratio) * 7)

    assert forecast_date == clamped_date
    # Without the clamp the date would be even sooner (higher pace_ratio -> fewer
    # weeks needed) - confirm the clamp actually pushed the date back out.
    assert forecast_date > naive_unclamped_date


def test_calc_forecast_zero_recent_hours_falls_back_to_pace_ratio_one() -> None:
    courses = [make_course(id=1, semester=2), make_course(id=2, semester=4)]
    today = date(2026, 3, 1)
    now = datetime(2026, 3, 1, 12, 0)
    forecast_date, recent_weekly_hours = _calc_forecast(
        courses, [], ects_earned=90, ects_total=180, today=today, now=now,
        week_quota_min=25, week_quota_max=30,
    )
    assert recent_weekly_hours == 0.0
    # pace_ratio falls back to 1.0 (not a ZeroDivisionError, not clamped away from 1.0).
    assert forecast_date == today + timedelta(days=52 * 7)


# ---------------------------------------------------------------------------
# _calc_month_comparison
# ---------------------------------------------------------------------------


def test_calc_month_comparison_no_year_data_when_history_does_not_reach_back() -> None:
    today = date(2026, 3, 15)
    history = [
        make_session(id=1, start=datetime(2026, 2, 10, 9, 0), end=datetime(2026, 2, 10, 10, 0)),  # 1h Feb
        make_session(id=2, start=datetime(2026, 3, 5, 9, 0), end=datetime(2026, 3, 5, 12, 0)),  # 3h Mar
    ]
    this_month_hours, delta_last_month, delta_last_year = _calc_month_comparison(history, today)
    assert this_month_hours == 3.0
    assert delta_last_month == 2.0
    assert delta_last_year is None


def test_calc_month_comparison_populated_last_year_when_history_reaches_back() -> None:
    today = date(2026, 3, 15)
    history = [
        # earliest session on/before 2025-03-01 -> satisfies the has_year_data gate.
        make_session(id=1, start=datetime(2025, 1, 10, 9, 0), end=datetime(2025, 1, 10, 10, 0)),
        # same month last year: 2h.
        make_session(id=2, start=datetime(2025, 3, 5, 9, 0), end=datetime(2025, 3, 5, 11, 0)),
        # last month (Feb 2026): 1h.
        make_session(id=3, start=datetime(2026, 2, 10, 9, 0), end=datetime(2026, 2, 10, 10, 0)),
        # this month (Mar 2026): 3h.
        make_session(id=4, start=datetime(2026, 3, 5, 9, 0), end=datetime(2026, 3, 5, 12, 0)),
    ]
    this_month_hours, delta_last_month, delta_last_year = _calc_month_comparison(history, today)
    assert this_month_hours == 3.0
    assert delta_last_month == 2.0
    assert delta_last_year == 1.0
