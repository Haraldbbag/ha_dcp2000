"""Binary sensors (connection + GPI inputs) for the Doremi DCP-2000."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import GPIO_LINES
from .coordinator import DCPConfigEntry, DCPCoordinator
from .entity import DCPEntity, ScheduleEntity
from .schedule import ScheduleCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: DCPConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    c = entry.runtime_data
    entities: list[BinarySensorEntity] = [DCPConnected(c)]
    if c.gpio_enabled:
        entities += [DCPGpi(c, line) for line in range(GPIO_LINES)]
    if c.schedule is not None:
        entities.append(SchedulerRunning(c.schedule))
    async_add_entities(entities)


class DCPConnected(DCPEntity, BinarySensorEntity):
    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DCPCoordinator) -> None:
        super().__init__(coordinator, "connected")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success


class DCPGpi(DCPEntity, BinarySensorEntity):
    _attr_translation_key = "gpi"

    def __init__(self, coordinator: DCPCoordinator, line: int) -> None:
        super().__init__(coordinator, f"gpi_{line}")
        self._line = line
        self._attr_translation_placeholders = {"line": str(line + 1)}

    @property
    def is_on(self) -> bool | None:
        gpi = self.coordinator.data.gpi
        return None if gpi is None else bool(gpi & (1 << self._line))


class SchedulerRunning(ScheduleEntity, BinarySensorEntity):
    """Scheduler of the server runs (it loads and starts the planned shows itself)."""

    _attr_translation_key = "scheduler"

    def __init__(self, coordinator: ScheduleCoordinator) -> None:
        super().__init__(coordinator, "scheduler")

    @property
    def is_on(self) -> bool | None:
        status = self.coordinator.data.scheduler_status
        if status in (None, "Unknown"):
            return None
        return status == "Running"

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {"status": self.coordinator.data.scheduler_status}
