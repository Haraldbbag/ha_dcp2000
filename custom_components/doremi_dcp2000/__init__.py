"""The Doremi DCP-2000 integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .client import DCP2000Client, DCPError, KLVClient
from .const import DOMAIN, CONF_KLV, CONF_KLV_PORT, CONF_KLV_REG, DEFAULT_KLV_PORT, DEFAULT_PORT
from .coordinator import DCPConfigEntry, DCPCoordinator

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
CARD_URL = "/doremi_dcp2000/doremi-dcp2000-card.js"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the dashboard card and load it in every frontend."""
    version = (await async_get_integration(hass, DOMAIN)).version
    path = Path(__file__).parent / "frontend" / "doremi-dcp2000-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), False)])
    add_extra_js_url(hass, f"{CARD_URL}?v={version}")
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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DCPConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: DCPConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
