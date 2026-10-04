"""Layout pickers for a studylife-display device.

Unlike select.py's active-course picker (a pure local choice, no API call), selecting an
option here calls POST /api/layout on the display directly - it both saves the choice and
triggers an immediate full panel refresh, exactly like that project's own "Apply" button -
then asks the coordinator to refresh so the new choice/resolved layout show up in Home
Assistant right away instead of waiting for the next poll.

Three selects:

- "layout": the persisted choice - every pseudo choice GET /api/layouts offers ("auto")
  followed by every real layout key.
- "duo_left" / "duo_right" (EntityCategory.CONFIG): the two halves of the "duo" layout,
  each pickable from the display's `panes` list. Only created when the display reports
  panes at all - older displays (before the extended /api/layouts) don't, and get no duo
  entities rather than two permanently-unavailable ones. Changing a half re-sends the
  CURRENT layout choice together with the new pair, so the choice itself stays as it is.
- "language" / "rotation" (EntityCategory.CONFIG): display settings from
  GET /api/settings (studylife-display >= 1.11), each created only when the display
  reports that key. Written with POST /api/settings.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .display_api import DisplayApiError
from .display_coordinator import DisplayCoordinator
from .display_entity import StudyLifeDisplayEntity

LANGUAGE_OPTIONS = ["de", "en"]
ROTATION_OPTIONS = ["0", "180"]
DUO_LEFT = 0
DUO_RIGHT = 1


async def async_setup_display_select_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: DisplayCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = [StudyLifeDisplayLayoutSelect(coordinator, entry)]
    if coordinator.data.panes:
        entities.append(StudyLifeDisplayDuoSelect(coordinator, entry, DUO_LEFT))
        entities.append(StudyLifeDisplayDuoSelect(coordinator, entry, DUO_RIGHT))
    if "language" in coordinator.data.settings:
        entities.append(StudyLifeDisplayLanguageSelect(coordinator, entry))
    if "rotation" in coordinator.data.settings:
        entities.append(StudyLifeDisplayRotationSelect(coordinator, entry))
    async_add_entities(entities)


class StudyLifeDisplayLayoutSelect(StudyLifeDisplayEntity, SelectEntity):
    """The layout choice - every pseudo choice plus every layout key GET /api/layouts
    lists."""

    _attr_icon = "mdi:image-multiple-outline"
    _attr_translation_key = "display_layout"

    def __init__(self, coordinator: DisplayCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "layout")

    @property
    def options(self) -> list[str]:
        return [option.key for option in self.data.pseudo_options] + [
            option.key for option in self.data.layout_options
        ]

    @property
    def current_option(self) -> str | None:
        return self.data.layout_choice

    async def async_select_option(self, option: str) -> None:
        try:
            await self.coordinator.client.async_set_layout(option)
        except DisplayApiError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()


class StudyLifeDisplayDuoSelect(StudyLifeDisplayEntity, SelectEntity):
    """One half of the "duo" layout - which layout is drawn on the left or right."""

    _attr_icon = "mdi:view-split-vertical"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: DisplayCoordinator, entry: ConfigEntry, side: int
    ) -> None:
        key = "duo_left" if side == DUO_LEFT else "duo_right"
        super().__init__(coordinator, entry, key)
        self._side = side
        self._attr_translation_key = f"display_{key}"

    @property
    def options(self) -> list[str]:
        return list(self.data.panes)

    @property
    def current_option(self) -> str | None:
        duo = self.data.duo
        return duo[self._side] if len(duo) > self._side else None

    async def async_select_option(self, option: str) -> None:
        # Both halves are always sent together; the display rejects a lone one. Fill the
        # other half from the current pair (or the first pane, should the pair be short).
        duo = list(self.data.duo)
        while len(duo) < 2:
            duo.append(self.data.panes[0])
        duo[self._side] = option
        try:
            await self.coordinator.client.async_set_layout(
                self.data.layout_choice, duo=duo
            )
        except DisplayApiError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()


class StudyLifeDisplayLanguageSelect(StudyLifeDisplayEntity, SelectEntity):
    """The language the panel is drawn in."""

    _attr_icon = "mdi:translate"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "display_language"
    _attr_options = LANGUAGE_OPTIONS

    def __init__(self, coordinator: DisplayCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "language")

    @property
    def current_option(self) -> str | None:
        value = self.data.settings.get("language")
        return value if value in LANGUAGE_OPTIONS else None

    async def async_select_option(self, option: str) -> None:
        await self._async_apply_settings({"language": option})


class StudyLifeDisplayRotationSelect(StudyLifeDisplayEntity, SelectEntity):
    """How the panel is mounted: 0 or 180 degrees. Options are strings (a select's
    states are text); the display wants the integer."""

    _attr_icon = "mdi:screen-rotation"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "display_rotation"
    _attr_options = ROTATION_OPTIONS

    def __init__(self, coordinator: DisplayCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "rotation")

    @property
    def current_option(self) -> str | None:
        value = str(self.data.settings.get("rotation"))
        return value if value in ROTATION_OPTIONS else None

    async def async_select_option(self, option: str) -> None:
        await self._async_apply_settings({"rotation": int(option)})
