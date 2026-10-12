"""Buttons (projector, transport helpers) for the Doremi DCP-2000."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import protocol as p
from .client import DCPError
from .coordinator import DCPConfigEntry, DCPCoordinator
from .entity import DCPEntity


@dataclass(frozen=True, kw_only=True)
class DCPButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[DCPCoordinator], Awaitable]
    klv_only: bool = False


async def _spl_play(c: DCPCoordinator) -> None:
    c.mark_start_requested()
    await c.klv.simple("PlaySPL")


BUTTONS: tuple[DCPButtonDescription, ...] = (
    DCPButtonDescription(
        key="shutter_open", translation_key="shutter_open",
        press_fn=lambda c: c.client.projector(p.PROJ_SHUTTER, 0x00),
    ),
    DCPButtonDescription(
        key="shutter_close", translation_key="shutter_close",
        press_fn=lambda c: c.client.projector(p.PROJ_SHUTTER, 0x01),
    ),
    DCPButtonDescription(
        key="projector_on", translation_key="projector_on",
        press_fn=lambda c: c.client.projector(p.PROJ_POWER, 0x01),
    ),
    DCPButtonDescription(
        key="projector_off", translation_key="projector_off",
        press_fn=lambda c: c.client.projector(p.PROJ_POWER, 0x00),
    ),
    DCPButtonDescription(
        key="step_back", translation_key="step_back",
        press_fn=lambda c: c.client.step_back(),
    ),
    DCPButtonDescription(
        key="measure_fps", translation_key="measure_fps",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda c: c.async_measure_fps(),
    ),
    DCPButtonDescription(
        key="refresh_library", translation_key="refresh_library",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda c: c.async_refresh_library(),
    ),
    DCPButtonDescription(
        key="spl_play", translation_key="spl_play", klv_only=True,
        press_fn=lambda c: _spl_play(c),
    ),
    DCPButtonDescription(
        key="spl_pause", translation_key="spl_pause", klv_only=True,
        press_fn=lambda c: c.klv.simple("PauseSPL"),
    ),
    DCPButtonDescription(
        key="spl_eject", translation_key="spl_eject", klv_only=True,
        press_fn=lambda c: c.klv.simple("EjectSPL"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: DCPConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    c = entry.runtime_data
    async_add_entities(DCPButton(c, d) for d in BUTTONS if not d.klv_only or c.klv)


class DCPButton(DCPEntity, ButtonEntity):
    entity_description: DCPButtonDescription

    def __init__(self, coordinator: DCPCoordinator, desc: DCPButtonDescription) -> None:
        super().__init__(coordinator, desc.key)
        self.entity_description = desc

    async def async_press(self) -> None:
        try:
            await self.entity_description.press_fn(self.coordinator)
        except DCPError as err:
            raise HomeAssistantError(f"DCP-2000: {err}") from err
        if self.entity_description.key != "refresh_library":
            await self.coordinator.async_request_refresh()
