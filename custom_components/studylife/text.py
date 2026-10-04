"""Text platform - only studylife-display entries have text entities (the cycle order,
see display_text.py); a StudyLife account entry never forwards this platform at all
(PLATFORMS_ACCOUNT in __init__.py), the branch below just keeps the two entry types'
setup paths shaped alike across every platform module."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ENTRY_TYPE, ENTRY_TYPE_DISPLAY
from .display_text import async_setup_display_text_entry


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DISPLAY:
        await async_setup_display_text_entry(hass, entry, async_add_entities)
