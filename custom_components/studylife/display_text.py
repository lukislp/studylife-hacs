"""Text settings of a studylife-display device (EntityCategory.CONFIG).

The window rules and the daily clear time from GET /api/settings, one text entity per
key, each created only when the display reports that key (older displays lack some of
them, or the whole route). Values are `[weekdays] HH-HH` / `HH:MM-HH:MM` windows or
`HH:MM`; an empty string is valid and switches the rule off. No pattern is enforced
here - the display validates and a rejection (400) surfaces as the display's own reason.
"""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextEntityDescription, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .display_coordinator import DisplayCoordinator
from .display_entity import StudyLifeDisplayEntity

DISPLAY_TEXT_DESCRIPTIONS: tuple[TextEntityDescription, ...] = tuple(
    TextEntityDescription(
        key=key,
        translation_key=f"display_{key}",
        icon=icon,
        entity_category=EntityCategory.CONFIG,
        native_min=0,
        native_max=40,
        mode=TextMode.TEXT,
    )
    for key, icon in (
        ("quiet_hours", "mdi:sleep"),
        ("clear_at", "mdi:broom"),
        ("auto_review", "mdi:calendar-week"),
        ("auto_agenda", "mdi:calendar-today"),
        ("auto_tomorrow", "mdi:calendar-arrow-right"),
        ("auto_quiet", "mdi:volume-off"),
    )
)


async def async_setup_display_text_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: DisplayCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        StudyLifeDisplayText(coordinator, entry, description)
        for description in DISPLAY_TEXT_DESCRIPTIONS
        if description.key in coordinator.data.settings
    )


class StudyLifeDisplayText(StudyLifeDisplayEntity, TextEntity):
    """One text setting, written with POST /api/settings."""

    def __init__(
        self,
        coordinator: DisplayCoordinator,
        entry: ConfigEntry,
        description: TextEntityDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str | None:
        value = self.data.settings.get(self.entity_description.key)
        return None if value is None else str(value)

    async def async_set_value(self, value: str) -> None:
        await self._async_apply_settings({self.entity_description.key: value})
