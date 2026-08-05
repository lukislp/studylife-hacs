"""Tests for the shared base entity classes
(custom_components/studylife/entity.py).

These exercise StudyLifeEntity/StudyLifeProgramEntity as plain Python objects -
unique_id/device_info/available are simple property reads off _attr_* fields and
self.coordinator, none of which require a running hass or a real
DataUpdateCoordinator, so a lightweight fake coordinator (just the attributes
these properties actually touch) is enough.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.const import CONF_API_KEY, CONF_URL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife.const import DOMAIN
from custom_components.studylife.entity import StudyLifeEntity, StudyLifeProgramEntity

from .conftest import TEST_API_KEY, TEST_URL


def _make_entry() -> MockConfigEntry:
    """A config entry that doesn't need to be added to hass - unique_id/device_info
    only read entry.entry_id and entry.data, both available right after construction."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="StudyLife",
        data={CONF_URL: TEST_URL, CONF_API_KEY: TEST_API_KEY},
        unique_id=TEST_URL,
    )


def _fake_coordinator(programs: dict[str, object], last_update_success: bool = True):
    """Minimal stand-in for StudyLifeCoordinator, carrying only what
    CoordinatorEntity.available and StudyLifeProgramEntity.available touch."""
    return SimpleNamespace(
        data=SimpleNamespace(programs=programs),
        last_update_success=last_update_success,
    )


# ---------------------------------------------------------------------------
# StudyLifeEntity - hub-level (no program_id)
# ---------------------------------------------------------------------------


def test_hub_entity_unique_id_and_device_info() -> None:
    entry = _make_entry()
    entity = StudyLifeEntity(MagicMock(), entry, "week_hours")

    assert entity.unique_id == f"{entry.entry_id}_week_hours"
    assert entity.device_info is not None
    assert entity.device_info["identifiers"] == {(DOMAIN, entry.entry_id)}
    assert entity.device_info["name"] == "StudyLife"
    assert "via_device" not in entity.device_info


# ---------------------------------------------------------------------------
# StudyLifeEntity - per-programme (program_id given)
# ---------------------------------------------------------------------------


def test_program_entity_unique_id_and_device_info() -> None:
    entry = _make_entry()
    entity = StudyLifeEntity(
        MagicMock(), entry, "week_hours", program_id="5", program_name="Master's"
    )

    assert entity.unique_id == f"{entry.entry_id}_program_5_week_hours"
    assert "program_5" in entity.unique_id
    assert entity.device_info is not None
    assert entity.device_info["identifiers"] == {(DOMAIN, f"{entry.entry_id}_program_5")}
    assert entity.device_info["name"] == "StudyLife — Master's"
    assert entity.device_info["via_device"] == (DOMAIN, entry.entry_id)


# ---------------------------------------------------------------------------
# StudyLifeProgramEntity.available
# ---------------------------------------------------------------------------


def test_program_entity_available_when_program_present() -> None:
    entry = _make_entry()
    coordinator = _fake_coordinator(programs={"5": object()})
    entity = StudyLifeProgramEntity(
        coordinator, entry, "week_hours", program_id="5", program_name="Master's"
    )

    assert entity.available is True


def test_program_entity_unavailable_when_program_disappears() -> None:
    """Programme 5 no longer present in a fresh poll's data (e.g. deleted via the
    StudyLife web UI) - the entity should flip unavailable rather than error."""
    entry = _make_entry()
    coordinator = _fake_coordinator(programs={})
    entity = StudyLifeProgramEntity(
        coordinator, entry, "week_hours", program_id="5", program_name="Master's"
    )

    assert entity.available is False


def test_program_entity_unavailable_when_coordinator_update_failed() -> None:
    """available also honors the base CoordinatorEntity check (last_update_success)
    even when the programme itself is still present in the last-known-good data."""
    entry = _make_entry()
    coordinator = _fake_coordinator(programs={"5": object()}, last_update_success=False)
    entity = StudyLifeProgramEntity(
        coordinator, entry, "week_hours", program_id="5", program_name="Master's"
    )

    assert entity.available is False


def test_program_entity_program_data_property() -> None:
    entry = _make_entry()
    program_data = object()
    coordinator = _fake_coordinator(programs={"5": program_data})
    entity = StudyLifeProgramEntity(
        coordinator, entry, "week_hours", program_id="5", program_name="Master's"
    )

    assert entity.program_data is program_data

    coordinator.data.programs = {}
    assert entity.program_data is None
