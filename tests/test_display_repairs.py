"""Tests for the display Repairs issues (custom_components/studylife/display_repairs.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.studylife.const import DOMAIN
from custom_components.studylife.display_api import DisplayApiError
from custom_components.studylife.display_coordinator import _parse_display_data
from custom_components.studylife.display_repairs import desired_issues

from .conftest import (
    TEST_DISPLAY_URL,
    make_raw_display_layouts,
    make_raw_display_state,
    setup_display_integration,
    setup_integration,
)


def _issue(hass: HomeAssistant, entry: MockConfigEntry, condition: str):
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"display_{condition}_{entry.entry_id}"
    )


def _ids(hass: HomeAssistant) -> set[str]:
    return {i for (d, i) in ir.async_get(hass).issues if d == DOMAIN}


def _desired(**state) -> set[str]:
    return desired_issues(
        _parse_display_data(make_raw_display_state(**state), make_raw_display_layouts())
    )


async def _poll(coordinator) -> None:
    await coordinator.async_refresh()
    await coordinator.hass.async_block_till_done()


def test_desired_issues_pure() -> None:
    assert _desired() == set()
    assert _desired(status="error", last_error={"kind": "rejected"}) == {"key_rejected"}
    assert _desired(status="error", last_error={"kind": "no_data"}) == {"no_data"}
    assert _desired(status="degraded", stale_minutes=59) == set()
    assert _desired(status="degraded", stale_minutes=60) == {"stale"}
    assert _desired(status="ok", last_error={"kind": "stale"}) == {"stale"}
    assert _desired(status="degraded", last_error={"kind": "transient"}) == set()
    assert _desired(sessions_ok=False) == {"sessions_scope"}


async def test_healthy_display_has_no_issues(
    hass, mock_display_config_entry, mock_display_api_client
) -> None:
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    assert _ids(hass) == set()


@pytest.mark.parametrize(
    ("state", "condition", "severity", "learn_more"),
    [
        (
            {"status": "error", "last_error": {"kind": "rejected"}},
            "key_rejected",
            ir.IssueSeverity.ERROR,
            f"{TEST_DISPLAY_URL}/connect",
        ),
        (
            {"status": "error", "last_error": {"kind": "no_data"}},
            "no_data",
            ir.IssueSeverity.ERROR,
            f"{TEST_DISPLAY_URL}/connect",
        ),
        (
            {"status": "degraded", "stale_minutes": 75},
            "stale",
            ir.IssueSeverity.WARNING,
            TEST_DISPLAY_URL,
        ),
        (
            {"sessions_ok": False},
            "sessions_scope",
            ir.IssueSeverity.WARNING,
            TEST_DISPLAY_URL,
        ),
    ],
)
async def test_condition_creates_issue_and_clearing_deletes_it(
    hass,
    mock_display_config_entry,
    mock_display_api_client,
    state,
    condition,
    severity,
    learn_more,
) -> None:
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        **state
    )
    coordinator = await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )

    issue = _issue(hass, mock_display_config_entry, condition)
    assert issue is not None
    assert issue.severity == severity
    assert issue.is_fixable is False
    assert issue.translation_key == f"display_{condition}"
    assert issue.learn_more_url == learn_more
    assert issue.translation_placeholders == {
        "name": mock_display_config_entry.title,
        "url": TEST_DISPLAY_URL,
    }

    mock_display_api_client.async_get_state.return_value = make_raw_display_state()
    await _poll(coordinator)
    assert _ids(hass) == set()


async def test_several_conditions_at_once(
    hass, mock_display_config_entry, mock_display_api_client
) -> None:
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        status="degraded",
        stale_minutes=90,
        sessions_ok=False,
        last_error={"kind": "stale"},
    )
    coordinator = await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    eid = mock_display_config_entry.entry_id
    assert _ids(hass) == {f"display_stale_{eid}", f"display_sessions_scope_{eid}"}

    # Only the sessions problem remains.
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        sessions_ok=False
    )
    await _poll(coordinator)
    assert _ids(hass) == {f"display_sessions_scope_{eid}"}


async def test_unreachable_after_three_failures_and_cleared_on_success(
    hass, mock_display_config_entry, mock_display_api_client
) -> None:
    coordinator = await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    mock_display_api_client.async_get_state.side_effect = DisplayApiError("down")

    for _ in range(2):
        await _poll(coordinator)
    assert _issue(hass, mock_display_config_entry, "unreachable") is None

    await _poll(coordinator)
    issue = _issue(hass, mock_display_config_entry, "unreachable")
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.ERROR
    assert issue.translation_key == "display_unreachable"

    mock_display_api_client.async_get_state.side_effect = None
    await _poll(coordinator)
    assert _issue(hass, mock_display_config_entry, "unreachable") is None


async def test_success_resets_the_failure_count(
    hass, mock_display_config_entry, mock_display_api_client
) -> None:
    coordinator = await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    mock_display_api_client.async_get_state.side_effect = DisplayApiError("down")
    for _ in range(2):
        await _poll(coordinator)
    mock_display_api_client.async_get_state.side_effect = None
    await _poll(coordinator)
    mock_display_api_client.async_get_state.side_effect = DisplayApiError("down")
    for _ in range(2):
        await _poll(coordinator)
    assert _issue(hass, mock_display_config_entry, "unreachable") is None


async def test_unload_deletes_issues(
    hass, mock_display_config_entry, mock_display_api_client
) -> None:
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        sessions_ok=False
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    assert _ids(hass)

    assert await hass.config_entries.async_unload(mock_display_config_entry.entry_id)
    await hass.async_block_till_done()
    assert _ids(hass) == set()


async def test_remove_entry_deletes_issues(
    hass, mock_display_config_entry, mock_display_api_client
) -> None:
    mock_display_api_client.async_get_state.return_value = make_raw_display_state(
        sessions_ok=False
    )
    await setup_display_integration(
        hass, mock_display_config_entry, mock_display_api_client
    )
    assert _ids(hass)
    await hass.config_entries.async_remove(mock_display_config_entry.entry_id)
    await hass.async_block_till_done()
    assert _ids(hass) == set()


async def test_account_entry_creates_no_issues(
    hass, mock_config_entry, mock_api_client: AsyncMock
) -> None:
    await setup_integration(hass, mock_config_entry, mock_api_client)
    assert _ids(hass) == set()
