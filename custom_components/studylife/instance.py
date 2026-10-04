"""Persisting a StudyLife server's stable instance id in its account entry."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import StudyLifeApiClient
from .const import CONF_INSTANCE_ID

_LOGGER = logging.getLogger(__name__)


async def async_fetch_and_store_instance_id(
    hass: HomeAssistant, entry: ConfigEntry, client: StudyLifeApiClient
) -> str | None:
    """Fetch the server's instance id once and store it in the entry data.

    Returns the id, or None when the server does not publish one (older server, error).
    Nothing is persisted then, so a later setup or discovery tries again. Updating the
    entry data does not reload the entry (see _async_update_listener's `unchanged`)."""
    try:
        info = await client.async_get_instance()
    except Exception:
        _LOGGER.debug("Fetching the instance id failed", exc_info=True)
        return None
    instance_id = info.get("id") if info else None
    if not instance_id:
        return None
    if entry.data.get(CONF_INSTANCE_ID) != instance_id:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_INSTANCE_ID: instance_id}
        )
    return instance_id
