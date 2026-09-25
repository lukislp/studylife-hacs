"""Camera entity showing the exact frame currently on a studylife-display panel.

CAMERA is only ever forwarded to for a display config entry (see __init__.py's
PLATFORMS_DISPLAY) - a StudyLife account entry never reaches this file, so unlike
sensor.py/select.py there is no entry-type branch to make here.
"""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .display_api import DisplayApiError
from .display_coordinator import DisplayCoordinator
from .display_entity import StudyLifeDisplayEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: DisplayCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([StudyLifeDisplayCamera(coordinator, entry)])


class StudyLifeDisplayCamera(StudyLifeDisplayEntity, Camera):
    """GET /api/current.png, fetched fresh on demand - the same PNG the display's own
    web interface shows under "Currently on the panel"."""

    _attr_translation_key = "current_frame"

    def __init__(self, coordinator: DisplayCoordinator, entry: ConfigEntry) -> None:
        StudyLifeDisplayEntity.__init__(self, coordinator, entry, "current_frame")
        Camera.__init__(self)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        try:
            return await self.coordinator.client.async_get_current_png()
        except DisplayApiError:
            _LOGGER.debug("could not fetch the current frame for %s", self.entity_id)
            return None
