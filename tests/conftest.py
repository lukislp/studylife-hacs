"""Shared fixtures for the StudyLife integration test suite."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife.const import DOMAIN

pytest_plugins = "pytest_homeassistant_custom_component"

TEST_URL = "http://studylife.local:5000"
TEST_API_KEY = "test-api-key"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Home Assistant only loads custom_components/ during tests when this
    fixture (provided by pytest-homeassistant-custom-component) is requested -
    autouse so every test in this suite gets it without repeating it everywhere."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A single-programme StudyLife config entry, not yet added to hass."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="StudyLife",
        data={CONF_URL: TEST_URL, CONF_API_KEY: TEST_API_KEY},
        unique_id=TEST_URL,
    )


@pytest.fixture
def mock_api_client() -> AsyncMock:
    """An AsyncMock standing in for StudyLifeApiClient, pre-wired with empty-but-
    valid responses for every fetch the coordinator makes during a refresh, so
    tests only need to override the handful of return_values they actually care
    about instead of re-specifying the whole surface every time."""
    client = AsyncMock()
    client.base_url = TEST_URL
    client.api_key = TEST_API_KEY
    client.async_get_sessions.return_value = []
    client.async_get_session_history.return_value = []
    client.async_get_settings.return_value = {}
    client.async_get_notes.return_value = []
    client.async_get_course_goals.return_value = []
    client.async_get_timer_state.return_value = {}
    client.async_get_study_programs.return_value = []
    client.async_get_courses.return_value = []
    return client


def make_raw_session(
    *,
    id: int = 1,
    course_id: int = 100,
    course_name: str = "Algorithms",
    course_color: str = "#ff0000",
    start: str = "2026-01-06T10:00:00",
    end: str = "2026-01-06T11:00:00",
    topic: str | None = None,
    notes: str | None = None,
    is_completed: bool = True,
    timer_mode_id: int = 1,
    recurrence_group_id: str | None = None,
) -> dict[str, Any]:
    """Build a raw (server-shaped, camelCase) session dict, as api.py's
    _to_session expects to consume it."""
    return {
        "id": id,
        "courseId": course_id,
        "courseName": course_name,
        "courseColor": course_color,
        "startTime": start,
        "endTime": end,
        "topic": topic,
        "notes": notes,
        "isCompleted": is_completed,
        "timerModeId": timer_mode_id,
        "recurrenceGroupId": recurrence_group_id,
    }


def make_session(**kwargs: Any):
    """Build a StudySession dataclass instance directly, for tests exercising
    the _calc_* helpers (which take StudySession, not raw dicts)."""
    from custom_components.studylife.coordinator import StudySession

    defaults: dict[str, Any] = {
        "id": 1,
        "course_id": 100,
        "course_name": "Algorithms",
        "course_color": "#ff0000",
        "start": datetime(2026, 1, 6, 10, 0),
        "end": datetime(2026, 1, 6, 11, 0),
        "topic": None,
        "notes": None,
        "is_completed": True,
        "timer_mode_id": 1,
        "recurrence_group_id": None,
    }
    defaults.update(kwargs)
    return StudySession(**defaults)


def make_course(
    *,
    id: int = 100,
    name: str = "Algorithms",
    color: str = "#ff0000",
    ects: int = 5,
    semester: int = 1,
    group: str | None = None,
    topics: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "name": name,
        "color": color,
        "ects": ects,
        "semester": semester,
        "group": group,
        "topics": topics or [],
    }


def make_raw_study_program(
    *,
    id: int | None = None,
    name: str = "Default",
    is_built_in: bool = True,
    is_completed: bool = False,
) -> dict[str, Any]:
    """Build a raw (server-shaped) study-programme dict, as api.py's
    _to_study_program expects to consume it. id=None mirrors the server's
    built-in-programme convention."""
    return {"id": id, "name": name, "isBuiltIn": is_built_in, "isCompleted": is_completed}


async def setup_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api_client: AsyncMock
):
    """Runs the real integration setup path (custom_components/studylife/__init__.py):
    patches StudyLifeApiClient so the coordinator's first refresh is driven by
    `mock_api_client`, then forwards to every platform exactly like a real HA
    startup would. Returns the live StudyLifeCoordinator instance from
    hass.data, so callers can drive further refreshes (e.g. to exercise dynamic
    per-programme entity discovery) without a second full setup."""
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.studylife.StudyLifeApiClient", return_value=mock_api_client
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    return hass.data[DOMAIN][mock_config_entry.entry_id]


def get_entity_id(
    hass: HomeAssistant,
    entry_id: str,
    key: str,
    *,
    program_id: str | None = None,
    platform: str = "sensor",
) -> str | None:
    """Looks up the entity_id HA assigned for a given description key via the
    entity registry, reconstructing the exact unique_id scheme entity.py uses
    (see StudyLifeEntity.__init__) - avoids guessing at slugified names, which
    depend on translation loading and device-name collisions."""
    unique_id = (
        f"{entry_id}_{key}" if program_id is None else f"{entry_id}_program_{program_id}_{key}"
    )
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)


def make_course_goal(
    *,
    course_id: int = 100,
    course_name: str = "Algorithms",
    grade: float | None = None,
    target_date: str | None = None,
    completed_at: str | None = None,
    completed_topics: str = "",
) -> dict[str, Any]:
    return {
        "courseId": course_id,
        "courseName": course_name,
        "grade": grade,
        "targetDate": target_date,
        "completedAt": completed_at,
        "completedTopics": completed_topics,
    }
