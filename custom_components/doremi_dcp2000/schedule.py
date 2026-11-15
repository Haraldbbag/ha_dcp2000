"""Show schedule of one Doremi server: scheduler (SOAP, read only) + what really runs (KLV)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DAY_START,
    CONF_EXTERNAL_PLAYERS,
    CONF_HISTORY_DAYS,
    CONF_PAUSE_LIMIT,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_INTERVAL,
    DEFAULT_DAY_START,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_PAUSE_LIMIT,
    DEFAULT_SCHEDULE_DAYS,
    DEFAULT_SCHEDULE_INTERVAL,
    DOMAIN,
    SCHEDULE_FAIL_TOLERANCE,
    SCHEDULE_INFO_BATCH,
    SPL_TITLE_REFRESH,
)
from .schedule_logic import ScheduleBook
from .soap import DoremiSoapClient, SoapAuthError, SoapError, SoapFault

if TYPE_CHECKING:
    from .coordinator import DCPConfigEntry, DCPCoordinator

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1


@dataclass
class ScheduleData:
    scheduler_status: str | None = None
    updated_at: datetime | None = None
    raw: list[dict[str, Any]] = field(default_factory=list)


def remaining_from_state(state: State | None, now: datetime) -> float | None:
    """Remaining seconds of an external player, None when it does not play / is unknown."""
    if state is None:
        return None
    if state.domain == "media_player":
        if state.state != "playing":
            return None
        duration = state.attributes.get("media_duration")
        position = state.attributes.get("media_position")
        if not duration or position is None:
            return None
        updated = state.attributes.get("media_position_updated_at")
        if isinstance(updated, str):
            updated = dt_util.parse_datetime(updated)
        elapsed = (now - updated).total_seconds() if isinstance(updated, datetime) else 0.0
        rest = float(duration) - float(position) - max(0.0, elapsed)
        return rest if rest > 0 else None
    # sensor: seconds (or with unit min/h) or "HH:MM:SS"
    value = state.state
    if value in (None, "", "unknown", "unavailable"):
        return None
    seconds: float | None = None
    if ":" in value:
        try:
            parts = [float(x) for x in value.split(":")]
        except ValueError:
            return None
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + part
    else:
        try:
            seconds = float(value)
        except ValueError:
            return None
        unit = state.attributes.get("unit_of_measurement")
        if unit == "min":
            seconds *= 60
        elif unit == "h":
            seconds *= 3600
        elif unit == "ms":
            seconds /= 1000
    return seconds if seconds and seconds > 0 else None


class ScheduleCoordinator(DataUpdateCoordinator[ScheduleData]):
    """Polls the scheduler every few minutes and follows the SPL status of the main coordinator."""

    config_entry: DCPConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: DCPConfigEntry, main: DCPCoordinator, soap: DoremiSoapClient
    ) -> None:
        opts = entry.options
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} schedule {soap.host}",
            update_interval=timedelta(
                minutes=opts.get(CONF_SCHEDULE_INTERVAL, DEFAULT_SCHEDULE_INTERVAL)
            ),
        )
        self.main = main
        self.soap = soap
        self.days = int(opts.get(CONF_SCHEDULE_DAYS, DEFAULT_SCHEDULE_DAYS))
        self.history = timedelta(days=int(opts.get(CONF_HISTORY_DAYS, DEFAULT_HISTORY_DAYS)))
        self.day_start = int(opts.get(CONF_DAY_START, DEFAULT_DAY_START))
        self.pause_limit = timedelta(minutes=int(opts.get(CONF_PAUSE_LIMIT, DEFAULT_PAUSE_LIMIT)))
        self.external_players: list[str] = list(opts.get(CONF_EXTERNAL_PLAYERS, []) or [])
        self.book = ScheduleBook()
        self._store: Store[dict] = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}.schedule")
        self._infos: dict[int, dict[str, Any]] = {}
        self._spl_titles: dict[str, str] = {}
        self._spl_titles_at = 0.0
        self._failures = 0
        self._warned = False
        self._unsub: CALLBACK_TYPE | None = None
        self.data = ScheduleData()

    # ------------------------------------------------------------ life cycle
    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        self.book = ScheduleBook.from_dict(stored.get("book"))
        self._infos = {int(k): v for k, v in (stored.get("infos") or {}).items()}
        self._unsub = self.main.async_add_listener(self._on_main_update)

    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {"book": self.book.to_dict(), "infos": {str(k): v for k, v in self._infos.items()}},
            30,
        )

    async def async_shutdown(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        await super().async_shutdown()
        await self._store.async_save(
            {"book": self.book.to_dict(), "infos": {str(k): v for k, v in self._infos.items()}}
        )
        await self.soap.logout()

    # --------------------------------------------------------- scheduler poll
    def _titles(self) -> dict[str, str]:
        titles = dict(self._spl_titles)
        for uuid in {s.spl_uuid for s in self.book.shows.values() if s.spl_uuid}:
            label = self.main.spl_title(uuid)
            if label:
                titles[uuid] = label
        return titles

    async def _async_update_data(self) -> ScheduleData:
        try:
            data = await self._poll()
        except SoapAuthError as err:
            # Wrong user/password: stop polling (no account lockout), ask for new credentials.
            raise ConfigEntryAuthFailed(f"Login at {self.soap.host} refused: {err}") from err
        except SoapError as err:
            self._failures += 1
            if not self._warned:
                _LOGGER.warning("Schedule from %s not readable: %s", self.soap.host, err)
                self._warned = True
            if self._failures < SCHEDULE_FAIL_TOLERANCE and self.data.updated_at:
                return self.data  # keep the last known schedule for a while
            raise UpdateFailed(str(err)) from err
        if self._warned:
            _LOGGER.info("Schedule from %s readable again", self.soap.host)
        self._failures = 0
        self._warned = False
        return data

    async def _poll(self) -> ScheduleData:
        now = dt_util.now()
        day_start, _ = ScheduleBook.day_bounds(now, self.day_start)
        window_start = day_start - timedelta(days=1)
        window_end = now + timedelta(days=self.days)
        status = await self.soap.scheduler_status()
        try:
            ids = await self.soap.schedule_list(window_start, window_end)
        except SoapFault as err:
            # Date filter not understood by this firmware: read all and filter here.
            _LOGGER.debug("GetScheduleList with dates failed on %s (%s), without filter", self.soap.host, err)
            ids = await self.soap.schedule_list()

        # Details only for new shows; status again for shows around now.
        recent_from, recent_to = now - timedelta(hours=12), now + timedelta(hours=1)
        todo = [i for i in ids if i not in self._infos]
        for i in ids:
            info = self._infos.get(i)
            if info is None or info.get("status") not in (None, "Pending", "Unknown"):
                continue
            show = self.book.shows.get(f"s{i}")
            if show and recent_from <= show.planned_start <= recent_to:
                todo.append(i)
        for sid in todo[:SCHEDULE_INFO_BATCH]:
            try:
                self._infos[sid] = await self.soap.schedule_info(sid)
            except SoapFault as err:
                if "not found" in err.fault.lower():
                    self._infos.pop(sid, None)
                    continue
                raise
        for gone in set(self._infos) - set(ids):
            del self._infos[gone]

        infos = [self._infos[i] for i in ids if i in self._infos]
        missing = {i.get("spl_uuid") for i in infos} - set(self._titles())
        if missing - {None} and time.monotonic() - self._spl_titles_at > SPL_TITLE_REFRESH:
            self._spl_titles_at = time.monotonic()
            try:
                listing = await self.soap.spl_list_info()
                self._spl_titles = {u: v["title"] for u, v in listing.items() if v.get("title")}
            except SoapFault as err:
                _LOGGER.debug("GetSPLListInfo on %s failed: %s", self.soap.host, err)

        changed = self.book.merge_server(
            infos, self._titles(), window_start, now, dt_util.get_default_time_zone()
        )
        changed |= self.book.prune(now, self.history)
        self._save()  # also keeps the cached infos
        return ScheduleData(scheduler_status=status, updated_at=now, raw=infos)

    # ------------------------------------------------- what really runs (2 s)
    def external_remaining(self, now: datetime) -> tuple[float | None, str | None]:
        """First playing player of the list counts."""
        for entity_id in self.external_players:
            rest = remaining_from_state(self.hass.states.get(entity_id), now)
            if rest is not None:
                return rest, entity_id
        return None, None

    @callback
    def _on_main_update(self) -> None:
        main = self.main
        if not main.klv or not main.last_update_success:
            return
        spl = main.data.spl
        if spl is None:
            return  # KLV status not read this round
        now = dt_util.now()
        ext = None
        if spl.state == "paused" and self.external_players:
            ext, _ = self.external_remaining(now)
        uuid = spl.spl_uuid.lower() if spl.spl_uuid else None
        changed = self.book.track(
            now,
            spl.state,
            uuid,
            spl.remaining,
            main.spl_title(uuid) if uuid else None,
            ext,
            self.pause_limit,
        )
        if changed:
            self._save()
            self.async_update_listeners()  # does not move the scheduler poll
