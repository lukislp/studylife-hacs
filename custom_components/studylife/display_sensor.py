"""Sensors for a studylife-display device.

Two kinds:

- The one primary sensor, "current layout": what is ACTUALLY on the panel right now -
  current_frame's layout when a dashboard is shown, or the frame's kind
  ("error"/"setup") when it isn't. Not the persisted CHOICE (that is the select entity,
  and can be "auto", which doesn't itself name a layout), and not the "resolved" layout
  GET /api/layouts reports, which studylife-display computes from sample data whenever
  there is no cache yet and so can claim a layout that was never actually drawn.
  "resolved" is only used as a last resort, before the panel has shown anything at all
  (current_frame is still None).
- Diagnostic sensors (DISPLAY_DIAGNOSTIC_SENSOR_DESCRIPTIONS, EntityCategory.DIAGNOSTIC,
  so Home Assistant files them under the device's "Diagnostic" group): the health
  fields GET /api/state carries alongside the frame - status, resolved layout, frame
  kind, shown-at, data age, last error, version. Each is its own entity so it can be
  graphed, used in automation triggers and shown on dashboards individually, instead of
  being buried as attributes of the current-layout sensor (where they used to live).
  The two boolean health fields (quiet hours, sessions ok) are binary sensors - see
  display_binary_sensor.py.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .display_coordinator import DisplayCoordinator, DisplayData
from .display_entity import StudyLifeDisplayEntity

# studylife-display's health.py `state` values, in the order its docstring lists them.
DISPLAY_STATUS_OPTIONS = ["ok", "degraded", "error", "setup"]
# `_current_frame_json`'s "kind" values.
DISPLAY_FRAME_KIND_OPTIONS = ["dashboard", "error", "setup"]
# studylife-display's status_store.py error kinds, plus "none" for "no error recorded"
# so the sensor reads as a proper value rather than `unknown` on a healthy display.
LAST_ERROR_NONE = "none"
LAST_ERROR_FALLBACK_KIND = "transient"
DISPLAY_LAST_ERROR_OPTIONS = [
    LAST_ERROR_NONE,
    "rejected",
    "stale",
    "no_data",
    LAST_ERROR_FALLBACK_KIND,
]


@dataclass(frozen=True, kw_only=True)
class StudyLifeDisplaySensorDescription(SensorEntityDescription):
    value_fn: Callable[[DisplayData], Any] = lambda data: None
    attrs_fn: Callable[[DisplayData], dict[str, Any]] = lambda data: {}


def _current_layout(data: DisplayData) -> str | None:
    if data.current_frame_kind is not None:
        # A real frame record exists - trust it over "resolved", which studylife-display
        # computes from sample data whenever there is no cache yet and would otherwise
        # claim a layout that was never actually drawn (e.g. while the panel is genuinely
        # showing the setup/error screen, kind != "dashboard").
        return (
            data.current_frame_layout
            if data.current_frame_kind == "dashboard"
            else data.current_frame_kind
        )
    # Nothing has ever been shown on the panel yet - the best available guess.
    return data.resolved_layout


def _last_error_kind(data: DisplayData) -> str:
    if not data.last_error:
        return LAST_ERROR_NONE
    kind = data.last_error.get("kind")
    # A kind this integration doesn't know (added by a newer studylife-display) would
    # make HA log a "not a valid option" error and drop the state - fall back to the
    # catch-all kind instead so the sensor stays usable.
    return kind if kind in DISPLAY_LAST_ERROR_OPTIONS else LAST_ERROR_FALLBACK_KIND


def _last_error_attrs(data: DisplayData) -> dict[str, Any]:
    if not data.last_error:
        return {}
    return {
        "message": data.last_error.get("message"),
        "http_status": data.last_error.get("status"),
        "at": data.last_error.get("at"),
    }


def _shown_at(data: DisplayData) -> Any:
    if data.current_frame_shown_at is None:
        return None
    # shown_at carries an offset on the wire; as_local only matters for the defensive
    # case of a naive value, which HA's TIMESTAMP device class would reject.
    return dt_util.as_local(data.current_frame_shown_at)


CURRENT_LAYOUT_DESCRIPTION = StudyLifeDisplaySensorDescription(
    key="current_layout",
    translation_key="display_current_layout",
    icon="mdi:tablet-dashboard",
    value_fn=_current_layout,
)

DISPLAY_DIAGNOSTIC_SENSOR_DESCRIPTIONS: tuple[
    StudyLifeDisplaySensorDescription, ...
] = (
    StudyLifeDisplaySensorDescription(
        key="status",
        translation_key="display_status",
        icon="mdi:heart-pulse",
        device_class=SensorDeviceClass.ENUM,
        options=DISPLAY_STATUS_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.status,
    ),
    StudyLifeDisplaySensorDescription(
        key="resolved_layout",
        translation_key="display_resolved_layout",
        icon="mdi:auto-fix",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.resolved_layout,
    ),
    StudyLifeDisplaySensorDescription(
        key="frame_kind",
        translation_key="display_frame_kind",
        icon="mdi:image-frame",
        device_class=SensorDeviceClass.ENUM,
        options=DISPLAY_FRAME_KIND_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.current_frame_kind,
    ),
    StudyLifeDisplaySensorDescription(
        key="shown_at",
        translation_key="display_shown_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_shown_at,
    ),
    StudyLifeDisplaySensorDescription(
        key="stale_minutes",
        translation_key="display_stale_minutes",
        icon="mdi:clock-alert-outline",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.stale_minutes,
    ),
    StudyLifeDisplaySensorDescription(
        key="last_error",
        translation_key="display_last_error",
        icon="mdi:alert-circle-outline",
        device_class=SensorDeviceClass.ENUM,
        options=DISPLAY_LAST_ERROR_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_error_kind,
        attrs_fn=_last_error_attrs,
    ),
    StudyLifeDisplaySensorDescription(
        key="version",
        translation_key="display_version",
        icon="mdi:tag-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        # Also the device's sw_version, but that is only read once at entity creation
        # (see display_entity.py) - this sensor follows the display's upgrades live.
        value_fn=lambda data: data.version or None,
    ),
)


async def async_setup_display_sensor_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: DisplayCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            StudyLifeDisplaySensor(coordinator, entry, CURRENT_LAYOUT_DESCRIPTION),
            *(
                StudyLifeDisplaySensor(coordinator, entry, description)
                for description in DISPLAY_DIAGNOSTIC_SENSOR_DESCRIPTIONS
            ),
        ]
    )


class StudyLifeDisplaySensor(StudyLifeDisplayEntity, SensorEntity):
    """A single value read from a studylife-display's GET /api/state."""

    entity_description: StudyLifeDisplaySensorDescription

    def __init__(
        self,
        coordinator: DisplayCoordinator,
        entry: ConfigEntry,
        description: StudyLifeDisplaySensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attrs_fn(self.data)
