"""Sensors for the Doremi DCP-2000."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DCPConfigEntry, DCPCoordinator, DCPData
from .entity import DCPEntity

TRANSPORT_STATES = ["playing", "starting", "paused", "cued", "cueing", "stopped", "ejected"]


@dataclass(frozen=True, kw_only=True)
class DCPSensorDescription(SensorEntityDescription):
    value_fn: Callable[[DCPData], Any]
    attr_fn: Callable[[DCPData], dict[str, Any]] | None = None
    klv_only: bool = False
    gpio_only: bool = False


def _remaining(d: DCPData) -> float | None:
    if d.frame is None or not d.last_frame:
        return None
    return round(max(0, d.last_frame - d.frame) / d.fps)


SENSORS: tuple[DCPSensorDescription, ...] = (
    DCPSensorDescription(
        key="transport", translation_key="transport", device_class=SensorDeviceClass.ENUM,
        options=TRANSPORT_STATES, value_fn=lambda d: d.state,
    ),
    DCPSensorDescription(key="title", translation_key="title", value_fn=lambda d: d.title),
    DCPSensorDescription(key="clip_id", translation_key="clip_id", value_fn=lambda d: d.clip_name),
    DCPSensorDescription(
        key="cpl_uuid", translation_key="cpl_uuid", entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.uuid,
    ),
    DCPSensorDescription(key="timecode", translation_key="timecode", value_fn=lambda d: d.timecode),
    DCPSensorDescription(
        key="progress", translation_key="progress", native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT, value_fn=lambda d: d.progress,
    ),
    DCPSensorDescription(
        key="remaining", translation_key="remaining", device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS, value_fn=_remaining,
    ),
    DCPSensorDescription(
        key="frame_rate", translation_key="frame_rate", native_unit_of_measurement="fps",
        value_fn=lambda d: d.fps if d.clip_id else None,
        attr_fn=lambda d: {"source": d.fps_source},
    ),
    DCPSensorDescription(
        key="frame", translation_key="frame", entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False, value_fn=lambda d: d.frame,
    ),
    DCPSensorDescription(
        key="library", translation_key="library", entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT, value_fn=lambda d: len(d.clips),
    ),
    DCPSensorDescription(
        key="gpi", translation_key="gpi", entity_category=EntityCategory.DIAGNOSTIC,
        gpio_only=True, value_fn=lambda d: d.gpi,
    ),
    DCPSensorDescription(
        key="spl_state", translation_key="spl_state", device_class=SensorDeviceClass.ENUM,
        options=["stopped", "playing", "paused"], klv_only=True,
        value_fn=lambda d: d.spl.state if d.spl else None,
    ),
    DCPSensorDescription(
        key="spl_progress", translation_key="spl_progress", native_unit_of_measurement=PERCENTAGE,
        klv_only=True, value_fn=lambda d: d.spl.spl_percent if d.spl else None,
    ),
    DCPSensorDescription(
        key="spl_uuid", translation_key="spl_uuid", entity_category=EntityCategory.DIAGNOSTIC,
        klv_only=True, value_fn=lambda d: d.spl.spl_uuid if d.spl else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: DCPConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    c = entry.runtime_data
    async_add_entities(
        DCPSensor(c, desc)
        for desc in SENSORS
        if (not desc.klv_only or c.klv) and (not desc.gpio_only or c.gpio_enabled)
    )


class DCPSensor(DCPEntity, SensorEntity):
    entity_description: DCPSensorDescription

    def __init__(self, coordinator: DCPCoordinator, desc: DCPSensorDescription) -> None:
        super().__init__(coordinator, desc.key)
        self.entity_description = desc

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attr_fn
        return fn(self.coordinator.data) if fn else None
