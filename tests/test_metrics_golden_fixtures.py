"""Golden-fixture tests against docs/api/metrics-fixtures.json in the studylife repo -
audit finding D4: coordinator.py's `_calc_*` helpers re-implement StudyLife.Shared's
StudyMetrics/CourseCatalog metric calculations (streak, longest streak, weekly/monthly
quota, ECTS-weighted grade average, ECTS progress, graduation forecast) as a "deliberately
parallel implementation with identical semantics, manually kept in sync" (coordinator.py's
own module docstring) - with no CI on either side checking the two stay in sync.

The fixture file is the single source of truth: tests/StudyLife.Shared.Tests/
MetricsGoldenFixtureTests.cs in the studylife repo derives every "expected" value by
actually RUNNING StudyMetrics/CourseCatalog against each scenario's inputs. This module
loads the SAME fixtures and asserts these `_calc_*` helpers reproduce the same numbers from
the same inputs - so a change to either side's math shows up here as a real, specific
assertion failure instead of silent drift.

Fixture source (`STUDYLIFE_METRICS_FIXTURES`): a local file path or an http(s) URL. Defaults
to the studylife repo's own raw GitHub URL (docs/api/metrics-fixtures.json on main) - same
reachability policy as the OpenAPI contract test in this same file/package: CI fails loudly
on an unreachable/unparsable fixture file (that IS the point of a drift-detection test),
local dev with no override and no internet skips with a clear message, an explicitly
configured STUDYLIFE_METRICS_FIXTURES that turns out unreachable is a real error and fails.

FORMERLY KNOWN DRIFT, NOW FIXED (audit finding D4 - see coordinator.py/api.py for the actual
fix, and git history for the two scenarios that used to be pinned in KNOWN_DRIFT_XFAIL here):

- "week_quota_future_dated_session_drift": /api/sessions/history has no upper date bound,
  so it can return sessions scheduled arbitrarily far in the future. The real app
  (Index.razor.cs) bounds its week-hours filter to `>= weekStart && < weekStart + 7 days`;
  coordinator.py's week_hours filter (`_async_update_data` and `_build_program_data`) now
  applies the exact same upper bound. month_hours was CHECKED against the same bug class and
  found to be correct as-is: Index.razor.cs's own month filter (`monthSessions = history.
  Where(s => s.StartTime.Date >= monthStart)`) has no upper bound either - that's the real,
  intended C# behavior, not a second instance of this drift, and the fixtures' expected
  monthHours values were computed against that same unbounded filter. Adding an upper bound
  to coordinator.py's month filter would therefore make fixtures FAIL, not pass - it was
  deliberately left alone.
- "custom_program_group_quota_not_embedded_in_name": coordinator.py's _calc_ects_progress/
  _group_quota now accept an optional `group_quotas` mapping - the AUTHORITATIVE elective-
  group ECTS quota (GET /api/studyprograms/{id} -> StudyProgramDetailDto.GroupEctsQuotas),
  which the coordinator fetches once per poll cycle for the currently ACTIVE study programme
  when it's a custom one (api.py's new `async_get_study_program`). When given, this is used
  instead of the "(N ECTS)"-in-the-name regex - see _calc_ects_progress's docstring for the
  built-in-programme/non-active-programme fallback story. This specific golden-fixture
  scenario models a real DB-configured group quota (5 ECTS for a group named plain
  "Electives", no "(N ECTS)" substring) that the fixture JSON itself has no field for -
  session/course/settings-shaped scenario inputs don't carry an out-of-band server-side
  quota config - so `SCENARIO_GROUP_ECTS_QUOTAS` below hardcodes that one value (taken
  directly from the scenario's own "description" field in the fixture) as this offline unit
  test's stand-in for what a live GET /api/studyprograms/{id} would have returned.

KNOWN_DRIFT_XFAIL is now empty (kept, not deleted, as the anchor the block below is written
against) - if either of the above ever starts failing again (a regression to the old
behavior), the test below turns that into a hard failure immediately, the same guarantee it
gave while they were pinned as expected failures.
"""
from __future__ import annotations

import functools
import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from custom_components.studylife.coordinator import (
    _calc_average_grade,
    _calc_ects_progress,
    _calc_forecast,
    _calc_longest_streak,
    _calc_month_quota,
    _calc_streak,
    _calc_week_quota,
    _to_session,
    _week_start,
)

from ._network_fetch import allow_network_for

DEFAULT_FIXTURES_URL = "https://raw.githubusercontent.com/lukislp/studylife/main/docs/api/metrics-fixtures.json"
FIXTURES_HOST = "raw.githubusercontent.com"

# Both scenarios this used to pin (week_quota_future_dated_session_drift,
# custom_program_group_quota_not_embedded_in_name) are fixed now - see the module docstring
# above. Left empty (not deleted) so a future regression back to either old behavior is
# caught immediately by the "unexpected_passes"/strict-xfail machinery below turning into a
# silent "well it's expected to fail" instead of a loud one.
KNOWN_DRIFT_XFAIL: dict[str, str] = {}

# custom_program_group_quota_not_embedded_in_name's real, DB-configured GroupEctsQuotas
# (StudyProgramDetailDto.GroupEctsQuotas) - see the module docstring above for why this is
# hardcoded here instead of read off the fixture: the fixture JSON's scenario shape (settings/
# sessions/courses/courseGoals/completedCourseIds) has no field for it, since every other
# scenario needs no such out-of-band, server-side-only config. Value taken verbatim from this
# scenario's own "description" field in the fixture file.
SCENARIO_GROUP_ECTS_QUOTAS: dict[str, dict[str, int]] = {
    "custom_program_group_quota_not_embedded_in_name": {"Electives": 5},
}


def _is_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() == "true"


@functools.lru_cache(maxsize=4)
def _fetch_fixtures(source: str) -> dict[str, Any]:
    if source.startswith(("http://", "https://")):
        allow_network_for(FIXTURES_HOST)
        with urllib.request.urlopen(source, timeout=15.0) as response:
            data: dict[str, Any] = json.loads(response.read().decode("utf-8"))
            return data
    parsed: dict[str, Any] = json.loads(Path(source).read_text(encoding="utf-8"))
    return parsed


@pytest.fixture
def fixtures() -> dict[str, Any]:
    """Function-scoped (not session-scoped): `allow_network_for` (see _network_fetch.py) must
    run from inside a fixture/test body, after pytest-homeassistant-custom-component's own
    pytest_runtest_setup hook has already re-clamped sockets to 127.0.0.1-only for this test -
    a session-scoped fixture's body only runs once, before that per-test hook has necessarily
    fired for every subsequent test. _fetch_fixtures is process-wide lru_cache'd, so only the
    FIRST test that actually needs the fixture file hits the network - every other one (this
    module has only the one test body left, but the cache is a general safety net) is a cache
    hit."""
    configured = os.environ.get("STUDYLIFE_METRICS_FIXTURES")
    explicit = configured is not None
    source = configured if explicit else DEFAULT_FIXTURES_URL
    try:
        return _fetch_fixtures(source)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        if _is_ci():
            pytest.fail(
                f"Could not load docs/api/metrics-fixtures.json from {source!r} in CI - "
                f"this drift-detection suite MUST fail here, not skip: {exc!r}"
            )
        if explicit:
            pytest.fail(
                f"STUDYLIFE_METRICS_FIXTURES={source!r} was set explicitly but could not be "
                f"loaded - this is a real configuration error, not the offline-dev fallback "
                f"below: {exc!r}"
            )
        pytest.skip(
            f"Skipping metrics golden-fixture tests: STUDYLIFE_METRICS_FIXTURES is unset and "
            f"the default fixtures URL ({source!r}) is unreachable - assuming offline local "
            f"dev. Set STUDYLIFE_METRICS_FIXTURES to a local docs/api/metrics-fixtures.json "
            f"path to run these tests offline. Original error: {exc!r}"
        )


def _scenario_ids(fixtures_data: dict[str, Any]) -> list[str]:
    return [s["name"] for s in fixtures_data.get("scenarios", [])]


def _run_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    """Runs the exact same `_calc_*` helpers the coordinator's own `_async_update_data`/
    `_build_program_data` call, on one fixture scenario's inputs, and returns a dict shaped
    like the fixture's own "expected" block for easy comparison."""
    now = datetime.fromisoformat(scenario["now"])
    today = now.date()
    settings = dict(scenario["settings"])
    settings["completedCourseIds"] = scenario["completedCourseIds"]

    sessions = [_to_session(raw) for raw in scenario["sessions"]]
    courses = scenario["courses"]
    course_goals = scenario["courseGoals"]

    # Mirrors _async_update_data's week/month hours filters EXACTLY, including week_hours'
    # upper bound (`< week_start + 7 days`, audit finding D4 fix) AND month_hours staying
    # deliberately unbounded (checked against the real C# app and confirmed correct as-is -
    # see the module docstring and coordinator.py's own comment on month_sessions).
    week_start = _week_start(today)
    week_end = week_start + timedelta(days=7)
    week_hours = sum(
        s.duration_minutes for s in sessions if week_start <= s.start.date() < week_end
    ) / 60.0
    month_start = today.replace(day=1)
    month_hours = sum(
        s.duration_minutes for s in sessions if s.start.date() >= month_start
    ) / 60.0

    completed_history = [s for s in sessions if s.is_completed or s.end <= now]
    streak = _calc_streak(completed_history, today)
    longest_streak = _calc_longest_streak(completed_history)

    week_quota = _calc_week_quota(
        week_hours, settings["weeklyGoalMinHours"], settings["weeklyGoalMaxHours"]
    )
    month_quota = _calc_month_quota(
        month_hours, today, month_start,
        settings["monthlyGoalMinHours"], settings["monthlyGoalMaxHours"],
    )

    average_grade = _calc_average_grade(course_goals, courses)
    group_quotas = SCENARIO_GROUP_ECTS_QUOTAS.get(scenario["name"])
    ects_earned, ects_total = _calc_ects_progress(courses, settings, group_quotas)

    forecast_date, recent_weekly_hours = _calc_forecast(
        courses, sessions, ects_earned, ects_total, today, now,
        settings["weeklyGoalMinHours"], settings["weeklyGoalMaxHours"],
    )

    return {
        "streak": streak,
        "longestStreak": longest_streak,
        "weekHours": week_hours,
        "weekQuotaPercent": week_quota.percent,
        "weekQuotaWarning": week_quota.warning,
        "weekQuotaMissingHours": week_quota.missing_hours,
        "monthHours": month_hours,
        "monthQuotaPercent": month_quota.percent,
        "monthQuotaWarning": month_quota.warning,
        "monthQuotaMissingHours": month_quota.missing_hours,
        "averageGrade": average_grade,
        "ectsEarned": ects_earned,
        "ectsTotal": ects_total,
        "forecastAvailable": forecast_date is not None,
        "forecastDate": forecast_date.isoformat() if forecast_date else None,
        "forecastRecentWeeklyHours": recent_weekly_hours,
    }


def _assert_matches(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    assert actual["streak"] == expected["streak"]
    assert actual["longestStreak"] == expected["longestStreak"]

    assert actual["weekHours"] == pytest.approx(expected["weekHours"], abs=0.01)
    assert actual["weekQuotaPercent"] == pytest.approx(expected["weekQuotaPercent"], abs=0.05)
    assert actual["weekQuotaWarning"] == expected["weekQuotaWarning"]
    assert actual["weekQuotaMissingHours"] == pytest.approx(expected["weekQuotaMissingHours"], abs=0.01)

    assert actual["monthHours"] == pytest.approx(expected["monthHours"], abs=0.01)
    assert actual["monthQuotaPercent"] == pytest.approx(expected["monthQuotaPercent"], abs=0.05)
    assert actual["monthQuotaWarning"] == expected["monthQuotaWarning"]
    assert actual["monthQuotaMissingHours"] == pytest.approx(expected["monthQuotaMissingHours"], abs=0.01)

    if expected["averageGrade"] is None:
        assert actual["averageGrade"] is None
    else:
        assert actual["averageGrade"] == pytest.approx(expected["averageGrade"], abs=0.01)

    assert actual["ectsEarned"] == expected["ectsEarned"]
    assert actual["ectsTotal"] == expected["ectsTotal"]

    assert actual["forecastAvailable"] == expected["forecastAvailable"]
    if expected["forecastAvailable"]:
        # Compared by DATE only: the two implementations accumulate the fractional
        # weeks-needed math slightly differently (float vs double rounding), see the
        # fixture file's "$fieldNotes".
        actual_date = date.fromisoformat(actual["forecastDate"][:10])
        expected_date = date.fromisoformat(expected["forecastDate"][:10])
        assert actual_date == expected_date
        assert actual["forecastRecentWeeklyHours"] == pytest.approx(
            expected["forecastRecentWeeklyHours"], abs=0.05
        )
    else:
        assert actual["forecastDate"] is None


def test_fixture_file_has_scenarios(fixtures: dict[str, Any]) -> None:
    names = _scenario_ids(fixtures)
    assert names, "docs/api/metrics-fixtures.json has no scenarios"
    assert len(names) == len(set(names)), "duplicate scenario names in the fixture file"


def test_coordinator_metrics_match_fixture(fixtures: dict[str, Any]) -> None:
    """Runs every scenario in one test body (rather than one parametrized test per scenario)
    so the fixture-loading network fetch stays inside a single, ordinary function-scoped
    fixture - `pytest_generate_tests`-based dynamic parametrization would need that same fetch
    at COLLECTION time, before any test has even started (see _network_fetch.py: the socket
    unblocking it needs can only run from inside a fixture/test body).

    Failures for every scenario are collected and reported together (one assertion at the
    end lists every mismatch by name) instead of stopping at the first one, so a single test
    run surfaces the full picture. Any scenario named as a key in KNOWN_DRIFT_XFAIL (currently
    empty - see the module docstring for the two entries that used to live there and how they
    got fixed) is asserted to currently FAIL (`strict=True`-equivalent: if one of them starts
    passing, that is ALSO reported as a failure here, so the entry has to be removed from
    KNOWN_DRIFT_XFAIL by whoever fixes it - the same guarantee `pytest.mark.xfail(strict=True)`
    would give a per-scenario parametrized test)."""
    unexpected_failures: list[str] = []
    unexpected_passes: list[str] = []

    for scenario in fixtures["scenarios"]:
        name = scenario["name"]
        actual = _run_scenario(scenario)
        try:
            _assert_matches(actual, scenario["expected"])
        except AssertionError as exc:
            if name in KNOWN_DRIFT_XFAIL:
                continue  # expected - see KNOWN_DRIFT_XFAIL / the module docstring
            unexpected_failures.append(f"{name}: {exc}")
        else:
            if name in KNOWN_DRIFT_XFAIL:
                unexpected_passes.append(name)

    problems = []
    if unexpected_failures:
        problems.append("Scenarios that should match but DON'T:\n" + "\n".join(unexpected_failures))
    if unexpected_passes:
        problems.append(
            "Scenarios marked as KNOWN, CONFIRMED DRIFT that now unexpectedly MATCH the C# "
            "truth - the underlying bug appears fixed; remove these from KNOWN_DRIFT_XFAIL: "
            + ", ".join(unexpected_passes)
        )
    assert not problems, "\n\n".join(problems)
