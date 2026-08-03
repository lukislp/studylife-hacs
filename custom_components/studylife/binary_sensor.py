"""Binary sensors mirroring the warning states shown on the StudyLife dashboard.

Hub set (static, on the per-config-entry hub device): app-global warnings and
live states. Per-programme set (dynamic, one per study programme device):
the programme's manual completion flag and whether it's the one the app
currently treats as active - same discovery/removal semantics as the
per-programme sensors (see sensor.py's module docstring).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import StudyLifeCoordinator, StudyLifeData, StudyLifeProgramData
from .entity import StudyLifeEntity, StudyLifeProgramEntity


@dataclass(frozen=True, kw_only=True)
class StudyLifeBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[StudyLifeData], bool] = lambda data: False
    attrs_fn: Callable[[StudyLifeData], dict[str, Any]] = lambda data: {}


@dataclass(frozen=True, kw_only=True)
class StudyLifeProgramBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[StudyLifeProgramData], bool] = lambda program: False
    attrs_fn: Callable[[StudyLifeProgramData], dict[str, Any]] = lambda program: {}


BINARY_SENSOR_DESCRIPTIONS: tuple[StudyLifeBinarySensorDescription, ...] = (
    StudyLifeBinarySensorDescription(
        key="studying_now",
        translation_key="studying_now",
        icon="mdi:book-open-page-variant",
        value_fn=lambda data: data.active_session is not None,
    ),
    StudyLifeBinarySensorDescription(
        key="timer_running",
        translation_key="timer_running",
        icon="mdi:timer-play-outline",
        value_fn=lambda data: data.timer_state.is_running,
    ),
    StudyLifeBinarySensorDescription(
        key="week_quota_warning",
        translation_key="week_quota_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: data.week_quota.warning,
    ),
    StudyLifeBinarySensorDescription(
        key="month_quota_warning",
        translation_key="month_quota_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: data.month_quota.warning,
    ),
    StudyLifeBinarySensorDescription(
        key="inactivity_warning",
        translation_key="inactivity_warning",
        icon="mdi:calendar-remove-outline",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: data.inactivity_warning,
        attrs_fn=lambda data: {
            "days_since_last_session": data.days_since_last_session,
            "threshold_days": data.settings.get("inactivityThresholdDays") or 5,
        },
    ),
)


PROGRAM_BINARY_SENSOR_DESCRIPTIONS: tuple[StudyLifeProgramBinarySensorDescription, ...] = (
    StudyLifeProgramBinarySensorDescription(
        # The programme's MANUAL completion flag (PUT /api/studyprograms/{id}/completed,
        # set via the web UI) - never flipped automatically, not even at 100% ECTS.
        # Always off for the built-in programme, which has no such flag server-side.
        key="program_completed",
        translation_key="program_completed",
        icon="mdi:school",
        value_fn=lambda program: program.program.is_completed,
    ),
    StudyLifeProgramBinarySensorDescription(
        # Whether THIS programme is the one the StudyLife app currently treats as
        # active (settings.activeStudyProgramId) - per-device counterpart of the
        # hub's sensor active_program. HA visibility no longer depends on it.
        key="program_active",
        translation_key="program_active",
        icon="mdi:star-check-outline",
        value_fn=lambda program: program.is_active,
        attrs_fn=lambda program: {
            "program_id": program.program.id,
            "is_built_in": program.program.is_built_in,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: StudyLifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        StudyLifeBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
    )

    # Same dynamic per-programme pattern as sensor.py: initial set from the already
    # completed first refresh, listener adds sets for programmes created later.
    known_program_ids: set[str] = set()

    def _sync_program_entities() -> None:
        new_ids = [pid for pid in coordinator.data.programs if pid not in known_program_ids]
        if not new_ids:
            return
        entities: list[StudyLifeProgramBinarySensor] = []
        for pid in new_ids:
            known_program_ids.add(pid)
            program_name = coordinator.data.programs[pid].program.name
            entities.extend(
                StudyLifeProgramBinarySensor(coordinator, entry, description, pid, program_name)
                for description in PROGRAM_BINARY_SENSOR_DESCRIPTIONS
            )
        async_add_entities(entities)

    _sync_program_entities()
    entry.async_on_unload(coordinator.async_add_listener(_sync_program_entities))


class StudyLifeBinarySensor(StudyLifeEntity, BinarySensorEntity):
    entity_description: StudyLifeBinarySensorDescription

    def __init__(
        self,
        coordinator: StudyLifeCoordinator,
        entry: ConfigEntry,
        description: StudyLifeBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return self.entity_description.value_fn(self.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attrs_fn(self.data)


class StudyLifeProgramBinarySensor(StudyLifeProgramEntity, BinarySensorEntity):
    """A per-programme flag on that programme's device - unavailable once the
    programme has been deleted from StudyLife."""

    entity_description: StudyLifeProgramBinarySensorDescription

    def __init__(
        self,
        coordinator: StudyLifeCoordinator,
        entry: ConfigEntry,
        description: StudyLifeProgramBinarySensorDescription,
        program_id: str,
        program_name: str,
    ) -> None:
        super().__init__(coordinator, entry, description.key, program_id, program_name)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        program = self.program_data
        return self.entity_description.value_fn(program) if program is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        program = self.program_data
        return self.entity_description.attrs_fn(program) if program is not None else {}
