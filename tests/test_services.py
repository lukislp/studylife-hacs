"""Tests for the studylife.* services (custom_components/studylife/services.py).

Each service resolves a StudyLifeCoordinator via `_resolve_coordinator` and then
mutates state through `coordinator.client` before triggering a refresh. Rather
than spinning up a full coordinator + API client + config entry setup, these
tests populate `hass.data[DOMAIN]` directly with lightweight coordinator
doubles (`make_coordinator`/`make_coordinator_data`) - `coordinator.data` only
needs to duck-type the handful of StudyLifeData attributes services.py
actually reads (sessions/courses/course_goals/study_programs/settings).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife.api import StudyLifeApiCourseRejectedError
from custom_components.studylife.const import DOMAIN
from custom_components.studylife.coordinator import StudyProgram
from custom_components.studylife.services import _to_naive_iso, async_register_services

from .conftest import make_course, make_session


def test_to_naive_iso_strips_tzinfo_via_local_conversion() -> None:
    """A tz-aware datetime is converted to HA's local time, then the tzinfo is dropped -
    the API expects a naive-local ISO string, not one with a UTC offset suffix.

    dt_util.DEFAULT_TIME_ZONE is process-wide mutable state that other (hass-using)
    tests in the same run may have already changed - pinned to UTC here and restored
    after, so this test's expected value doesn't depend on execution order.
    """
    original_tz = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(dt_util.UTC)
    try:
        aware = datetime(2026, 1, 6, 10, 0, tzinfo=timezone(timedelta(hours=2)))
        result = _to_naive_iso(aware)
        assert "+" not in result and "Z" not in result
        # 10:00+02:00 == 08:00 UTC.
        assert result == "2026-01-06T08:00:00"
        # Already-naive values pass through untouched.
        assert _to_naive_iso(datetime(2026, 1, 6, 10, 0)) == "2026-01-06T10:00:00"
    finally:
        dt_util.set_default_time_zone(original_tz)


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def make_coordinator_data(
    *,
    sessions: list | None = None,
    courses: list | None = None,
    course_goals: list | None = None,
    study_programs: list | None = None,
    settings: dict | None = None,
) -> SimpleNamespace:
    """A duck-typed stand-in for StudyLifeData, carrying only the attributes
    services.py actually reads off `coordinator.data`."""
    return SimpleNamespace(
        sessions=sessions if sessions is not None else [],
        courses=courses if courses is not None else [],
        course_goals=course_goals if course_goals is not None else [],
        study_programs=study_programs if study_programs is not None else [],
        settings=settings if settings is not None else {},
    )


def make_coordinator(
    *,
    client: AsyncMock | None = None,
    data: SimpleNamespace | None = None,
) -> MagicMock:
    """An AsyncMock-flavoured StudyLifeCoordinator double: `.client` is an
    AsyncMock (StudyLifeApiClient stand-in), `.data` duck-types StudyLifeData,
    and `.async_request_refresh` is an awaitable spy."""
    coordinator = MagicMock()
    coordinator.client = client if client is not None else AsyncMock()
    coordinator.data = data if data is not None else make_coordinator_data()
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


async def _register(hass: HomeAssistant) -> None:
    await async_register_services(hass)


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


async def test_create_session_success(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    course = make_course(id=100, name="Algorithms", color="#ff0000")
    coordinator = make_coordinator(client=client, data=make_coordinator_data(courses=[course]))
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    await hass.services.async_call(
        DOMAIN,
        "create_session",
        {
            "course_id": 100,
            "start_time": "2026-01-06T10:00:00",
            "end_time": "2026-01-06T11:00:00",
            "topic": "Sorting",
        },
        blocking=True,
    )

    client.async_create_session.assert_awaited_once()
    payload = client.async_create_session.call_args.args[0]
    assert payload["courseId"] == 100
    assert payload["courseName"] == "Algorithms"
    assert payload["courseColor"] == "#ff0000"
    assert payload["startTime"] == "2026-01-06T10:00:00"
    assert payload["endTime"] == "2026-01-06T11:00:00"
    assert payload["topic"] == "Sorting"
    assert payload["isCompleted"] is False
    assert payload["timerModeId"] == 1
    coordinator.async_request_refresh.assert_awaited_once()


async def test_create_session_course_name_and_color_are_ignored(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """course_name/course_color are deprecated - the server derives them from the
    catalog and rejects client-supplied values, so the payload must always carry
    the catalog-resolved values regardless of what the caller passes."""
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    course = make_course(id=100, name="Algorithms", color="#ff0000")
    coordinator = make_coordinator(client=client, data=make_coordinator_data(courses=[course]))
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    await hass.services.async_call(
        DOMAIN,
        "create_session",
        {
            "course_id": 100,
            "course_name": "Custom Name",
            "course_color": "#123456",
            "start_time": "2026-01-06T10:00:00",
            "end_time": "2026-01-06T11:00:00",
        },
        blocking=True,
    )

    payload = client.async_create_session.call_args.args[0]
    assert payload["courseName"] == "Algorithms"
    assert payload["courseColor"] == "#ff0000"


async def test_create_session_course_not_in_catalog(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = make_coordinator(client=client, data=make_coordinator_data(courses=[]))
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "create_session",
            {
                "course_id": 999,
                "start_time": "2026-01-06T10:00:00",
                "end_time": "2026-01-06T11:00:00",
            },
            blocking=True,
        )

    assert exc_info.value.translation_key == "course_not_in_catalog"
    client.async_create_session.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()


async def test_create_session_stale_catalog_400_raises_clean_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The course_id passes the local catalog pre-check (_require_course) but the
    server still answers 400 - the catalog cache was stale (course completed/
    removed server-side since the last poll). Must surface a clean, actionable
    HomeAssistantError naming the course_id, not the raw StudyLifeApiCourseRejectedError,
    and must trigger an immediate coordinator refresh so the cache is current again."""
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_create_session.side_effect = StudyLifeApiCourseRejectedError(
        100, "POST .../api/sessions returned 400 for courseId 100"
    )
    course = make_course(id=100, name="Algorithms")
    coordinator = make_coordinator(client=client, data=make_coordinator_data(courses=[course]))
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "create_session",
            {
                "course_id": 100,
                "start_time": "2026-01-06T10:00:00",
                "end_time": "2026-01-06T11:00:00",
            },
            blocking=True,
        )

    assert exc_info.value.translation_key == "course_rejected_by_server"
    assert exc_info.value.translation_placeholders == {"course_id": "100"}
    client.async_create_session.assert_awaited_once()
    coordinator.async_request_refresh.assert_awaited_once()


# ---------------------------------------------------------------------------
# update_session
# ---------------------------------------------------------------------------


async def test_update_session_partial_update_keeps_other_fields(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    existing = make_session(
        id=5,
        course_id=100,
        course_name="Algorithms",
        course_color="#ff0000",
        start=datetime(2026, 1, 6, 10, 0),
        end=datetime(2026, 1, 6, 11, 0),
        topic="Old topic",
        notes="Old notes",
        is_completed=False,
        timer_mode_id=1,
    )
    coordinator = make_coordinator(client=client, data=make_coordinator_data(sessions=[existing]))
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    await hass.services.async_call(
        DOMAIN,
        "update_session",
        {"session_id": 5, "notes": "New notes"},
        blocking=True,
    )

    client.async_update_session.assert_awaited_once()
    call_args = client.async_update_session.call_args
    assert call_args.args[0] == 5
    payload = call_args.args[1]
    assert payload["id"] == 5
    assert payload["notes"] == "New notes"
    assert payload["topic"] == "Old topic"
    assert payload["courseId"] == 100
    assert payload["courseName"] == "Algorithms"
    assert payload["startTime"] == "2026-01-06T10:00:00"
    assert payload["endTime"] == "2026-01-06T11:00:00"
    assert payload["isCompleted"] is False
    assert payload["timerModeId"] == 1
    coordinator.async_request_refresh.assert_awaited_once()


async def test_update_session_changing_course_resolves_from_catalog(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    existing = make_session(id=5, course_id=100, course_name="Algorithms", course_color="#ff0000")
    new_course = make_course(id=200, name="Databases", color="#00ff00")
    coordinator = make_coordinator(
        client=client,
        data=make_coordinator_data(sessions=[existing], courses=[new_course]),
    )
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    await hass.services.async_call(
        DOMAIN,
        "update_session",
        {"session_id": 5, "course_id": 200},
        blocking=True,
    )

    payload = client.async_update_session.call_args.args[1]
    assert payload["courseId"] == 200
    assert payload["courseName"] == "Databases"
    assert payload["courseColor"] == "#00ff00"


async def test_update_session_changing_course_ignores_supplied_name_and_color(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Even when changing the course, a caller-supplied course_name/course_color
    must be ignored in favor of the catalog-resolved values (deprecated fields)."""
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    existing = make_session(id=5, course_id=100, course_name="Algorithms", course_color="#ff0000")
    new_course = make_course(id=200, name="Databases", color="#00ff00")
    coordinator = make_coordinator(
        client=client,
        data=make_coordinator_data(sessions=[existing], courses=[new_course]),
    )
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    await hass.services.async_call(
        DOMAIN,
        "update_session",
        {
            "session_id": 5,
            "course_id": 200,
            "course_name": "Custom Name",
            "course_color": "#123456",
        },
        blocking=True,
    )

    payload = client.async_update_session.call_args.args[1]
    assert payload["courseId"] == 200
    assert payload["courseName"] == "Databases"
    assert payload["courseColor"] == "#00ff00"


async def test_update_session_unknown_course_id_raises_without_api_call(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Changing to a course_id that isn't in the catalog must raise course_not_in_catalog
    and never reach the API - the server would reject it with its own 400 anyway."""
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    existing = make_session(id=5, course_id=100, course_name="Algorithms", course_color="#ff0000")
    coordinator = make_coordinator(
        client=client,
        data=make_coordinator_data(sessions=[existing], courses=[]),
    )
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "update_session",
            {"session_id": 5, "course_id": 999},
            blocking=True,
        )

    assert exc_info.value.translation_key == "course_not_in_catalog"
    client.async_update_session.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()


async def test_update_session_stale_catalog_400_raises_clean_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Same staleness window as create_session: the session's existing course_id
    passes the local catalog pre-check (it's not even re-checked when the course
    isn't changing), but the server still answers 400 because that course was
    completed/removed server-side since the last poll."""
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_update_session.side_effect = StudyLifeApiCourseRejectedError(
        100, "PUT .../api/sessions/5 returned 400 for courseId 100"
    )
    existing = make_session(id=5, course_id=100, course_name="Algorithms", course_color="#ff0000")
    coordinator = make_coordinator(client=client, data=make_coordinator_data(sessions=[existing]))
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "update_session",
            {"session_id": 5, "notes": "New notes"},
            blocking=True,
        )

    assert exc_info.value.translation_key == "course_rejected_by_server"
    assert exc_info.value.translation_placeholders == {"course_id": "100"}
    client.async_update_session.assert_awaited_once()
    coordinator.async_request_refresh.assert_awaited_once()


async def test_update_session_not_found(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)
    coordinator = make_coordinator(data=make_coordinator_data(sessions=[]))
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "update_session",
            {"session_id": 999, "notes": "x"},
            blocking=True,
        )

    assert exc_info.value.translation_key == "session_not_found"
    coordinator.async_request_refresh.assert_not_awaited()


# ---------------------------------------------------------------------------
# delete_session
# ---------------------------------------------------------------------------


async def test_delete_session_success(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = make_coordinator(client=client)
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    await hass.services.async_call(DOMAIN, "delete_session", {"session_id": 7}, blocking=True)

    client.async_delete_session.assert_awaited_once_with(7)
    coordinator.async_request_refresh.assert_awaited_once()


# ---------------------------------------------------------------------------
# set_course_goal
# ---------------------------------------------------------------------------


async def test_set_course_goal_success_resolves_name_from_catalog(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    course = make_course(id=100, name="Algorithms")
    coordinator = make_coordinator(
        client=client, data=make_coordinator_data(courses=[course], course_goals=[])
    )
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    await hass.services.async_call(
        DOMAIN,
        "set_course_goal",
        {"course_id": 100, "target_date": "2026-06-15", "grade": 1.7},
        blocking=True,
    )

    client.async_set_course_goal.assert_awaited_once()
    call_args = client.async_set_course_goal.call_args
    assert call_args.args[0] == 100
    payload = call_args.args[1]
    assert payload["courseId"] == 100
    assert payload["courseName"] == "Algorithms"
    assert payload["grade"] == 1.7
    assert payload["targetDate"].startswith("2026-06-15")
    coordinator.async_request_refresh.assert_awaited_once()


async def test_set_course_goal_course_name_is_ignored(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """course_name is deprecated - the server derives it from the catalog, so a
    caller-supplied value must never end up in the payload."""
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    course = make_course(id=100, name="Algorithms")
    coordinator = make_coordinator(
        client=client, data=make_coordinator_data(courses=[course], course_goals=[])
    )
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    await hass.services.async_call(
        DOMAIN,
        "set_course_goal",
        {"course_id": 100, "course_name": "Custom Name", "target_date": "2026-06-15"},
        blocking=True,
    )

    payload = client.async_set_course_goal.call_args.args[1]
    assert payload["courseName"] == "Algorithms"


async def test_set_course_goal_course_not_in_catalog(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = make_coordinator(client=client, data=make_coordinator_data(courses=[], course_goals=[]))
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN, "set_course_goal", {"course_id": 999}, blocking=True
        )

    assert exc_info.value.translation_key == "course_not_in_catalog"
    client.async_set_course_goal.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()


async def test_set_course_goal_stale_catalog_400_raises_clean_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Same staleness window as create_session/update_session, for the course-goal
    write path."""
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_set_course_goal.side_effect = StudyLifeApiCourseRejectedError(
        100, "PUT .../api/coursegoals/100 returned 400 for courseId 100"
    )
    course = make_course(id=100, name="Algorithms")
    coordinator = make_coordinator(client=client, data=make_coordinator_data(courses=[course]))
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "set_course_goal",
            {"course_id": 100, "grade": 1.7},
            blocking=True,
        )

    assert exc_info.value.translation_key == "course_rejected_by_server"
    assert exc_info.value.translation_placeholders == {"course_id": "100"}
    client.async_set_course_goal.assert_awaited_once()
    coordinator.async_request_refresh.assert_awaited_once()


async def test_set_course_goal_course_not_in_catalog_even_with_existing_goal(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Previously, an existing goal's stored courseName let set_course_goal fall back
    to it even for a course_id no longer in the catalog. The server now requires the
    course to resolve every time, so that fallback path must be gone too."""
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    existing_goal = {"courseId": 999, "courseName": "Stale Course", "grade": 2.0}
    coordinator = make_coordinator(
        client=client, data=make_coordinator_data(courses=[], course_goals=[existing_goal])
    )
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN, "set_course_goal", {"course_id": 999, "grade": 1.5}, blocking=True
        )

    assert exc_info.value.translation_key == "course_not_in_catalog"
    client.async_set_course_goal.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()


# ---------------------------------------------------------------------------
# generate_exam_plan
# ---------------------------------------------------------------------------


async def test_generate_exam_plan_success(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = make_coordinator(client=client)
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    await hass.services.async_call(
        DOMAIN,
        "generate_exam_plan",
        {
            "course_id": 100,
            "exam_date": "2026-06-15",
            "session_length_minutes": 60,
            "total_hours": 20,
        },
        blocking=True,
    )

    client.async_generate_exam_plan.assert_awaited_once()
    request = client.async_generate_exam_plan.call_args.args[0]
    assert request["courseId"] == 100
    assert request["examDate"].startswith("2026-06-15")
    assert request["sessionLengthMinutes"] == 60
    assert request["totalHours"] == 20
    coordinator.async_request_refresh.assert_awaited_once()


async def test_generate_exam_plan_stale_catalog_400_raises_clean_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """generate_exam_plan also writes with a courseId (creating sessions server-side)
    and is subject to the same catalog-staleness window as the other write services."""
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_generate_exam_plan.side_effect = StudyLifeApiCourseRejectedError(
        100, "POST .../api/planner/exam-plan returned 400 for courseId 100"
    )
    coordinator = make_coordinator(client=client)
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "generate_exam_plan",
            {"course_id": 100, "exam_date": "2026-06-15"},
            blocking=True,
        )

    assert exc_info.value.translation_key == "course_rejected_by_server"
    assert exc_info.value.translation_placeholders == {"course_id": "100"}
    client.async_generate_exam_plan.assert_awaited_once()
    coordinator.async_request_refresh.assert_awaited_once()


# ---------------------------------------------------------------------------
# set_active_program
# ---------------------------------------------------------------------------


async def test_set_active_program_success(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    program = StudyProgram(id=42, name="Custom", is_built_in=False, is_completed=False)
    coordinator = make_coordinator(
        client=client,
        data=make_coordinator_data(
            study_programs=[program], settings={"activeStudyProgramId": None, "foo": "bar"}
        ),
    )
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    await hass.services.async_call(DOMAIN, "set_active_program", {"program_id": 42}, blocking=True)

    client.async_update_settings.assert_awaited_once()
    payload = client.async_update_settings.call_args.args[0]
    assert payload["activeStudyProgramId"] == 42
    assert payload["foo"] == "bar"
    coordinator.async_request_refresh.assert_awaited_once()


async def test_set_active_program_omitted_switches_to_builtin(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    builtin = StudyProgram(id=None, name="StudyLife", is_built_in=True, is_completed=False)
    coordinator = make_coordinator(
        client=client,
        data=make_coordinator_data(study_programs=[builtin], settings={"activeStudyProgramId": 5}),
    )
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    await hass.services.async_call(DOMAIN, "set_active_program", {}, blocking=True)

    payload = client.async_update_settings.call_args.args[0]
    assert payload["activeStudyProgramId"] is None
    coordinator.async_request_refresh.assert_awaited_once()


async def test_set_active_program_unknown_program(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    coordinator = make_coordinator(data=make_coordinator_data(study_programs=[]))
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN, "set_active_program", {"program_id": 999}, blocking=True
        )

    assert exc_info.value.translation_key == "unknown_program"
    coordinator.async_request_refresh.assert_not_awaited()


# ---------------------------------------------------------------------------
# _resolve_coordinator (exercised through delete_session, the simplest service)
# ---------------------------------------------------------------------------


async def test_resolve_coordinator_single_entry_no_device_id(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = make_coordinator(client=client)
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    await hass.services.async_call(DOMAIN, "delete_session", {"session_id": 1}, blocking=True)

    client.async_delete_session.assert_awaited_once_with(1)
    coordinator.async_request_refresh.assert_awaited_once()


async def test_resolve_coordinator_multiple_entries_no_device_id(hass: HomeAssistant) -> None:
    coordinator1 = make_coordinator()
    coordinator2 = make_coordinator()
    hass.data[DOMAIN] = {"entry1": coordinator1, "entry2": coordinator2}
    await _register(hass)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(DOMAIN, "delete_session", {"session_id": 1}, blocking=True)

    assert exc_info.value.translation_key == "multiple_servers_need_device_id"
    coordinator1.async_request_refresh.assert_not_awaited()
    coordinator2.async_request_refresh.assert_not_awaited()


async def test_resolve_coordinator_no_integration_configured_empty_dict(hass: HomeAssistant) -> None:
    hass.data[DOMAIN] = {}
    await _register(hass)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(DOMAIN, "delete_session", {"session_id": 1}, blocking=True)

    assert exc_info.value.translation_key == "no_integration_configured"


async def test_resolve_coordinator_no_integration_configured_missing_key(hass: HomeAssistant) -> None:
    hass.data.pop(DOMAIN, None)
    await _register(hass)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(DOMAIN, "delete_session", {"session_id": 1}, blocking=True)

    assert exc_info.value.translation_key == "no_integration_configured"


async def test_resolve_coordinator_unknown_device_id(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    coordinator = make_coordinator()
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "delete_session",
            {"session_id": 1, "device_id": "nonexistent-device-id"},
            blocking=True,
        )

    assert exc_info.value.translation_key == "unknown_device"
    coordinator.async_request_refresh.assert_not_awaited()


async def test_resolve_coordinator_device_not_studylife(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    coordinator = make_coordinator()
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    # A device that belongs to a DIFFERENT (non-studylife) config entry - not
    # present in hass.data[DOMAIN], so it can't be mapped to a coordinator even
    # though the device itself is a real, registered device.
    other_entry = MockConfigEntry(domain="other_integration")
    other_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=other_entry.entry_id,
        identifiers={("other_integration", "some-device")},
    )

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "delete_session",
            {"session_id": 1, "device_id": device.id},
            blocking=True,
        )

    assert exc_info.value.translation_key == "device_not_studylife"
    coordinator.async_request_refresh.assert_not_awaited()


async def test_resolve_coordinator_device_id_resolves_matching_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = make_coordinator(client=client)
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator}
    await _register(hass)

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "hub")},
    )

    await hass.services.async_call(
        DOMAIN,
        "delete_session",
        {"session_id": 1, "device_id": device.id},
        blocking=True,
    )

    client.async_delete_session.assert_awaited_once_with(1)
    coordinator.async_request_refresh.assert_awaited_once()
