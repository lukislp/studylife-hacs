"""Home Assistant Repairs issues for a studylife-display config entry.

The display coordinator calls async_sync_display_issues() after every successful poll
and async_report_unreachable() when its own API stays unreachable; an issue exists
exactly as long as its condition holds and is deleted as soon as the condition clears.
Auth failures are not reported here: they already start the reauth flow.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

if TYPE_CHECKING:  # the coordinator imports this module, not the other way round
    from .display_coordinator import DisplayData

ISSUE_KEY_REJECTED = "key_rejected"
ISSUE_NO_DATA = "no_data"
ISSUE_STALE = "stale"
ISSUE_SESSIONS_SCOPE = "sessions_scope"
ISSUE_UNREACHABLE = "unreachable"

ALL_ISSUES = (
    ISSUE_KEY_REJECTED,
    ISSUE_NO_DATA,
    ISSUE_STALE,
    ISSUE_SESSIONS_SCOPE,
    ISSUE_UNREACHABLE,
)

_SEVERITY = {
    ISSUE_KEY_REJECTED: ir.IssueSeverity.ERROR,
    ISSUE_NO_DATA: ir.IssueSeverity.ERROR,
    ISSUE_STALE: ir.IssueSeverity.WARNING,
    ISSUE_SESSIONS_SCOPE: ir.IssueSeverity.WARNING,
    ISSUE_UNREACHABLE: ir.IssueSeverity.ERROR,
}

# The two key problems are fixed on the display's /connect page; the rest has no
# better pointer than the display's web root.
_CONNECT_ISSUES = {ISSUE_KEY_REJECTED, ISSUE_NO_DATA}

# Data older than this many minutes is reported even while the display says "degraded".
STALE_REPAIR_MINUTES = 60

UNREACHABLE_THRESHOLD = 3  # consecutive failed polls


def issue_id(condition: str, entry: ConfigEntry) -> str:
    return f"display_{condition}_{entry.entry_id}"


def desired_issues(data: DisplayData) -> set[str]:
    """The conditions (ISSUE_* names) that currently hold for this display state."""
    wanted: set[str] = set()
    last_kind = (data.last_error or {}).get("kind")
    if data.status == "error" and last_kind == "rejected":
        wanted.add(ISSUE_KEY_REJECTED)
    if last_kind == "no_data":
        wanted.add(ISSUE_NO_DATA)
    if (
        data.status == "degraded"
        and data.stale_minutes is not None
        and data.stale_minutes >= STALE_REPAIR_MINUTES
    ) or last_kind == "stale":
        wanted.add(ISSUE_STALE)
    if data.sessions_ok is False:
        wanted.add(ISSUE_SESSIONS_SCOPE)
    return wanted


def _create(hass: HomeAssistant, entry: ConfigEntry, condition: str) -> None:
    url = str(entry.data.get(CONF_URL, "")).rstrip("/")
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id(condition, entry),
        is_fixable=False,
        learn_more_url=f"{url}/connect" if condition in _CONNECT_ISSUES else url,
        severity=_SEVERITY[condition],
        translation_key=f"display_{condition}",
        translation_placeholders={"name": entry.title, "url": url},
    )


def async_sync_display_issues(
    hass: HomeAssistant, entry: ConfigEntry, data: DisplayData
) -> None:
    """Create/delete the data-driven issues after a successful poll; a successful
    poll also means the display is reachable again."""
    wanted = desired_issues(data)
    for condition in ALL_ISSUES:
        if condition in wanted:
            _create(hass, entry, condition)
        else:
            ir.async_delete_issue(hass, DOMAIN, issue_id(condition, entry))


def async_report_unreachable(hass: HomeAssistant, entry: ConfigEntry) -> None:
    _create(hass, entry, ISSUE_UNREACHABLE)


def async_delete_display_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    for condition in ALL_ISSUES:
        ir.async_delete_issue(hass, DOMAIN, issue_id(condition, entry))
