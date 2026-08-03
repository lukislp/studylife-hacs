"""Picker for the user's active courses.

Home Assistant service fields (services.yaml) can only offer a static option
list, but which courses are "active" (selected in Setup, not yet completed)
is per-user and changes over time - so instead of hand-typing course_id/
course_name into create_session/set_course_goal, this exposes them as a
regular select entity. Its state (course name) and course_id attribute can be
referenced directly from automations/scripts, e.g.:

    action: studylife.create_session
    data:
      course_id: "{{ state_attr('select.studylife_active_course', 'course_id') }}"
      start_time: ...
      end_time: ...

(course_name/course_color can then be omitted too - services.py resolves them
from the course catalog when only course_id is given.)

Deliberately a single entity on the HUB device, scoped to the programme the
StudyLife app itself treats as active - NOT duplicated per programme device.
"Which course am I planning a session for right now" is inherently tied to
whatever the app is set to; per-programme copies would just multiply pickers
whose selections feed the same global services. Switch the app's active
programme (studylife.set_active_program) and the option list follows.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .coordinator import StudyLifeCoordinator, StudyLifeData
from .entity import StudyLifeEntity


def _active_courses(data: StudyLifeData) -> list[dict[str, Any]]:
    """Courses the user selected in Setup and hasn't marked completed yet."""
    selected_ids = set(data.settings.get("selectedCourseIds") or [])
    completed_ids = set(data.settings.get("completedCourseIds") or [])
    return [c for c in data.courses if c["id"] in selected_ids and c["id"] not in completed_ids]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: StudyLifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([StudyLifeActiveCourseSelect(coordinator, entry)])


class StudyLifeActiveCourseSelect(StudyLifeEntity, RestoreEntity, SelectEntity):
    """Dropdown of active courses - purely a picker, doesn't call the API itself."""

    _attr_icon = "mdi:book-open-variant"
    _attr_translation_key = "active_course"

    def __init__(self, coordinator: StudyLifeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "active_course")
        self._selected: str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self.options:
            self._selected = last_state.state

    @property
    def options(self) -> list[str]:
        return [c["name"] for c in _active_courses(self.data)]

    @property
    def current_option(self) -> str | None:
        options = self.options
        if self._selected in options:
            return self._selected
        return options[0] if options else None

    async def async_select_option(self, option: str) -> None:
        self._selected = option
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        active = _active_courses(self.data)
        course = next((c for c in active if c["name"] == self.current_option), None)
        return {
            "course_id": course["id"] if course else None,
            "course_code": course.get("code") if course else None,
            "course_color": course.get("color") if course else None,
            "active_courses": active,
        }
