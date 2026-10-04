"""Diagnostic binary sensors for a studylife-display device.

The two boolean health fields GET /api/state carries - whether the display is inside its
configured quiet hours right now, and whether its last fetch of StudyLife's sessions
succeeded. Both are EntityCategory.DIAGNOSTIC, i.e. filed under the device's
"Diagnostic" group next to the diagnostic sensors in display_sensor.py; they used to be
attributes of the current-layout sensor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .display_coordinator import DisplayCoordinator, DisplayData
from .display_entity import StudyLifeDisplayEntity


@dataclass(frozen=True, kw_only=True)
class StudyLifeDisplayBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[DisplayData], bool] = lambda data: False


DISPLAY_BINARY_SENSOR_DESCRIPTIONS: tuple[
    StudyLifeDisplayBinarySensorDescription, ...
] = (
    StudyLifeDisplayBinarySensorDescription(
        key="quiet_hours_active",
        translation_key="display_quiet_hours",
        icon="mdi:weather-night",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.quiet_hours_active,
    ),
    StudyLifeDisplayBinarySensorDescription(
        # PROBLEM semantics: "on" means something is wrong, so the wire's sessions_ok
        # is inverted here - the entity is on while the sessions fetch is failing.
        key="sessions_problem",
        translation_key="display_sessions_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: not data.sessions_ok,
    ),
)


async def async_setup_display_binary_sensor_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: DisplayCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        StudyLifeDisplayBinarySensor(coordinator, entry, description)
        for description in DISPLAY_BINARY_SENSOR_DESCRIPTIONS
    )


class StudyLifeDisplayBinarySensor(StudyLifeDisplayEntity, BinarySensorEntity):
    """A single boolean read from a studylife-display's GET /api/state."""

    entity_description: StudyLifeDisplayBinarySensorDescription

    def __init__(
        self,
        coordinator: DisplayCoordinator,
        entry: ConfigEntry,
        description: StudyLifeDisplayBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return self.entity_description.value_fn(self.data)
