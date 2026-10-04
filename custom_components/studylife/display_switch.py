"""Switch settings of a studylife-display device (EntityCategory.CONFIG).

Currently just "update_check" (whether the display looks for a new version), created
only when GET /api/settings reports that key.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .display_coordinator import DisplayCoordinator
from .display_entity import StudyLifeDisplayEntity


async def async_setup_display_switch_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: DisplayCoordinator = hass.data[DOMAIN][entry.entry_id]
    if "update_check" in coordinator.data.settings:
        async_add_entities([StudyLifeDisplayUpdateCheckSwitch(coordinator, entry)])


class StudyLifeDisplayUpdateCheckSwitch(StudyLifeDisplayEntity, SwitchEntity):
    """Whether the display checks for a new studylife-display version."""

    _attr_icon = "mdi:update"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "display_update_check"

    def __init__(self, coordinator: DisplayCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "update_check")

    @property
    def is_on(self) -> bool | None:
        value = self.data.settings.get("update_check")
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_apply_settings({"update_check": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_apply_settings({"update_check": False})
