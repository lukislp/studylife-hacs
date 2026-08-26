"""Thin async client for the StudyLife REST API.

The API is protected by a per-user, long-lived API key generated from the
StudyLife app's Setup page ("Home Assistant" card - see docs/ARCHITECTURE.md);
this client sends it as an X-Api-Key header on every request. The key never
rotates and never expires - it stays valid until the user regenerates or
revokes it in the app, so there is no rotation-adoption machinery here anymore
(the previous global key rotated monthly and announced its successor via an
X-Api-Key-Rotated response header; that server mechanism is gone). A 401 -
key regenerated/revoked in the app, or never configured - raises
StudyLifeApiAuthError so the coordinator can trigger Home Assistant's reauth
flow. Every call is a plain JSON GET/POST/PUT/DELETE against the ASP.NET Core
backend in src/StudyLife.Server/Controllers. GETs on endpoints where the
server emits ETags are sent conditionally (If-None-Match), so a 304 Not
Modified reuses the previously parsed body instead of re-parsing an identical
download every poll cycle.

Metrics (streak/quotas/ECTS/forecast/achievements/...) are NOT computed here or
in coordinator.py - GET /api/metrics/summary and GET /api/metrics/achievements
return them pre-computed by StudyLife.Shared, the same code the app itself
runs (owner decision: every metric lives in exactly ONE place). Those two
endpoints are newer than the rest of this API surface, so a server that
predates them answers with a plain 404 - see StudyLifeApiEndpointMissingError.
"""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import REQUEST_TIMEOUT

# Shown (wrapped in a coordinator UpdateFailed) when GET /api/metrics/summary or GET
# /api/metrics/achievements 404s - see StudyLifeApiEndpointMissingError. Names the README's
# minimum-version note explicitly so the failure is actionable, not just "something's wrong".
_METRICS_ENDPOINT_MISSING_HINT = (
    "the StudyLife server does not expose this endpoint yet - it predates the server release "
    "that added Home Assistant metrics support (see this integration's README for the minimum "
    "StudyLife server version). Update the StudyLife server, then reload this integration."
)


class StudyLifeApiError(Exception):
    """Raised when the StudyLife API can't be reached or returns an error."""


class StudyLifeApiAuthError(StudyLifeApiError):
    """Raised when the server rejects the API key (HTTP 401)."""


class StudyLifeApiEndpointMissingError(StudyLifeApiError):
    """Raised when the server answers a call this client version expects to succeed
    with a plain 404 - in practice this means the StudyLife server predates the
    release that added the endpoint (currently only GET /api/metrics/summary and GET
    /api/metrics/achievements - the "ha metrics" server release), not that some
    specific resource is missing. Distinct from the generic StudyLifeApiError so the
    coordinator's UpdateFailed message names the real, actionable cause ("update your
    StudyLife server") instead of a generic "error fetching ..." string - see the
    `missing_endpoint_hint` plumbing in `_request`/`_get` below."""


class StudyLifeApiClient:
    """Talks to /api/sessions, /api/settings, /api/notes and /api/coursegoals."""

    def __init__(self, base_url: str, session: aiohttp.ClientSession, api_key: str | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._api_key = api_key or None
        # Conditional-GET cache: request path (incl. query string, since /api/sessions/history
        # is called with parameters) -> (etag, parsed JSON body). The server returns ETag
        # headers on its cached GET endpoints (/api/sessions, /api/sessions/history,
        # /api/settings, /api/courses - see src/StudyLife.Server/Services/CacheHelper.cs) and
        # answers a matching If-None-Match with an empty 304, so unchanged data isn't
        # re-downloaded every poll cycle. Endpoints without an ETag (e.g. /api/notes,
        # /api/coursegoals, /api/timerstate) never enter the cache and behave as before.
        # In-memory only: a Home Assistant restart simply does one full fetch again.
        self._etag_cache: dict[str, tuple[str, Any]] = {}

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def api_key(self) -> str | None:
        """The long-lived per-user key this client authenticates with."""
        return self._api_key

    async def _request(
        self, method: str, path: str, json: Any = None, *, missing_endpoint_hint: str | None = None
    ) -> Any:
        url = f"{self._base_url}{path}"
        headers = {"X-Api-Key": self._api_key} if self._api_key else {}
        cached = self._etag_cache.get(path) if method == "GET" else None
        if cached is not None:
            headers["If-None-Match"] = cached[0]
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await self._session.request(method, url, json=json, headers=headers)
                if response.status == 401:
                    # Key rejected - it was regenerated/revoked in the StudyLife app (the
                    # long-lived key never expires by itself). Distinct exception type so
                    # the coordinator can surface Home Assistant's reauth flow instead of
                    # a generic failure.
                    response.release()
                    raise StudyLifeApiAuthError(
                        f"API key rejected (401) by {url} - re-pair (StudyLife app's Setup page,"
                        ' "Home Assistant" card)'
                    )
                if response.status == 404 and missing_endpoint_hint is not None:
                    # Caller told us in advance what a 404 on THIS path means (see
                    # async_get_metrics_summary/async_get_metrics_achievements below) -
                    # a generic StudyLifeApiError from raise_for_status() further down would
                    # be technically correct but useless to a user staring at an UpdateFailed
                    # notification, so this raises a distinct, actionable error instead.
                    response.release()
                    raise StudyLifeApiEndpointMissingError(f"{method} {url} returned 404 - {missing_endpoint_hint}")
                if cached is not None and response.status == 304:
                    return cached[1]
                response.raise_for_status()
                if response.content_length == 0 or response.status == 204:
                    return None
                body = await response.json()
                if method == "GET":
                    etag = response.headers.get("ETag")
                    if etag:
                        self._etag_cache[path] = (etag, body)
                    else:
                        # Server (no longer) sends an ETag for this path - drop any stale entry.
                        self._etag_cache.pop(path, None)
                return body
        except asyncio.TimeoutError as err:
            raise StudyLifeApiError(f"Timeout fetching {url}") from err
        except aiohttp.ClientError as err:
            raise StudyLifeApiError(f"Error fetching {url}: {err}") from err

    async def _get(self, path: str, *, missing_endpoint_hint: str | None = None) -> Any:
        return await self._request("GET", path, missing_endpoint_hint=missing_endpoint_hint)

    async def async_get_sessions(self) -> list[dict[str, Any]]:
        return await self._get("/api/sessions")

    async def async_get_session_history(self, days: int = 400, only_completed: bool = False) -> list[dict[str, Any]]:
        """Long-range session history - /api/sessions only covers ±7/90 days, too
        narrow for streak/month-quota calculations that look further back."""
        only_completed_param = "true" if only_completed else "false"
        return await self._get(f"/api/sessions/history?days={days}&onlyCompleted={only_completed_param}")

    async def async_get_settings(self) -> dict[str, Any]:
        return await self._get("/api/settings")

    async def async_get_notes(self) -> list[dict[str, Any]]:
        return await self._get("/api/notes")

    async def async_get_course_goals(self) -> list[dict[str, Any]]:
        return await self._get("/api/coursegoals")

    async def async_get_courses(self, program_id: int | None = None) -> list[dict[str, Any]]:
        """Course catalog. Without `program_id` the server resolves the ACTIVE study
        programme from the settings; with one, that specific programme's catalog is
        returned (0 = the built-in catalog, matching CoursesController's convention).
        The programme-specific URL keeps each programme's ETag cache entry separate."""
        if program_id is None:
            return await self._get("/api/courses")
        return await self._get(f"/api/courses?program={program_id}")

    async def async_get_study_programs(self) -> list[dict[str, Any]]:
        """List of all study programmes (the built-in one plus any custom ones the
        user created), each {id, name, isBuiltIn, isCompleted} - id is null for the
        built-in entry. Doesn't take a `program` filter itself; which one is active
        lives on /api/settings (activeStudyProgramId)."""
        return await self._get("/api/studyprograms")

    async def async_get_timer_state(self) -> dict[str, Any]:
        return await self._get("/api/timerstate")

    async def async_get_metrics_summary(self, program_id: int | None = None) -> dict[str, Any]:
        """GET /api/metrics/summary - every dashboard metric StudyLife.Shared computes for
        ONE study programme in a single response (streak, week/month quota, ECTS, average
        grade, forecast, course hours, neglected course, weekly report, topics, month
        comparison, upcoming course goals - see docs/api's metrics contract for the exact
        shape). `program_id=None` lets the server resolve the caller's ACTIVE programme
        (same convention as async_get_courses); an explicit id (0 = built-in) asks for one
        specific programme - the coordinator calls this once per study programme.

        Deliberately never sends the contract's optional `now=` override: that exists so
        server-side fixture tests can call deterministically, not for production traffic -
        the server's own local clock is authoritative here, matching every other GET this
        client makes. (It would also defeat the ETag cache: a `now=` that changes every poll
        would make every request's cache key unique forever.)"""
        path = "/api/metrics/summary" if program_id is None else f"/api/metrics/summary?program={program_id}"
        return await self._get(path, missing_endpoint_hint=_METRICS_ENDPOINT_MISSING_HINT)

    async def async_get_metrics_achievements(self, program_id: int | None = None) -> dict[str, Any]:
        """GET /api/metrics/achievements - all 44 achievement tiers' unlock state for ONE
        study programme, computed server-side from the exact aggregation
        RunAchievementCheckAsync/BuildAchievements use. Same `program_id` convention as
        async_get_metrics_summary."""
        path = "/api/metrics/achievements" if program_id is None else f"/api/metrics/achievements?program={program_id}"
        return await self._get(path, missing_endpoint_hint=_METRICS_ENDPOINT_MISSING_HINT)

    async def async_create_session(self, session: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/sessions", json=session)

    async def async_update_session(self, session_id: int, session: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", f"/api/sessions/{session_id}", json=session)

    async def async_delete_session(self, session_id: int) -> None:
        await self._request("DELETE", f"/api/sessions/{session_id}")

    async def async_set_course_goal(self, course_id: int, goal: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", f"/api/coursegoals/{course_id}", json=goal)

    async def async_update_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        """PUT /api/settings expects the full UserSettingsDto back, not a partial patch -
        callers should start from the coordinator's last-polled `settings` dict and only
        override the field(s) they mean to change (same merge-then-PUT pattern as
        services.py's handle_update_session/handle_set_course_goal)."""
        return await self._request("PUT", "/api/settings", json=settings)

    async def async_generate_exam_plan(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        """Server-side exam backward-planner (mirrors the browser's Planner page) - spreads
        the course's open topics across free calendar slots up to the exam date and creates
        the sessions directly (no preview step, since there's no browser to confirm one)."""
        return await self._request("POST", "/api/planner/exam-plan", json=request)

    async def async_test_connection(self) -> None:
        """Raise StudyLifeApiError if the base URL doesn't look like a StudyLife server."""
        await self.async_get_settings()
