"""The StudyLife integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_API_KEY,
    CONF_API_TOKEN,
    CONF_URL,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import StudyLifeApiClient
from .const import (
    CONF_ENTRY_TYPE,
    CONF_SCAN_INTERVAL,
    DEFAULT_DISPLAY_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENTRY_TYPE_ACCOUNT,
    ENTRY_TYPE_DISPLAY,
)
from .coordinator import StudyLifeCoordinator
from .display_api import DisplayApiClient
from .display_coordinator import DisplayCoordinator
from .services import (
    SERVICE_CREATE_SESSION,
    SERVICE_DELETE_SESSION,
    SERVICE_GENERATE_EXAM_PLAN,
    SERVICE_SET_ACTIVE_PROGRAM,
    SERVICE_SET_COURSE_GOAL,
    SERVICE_UPDATE_SESSION,
    async_register_services,
)

PLATFORMS_ACCOUNT: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.SELECT,
]
PLATFORMS_DISPLAY: list[Platform] = [Platform.SENSOR, Platform.SELECT, Platform.CAMERA]


def _entry_type(entry: ConfigEntry) -> str:
    # Entries created before ENTRY_TYPE_DISPLAY existed carry no CONF_ENTRY_TYPE at all -
    # every one of those is a StudyLife account, so that absence defaults to ACCOUNT
    # rather than requiring a migration step.
    return entry.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_ACCOUNT)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if _entry_type(entry) == ENTRY_TYPE_DISPLAY:
        return await _async_setup_display_entry(hass, entry)
    return await _async_setup_account_entry(hass, entry)


async def _async_setup_account_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    # Since phase 3, the key is per-user and long-lived (no more server-side rotation) -
    # the former X-Api-Key-Rotated adoption logic and its persist callback are gone.
    client = StudyLifeApiClient(
        entry.data[CONF_URL], session, entry.data.get(CONF_API_KEY)
    )

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = StudyLifeCoordinator(hass, client, timedelta(seconds=scan_interval))
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS_ACCOUNT)
    return True


async def _async_setup_display_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(
        hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True)
    )
    client = DisplayApiClient(
        entry.data[CONF_URL],
        session,
        entry.data[CONF_API_TOKEN],
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, True),
    )

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_DISPLAY_SCAN_INTERVAL)
    coordinator = DisplayCoordinator(hass, client, timedelta(seconds=scan_interval))
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS_DISPLAY)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None:
        scan_interval = entry.options.get(
            CONF_SCAN_INTERVAL,
            DEFAULT_DISPLAY_SCAN_INTERVAL
            if _entry_type(entry) == ENTRY_TYPE_DISPLAY
            else DEFAULT_SCAN_INTERVAL,
        )
        if isinstance(coordinator, DisplayCoordinator):
            unchanged = (
                coordinator.client.base_url == entry.data[CONF_URL]
                and coordinator.client.token == entry.data.get(CONF_API_TOKEN)
                and coordinator.client.verify_ssl
                == entry.data.get(CONF_VERIFY_SSL, True)
                and coordinator.update_interval == timedelta(seconds=scan_interval)
            )
        else:
            unchanged = (
                coordinator.client.base_url == entry.data[CONF_URL]
                and coordinator.client.api_key == entry.data.get(CONF_API_KEY)
                and coordinator.update_interval == timedelta(seconds=scan_interval)
            )
        if unchanged:
            # Entry update that changes nothing the running instances don't already use -
            # reloading would tear down all entities for no benefit (and could not even
            # run during initial setup).
            return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    platforms = (
        PLATFORMS_DISPLAY
        if _entry_type(entry) == ENTRY_TYPE_DISPLAY
        else PLATFORMS_ACCOUNT
    )
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_CREATE_SESSION)
            hass.services.async_remove(DOMAIN, SERVICE_UPDATE_SESSION)
            hass.services.async_remove(DOMAIN, SERVICE_DELETE_SESSION)
            hass.services.async_remove(DOMAIN, SERVICE_SET_COURSE_GOAL)
            hass.services.async_remove(DOMAIN, SERVICE_GENERATE_EXAM_PLAN)
            hass.services.async_remove(DOMAIN, SERVICE_SET_ACTIVE_PROGRAM)
    return unloaded
