"""Tests for StudyLifeApiClient (custom_components/studylife/api.py).

Deliberately standalone: aiohttp + aioresponses only, no Home Assistant test
harness (no hass fixture, no pytest_homeassistant_custom_component pieces),
mirroring the fact that api.py itself has zero HA imports.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import aiohttp
import pytest
import yarl
from aioresponses import aioresponses

from custom_components.studylife.api import (
    StudyLifeApiAuthError,
    StudyLifeApiClient,
    StudyLifeApiError,
)

BASE_URL = "http://test"
API_KEY = "key123"


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as s:
        yield s


@pytest.fixture
def client(session: aiohttp.ClientSession) -> StudyLifeApiClient:
    return StudyLifeApiClient(BASE_URL, session, API_KEY)


@pytest.fixture
def client_no_key(session: aiohttp.ClientSession) -> StudyLifeApiClient:
    return StudyLifeApiClient(BASE_URL, session, None)


def _calls(m: aioresponses, method: str, url: str) -> list:
    return m.requests[(method, yarl.URL(url))]


# --------------------------------------------------------------------------
# GET success paths
# --------------------------------------------------------------------------


async def test_get_sessions(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/sessions", payload=[{"id": 1}])
        assert await client.async_get_sessions() == [{"id": 1}]


async def test_get_settings(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/settings", payload={"activeStudyProgramId": 1})
        assert await client.async_get_settings() == {"activeStudyProgramId": 1}


async def test_get_notes(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/notes", payload=[{"id": 1, "text": "hi"}])
        assert await client.async_get_notes() == [{"id": 1, "text": "hi"}]


async def test_get_course_goals(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/coursegoals", payload=[{"courseId": 100}])
        assert await client.async_get_course_goals() == [{"courseId": 100}]


async def test_get_study_programs(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        payload = [{"id": None, "name": "Default", "isBuiltIn": True, "isCompleted": False}]
        m.get(f"{BASE_URL}/api/studyprograms", payload=payload)
        assert await client.async_get_study_programs() == payload


async def test_get_timer_state(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/timerstate", payload={"isRunning": False})
        assert await client.async_get_timer_state() == {"isRunning": False}


# --------------------------------------------------------------------------
# Session history / courses query-string building
# --------------------------------------------------------------------------


async def test_get_session_history_default_params(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/sessions/history?days=400&onlyCompleted=false", payload=[])
        assert await client.async_get_session_history() == []


async def test_get_session_history_custom_params(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/sessions/history?days=30&onlyCompleted=true", payload=[{"id": 1}])
        result = await client.async_get_session_history(days=30, only_completed=True)
        assert result == [{"id": 1}]


async def test_get_courses_without_program_id(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/courses", payload=[{"id": 1}], repeat=True)
        assert await client.async_get_courses() == [{"id": 1}]
        assert await client.async_get_courses(None) == [{"id": 1}]


async def test_get_courses_with_program_id(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/courses?program=5", payload=[{"id": 2}])
        assert await client.async_get_courses(5) == [{"id": 2}]


# --------------------------------------------------------------------------
# Write methods: correct verb/url/body sent, correct value returned
# --------------------------------------------------------------------------


async def test_create_session_posts_body_and_returns_json(client: StudyLifeApiClient) -> None:
    body = {"courseId": 100, "startTime": "2026-01-06T10:00:00"}
    with aioresponses() as m:
        m.post(f"{BASE_URL}/api/sessions", payload={"id": 1, **body})
        result = await client.async_create_session(body)
        assert result == {"id": 1, **body}
        calls = _calls(m, "POST", f"{BASE_URL}/api/sessions")
        assert calls[0].kwargs["json"] == body


async def test_update_session_puts_body_and_returns_json(client: StudyLifeApiClient) -> None:
    body = {"courseId": 100, "topic": "Graphs"}
    with aioresponses() as m:
        m.put(f"{BASE_URL}/api/sessions/42", payload={"id": 42, **body})
        result = await client.async_update_session(42, body)
        assert result == {"id": 42, **body}
        calls = _calls(m, "PUT", f"{BASE_URL}/api/sessions/42")
        assert calls[0].kwargs["json"] == body


async def test_delete_session_sends_delete_and_returns_none(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.delete(f"{BASE_URL}/api/sessions/42", status=204)
        result = await client.async_delete_session(42)
        assert result is None
        calls = _calls(m, "DELETE", f"{BASE_URL}/api/sessions/42")
        assert len(calls) == 1


async def test_set_course_goal_puts_body_and_returns_json(client: StudyLifeApiClient) -> None:
    goal = {"grade": 1.3, "targetDate": "2026-06-01"}
    with aioresponses() as m:
        m.put(f"{BASE_URL}/api/coursegoals/100", payload={"courseId": 100, **goal})
        result = await client.async_set_course_goal(100, goal)
        assert result == {"courseId": 100, **goal}
        calls = _calls(m, "PUT", f"{BASE_URL}/api/coursegoals/100")
        assert calls[0].kwargs["json"] == goal


async def test_update_settings_puts_body_and_returns_json(client: StudyLifeApiClient) -> None:
    settings = {"activeStudyProgramId": 2}
    with aioresponses() as m:
        m.put(f"{BASE_URL}/api/settings", payload=settings)
        result = await client.async_update_settings(settings)
        assert result == settings
        calls = _calls(m, "PUT", f"{BASE_URL}/api/settings")
        assert calls[0].kwargs["json"] == settings


async def test_generate_exam_plan_posts_body_and_returns_json(client: StudyLifeApiClient) -> None:
    request = {"courseId": 100, "examDate": "2026-06-01"}
    with aioresponses() as m:
        m.post(f"{BASE_URL}/api/planner/exam-plan", payload=[{"id": 1}])
        result = await client.async_generate_exam_plan(request)
        assert result == [{"id": 1}]
        calls = _calls(m, "POST", f"{BASE_URL}/api/planner/exam-plan")
        assert calls[0].kwargs["json"] == request


# --------------------------------------------------------------------------
# async_test_connection
# --------------------------------------------------------------------------


async def test_test_connection_succeeds_silently(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/settings", payload={"activeStudyProgramId": None})
        assert await client.async_test_connection() is None


async def test_test_connection_propagates_auth_error(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/settings", status=401)
        with pytest.raises(StudyLifeApiAuthError):
            await client.async_test_connection()


async def test_test_connection_propagates_api_error(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/settings", status=500)
        with pytest.raises(StudyLifeApiError):
            await client.async_test_connection()


# --------------------------------------------------------------------------
# X-Api-Key header
# --------------------------------------------------------------------------


async def test_api_key_header_sent_when_present(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/settings", payload={})
        await client.async_get_settings()
        calls = _calls(m, "GET", f"{BASE_URL}/api/settings")
        assert calls[0].kwargs["headers"]["X-Api-Key"] == API_KEY


async def test_api_key_header_absent_when_no_key(client_no_key: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/settings", payload={})
        await client_no_key.async_get_settings()
        calls = _calls(m, "GET", f"{BASE_URL}/api/settings")
        assert "X-Api-Key" not in calls[0].kwargs["headers"]


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------


async def test_401_raises_auth_error(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/settings", status=401)
        with pytest.raises(StudyLifeApiAuthError):
            await client.async_get_settings()


async def test_500_raises_api_error_but_not_auth_error(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/settings", status=500)
        with pytest.raises(StudyLifeApiError) as excinfo:
            await client.async_get_settings()
    assert not isinstance(excinfo.value, StudyLifeApiAuthError)


async def test_connection_error_raises_api_error(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/settings", exception=aiohttp.ClientConnectionError("boom"))
        with pytest.raises(StudyLifeApiError):
            await client.async_get_settings()


async def test_timeout_raises_api_error(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/settings", exception=asyncio.TimeoutError())
        with pytest.raises(StudyLifeApiError):
            await client.async_get_settings()


# --------------------------------------------------------------------------
# Empty-body responses (204 / Content-Length: 0)
# --------------------------------------------------------------------------


async def test_delete_returns_none_on_204(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.delete(f"{BASE_URL}/api/sessions/42", status=204)
        assert await client.async_delete_session(42) is None


async def test_get_returns_none_when_content_length_zero(client: StudyLifeApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/settings", status=200, body="", headers={"Content-Length": "0"})
        assert await client.async_get_settings() is None


# --------------------------------------------------------------------------
# ETag conditional-GET cache
# --------------------------------------------------------------------------


async def test_etag_cache_lifecycle(client: StudyLifeApiClient) -> None:
    """First 200 caches (etag, body); a 304 replays the cached body without
    touching JSON parsing; a later 200 with no ETag header drops the cache
    entry again so the next request goes out without If-None-Match."""
    url = f"{BASE_URL}/api/sessions"
    with aioresponses() as m:
        m.get(url, payload=[{"id": 1}], headers={"ETag": '"abc"'})
        m.get(url, status=304)  # no body at all - would blow up a naive .json() call
        m.get(url, payload=[{"id": 2}])  # no ETag this time -> cache should be dropped
        m.get(url, payload=[{"id": 3}], headers={"ETag": '"xyz"'})

        first = await client.async_get_sessions()
        assert first == [{"id": 1}]

        second = await client.async_get_sessions()
        assert second == [{"id": 1}]  # served from cache via the 304

        third = await client.async_get_sessions()
        assert third == [{"id": 2}]

        fourth = await client.async_get_sessions()
        assert fourth == [{"id": 3}]

        calls = _calls(m, "GET", url)
        assert len(calls) == 4
        assert "If-None-Match" not in calls[0].kwargs["headers"]
        assert calls[1].kwargs["headers"]["If-None-Match"] == '"abc"'
        # cache entry is still (abc, [1]) going into this request - the 304 doesn't touch it
        assert calls[2].kwargs["headers"]["If-None-Match"] == '"abc"'
        # cache was popped by the no-ETag 200 above, so no conditional header here
        assert "If-None-Match" not in calls[3].kwargs["headers"]


async def test_etag_cache_is_keyed_per_path(client: StudyLifeApiClient) -> None:
    """/api/courses and /api/courses?program=5 must not share a cache entry."""
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/courses", payload=[{"id": 1}], headers={"ETag": '"a"'})
        m.get(f"{BASE_URL}/api/courses?program=5", payload=[{"id": 2}], headers={"ETag": '"b"'})
        m.get(f"{BASE_URL}/api/courses", status=304)
        m.get(f"{BASE_URL}/api/courses?program=5", status=304)

        assert await client.async_get_courses() == [{"id": 1}]
        assert await client.async_get_courses(5) == [{"id": 2}]
        assert await client.async_get_courses() == [{"id": 1}]
        assert await client.async_get_courses(5) == [{"id": 2}]
