"""Services to manage StudyLife sessions and course goals from Home Assistant.

Mirrors what the Calendar/Setup pages do client-side (POST/PUT/DELETE
/api/sessions, PUT /api/coursegoals/{courseId}) - see docs/ARCHITECTURE.md
for the DTO shapes.
"""
from __future__ import annotations

from datetime import datetime

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .coordinator import StudyLifeCoordinator

SERVICE_CREATE_SESSION = "create_session"
SERVICE_UPDATE_SESSION = "update_session"
SERVICE_DELETE_SESSION = "delete_session"
SERVICE_SET_COURSE_GOAL = "set_course_goal"
SERVICE_GENERATE_EXAM_PLAN = "generate_exam_plan"
SERVICE_SET_ACTIVE_PROGRAM = "set_active_program"

CREATE_SESSION_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("course_id"): vol.Coerce(int),
        vol.Optional("course_name"): cv.string,
        vol.Optional("course_color"): cv.string,
        vol.Required("start_time"): cv.datetime,
        vol.Required("end_time"): cv.datetime,
        vol.Optional("topic"): cv.string,
        vol.Optional("notes"): cv.string,
        vol.Optional("timer_mode_id", default=1): vol.Coerce(int),
    }
)

UPDATE_SESSION_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("session_id"): vol.Coerce(int),
        vol.Optional("course_id"): vol.Coerce(int),
        vol.Optional("course_name"): cv.string,
        vol.Optional("course_color"): cv.string,
        vol.Optional("start_time"): cv.datetime,
        vol.Optional("end_time"): cv.datetime,
        vol.Optional("topic"): cv.string,
        vol.Optional("notes"): cv.string,
        vol.Optional("is_completed"): cv.boolean,
        vol.Optional("timer_mode_id"): vol.Coerce(int),
    }
)

DELETE_SESSION_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("session_id"): vol.Coerce(int),
    }
)

SET_COURSE_GOAL_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("course_id"): vol.Coerce(int),
        vol.Optional("course_name"): cv.string,
        vol.Optional("target_date"): cv.date,
        vol.Optional("grade"): vol.Coerce(float),
        vol.Optional("completion_note"): cv.string,
    }
)

GENERATE_EXAM_PLAN_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("course_id"): vol.Coerce(int),
        vol.Required("exam_date"): cv.date,
        vol.Optional("session_length_minutes"): vol.Coerce(int),
        vol.Optional("total_hours"): vol.Coerce(float),
    }
)

SET_ACTIVE_PROGRAM_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        # Omitted = switch back to the built-in programme (ActiveStudyProgramId = null),
        # mirroring how the "0 = built-in" id works for CoursesController's `program` param.
        vol.Optional("program_id"): vol.Coerce(int),
    }
)


def _to_naive_iso(value: datetime) -> str:
    """Turn a (possibly tz-aware) datetime into the naive-local ISO string the API expects."""
    if value.tzinfo is not None:
        value = dt_util.as_local(value).replace(tzinfo=None)
    return value.isoformat()


def _resolve_course(coordinator: StudyLifeCoordinator, course_id: int) -> dict | None:
    """Look up a course_id in the catalog (/api/courses) - lets create_session/
    set_course_goal/update_session be called with just a course_id (e.g. from
    select.studylife_active_course's course_id attribute) instead of having to
    also hand-type the matching course_name/course_color."""
    return next((c for c in coordinator.data.courses if c["id"] == course_id), None)


def _resolve_coordinator(hass: HomeAssistant, call: ServiceCall) -> StudyLifeCoordinator:
    entries: dict[str, StudyLifeCoordinator] = hass.data.get(DOMAIN, {})
    if not entries:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="no_integration_configured"
        )

    device_id = call.data.get("device_id")
    if device_id:
        device = dr.async_get(hass).async_get(device_id)
        if device is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unknown_device",
                translation_placeholders={"device_id": device_id},
            )
        entry_id = next((eid for eid in device.config_entries if eid in entries), None)
        if entry_id is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="device_not_studylife"
            )
        return entries[entry_id]

    if len(entries) == 1:
        return next(iter(entries.values()))

    raise HomeAssistantError(
        translation_domain=DOMAIN, translation_key="multiple_servers_need_device_id"
    )


async def async_register_services(hass: HomeAssistant) -> None:
    """Register the domain-wide session/course-goal services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_CREATE_SESSION):
        return

    async def handle_create_session(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call)
        course_id = call.data["course_id"]
        course = _resolve_course(coordinator, course_id)
        course_name = call.data.get("course_name") or (course["name"] if course else None)
        if not course_name:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="course_not_in_catalog",
                translation_placeholders={"course_id": str(course_id)},
            )
        course_color = call.data.get("course_color") or (course.get("color") if course else "#6C5CE7")

        payload = {
            "id": 0,
            "courseId": course_id,
            "courseName": course_name,
            "courseColor": course_color,
            "startTime": _to_naive_iso(call.data["start_time"]),
            "endTime": _to_naive_iso(call.data["end_time"]),
            "topic": call.data.get("topic"),
            "notes": call.data.get("notes"),
            "isCompleted": False,
            "timerModeId": call.data.get("timer_mode_id", 1),
        }
        await coordinator.client.async_create_session(payload)
        await coordinator.async_request_refresh()

    async def handle_update_session(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call)
        session_id = call.data["session_id"]
        existing = next((s for s in coordinator.data.sessions if s.id == session_id), None)
        if existing is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="session_not_found",
                translation_placeholders={"session_id": str(session_id)},
            )

        new_course_id = call.data.get("course_id")
        changing_course = new_course_id is not None and new_course_id != existing.course_id
        resolved = _resolve_course(coordinator, new_course_id) if changing_course else None

        payload = {
            "id": session_id,
            "courseId": new_course_id if new_course_id is not None else existing.course_id,
            "courseName": call.data.get("course_name") or (resolved["name"] if resolved else existing.course_name),
            "courseColor": call.data.get("course_color") or (resolved.get("color") if resolved else existing.course_color),
            "startTime": _to_naive_iso(call.data["start_time"]) if "start_time" in call.data else existing.start.isoformat(),
            "endTime": _to_naive_iso(call.data["end_time"]) if "end_time" in call.data else existing.end.isoformat(),
            "topic": call.data.get("topic", existing.topic),
            "notes": call.data.get("notes", existing.notes),
            "isCompleted": call.data.get("is_completed", existing.is_completed),
            "timerModeId": call.data.get("timer_mode_id", existing.timer_mode_id),
        }
        await coordinator.client.async_update_session(session_id, payload)
        await coordinator.async_request_refresh()

    async def handle_delete_session(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call)
        await coordinator.client.async_delete_session(call.data["session_id"])
        await coordinator.async_request_refresh()

    async def handle_set_course_goal(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call)
        course_id = call.data["course_id"]
        existing = next(
            (g for g in coordinator.data.course_goals if g.get("courseId") == course_id), None
        )
        course_name = call.data.get("course_name")
        if not course_name:
            course = _resolve_course(coordinator, course_id)
            course_name = course["name"] if course else (existing.get("courseName") if existing else None)
        if not course_name:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="course_not_in_catalog",
                translation_placeholders={"course_id": str(course_id)},
            )
        target_date = call.data.get("target_date")
        payload = {
            "courseId": course_id,
            "courseName": course_name,
            "targetDate": (
                datetime.combine(target_date, datetime.min.time()).isoformat()
                if target_date is not None
                else (existing.get("targetDate") if existing else None)
            ),
            "grade": call.data.get("grade", existing.get("grade") if existing else None),
            "completionNote": call.data.get(
                "completion_note", existing.get("completionNote") if existing else None
            ),
            "completedAt": existing.get("completedAt") if existing else None,
        }
        await coordinator.client.async_set_course_goal(course_id, payload)
        await coordinator.async_request_refresh()

    async def handle_generate_exam_plan(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call)
        exam_date = call.data["exam_date"]
        request = {
            "courseId": call.data["course_id"],
            "examDate": datetime.combine(exam_date, datetime.min.time()).isoformat(),
            "sessionLengthMinutes": call.data.get("session_length_minutes"),
            "totalHours": call.data.get("total_hours"),
        }
        await coordinator.client.async_generate_exam_plan(request)
        await coordinator.async_request_refresh()

    async def handle_set_active_program(call: ServiceCall) -> None:
        # Switches which programme the StudyLife APP itself treats as active/default
        # (course selection, dashboard, session scoping in the web UI). HA-side this
        # no longer gates visibility - every programme keeps its own device either
        # way; only the hub's active-catalog bits (course picker, goals calendar,
        # motivational_style's resolved courses) follow the switch.
        coordinator = _resolve_coordinator(hass, call)
        program_id = call.data.get("program_id")
        known_ids = {p.id for p in coordinator.data.study_programs}
        if program_id not in known_ids:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unknown_program",
                translation_placeholders={"program_id": str(program_id)},
            )
        # PUT /api/settings expects the full UserSettingsDto - start from the last-polled
        # settings dict (already in that exact shape) and only flip this one field, same
        # merge-then-PUT pattern as handle_update_session/handle_set_course_goal above.
        payload = {**coordinator.data.settings, "activeStudyProgramId": program_id}
        await coordinator.client.async_update_settings(payload)
        await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_CREATE_SESSION, handle_create_session, schema=CREATE_SESSION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UPDATE_SESSION, handle_update_session, schema=UPDATE_SESSION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_SESSION, handle_delete_session, schema=DELETE_SESSION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_COURSE_GOAL, handle_set_course_goal, schema=SET_COURSE_GOAL_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GENERATE_EXAM_PLAN, handle_generate_exam_plan, schema=GENERATE_EXAM_PLAN_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_ACTIVE_PROGRAM, handle_set_active_program, schema=SET_ACTIVE_PROGRAM_SCHEMA
    )
