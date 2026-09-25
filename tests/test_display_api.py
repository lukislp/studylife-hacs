"""Tests for DisplayApiClient (custom_components/studylife/display_api.py).

Deliberately standalone: aiohttp + aioresponses only, no Home Assistant test harness -
mirrors test_api.py's own approach for the StudyLife account client.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import aiohttp
import pytest
import yarl
from aioresponses import aioresponses

from custom_components.studylife.display_api import (
    DisplayApiAuthError,
    DisplayApiClient,
    DisplayApiError,
)

BASE_URL = "http://display.test:8795"
TOKEN = "tok123"


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as s:
        yield s


@pytest.fixture
def client(session: aiohttp.ClientSession) -> DisplayApiClient:
    return DisplayApiClient(BASE_URL, session, TOKEN)


def _calls(m: aioresponses, method: str, url: str) -> list:
    return m.requests[(method, yarl.URL(url))]


async def test_get_state_ok(client: DisplayApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/state", payload={"status": "ok"})
        assert await client.async_get_state() == {"status": "ok"}


async def test_get_state_accepts_503_as_a_legitimate_error_payload(
    client: DisplayApiClient,
) -> None:
    """/api/state answers 503 when status is "error" - still valid JSON, not a
    client-side failure to raise on."""
    with aioresponses() as m:
        m.get(
            f"{BASE_URL}/api/state",
            status=503,
            payload={"status": "error", "setup": False},
        )
        assert await client.async_get_state() == {"status": "error", "setup": False}


async def test_get_layouts(client: DisplayApiClient) -> None:
    with aioresponses() as m:
        m.get(
            f"{BASE_URL}/api/layouts",
            payload={"choice": "auto", "resolved": "focus", "options": []},
        )
        result = await client.async_get_layouts()
        assert result["choice"] == "auto"


async def test_set_layout_posts_the_chosen_key(client: DisplayApiClient) -> None:
    with aioresponses() as m:
        m.post(f"{BASE_URL}/api/layout", payload={"outcome": "refreshed"})
        result = await client.async_set_layout("week")
        assert result == {"outcome": "refreshed"}
        request = _calls(m, "POST", f"{BASE_URL}/api/layout")[0]
        assert request.kwargs["json"] == {"layout": "week"}


async def test_refresh(client: DisplayApiClient) -> None:
    with aioresponses() as m:
        m.post(f"{BASE_URL}/api/refresh", payload={"outcome": "refreshed"})
        assert await client.async_refresh() == {"outcome": "refreshed"}


async def test_every_request_sends_the_bearer_token(client: DisplayApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/state", payload={"status": "ok"})
        await client.async_get_state()
        request = _calls(m, "GET", f"{BASE_URL}/api/state")[0]
        assert request.kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"


# --------------------------------------------------------------------------
# current.png
# --------------------------------------------------------------------------


async def test_get_current_png_returns_bytes(client: DisplayApiClient) -> None:
    with aioresponses() as m:
        m.get(
            f"{BASE_URL}/api/current.png", body=b"\x89PNGfake", content_type="image/png"
        )
        assert await client.async_get_current_png() == b"\x89PNGfake"


async def test_get_current_png_returns_none_before_the_first_frame(
    client: DisplayApiClient,
) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/current.png", status=404)
        assert await client.async_get_current_png() is None


async def test_get_current_png_wrong_token_raises_auth_error(
    client: DisplayApiClient,
) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/current.png", status=401)
        with pytest.raises(DisplayApiAuthError):
            await client.async_get_current_png()


# --------------------------------------------------------------------------
# Auth/error mapping
# --------------------------------------------------------------------------


async def test_wrong_token_is_401_raises_auth_error(client: DisplayApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/state", status=401)
        with pytest.raises(DisplayApiAuthError):
            await client.async_get_state()


async def test_api_off_entirely_is_404_raises_auth_error(
    client: DisplayApiClient,
) -> None:
    """DISPLAY_API_TOKEN not set on the display 404s every /api/ route - same
    exception type as a wrong token (both mean "fix the connection"), distinct
    message (see display_api.py's docstring)."""
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/state", status=404)
        with pytest.raises(DisplayApiAuthError, match="404"):
            await client.async_get_state()


async def test_server_error_raises_generic_error(client: DisplayApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/layouts", status=500)
        with pytest.raises(DisplayApiError):
            await client.async_get_layouts()


async def test_timeout_raises_display_api_error(
    client: DisplayApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _raise_timeout(*args: object, **kwargs: object) -> None:
        raise asyncio.TimeoutError

    monkeypatch.setattr(client._session, "request", _raise_timeout)
    with pytest.raises(DisplayApiError, match="Timeout"):
        await client.async_get_state()


async def test_test_connection_success_does_not_raise(client: DisplayApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/state", payload={"status": "ok"})
        await client.async_test_connection()


async def test_test_connection_bad_token_raises(client: DisplayApiClient) -> None:
    with aioresponses() as m:
        m.get(f"{BASE_URL}/api/state", status=401)
        with pytest.raises(DisplayApiAuthError):
            await client.async_test_connection()
