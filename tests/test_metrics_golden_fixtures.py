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

KNOWN, CONFIRMED DRIFT (not fixed here - see the fixture file's own per-scenario
"description" fields and the task report for full detail): two scenarios are listed in
KNOWN_DRIFT_XFAIL below (asserted to currently FAIL, strict-xfail style - see
test_coordinator_metrics_match_fixture's docstring) because coordinator.py currently,
genuinely computes different numbers than the real C# app:

- "week_quota_future_dated_session_drift": /api/sessions/history has no upper date bound,
  so it can return sessions scheduled arbitrarily far in the future. The real app
  (Index.razor.cs) bounds its week-hours filter to `< weekStart + 7 days`; coordinator.py's
  week_hours filter (`s.start.date() >= week_start`, see _async_update_data and
  _build_program_data) has NO upper bound, so a far-future-dated session inflates "this
  week's hours" here but not in the app.
- "custom_program_group_quota_not_embedded_in_name": coordinator.py's _calc_ects_progress/
  _group_quota can only recover a custom study programme's elective-group ECTS quota by
  regex-parsing "(N ECTS)" out of the group's display NAME (courses have no other quota
  field available over the API) - the real app instead fetches the true, separately
  DB-configured quota from GET /api/studyprograms/{id} (StudyProgramDetailDto.
  GroupEctsQuotas). coordinator.py's api.py never calls that endpoint at all, so any custom
  group whose name doesn't literally embed the "(N ECTS)" convention silently falls back to
  an uncapped raw sum instead of the true quota.

If either of these ever starts passing (e.g. coordinator.py gets fixed, or gains a
studyprograms-detail fetch), the test below turns that into a hard failure too - forcing
whoever fixes it to also remove the entry from KNOWN_DRIFT_XFAIL, instead of the fix going
unnoticed.
"""
from __future__ import annotations

import functools
import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime
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

# Scenario names with real, currently-confirmed drift from the C# truth - see the module
# docstring above for the root cause of each. Kept as a set (not skipped/removed from the
# fixture file) so the drift stays visible and pinned instead of silently disappearing.
KNOWN_DRIFT_XFAIL: dict[str, str] = {
    "week_quota_future_dated_session_drift": (
        "coordinator.py's week_hours filter has no upper date bound (unlike Index.razor.cs) "
        "- a future-dated session inflates week_hours here. Audit finding D4."
    ),
    "custom_program_group_quota_not_embedded_in_name": (
        "coordinator.py never fetches GET /api/studyprograms/{id} "
        "(StudyProgramDetailDto.GroupEctsQuotas) - its regex '(N ECTS)' name-parsing fallback "
        "produces an uncapped ECTS sum for custom groups whose name doesn't embed that "
        "convention. Audit finding D4."
    ),
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

    # Mirrors _async_update_data's week/month hours filters EXACTLY (including the missing
    # upper bound on week_hours - that's the real, current coordinator.py behavior being
    # tested here, not a "corrected" version of it).
    week_start = _week_start(today)
    week_hours = sum(
        s.duration_minutes for s in sessions if s.start.date() >= week_start
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
    ects_earned, ects_total = _calc_ects_progress(courses, settings)

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
    run surfaces the full picture. The two KNOWN_DRIFT_XFAIL scenarios are asserted to
    currently FAIL (`strict=True`-equivalent: if one of them starts passing, e.g. because
    coordinator.py got fixed, that is ALSO reported as a failure here, so the entry has to be
    removed from KNOWN_DRIFT_XFAIL by whoever fixes it - the same guarantee `pytest.mark.
    xfail(strict=True)` would give a per-scenario parametrized test)."""
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
