"""Golden-fixture tests against docs/api/metrics-fixtures.json in the studylife repo.

ROLE CHANGE (owner decision: every metric is computed in exactly ONE place, StudyLife -
see docs/api's metrics contract): coordinator.py no longer re-implements
StudyMetrics/CourseCatalog's math as parallel `_calc_*` helpers - GET /api/metrics/summary
now returns those numbers pre-computed, and coordinator.py's job is only to PARSE that
response into the same dataclasses the platforms already consume. So this module's job
changes too: it used to run coordinator.py's own reimplementation of the math against each
fixture scenario's raw inputs (sessions/courses/settings) and compare the result to the
fixture's "expected" block, which is how it caught real drift between the two
implementations (audit finding D4) - twice, in fact (see the two scenarios named below).

Now there is nothing left to re-derive: the fixture's own "expected" block already IS what
StudyMetrics computes for that scenario (docs/api/metrics-fixtures.json's own description -
"C# is the single source of truth ... tests/StudyLife.Shared.Tests/
MetricsGoldenFixtureTests.cs derives every 'expected' value by actually RUNNING
StudyMetrics/CourseCatalog"). So this module instead PACKAGES each scenario's "expected"
block as a GET /api/metrics/summary-shaped response (exactly the wire shape the real
endpoint would send for that scenario) and feeds it through coordinator.py's own
`_program_data_from_summary` parsing - the same function `_async_update_data` calls for
every study programme every poll cycle. This still exercises real, externally-pinned
numbers (not made-up test doubles) end-to-end through the actual parsing code, it just
tests a different boundary than before: "does HA's parser reproduce the server's numbers"
instead of "does HA's math match the server's math" - the latter question now belongs
entirely to the server's own MetricsGoldenFixtureTests.cs and the new endpoint-level
integration test docs/api's metrics contract describes ("the cross-repo lock").

FORMERLY KNOWN DRIFT, NOW MOOT (audit finding D4 - see coordinator.py's module docstring
for the actual fix): "week_quota_future_dated_session_drift" and
"custom_program_group_quota_not_embedded_in_name" used to be pinned as expected-to-fail
here, because they exercised bugs in coordinator.py's OWN math (an unbounded week-hours
filter; a regex-parsed elective-group ECTS quota). Both scenarios are FIXED BY
CONSTRUCTION now - not because either bug was hunted down and patched in this file, but
because the buggy code they exercised (`_calc_week_quota`'s session filter,
`_calc_ects_progress`'s regex fallback) no longer exists at all. There is no
KNOWN_DRIFT_XFAIL list left to maintain; both scenarios just run through the same assertion
as every other one.
"""
from __future__ import annotations

import functools
import json
import os
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from custom_components.studylife.coordinator import (
    StudyProgram,
    _program_data_from_summary,
)

from ._network_fetch import allow_network_for

DEFAULT_FIXTURES_URL = "https://raw.githubusercontent.com/lukislp/studylife/main/docs/api/metrics-fixtures.json"
FIXTURES_HOST = "raw.githubusercontent.com"


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


def _raw_summary_from_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    """Packages one fixture scenario's "expected" block (StudyMetrics' own, actually-run
    output for that scenario's inputs) as a GET /api/metrics/summary-shaped response - see
    the module docstring for why this is the right thing to feed through parsing instead of
    recomputing from scenario["sessions"]/["courses"]/etc. locally.

    Fields the old flat fixture "expected" shape has no equivalent for (hours.total/
    totalSessions, neglectedCourse, weeklyReport, courseHours, topics, monthComparison,
    upcomingCourseGoals) are filled with harmless defaults - this test only asserts the
    fields _program_data_from_summary actually reads from a scenario's "expected" block,
    it doesn't claim coverage of the endpoint's full response shape (test_openapi_contract.py
    and the fixture-derived tests in tests/conftest.py's make_raw_metrics_summary cover the
    wire shape itself; test_coordinator_calc.py covers the rest of the parsing surface with
    hand-built inputs).

    "hours.week"/"hours.month" and "weekQuota.hours"/"monthQuota.hours" deliberately use TWO
    DIFFERENT fixture fields (docs/ARCHITECTURE.md "Number semantics"): the former are
    studied-only (expected.weekStudiedHours/monthStudiedHours), the latter count every
    session planned in the window (expected.weekHours/monthHours) - same split
    MetricsController.ComputeSummaryAsync makes server-side."""
    settings = scenario["settings"]
    expected = scenario["expected"]
    return {
        "asOf": scenario["now"],
        "program": {"id": None, "name": "StudyLife", "isBuiltIn": True},
        "streak": {"current": expected["streak"], "longest": expected["longestStreak"]},
        "hours": {
            "week": expected["weekStudiedHours"],
            "month": expected["monthStudiedHours"],
            "total": expected["weekStudiedHours"],
            "totalSessions": 0,
        },
        "weekQuota": {
            "hours": expected["weekHours"],
            "targetMin": settings["weeklyGoalMinHours"],
            "targetMax": settings["weeklyGoalMaxHours"],
            "percent": expected["weekQuotaPercent"],
            "minPercent": expected["weekQuotaPercent"],
            "warning": expected["weekQuotaWarning"],
            "missingHours": expected["weekQuotaMissingHours"],
        },
        "monthQuota": {
            "hours": expected["monthHours"],
            "targetMin": settings["monthlyGoalMinHours"],
            "targetMax": settings["monthlyGoalMaxHours"],
            "percent": expected["monthQuotaPercent"],
            "minPercent": expected["monthQuotaPercent"],
            "warning": expected["monthQuotaWarning"],
            "missingHours": expected["monthQuotaMissingHours"],
        },
        "ects": {"earned": expected["ectsEarned"], "total": expected["ectsTotal"]},
        "averageGrade": expected["averageGrade"],
        "forecast": {
            "available": expected["forecastAvailable"],
            "alreadyDone": False,
            "date": expected["forecastDate"],
            "recentWeeklyHours": expected["forecastRecentWeeklyHours"],
        },
        "monthComparison": {
            "currentMonthHours": expected["monthStudiedHours"],
            "previousMonthHours": 0.0,
            "deltaVsPreviousMonth": 0.0,
            "hasYearData": False,
            "sameMonthLastYearHours": None,
            "deltaVsLastYear": None,
        },
        "neglectedCourse": None,
        "weeklyReport": {
            "weekId": "2026-W01", "hours": 0.0, "deltaVsPreviousWeek": 0.0,
            "topCourseName": None, "sessionCount": 0,
        },
        "courseHours": [],
        "topics": {"completed": 0, "total": 0},
        "upcomingCourseGoals": [],
    }


_ACTIVE_PROGRAM = StudyProgram(id=None, name="StudyLife", is_built_in=True, is_completed=False)


def _run_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    """Feeds one scenario's packaged response through the real `_program_data_from_summary`
    parser and returns a dict shaped like the fixture's own "expected" block, for a direct
    field-by-field comparison."""
    raw_summary = _raw_summary_from_scenario(scenario)
    data = _program_data_from_summary(_ACTIVE_PROGRAM, True, [], [], raw_summary)
    return {
        "streak": data.streak_days,
        "longestStreak": data.longest_streak_days,
        # data.week_hours/month_hours are the studied-only DTO fields (hours.week/month);
        # week_quota.hours/month_quota.hours stay the planned-window ones - see
        # _raw_summary_from_scenario's docstring.
        "weekStudiedHours": data.week_hours,
        "weekQuotaHours": data.week_quota.hours,
        "weekQuotaPercent": data.week_quota.percent,
        "weekQuotaWarning": data.week_quota.warning,
        "weekQuotaMissingHours": data.week_quota.missing_hours,
        "monthStudiedHours": data.month_hours,
        "monthQuotaHours": data.month_quota.hours,
        "monthQuotaPercent": data.month_quota.percent,
        "monthQuotaWarning": data.month_quota.warning,
        "monthQuotaMissingHours": data.month_quota.missing_hours,
        "averageGrade": data.average_grade,
        "ectsEarned": data.ects_earned,
        "ectsTotal": data.ects_total,
        "forecastAvailable": data.forecast_date is not None,
        "forecastDate": data.forecast_date,
        "forecastRecentWeeklyHours": data.forecast_recent_weekly_hours,
    }


def _assert_matches(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    """Exact equality throughout, unlike the old version of this test: parsing is a
    straight pass-through (dict lookups + type coercion), not a second implementation of
    the math, so there is no more float-accumulation drift to tolerate with
    `pytest.approx` - see the module docstring and (for history) the removed
    "$fieldNotes" tolerance guidance in metrics-fixtures.json itself, which no longer
    applies to this file."""
    assert actual["streak"] == expected["streak"]
    assert actual["longestStreak"] == expected["longestStreak"]

    assert actual["weekStudiedHours"] == expected["weekStudiedHours"]
    assert actual["weekQuotaHours"] == expected["weekHours"]
    assert actual["weekQuotaPercent"] == expected["weekQuotaPercent"]
    assert actual["weekQuotaWarning"] == expected["weekQuotaWarning"]
    assert actual["weekQuotaMissingHours"] == expected["weekQuotaMissingHours"]

    assert actual["monthStudiedHours"] == expected["monthStudiedHours"]
    assert actual["monthQuotaHours"] == expected["monthHours"]
    assert actual["monthQuotaPercent"] == expected["monthQuotaPercent"]
    assert actual["monthQuotaWarning"] == expected["monthQuotaWarning"]
    assert actual["monthQuotaMissingHours"] == expected["monthQuotaMissingHours"]

    assert actual["averageGrade"] == expected["averageGrade"]

    assert actual["ectsEarned"] == expected["ectsEarned"]
    assert actual["ectsTotal"] == expected["ectsTotal"]

    assert actual["forecastAvailable"] == expected["forecastAvailable"]
    if expected["forecastAvailable"]:
        expected_date = date.fromisoformat(expected["forecastDate"][:10])
        assert actual["forecastDate"] == expected_date
        assert actual["forecastRecentWeeklyHours"] == expected["forecastRecentWeeklyHours"]
    else:
        assert actual["forecastDate"] is None


def test_fixture_file_has_scenarios(fixtures: dict[str, Any]) -> None:
    names = _scenario_ids(fixtures)
    assert names, "docs/api/metrics-fixtures.json has no scenarios"
    assert len(names) == len(set(names)), "duplicate scenario names in the fixture file"


def test_coordinator_parsing_matches_fixture(fixtures: dict[str, Any]) -> None:
    """Runs every scenario in one test body (rather than one parametrized test per scenario)
    so the fixture-loading network fetch stays inside a single, ordinary function-scoped
    fixture - `pytest_generate_tests`-based dynamic parametrization would need that same fetch
    at COLLECTION time, before any test has even started (see _network_fetch.py: the socket
    unblocking it needs can only run from inside a fixture/test body).

    Failures for every scenario are collected and reported together (one assertion at the
    end lists every mismatch by name) instead of stopping at the first one, so a single test
    run surfaces the full picture."""
    failures: list[str] = []

    for scenario in fixtures["scenarios"]:
        name = scenario["name"]
        actual = _run_scenario(scenario)
        try:
            _assert_matches(actual, scenario["expected"])
        except AssertionError as exc:
            failures.append(f"{name}: {exc}")

    assert not failures, "Scenarios whose parsed values don't match the fixture:\n" + "\n".join(failures)
