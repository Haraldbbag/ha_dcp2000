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
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from homeassistant.util import dt as dt_util

from . import protocol as p
from .coordinator import DCPConfigEntry, DCPCoordinator, DCPData
from .entity import DCPEntity, ScheduleEntity
from .schedule import ScheduleCoordinator
from .schedule_logic import Show

TRANSPORT_STATES = ["playing", "starting", "paused", "cued", "cueing", "stopped", "ejected"]


@dataclass(frozen=True, kw_only=True)
class DCPSensorDescription(SensorEntityDescription):
    value_fn: Callable[[DCPData], Any]
    attr_fn: Callable[[DCPData], dict[str, Any]] | None = None
    klv_only: bool = False
    gpio_only: bool = False


def _remaining_s(d: DCPData) -> int | None:
    if d.frame is None or not d.last_frame:
        return None
    return round(max(0, d.last_frame - d.frame) / d.fps)


def _spl_remaining_s(d: DCPData) -> int | None:
    return d.spl.remaining if d.spl and d.spl.spl_uuid else None


def _spl_item(d: DCPData) -> dict | None:
    if d.spl_index is None or not d.spl_items:
        return None
    return d.spl_items[d.spl_index]


def _spl_content_attrs(d: DCPData) -> dict[str, Any]:
    items = [
        {
            "title": it["title"],
            "kind": it["kind"],
            "start": p.format_hms(it["start"]),
            "duration": p.format_hms(it["duration"]),
            "fps": it["fps"],
            "cues": it["cues"],
            "current": i == d.spl_index,
        }
        for i, it in enumerate(d.spl_items)
    ]
    return {
        "spl_uuid": d.spl.spl_uuid if d.spl else None,
        "current_index": d.spl_index,
        "count": len(items),
        "remaining": p.format_hms(_spl_remaining_s(d)),
        "items": items,
    }


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
        key="remaining", translation_key="remaining",
        value_fn=lambda d: p.format_hms(_remaining_s(d)),
        attr_fn=lambda d: {"seconds": _remaining_s(d)},
    ),
    DCPSensorDescription(
        key="spl_remaining", translation_key="spl_remaining", klv_only=True,
        value_fn=lambda d: p.format_hms(_spl_remaining_s(d)),
        attr_fn=lambda d: {"seconds": _spl_remaining_s(d)},
    ),
    DCPSensorDescription(
        key="spl_content", translation_key="spl_content", klv_only=True,
        value_fn=lambda d: (_spl_item(d) or {}).get("title"),
        attr_fn=_spl_content_attrs,
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
        options=["stopped", "playing", "paused", "unknown"], klv_only=True,
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
    entities: list[SensorEntity] = [
        DCPSensor(c, desc)
        for desc in SENSORS
        if (not desc.klv_only or c.klv) and (not desc.gpio_only or c.gpio_enabled)
    ]
    if c.schedule is not None:
        entities += [ScheduleSensor(c.schedule, desc) for desc in SCHEDULE_SENSORS]
        entities.append(ShowsTodaySensor(c.schedule))
        entities.append(LastShowStatusSensor(c.schedule))
    async_add_entities(entities)


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


# ----------------------------------------------------------------- schedule


@dataclass(frozen=True, kw_only=True)
class ScheduleSensorDescription(SensorEntityDescription):
    show_fn: Callable[[ScheduleCoordinator], Show | None]
    time_fn: Callable[[Show], Any]


SCHEDULE_SENSORS: tuple[ScheduleSensorDescription, ...] = (
    ScheduleSensorDescription(
        key="first_show_start", translation_key="first_show_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        show_fn=lambda c: c.book.first_of_day(dt_util.now(), c.day_start),
        time_fn=lambda s: s.start,
    ),
    ScheduleSensorDescription(
        key="last_show_end", translation_key="last_show_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        show_fn=lambda c: c.book.last_of_day(dt_util.now(), c.day_start),
        time_fn=lambda s: s.sensor_end,
    ),
    ScheduleSensorDescription(
        key="next_show_start", translation_key="next_show_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        show_fn=lambda c: c.book.next_show(dt_util.now()),
        time_fn=lambda s: s.planned_start,
    ),
    ScheduleSensorDescription(
        key="running_show_end", translation_key="running_show_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        show_fn=lambda c: c.book.running_show(),
        time_fn=lambda s: s.sensor_end,
    ),
)


class ScheduleSensor(ScheduleEntity, SensorEntity):
    entity_description: ScheduleSensorDescription
    tracking = True

    def __init__(self, coordinator: ScheduleCoordinator, desc: ScheduleSensorDescription) -> None:
        super().__init__(coordinator, desc.key)
        self.entity_description = desc

    def _show(self) -> Show | None:
        return self.entity_description.show_fn(self.coordinator)

    @property
    def native_value(self) -> Any:
        show = self._show()
        return self.entity_description.time_fn(show) if show else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        show = self._show()
        attrs = show.attrs() if show else {}
        if self.entity_description.key == "running_show_end" and show:
            ext, entity = self.coordinator.external_remaining(dt_util.now())
            attrs["externer_player"] = entity if self.coordinator.book.paused_since else None
            attrs["pause_seit"] = (
                self.coordinator.book.paused_since.isoformat() if self.coordinator.book.paused_since else None
            )
        return attrs


class ShowsTodaySensor(ScheduleEntity, SensorEntity):
    """Number of shows of the show day; list only of this day (keeps the database small)."""

    _attr_translation_key = "shows_today"
    _attr_state_class = SensorStateClass.MEASUREMENT
    tracking = True

    def __init__(self, coordinator: ScheduleCoordinator) -> None:
        super().__init__(coordinator, "shows_today")

    def _shows(self) -> list[Show]:
        return self.coordinator.book.day_shows(dt_util.now(), self.coordinator.day_start)

    @property
    def native_value(self) -> int:
        return len(self._shows())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "liste": [
                {
                    "beginn": dt_util.as_local(s.start).strftime("%H:%M"),
                    "ende": dt_util.as_local(s.sensor_end).strftime("%H:%M"),
                    "titel": s.summary(),
                    "dauer": p.format_hms(int((s.end - s.start).total_seconds())),
                    "status": s.status if not s.manual else None,
                    "quelle": s.source(),
                    "laeuft": s.running,
                    "abgebrochen": s.aborted,
                }
                for s in self._shows()
            ],
            "scheduler": self.coordinator.data.scheduler_status,
        }


class LastShowStatusSensor(ScheduleEntity, SensorEntity):
    _attr_translation_key = "last_show_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["success", "failure", "aborted", "unknown"]

    def __init__(self, coordinator: ScheduleCoordinator) -> None:
        super().__init__(coordinator, "last_show_status")

    def _show(self) -> Show | None:
        return self.coordinator.book.last_finished(dt_util.now())

    @property
    def native_value(self) -> str | None:
        show = self._show()
        if not show:
            return None
        if show.aborted:
            return "aborted"
        status = (show.status or "").lower()
        if status in ("success", "failure", "aborted"):
            return status
        return "success" if show.finished else "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        show = self._show()
        return show.attrs() if show else None
