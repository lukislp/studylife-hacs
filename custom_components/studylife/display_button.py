"""Buttons of a studylife-display device.

- "refresh": redraw the panel now (POST /api/refresh). A normal control, always created.
- "reset_settings" (EntityCategory.CONFIG): drop every override from the display's
  settings.json except the layout choice (POST /api/settings/reset). Only created when
  the display has /api/settings at all.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .display_api import DisplayApiError
from .display_coordinator import DisplayCoordinator
from .display_entity import StudyLifeDisplayEntity


async def async_setup_display_button_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: DisplayCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = [StudyLifeDisplayRefreshButton(coordinator, entry)]
    if coordinator.data.settings:
        entities.append(StudyLifeDisplayResetSettingsButton(coordinator, entry))
    async_add_entities(entities)


class StudyLifeDisplayRefreshButton(StudyLifeDisplayEntity, ButtonEntity):
    """Redraws the panel right now."""

    _attr_icon = "mdi:refresh"
    _attr_translation_key = "display_refresh"

    def __init__(self, coordinator: DisplayCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "refresh")

    async def async_press(self) -> None:
        try:
            result = await self.coordinator.client.async_refresh()
        except DisplayApiError as err:
            raise HomeAssistantError(str(err)) from err
        if result.get("outcome") == "failed":
            raise HomeAssistantError("The display could not refresh the panel")
        await self.coordinator.async_request_refresh()


class StudyLifeDisplayResetSettingsButton(StudyLifeDisplayEntity, ButtonEntity):
    """Removes every settings override (back to the environment values)."""

    _attr_icon = "mdi:restore"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "display_reset_settings"

    def __init__(self, coordinator: DisplayCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "reset_settings")

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.async_reset_settings()
        except DisplayApiError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()
