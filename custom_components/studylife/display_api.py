"""Thin async client for studylife-display's optional bearer-token JSON API.

A completely separate service from the StudyLife server itself: this talks directly to
a studylife-display Raspberry Pi's local web interface on the LAN (see that project's
README, "JSON API" section), authenticating with `Authorization: Bearer <token>` rather
than StudyLife's X-Api-Key. The API is off unless DISPLAY_API_TOKEN is set on the
display, in which case every route 404s - indistinguishable by status alone from a
right-looking-but-wrong token (also 401, see below), so both raise DisplayApiAuthError
with a message naming both possibilities.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import REQUEST_TIMEOUT


class DisplayApiError(Exception):
    """Raised when the display's API can't be reached or returns an error."""


class DisplayApiAuthError(DisplayApiError):
    """Raised on a 401 (wrong token) or a 404 (the API is off entirely on that
    display - DISPLAY_API_TOKEN not set there) - both mean Home Assistant cannot use
    the connection as configured, so the coordinator triggers reauth for either."""


class DisplayApiClient:
    """Talks to /api/state, /api/layouts, /api/layout, /api/refresh and
    /api/current.png - see studylife-display's api.py for the exact shapes."""

    def __init__(
        self,
        base_url: str,
        session: aiohttp.ClientSession,
        token: str,
        *,
        verify_ssl: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._token = token
        self._verify_ssl = verify_ssl

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def token(self) -> str:
        return self._token

    @property
    def verify_ssl(self) -> bool:
        return self._verify_ssl

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        ok_statuses: frozenset[int] = frozenset({200}),
    ) -> Any:
        url = f"{self._base_url}{path}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await self._session.request(
                    method,
                    url,
                    json=json,
                    headers=self._headers(),
                    ssl=self._verify_ssl,
                )
                if response.status == 404:
                    response.release()
                    raise DisplayApiAuthError(
                        f"{method} {url} returned 404 - the display's JSON API is off "
                        "(DISPLAY_API_TOKEN not set there) or this studylife-display "
                        "version predates it"
                    )
                if response.status == 401:
                    response.release()
                    raise DisplayApiAuthError(
                        f"{method} {url} returned 401 - the token was rejected; check "
                        "DISPLAY_API_TOKEN on the display matches what was entered here"
                    )
                if response.status not in ok_statuses:
                    response.raise_for_status()
                return await response.json()
        except asyncio.TimeoutError as err:
            raise DisplayApiError(f"Timeout fetching {url}") from err
        except aiohttp.ClientError as err:
            raise DisplayApiError(f"Error fetching {url}: {err}") from err

    async def async_get_state(self) -> dict[str, Any]:
        """GET /api/state - a 503 is a legitimate `"status": "error"` payload (e.g. no
        StudyLife account connected on that display yet), not a client failure."""
        return await self._request(
            "GET", "/api/state", ok_statuses=frozenset({200, 503})
        )

    async def async_get_layouts(self) -> dict[str, Any]:
        return await self._request("GET", "/api/layouts")

    async def async_set_layout(self, layout: str) -> dict[str, Any]:
        return await self._request("POST", "/api/layout", json={"layout": layout})

    async def async_refresh(self) -> dict[str, Any]:
        return await self._request("POST", "/api/refresh")

    async def async_get_current_png(self) -> bytes | None:
        """The frame currently on the panel, or None before the first one (404) - a
        display-side "nothing shown yet" is not an error worth surfacing as one."""
        url = f"{self._base_url}/api/current.png"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await self._session.get(
                    url, headers=self._headers(), ssl=self._verify_ssl
                )
                if response.status == 404:
                    response.release()
                    return None
                if response.status == 401:
                    response.release()
                    raise DisplayApiAuthError(
                        f"GET {url} returned 401 - the token was rejected"
                    )
                response.raise_for_status()
                return await response.read()
        except asyncio.TimeoutError as err:
            raise DisplayApiError(f"Timeout fetching {url}") from err
        except aiohttp.ClientError as err:
            raise DisplayApiError(f"Error fetching {url}: {err}") from err

    async def async_test_connection(self) -> None:
        """Raise DisplayApiAuthError/DisplayApiError if the token/URL don't work."""
        await self.async_get_state()
