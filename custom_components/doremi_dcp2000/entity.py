"""Base entities for the Doremi DCP-2000."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import DCPCoordinator

if TYPE_CHECKING:
    from .schedule import ScheduleCoordinator


def device_info(coordinator: DCPCoordinator) -> DeviceInfo:
    entry = coordinator.config_entry
    base = entry.unique_id or entry.entry_id
    info = coordinator.info
    return DeviceInfo(
        identifiers={(DOMAIN, base)},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model=info.product or "DCP-2000",
        serial_number=info.serial or None,
        sw_version=info.protocol_version or None,
        configuration_url=None,
    )


def _unique_id(coordinator: DCPCoordinator, key: str) -> str:
    entry = coordinator.config_entry
    return f"{entry.unique_id or entry.entry_id}_{key}"


class DCPEntity(CoordinatorEntity[DCPCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: DCPCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = _unique_id(coordinator, key)
        self._attr_device_info = device_info(coordinator)


class ScheduleEntity(CoordinatorEntity["ScheduleCoordinator"]):
    """Entity of the show schedule; belongs to the same device as the server."""

    _attr_has_entity_name = True
    tracking = False  # True: value also follows the SPL status, stays available without SOAP

    def __init__(self, coordinator: ScheduleCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = _unique_id(coordinator.main, key)
        self._attr_device_info = device_info(coordinator.main)

    @property
    def available(self) -> bool:
        if self.coordinator.last_update_success:
            return True
        return self.tracking and bool(self.coordinator.book.shows)
