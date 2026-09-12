"""Atheris fuzz harness for the coordinator's API-payload parsers and the small helpers.

Contract under test: every parser that turns a StudyLife API object into a dataclass
(_to_session, _parse_quota, _parse_achievements, ...) either returns or raises one of the
"this payload is not what the API promised" errors - KeyError, ValueError, TypeError,
AttributeError - which _async_update_data turns into UpdateFailed; never anything else.
_normalize_url and _excerpt never raise and keep their documented shape.

Run locally (Linux, needs the atheris wheel):
    pip install --require-hashes -r requirements_test.txt -r requirements_fuzz.txt
    PYTHONPATH=. python fuzz/fuzz_coordinator.py -max_total_time=60
CI runs the same harness for a short, fixed time budget (see .github/workflows/ci-cd.yml).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

import atheris

from custom_components.studylife.config_flow import _normalize_url
from custom_components.studylife.coordinator import (
    _parse_achievements,
    _parse_course_hours,
    _parse_forecast,
    _parse_neglected_course,
    _parse_next_course_goal,
    _parse_quota,
    _parse_weekly_report,
    _to_session,
    _to_study_program,
    _to_timer_state,
)
from custom_components.studylife.sensor import _excerpt

EXPECTED = (KeyError, ValueError, TypeError, AttributeError)

OBJECT_PARSERS: tuple[Callable[[Any], Any], ...] = (
    _to_session,
    _to_study_program,
    _to_timer_state,
    _parse_quota,
    _parse_forecast,
    _parse_next_course_goal,
    _parse_neglected_course,
    _parse_weekly_report,
    _parse_achievements,
)

# Keys the parsers read, so the fuzzer can hit the field-level logic instead of only the
# "missing key" branch of a random JSON document.
KEYS = (
    "id", "courseId", "courseName", "courseColor", "startTime", "endTime", "topic", "notes",
    "isCompleted", "timerModeId", "recurrenceGroupId", "name", "isBuiltIn", "sessionId",
    "isRunning", "isBreak", "currentRound", "phaseEndsAt", "hours", "targetMin", "targetMax",
    "percent", "warning", "missingHours", "available", "date", "recentWeeklyHours",
    "targetDate", "daysLeft", "sessionCount", "lastStudied", "daysSince", "weekId",
    "deltaVsPreviousWeek", "topCourseName", "tiers", "unlocked", "category", "threshold",
    "current",
)  # fmt: skip


def _timestamp(fdp: atheris.FuzzedDataProvider) -> str:
    stamp = (
        f"{fdp.ConsumeIntInRange(1, 9999):04d}-{fdp.ConsumeIntInRange(0, 13):02d}-"
        f"{fdp.ConsumeIntInRange(0, 32):02d}T{fdp.ConsumeIntInRange(0, 24):02d}:"
        f"{fdp.ConsumeIntInRange(0, 60):02d}:{fdp.ConsumeIntInRange(0, 60):02d}"
    )
    suffix = fdp.ConsumeIntInRange(0, 2)
    if suffix == 1:
        stamp += "Z"
    elif suffix == 2:
        stamp += f"+{fdp.ConsumeIntInRange(0, 14):02d}:00"
    return stamp


def _value(fdp: atheris.FuzzedDataProvider, depth: int = 0) -> Any:
    kind = fdp.ConsumeIntInRange(0, 7)
    if kind == 0:
        return None
    if kind == 1:
        return fdp.ConsumeBool()
    if kind == 2:
        return fdp.ConsumeInt(4)
    if kind == 3:
        return fdp.ConsumeFloat()
    if kind == 4:
        return _timestamp(fdp)
    if kind == 5 or depth > 2:
        return fdp.ConsumeUnicodeNoSurrogates(24)
    if kind == 6:
        return [_value(fdp, depth + 1) for _ in range(fdp.ConsumeIntInRange(0, 3))]
    return _payload(fdp, depth + 1)


def _payload(fdp: atheris.FuzzedDataProvider, depth: int = 0) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for _ in range(fdp.ConsumeIntInRange(0, 12)):
        key = KEYS[fdp.ConsumeIntInRange(0, len(KEYS) - 1)]
        payload[key] = _value(fdp, depth)
    return payload


def test_one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)

    # Either a structured payload built from the keys above or a raw JSON document.
    if fdp.ConsumeBool():
        payload: Any = _payload(fdp)
    else:
        try:
            payload = json.loads(fdp.ConsumeUnicodeNoSurrogates(512))
        except ValueError:
            payload = {}

    for parser in OBJECT_PARSERS:
        try:
            parser(payload)
        except EXPECTED:
            pass
    try:
        _parse_course_hours(payload if isinstance(payload, list) else [payload])
    except EXPECTED:
        pass

    url = _normalize_url(fdp.ConsumeUnicodeNoSurrogates(64))
    if not url.startswith(("http://", "https://")) or (
        len(url) > 8 and url.endswith("/")
    ):
        raise AssertionError(f"_normalize_url produced {url!r}")

    text = fdp.ConsumeUnicodeNoSurrogates(200)
    limit = fdp.ConsumeIntInRange(1, 150)
    excerpt = _excerpt(text, limit)
    if excerpt is not None and len(excerpt) > limit + 1:
        raise AssertionError(f"_excerpt({limit}) returned {len(excerpt)} characters")


if __name__ == "__main__":
    atheris.instrument_all()
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()
