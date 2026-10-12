"""GPO outputs for the Doremi DCP-2000.

The server cannot report its output state, so the last value written by Home
Assistant is restored after a restart and the entities use assumed state.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .client import DCPError
from .const import GPIO_LINES
from .coordinator import DCPConfigEntry, DCPCoordinator
from .entity import DCPEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: DCPConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    c = entry.runtime_data
    if c.gpio_enabled:
        async_add_entities(DCPGpo(c, line) for line in range(GPIO_LINES))


class DCPGpo(DCPEntity, SwitchEntity, RestoreEntity):
    _attr_translation_key = "gpo"
    _attr_assumed_state = True

    def __init__(self, coordinator: DCPCoordinator, line: int) -> None:
        super().__init__(coordinator, f"gpo_{line}")
        self._line = line
        self._attr_translation_placeholders = {"line": str(line + 1)}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == "on":
            self.coordinator.gpo_state |= 1 << self._line

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.gpo_state & (1 << self._line))

    async def _write(self, on: bool) -> None:
        mask = 1 << self._line
        value = (self.coordinator.gpo_state | mask) if on else (self.coordinator.gpo_state & ~mask)
        try:
            await self.coordinator.client.set_gpo(value)
        except DCPError as err:
            raise HomeAssistantError(f"DCP-2000: {err}") from err
        self.coordinator.gpo_state = value
        self.coordinator.async_update_listeners()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)
