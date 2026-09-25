"""Layout picker for a studylife-display device.

Unlike select.py's active-course picker (a pure local choice, no API call), selecting an
option here calls POST /api/layout on the display directly - it both saves the choice and
triggers an immediate full panel refresh, exactly like that project's own "Apply" button -
then asks the coordinator to refresh so the new choice/resolved layout show up in Home
Assistant right away instead of waiting for the next poll.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import AUTO_LAYOUT, DOMAIN
from .display_coordinator import DisplayCoordinator
from .display_entity import StudyLifeDisplayEntity


async def async_setup_display_select_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: DisplayCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([StudyLifeDisplayLayoutSelect(coordinator, entry)])


class StudyLifeDisplayLayoutSelect(StudyLifeDisplayEntity, SelectEntity):
    """The layout choice - "auto" plus every key GET /api/layouts lists."""

    _attr_icon = "mdi:image-multiple-outline"
    _attr_translation_key = "display_layout"

    def __init__(self, coordinator: DisplayCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "layout")

    @property
    def options(self) -> list[str]:
        return [AUTO_LAYOUT] + [option.key for option in self.data.layout_options]

    @property
    def current_option(self) -> str | None:
        return self.data.layout_choice

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.client.async_set_layout(option)
        await self.coordinator.async_request_refresh()
