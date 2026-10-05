"""Switch settings of a studylife-display device (EntityCategory.CONFIG).

"update_check" (whether the display looks for a new version) and "skip_unchanged"
(whether it skips redrawing an identical frame), each created only when GET
/api/settings reports its key.
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
    entities: list[StudyLifeDisplayEntity] = []
    if "update_check" in coordinator.data.settings:
        entities.append(StudyLifeDisplayUpdateCheckSwitch(coordinator, entry))
    if "skip_unchanged" in coordinator.data.settings:
        entities.append(StudyLifeDisplaySkipUnchangedSwitch(coordinator, entry))
    async_add_entities(entities)


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


class StudyLifeDisplaySkipUnchangedSwitch(StudyLifeDisplayEntity, SwitchEntity):
    """Whether the display skips redrawing a frame identical to the one shown."""

    _attr_icon = "mdi:image-off-outline"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "display_skip_unchanged"

    def __init__(self, coordinator: DisplayCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "skip_unchanged")

    @property
    def is_on(self) -> bool | None:
        value = self.data.settings.get("skip_unchanged")
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_apply_settings({"skip_unchanged": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_apply_settings({"skip_unchanged": False})
