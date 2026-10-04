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

from .const import AUTO_LAYOUT, DOMAIN
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
    layout_options: list[LayoutOption]  # the real layouts, incl. "duo"
    # Everything below arrived with studylife-display's extended GET /api/layouts
    # (cycle + duo). Older displays don't send these keys, so each has a fallback that
    # reproduces the pre-extension behaviour: just "auto" as the one pseudo choice, and
    # empty lists - display_select.py / display_text.py create the duo and cycle
    # entities only when the display actually reports panes / a cycle.
    # "auto", "cycle", ... - the choices that aren't layouts themselves.
    pseudo_options: list[LayoutOption]
    cycle: list[str]  # the configured cycle order, drawn one per refresh
    duo: list[str]  # the configured duo pair, left then right
    panes: list[str]  # the layouts that can be a duo half (every layout but "duo")
    next_in_cycle: str | None  # what "cycle" would draw on the next refresh


# What an older display (before the extended /api/layouts) implicitly offered: the one
# pseudo choice "auto", named the way studylife-display itself labels it.
FALLBACK_PSEUDO_OPTIONS: list[LayoutOption] = [
    LayoutOption(
        key=AUTO_LAYOUT,
        name={"de": "Automatisch", "en": "Automatic"},
        description={},
    )
]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return dt_util.parse_datetime(value)


def _parse_options(raw: Any) -> list[LayoutOption]:
    return [
        LayoutOption(
            key=option["key"],
            name=option.get("name", {}),
            description=option.get("description", {}),
        )
        for option in raw or []
    ]


def _parse_keys(raw: Any) -> list[str]:
    """A list of layout keys off the wire; anything that isn't a list (missing key on
    an older display, JSON null) is an empty list."""
    if not isinstance(raw, list):
        return []
    return [str(key) for key in raw]


def _parse_display_data(state: dict[str, Any], layouts: dict[str, Any]) -> DisplayData:
    frame = state.get("current_frame")
    kind = frame.get("kind") if frame else None
    layout = frame.get("layout") if frame else None
    shown_at = _parse_dt(frame.get("shown_at")) if frame else None
    options = _parse_options(layouts.get("options"))
    pseudo = _parse_options(layouts.get("pseudo")) or list(FALLBACK_PSEUDO_OPTIONS)
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
        pseudo_options=pseudo,
        cycle=_parse_keys(layouts.get("cycle")),
        duo=_parse_keys(layouts.get("duo")),
        panes=_parse_keys(layouts.get("panes")),
        next_in_cycle=layouts.get("next_in_cycle") or None,
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
