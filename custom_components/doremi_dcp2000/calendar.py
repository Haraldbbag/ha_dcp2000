"""Show schedule of the server as calendar (planned + really played shows)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import DCPConfigEntry
from .entity import ScheduleEntity
from .schedule import ScheduleCoordinator
from .schedule_logic import Show


async def async_setup_entry(
    hass: HomeAssistant, entry: DCPConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    schedule = entry.runtime_data.schedule
    if schedule is None:
        return
    async_add_entities([DCPScheduleCalendar(schedule)])
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "get_schedule", None, "async_get_schedule", supports_response=SupportsResponse.ONLY
    )


def _event(show: Show) -> CalendarEvent:
    return CalendarEvent(
        start=show.start,
        end=max(show.end, show.start),
        summary=show.summary(),
        description=show.description() or None,
        uid=show.key,
    )


class DCPScheduleCalendar(ScheduleEntity, CalendarEntity):
    _attr_translation_key = "schedule"
    tracking = True

    def __init__(self, coordinator: ScheduleCoordinator) -> None:
        super().__init__(coordinator, "schedule")

    @property
    def event(self) -> CalendarEvent | None:
        show = self.coordinator.book.calendar_event(dt_util.now())
        return _event(show) if show else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [_event(s) for s in self.coordinator.book.between(start_date, end_date)]

    async def async_get_schedule(self) -> ServiceResponse:
        """Raw answers of the server and the tracking state (for checks)."""
        c = self.coordinator
        now = dt_util.now()
        running = c.book.running_show()
        ext, ext_entity = c.external_remaining(now)
        result: dict[str, Any] = {
            "scheduler_status": c.data.scheduler_status,
            "updated_at": c.data.updated_at.isoformat() if c.data.updated_at else None,
            "server_schedules": c.data.raw,
            "running": running.attrs() if running else None,
            "paused_since": c.book.paused_since.isoformat() if c.book.paused_since else None,
            "external_player": ext_entity,
            "external_remaining_s": round(ext) if ext is not None else None,
            "stored_shows": len(c.book.shows),
        }
        return result
