"""Data update coordinator for a studylife-display config entry.

Polls GET /api/state and GET /api/layouts every DEFAULT_DISPLAY_SCAN_INTERVAL seconds
(configurable via the same options flow the StudyLife account entries use) and maps the
two JSON payloads onto one DisplayData - see display_api.py for the client and
studylife-display's api.py for the exact wire shapes these two calls return.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timedelta
from typing import Any

import homeassistant.util.dt as dt_util
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .display_api import DisplayApiAuthError, DisplayApiClient, DisplayApiError

_LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass
class LayoutOption:
    key: str
    name: dict[str, str]
    description: dict[str, str]


@dataclasses.dataclass
class DisplayData:
    status: str  # "ok" | "degraded" | "error" | "setup"
    setup: bool
    version: str
    stale_minutes: int | None
    quiet_hours_active: bool
    sessions_ok: bool
    last_error: dict[str, Any] | None
    layout_choice: str  # the persisted preference, e.g. "auto" or "focus"
    resolved_layout: str | None  # what "auto" actually resolves to right now
    current_frame_kind: (
        str | None
    )  # "dashboard" | "error" | "setup", None before ever shown
    current_frame_layout: str | None  # set only when current_frame_kind == "dashboard"
    current_frame_shown_at: datetime | None
    layout_options: list[LayoutOption]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return dt_util.parse_datetime(value)


def _parse_display_data(state: dict[str, Any], layouts: dict[str, Any]) -> DisplayData:
    frame = state.get("current_frame")
    kind = frame.get("kind") if frame else None
    layout = frame.get("layout") if frame else None
    shown_at = _parse_dt(frame.get("shown_at")) if frame else None
    options = [
        LayoutOption(
            key=option["key"],
            name=option.get("name", {}),
            description=option.get("description", {}),
        )
        for option in layouts.get("options", [])
    ]
    return DisplayData(
        status=state.get("status", "error"),
        setup=bool(state.get("setup")),
        version=state.get("version", ""),
        stale_minutes=state.get("stale_minutes"),
        quiet_hours_active=bool(state.get("quiet_hours_active")),
        sessions_ok=bool(state.get("sessions_ok", True)),
        last_error=state.get("last_error"),
        layout_choice=state.get("layout_choice") or layouts.get("choice", "auto"),
        resolved_layout=state.get("layout") or layouts.get("resolved"),
        current_frame_kind=kind,
        current_frame_layout=layout,
        current_frame_shown_at=shown_at,
        layout_options=options,
    )


class DisplayCoordinator(DataUpdateCoordinator[DisplayData]):
    """Coordinates polling of a studylife-display device's JSON API."""

    def __init__(
        self, hass: HomeAssistant, client: DisplayApiClient, update_interval: timedelta
    ) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_display", update_interval=update_interval
        )
        self._client = client

    @property
    def client(self) -> DisplayApiClient:
        return self._client

    async def _async_update_data(self) -> DisplayData:
        try:
            state = await self._client.async_get_state()
            layouts = await self._client.async_get_layouts()
        except DisplayApiAuthError as err:
            # Wrong or missing DISPLAY_API_TOKEN, or the API is off on that display -
            # ConfigEntryAuthFailed surfaces Home Assistant's reauth repair, which for a
            # display entry re-prompts for just the token (see config_flow.py).
            raise ConfigEntryAuthFailed(str(err)) from err
        except DisplayApiError as err:
            raise UpdateFailed(str(err)) from err
        return _parse_display_data(state, layouts)
