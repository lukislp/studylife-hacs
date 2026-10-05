"""Number settings of a studylife-display device (EntityCategory.CONFIG).

Two minute-valued integer settings from GET /api/settings, each created only when the
display reports its key (`redraw_after_minutes` is missing on older displays). Writes go
through POST /api/settings as exact ints; the display validates the range and a
rejection (400) surfaces as the display's own reason.
"""

from __future__ import annotations

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .display_coordinator import DisplayCoordinator
from .display_entity import StudyLifeDisplayEntity

DISPLAY_NUMBER_DESCRIPTIONS: tuple[NumberEntityDescription, ...] = (
    NumberEntityDescription(
        key="auto_recap_minutes",
        translation_key="display_auto_recap_minutes",
        icon="mdi:timer-check-outline",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=0,
        native_max_value=240,
        native_step=1,
    ),
    NumberEntityDescription(
        key="redraw_after_minutes",
        translation_key="display_redraw_after_minutes",
        icon="mdi:refresh-auto",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=0,
        native_max_value=1440,
        native_step=1,
    ),
)


async def async_setup_display_number_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: DisplayCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        StudyLifeDisplayNumber(coordinator, entry, description)
        for description in DISPLAY_NUMBER_DESCRIPTIONS
        if description.key in coordinator.data.settings
    )


class StudyLifeDisplayNumber(StudyLifeDisplayEntity, NumberEntity):
    """One integer setting (minutes), written with POST /api/settings."""

    def __init__(
        self,
        coordinator: DisplayCoordinator,
        entry: ConfigEntry,
        description: NumberEntityDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | None:
        value = self.data.settings.get(self.entity_description.key)
        # bool is an int subclass; a stray True/False is not a minute count.
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return None

    async def async_set_native_value(self, value: float) -> None:
        await self._async_apply_settings({self.entity_description.key: int(value)})
