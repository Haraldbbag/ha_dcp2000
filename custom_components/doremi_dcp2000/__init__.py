"""The Doremi DCP-2000 integration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .client import DCP2000Client, DCPError, KLVClient
from .const import (
    CONF_KLV,
    CONF_KLV_PORT,
    CONF_KLV_REG,
    CONF_SCHEDULE,
    CONF_SOAP_PASSWORD,
    CONF_SOAP_PORT,
    CONF_SOAP_USER,
    DEFAULT_KLV_PORT,
    DEFAULT_PORT,
    DEFAULT_SOAP_PASSWORD,
    DEFAULT_SOAP_PORT,
    DEFAULT_SOAP_USER,
    DOMAIN,
)
from .coordinator import DCPConfigEntry, DCPCoordinator
from .schedule import ScheduleCoordinator
from .soap import DoremiSoapClient

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.MEDIA_PLAYER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
CARD_URL = "/doremi_dcp2000/doremi-dcp2000-card.js"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the dashboard card and load it in every frontend."""
    version = (await async_get_integration(hass, DOMAIN)).version
    path = Path(__file__).parent / "frontend" / "doremi-dcp2000-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), False)])
    url = f"{CARD_URL}?v={version}"
    # Load the card as a dashboard resource, i.e. after the frontend has booted.
    # Loading it earlier via the start page (add_extra_js_url) breaks in Firefox:
    # the scoped-custom-element-registry polyfill installed during boot forgets
    # elements defined before it ("Custom element doesn't exist").
    try:
        registered = await _async_register_resource(hass, url)
    except Exception as err:  # noqa: BLE001 - never block setup over the card
        _LOGGER.warning("Could not register the dashboard card as resource: %s", err)
        registered = False
    if not registered:  # YAML resources: fall back to the start page
        add_extra_js_url(hass, url)
    return True


async def _async_register_resource(hass: HomeAssistant, url: str) -> bool:
    data = hass.data.get("lovelace")
    resources = getattr(data, "resources", None)
    if resources is None or getattr(data, "resource_mode", "storage") != "storage":
        return False  # YAML resources: the user manages them
    if not getattr(resources, "loaded", True):
        await resources.async_load()
        resources.loaded = True
    base = url.split("?", 1)[0]
    for item in resources.async_items():
        if item.get("url", "").split("?", 1)[0] == base:
            if item["url"] != url:
                await resources.async_update_item(item["id"], {"res_type": "module", "url": url})
            return True
    await resources.async_create_item({"res_type": "module", "url": url})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DCPConfigEntry) -> bool:
    host = entry.data[CONF_HOST]
    client = DCP2000Client(host, entry.data.get(CONF_PORT, DEFAULT_PORT))
    try:
        info = await client.device_info()
    except DCPError as err:
        await client.close()
        raise ConfigEntryNotReady(f"{host} not reachable: {err}") from err

    klv = None
    if entry.options.get(CONF_KLV, False):
        klv = KLVClient(
            host,
            entry.options.get(CONF_KLV_PORT, DEFAULT_KLV_PORT),
            entry.options.get(CONF_KLV_REG, 0x04),
        )

    coordinator = DCPCoordinator(hass, entry, client, klv, info)
    await coordinator.async_load_cache()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    if entry.options.get(CONF_SCHEDULE, False):
        soap = DoremiSoapClient(
            async_get_clientsession(hass),
            host,
            entry.options.get(CONF_SOAP_PORT, DEFAULT_SOAP_PORT),
            entry.options.get(CONF_SOAP_USER, DEFAULT_SOAP_USER),
            entry.options.get(CONF_SOAP_PASSWORD, DEFAULT_SOAP_PASSWORD),
        )
        schedule = ScheduleCoordinator(hass, entry, coordinator, soap)
        await schedule.async_load()
        coordinator.schedule = schedule
        # Not first_refresh: an unreachable scheduler must not block the server control.
        await schedule.async_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DCPConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        if entry.runtime_data.schedule is not None:
            await entry.runtime_data.schedule.async_shutdown()
        await entry.runtime_data.async_shutdown()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: DCPConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
