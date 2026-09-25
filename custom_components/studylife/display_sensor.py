"""Status sensor for a studylife-display device.

Its state is what is ACTUALLY on the panel right now: current_frame's layout when a
dashboard is shown, or the frame's kind ("error"/"setup") when it isn't - not the
persisted CHOICE, which can be "auto" and therefore doesn't itself name a layout, and not
the "resolved" layout GET /api/layouts reports, which studylife-display computes from
sample data whenever there is no cache yet and so can claim a layout that was never
actually drawn. "resolved" is only used as a last resort, before the panel has shown
anything at all (current_frame is still None).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .display_coordinator import DisplayCoordinator
from .display_entity import StudyLifeDisplayEntity


async def async_setup_display_sensor_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: DisplayCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([StudyLifeDisplayCurrentLayoutSensor(coordinator, entry)])


class StudyLifeDisplayCurrentLayoutSensor(StudyLifeDisplayEntity, SensorEntity):
    _attr_icon = "mdi:tablet-dashboard"
    _attr_translation_key = "display_current_layout"

    def __init__(self, coordinator: DisplayCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "current_layout")

    @property
    def native_value(self) -> str | None:
        data = self.data
        if data.current_frame_kind is not None:
            # A real frame record exists - trust it over "resolved", which studylife-
            # display computes from sample data whenever there is no cache yet and
            # would otherwise claim a layout that was never actually drawn (e.g. while
            # the panel is genuinely showing the setup/error screen, kind != "dashboard").
            return (
                data.current_frame_layout
                if data.current_frame_kind == "dashboard"
                else data.current_frame_kind
            )
        # Nothing has ever been shown on the panel yet - the best available guess.
        return data.resolved_layout

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.data
        return {
            "status": data.status,
            "layout_choice": data.layout_choice,
            "resolved_layout": data.resolved_layout,
            "current_frame_kind": data.current_frame_kind,
            "shown_at": data.current_frame_shown_at.isoformat()
            if data.current_frame_shown_at
            else None,
            "version": data.version,
            "stale_minutes": data.stale_minutes,
            "quiet_hours_active": data.quiet_hours_active,
            "sessions_ok": data.sessions_ok,
            "last_error": data.last_error,
        }
