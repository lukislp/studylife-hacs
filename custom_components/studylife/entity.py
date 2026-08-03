"""Shared base entities for StudyLife platforms.

Device layout: one "hub" device per config entry (app-global entities: sessions,
timer, notes, calendars, course picker, ...) plus one device PER STUDY PROGRAMME
(built-in and custom, completed or not), each carrying that programme's progress
sensors. Programme devices hang off the hub via `via_device`, so the HA UI shows
them as children of the StudyLife server they belong to.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import StudyLifeCoordinator, StudyLifeData, StudyLifeProgramData


class StudyLifeEntity(CoordinatorEntity[StudyLifeCoordinator]):
    """Base entity. Without a program_id it belongs to the per-config-entry hub
    device; with one, to that study programme's own device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: StudyLifeCoordinator,
        entry: ConfigEntry,
        key: str,
        program_id: str | None = None,
        program_name: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        if program_id is None:
            self._attr_unique_id = f"{entry.entry_id}_{key}"
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry.entry_id)},
                name="StudyLife",
                manufacturer="StudyLife (self-hosted)",
                model="Blazor WebAssembly app",
                configuration_url=entry.data.get(CONF_URL),
            )
        else:
            # program_id is the coordinator's stable string key ("builtin" for the
            # built-in programme, the DB id as string otherwise) - the server's own
            # null/0 convention isn't hashable-stable enough for HA identifiers.
            self._attr_unique_id = f"{entry.entry_id}_program_{program_id}_{key}"
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{entry.entry_id}_program_{program_id}")},
                name=f"StudyLife — {program_name}",
                manufacturer="StudyLife (self-hosted)",
                model="Study programme",
                configuration_url=entry.data.get(CONF_URL),
                via_device=(DOMAIN, entry.entry_id),
            )

    @property
    def data(self) -> StudyLifeData:
        return self.coordinator.data


class StudyLifeProgramEntity(StudyLifeEntity):
    """Base for entities scoped to one study programme's device.

    When the programme disappears from the coordinator data (deleted via the
    StudyLife web UI), the entity flips to unavailable instead of being removed
    from the registry - the stale device can then be deleted manually in the HA
    UI. Deliberately no active registry cleanup: simpler, and the standard
    pattern for integrations whose upstream objects can vanish.
    """

    def __init__(
        self,
        coordinator: StudyLifeCoordinator,
        entry: ConfigEntry,
        key: str,
        program_id: str,
        program_name: str,
    ) -> None:
        super().__init__(coordinator, entry, key, program_id=program_id, program_name=program_name)
        self._program_id = program_id

    @property
    def program_data(self) -> StudyLifeProgramData | None:
        """This programme's per-poll data, or None if it no longer exists."""
        return self.coordinator.data.programs.get(self._program_id)

    @property
    def available(self) -> bool:
        return super().available and self._program_id in self.coordinator.data.programs
