"""Show playlist selection for the Doremi DCP-2000 (needs the KLV API)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import DCPError
from .coordinator import DCPConfigEntry, DCPCoordinator
from .entity import DCPEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: DCPConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    c = entry.runtime_data
    if c.klv:
        async_add_entities([DCPShowPlaylist(c)])


class DCPShowPlaylist(DCPEntity, SelectEntity):
    """Choosing an entry loads that SPL (output stays black until Play)."""

    _attr_translation_key = "spl"

    def __init__(self, coordinator: DCPCoordinator) -> None:
        super().__init__(coordinator, "spl_select")

    @property
    def options(self) -> list[str]:
        options = list(self.coordinator.data.spls)
        current = self.current_option
        if current and current not in options:
            options.append(current)
        return options

    @property
    def current_option(self) -> str | None:
        spl = self.coordinator.data.spl
        return self.coordinator.spl_label(spl.spl_uuid) if spl else None

    @property
    def extra_state_attributes(self) -> dict:
        spl = self.coordinator.data.spl
        return {"spl_uuid": spl.spl_uuid if spl else None}

    async def async_select_option(self, option: str) -> None:
        uuid = self.coordinator.spl_by_name(option)
        try:
            await self.coordinator.klv.load_spl_uuid(uuid)
        except DCPError as err:
            raise HomeAssistantError(f"DCP-2000: {err}") from err
        await self.coordinator.async_request_refresh()
