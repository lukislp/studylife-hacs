"""Shared base entity for a studylife-display config entry.

One device per entry (unlike the StudyLife account's hub-plus-per-programme layout in
entity.py) - a display has nothing analogous to study programmes, and "one config entry,
one device" is exactly what lets several displays each show up as their own device (see
const.py's ENTRY_TYPE_DISPLAY and config_flow.py's async_step_display).
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .display_coordinator import DisplayCoordinator, DisplayData


class StudyLifeDisplayEntity(CoordinatorEntity[DisplayCoordinator]):
    """Base entity for the one device a display config entry owns."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: DisplayCoordinator, entry: ConfigEntry, key: str
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            # entry.title defaults to "StudyLife Display (<host>)" (see config_flow.py) so
            # several displays are told apart in the flat Settings > Devices list without
            # the user having to rename anything themselves.
            name=entry.title,
            manufacturer="StudyLife",
            model="studylife-display (e-paper)",
            configuration_url=entry.data.get(CONF_URL),
            sw_version=coordinator.data.version if coordinator.data else None,
        )

    @property
    def data(self) -> DisplayData:
        return self.coordinator.data
