"""Base entity for the Doremi DCP-2000."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import DCPCoordinator


class DCPEntity(CoordinatorEntity[DCPCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: DCPCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        base = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{base}_{key}"
        info = coordinator.info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=info.product or "DCP-2000",
            serial_number=info.serial or None,
            sw_version=info.protocol_version or None,
            configuration_url=None,
        )
