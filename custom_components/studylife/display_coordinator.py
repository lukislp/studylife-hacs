"""Data update coordinator for a studylife-display config entry.

Polls GET /api/state, GET /api/layouts and GET /api/settings every DEFAULT_DISPLAY_SCAN_INTERVAL seconds
(configurable via the same options flow the StudyLife account entries use) and maps the
JSON payloads onto one DisplayData - see display_api.py for the client and
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

from .const import AUTO_LAYOUT, CONF_DISPLAY_ID, DOMAIN
from .display_api import (
    DisplayApiAuthError,
    DisplayApiClient,
    DisplayApiError,
    DisplayApiNotFoundError,
)
from .display_repairs import (
    UNREACHABLE_THRESHOLD,
    async_report_unreachable,
    async_sync_display_issues,
)

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
    # (the duo release). Older displays don't send these keys, so each has a fallback
    # that reproduces the pre-extension behaviour: just "auto" as the one pseudo choice,
    # and empty lists - display_select.py creates the duo entities only when the display
    # actually reports panes.
    # "auto", ... - the choices that aren't layouts themselves.
    pseudo_options: list[LayoutOption]
    duo: list[str]  # the configured duo pair, left then right
    panes: list[str]  # the layouts that can be a duo half (every layout but "duo")
    # GET /api/settings (studylife-display >= 1.11): the effective values by key and,
    # per key, whether it comes from settings.json (True) or the environment/default
    # (False). Both are empty on a display without that route, which is how the
    # settings entities know not to exist.
    settings: dict[str, Any] = dataclasses.field(default_factory=dict)
    settings_sources: dict[str, bool] = dataclasses.field(default_factory=dict)
    # Stable id of the display (hash of its machine id; same across restarts and address
    # changes), from "id" in /api/state. None on a display that doesn't publish it yet.
    display_id: str | None = None


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


def _parse_settings(raw: Any) -> tuple[dict[str, Any], dict[str, bool]]:
    """(values, sources) of a GET /api/settings document; empty for anything else."""
    if not isinstance(raw, dict):
        return {}, {}
    values = raw.get("values")
    sources = raw.get("sources")
    return (
        dict(values) if isinstance(values, dict) else {},
        (
            {str(key): bool(value) for key, value in sources.items()}
            if isinstance(sources, dict)
            else {}
        ),
    )


def _parse_display_id(raw: Any) -> str | None:
    """The display id off the wire; None unless it is a non-empty string."""
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _parse_display_data(
    state: dict[str, Any],
    layouts: dict[str, Any],
    settings_document: Any = None,
) -> DisplayData:
    frame = state.get("current_frame")
    kind = frame.get("kind") if frame else None
    layout = frame.get("layout") if frame else None
    shown_at = _parse_dt(frame.get("shown_at")) if frame else None
    options = _parse_options(layouts.get("options"))
    pseudo = _parse_options(layouts.get("pseudo")) or list(FALLBACK_PSEUDO_OPTIONS)
    settings, settings_sources = _parse_settings(settings_document)
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
        duo=_parse_keys(layouts.get("duo")),
        panes=_parse_keys(layouts.get("panes")),
        settings=settings,
        settings_sources=settings_sources,
        display_id=_parse_display_id(state.get("id")),
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
        self._failed_polls = 0  # consecutive UpdateFailed, for the "unreachable" repair

    @property
    def client(self) -> DisplayApiClient:
        return self._client

    async def _async_update_data(self) -> DisplayData:
        try:
            state = await self._client.async_get_state()
            layouts = await self._client.async_get_layouts()
            try:
                settings = await self._client.async_get_settings()
            except DisplayApiNotFoundError:
                # A display older than 1.11 has no /api/settings; /api/state just
                # answered, so this is "route unknown", not a bad token. It keeps
                # working, merely without the settings entities.
                settings = None
        except DisplayApiAuthError as err:
            # Wrong or missing DISPLAY_API_TOKEN, or the API is off on that display -
            # ConfigEntryAuthFailed surfaces Home Assistant's reauth repair, which for a
            # display entry re-prompts for just the token (see config_flow.py).
            raise ConfigEntryAuthFailed(str(err)) from err
        except DisplayApiError as err:
            self._failed_polls += 1
            if self.config_entry and self._failed_polls >= UNREACHABLE_THRESHOLD:
                async_report_unreachable(self.hass, self.config_entry)
            raise UpdateFailed(str(err)) from err
        self._failed_polls = 0
        data = _parse_display_data(state, layouts, settings)
        if self.config_entry:
            async_sync_display_issues(self.hass, self.config_entry, data)
            # Persist the stable id so discovery knows it even while the entry is not
            # loaded. The update listener ignores this change (see __init__.py), so it
            # does not reload the entry.
            if (
                data.display_id
                and self.config_entry.data.get(CONF_DISPLAY_ID) != data.display_id
            ):
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**self.config_entry.data, CONF_DISPLAY_ID: data.display_id},
                )
        return data
