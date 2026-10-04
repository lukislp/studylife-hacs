"""Cycle-order editor for a studylife-display device.

The "cycle" pseudo layout draws the next layout of a configured list on every refresh.
That list is exposed here as one editable text entity (EntityCategory.CONFIG) whose value
is the keys joined with ", " - e.g. "classic, week, agenda, review" - because a select can
only hold one value and Home Assistant has no native list-editing entity. Setting a new
value splits it on commas and sends the keys to POST /api/layout together with the
CURRENT layout choice, so editing the order never switches the display to "cycle" by
itself (pick "cycle" in the layout select for that).

Only created when the display reports a cycle list at all - older displays (before the
extended GET /api/layouts) don't, and get no entity rather than a permanently-unavailable
one. The display validates every key and answers a 400 with its reason for an unknown
one, writing nothing; that reason surfaces as the service call's error.
"""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .display_api import DisplayApiError
from .display_coordinator import DisplayCoordinator
from .display_entity import StudyLifeDisplayEntity

CYCLE_SEPARATOR = ", "
# Lowercase layout keys (letters, digits, "_" / "-"), comma-separated, optional spaces
# around the commas - matches what the display's own cycle setting accepts.
CYCLE_PATTERN = r"^[a-z0-9_-]+(\s*,\s*[a-z0-9_-]+)*$"
CYCLE_MAX_LENGTH = 200


def parse_cycle(value: str) -> list[str]:
    """Split a comma-separated cycle string into keys, dropping blanks."""
    return [key.strip() for key in value.split(",") if key.strip()]


async def async_setup_display_text_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: DisplayCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data.cycle:
        return
    async_add_entities([StudyLifeDisplayCycleText(coordinator, entry)])


class StudyLifeDisplayCycleText(StudyLifeDisplayEntity, TextEntity):
    """The configured cycle order as a comma-separated list of layout keys."""

    _attr_icon = "mdi:format-list-numbered"
    _attr_translation_key = "display_cycle"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = TextMode.TEXT
    _attr_native_min = 1
    _attr_native_max = CYCLE_MAX_LENGTH
    _attr_pattern = CYCLE_PATTERN

    def __init__(self, coordinator: DisplayCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "cycle")

    @property
    def native_value(self) -> str | None:
        return CYCLE_SEPARATOR.join(self.data.cycle) or None

    async def async_set_value(self, value: str) -> None:
        cycle = parse_cycle(value)
        if not cycle:
            raise HomeAssistantError("The cycle needs at least one layout key")
        try:
            await self.coordinator.client.async_set_layout(
                self.data.layout_choice, cycle=cycle
            )
        except DisplayApiError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()
