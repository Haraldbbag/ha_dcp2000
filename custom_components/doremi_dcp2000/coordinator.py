"""Polling coordinator for the Doremi DCP-2000."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import protocol as p
from .client import DCP2000Client, DCPCommandError, DCPError, DeviceInfo, KLVClient
from .const import (
    CONF_FPS,
    CONF_GPIO,
    CONF_SCAN_INTERVAL,
    DEFAULT_FPS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LIBRARY_REFRESH,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class DCPData:
    status: p.TransportStatus = field(default_factory=p.TransportStatus)
    frame: int | None = None
    last_frame: int | None = None
    timecode: str | None = None
    clip_id: bytes | None = None
    uuid: str | None = None
    title: str | None = None
    cpl: p.CplInfo | None = None
    fps: float = DEFAULT_FPS
    gpi: int | None = None
    spl: p.SplStatus | None = None
    clips: dict[str, bytes] = field(default_factory=dict)
    position_updated_at: datetime | None = None

    @property
    def state(self) -> str:
        return self.status.state

    @property
    def clip_name(self) -> str | None:
        return p.clip_id_to_str(self.clip_id) if self.clip_id else None

    @property
    def position(self) -> float | None:
        return None if self.frame is None else self.frame / self.fps

    @property
    def duration(self) -> float | None:
        return None if not self.last_frame else self.last_frame / self.fps

    @property
    def progress(self) -> float | None:
        if self.frame is None or not self.last_frame:
            return None
        return round(min(100.0, 100.0 * self.frame / self.last_frame), 1)


type DCPConfigEntry = ConfigEntry[DCPCoordinator]


class DCPCoordinator(DataUpdateCoordinator[DCPData]):
    """Fetch transport state from one DCP-2000."""

    config_entry: DCPConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: DCPConfigEntry,
        client: DCP2000Client,
        klv: KLVClient | None,
        info: DeviceInfo,
    ) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {client.host}",
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.klv = klv
        self.info = info
        self.gpio_enabled: bool = entry.options.get(CONF_GPIO, False)
        self.default_fps: float = float(entry.options.get(CONF_FPS, DEFAULT_FPS))
        self.gpo_state: int = 0  # the device cannot report outputs; we remember them
        self._library_at = 0.0
        self._ext_supported = True
        self._klv_warned = False
        self._titles: dict[bytes, str | None] = {}
        self.data = DCPData(fps=self.default_fps)

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        await self.client.close()
        if self.klv:
            await self.klv.close()

    async def _async_update_data(self) -> DCPData:
        prev = self.data or DCPData(fps=self.default_fps)
        try:
            return await self._fetch(prev)
        except DCPError as err:
            raise UpdateFailed(str(err)) from err

    async def _fetch(self, prev: DCPData) -> DCPData:
        c = self.client
        data = replace(prev)
        data.status = await c.status()
        data.frame = await c.current_frame()
        data.position_updated_at = dt_util.utcnow()
        try:
            data.timecode = await c.timecode()
        except DCPCommandError:
            data.timecode = None
        data.clip_id = await c.loaded_id()

        if data.clip_id != prev.clip_id or (data.clip_id and data.last_frame is None):
            await self._load_clip_details(data)
        elif data.clip_id is None:
            data.last_frame = data.uuid = data.title = data.cpl = None
            data.fps = self.default_fps

        if self.gpio_enabled:
            try:
                _, data.gpi = await c.gpi()
            except DCPCommandError as err:
                _LOGGER.debug("GPI sense failed: %s", err)
                data.gpi = None

        if self.klv:
            try:
                data.spl = await self.klv.status_spl()
                self._klv_warned = False
            except DCPError as err:
                data.spl = None
                if not self._klv_warned:
                    _LOGGER.warning("SPL status from %s failed: %s", c.host, err)
                    self._klv_warned = True

        if time.monotonic() - self._library_at > LIBRARY_REFRESH or not data.clips:
            await self._refresh_library(data)
        return data

    async def _load_clip_details(self, data: DCPData) -> None:
        c = self.client
        data.uuid = data.title = data.cpl = None
        data.fps = self.default_fps
        if data.clip_id is None:
            data.last_frame = None
            return
        try:
            data.last_frame = await c.last_frame()
        except DCPCommandError:
            data.last_frame = None
        if self._ext_supported:
            try:
                data.uuid = await c.current_uuid()
                data.title = await c.current_title()
            except DCPCommandError as err:
                _LOGGER.info("Extended commands not supported by %s: %s", c.host, err)
                self._ext_supported = False
        if self.klv and data.uuid:
            try:
                data.cpl = await self.klv.get_cpl(data.uuid)
                data.fps = data.cpl.fps or self.default_fps
                data.title = data.title or data.cpl.title
            except (DCPError, ValueError) as err:
                _LOGGER.debug("GetCPL failed: %s", err)

    async def _refresh_library(self, data: DCPData) -> None:
        try:
            clips = await self.client.list_clips()
        except DCPCommandError as err:
            _LOGGER.debug("Listing clips failed: %s", err)
            clips = []
        names: dict[str, bytes] = {}
        for clip in clips:
            label = p.clip_id_to_str(clip)
            title = await self._clip_title(clip)
            names[f"{label} – {title}" if title else label] = clip
        data.clips = names
        self._library_at = time.monotonic()

    async def _clip_title(self, clip: bytes) -> str | None:
        """Title of a clip that is not loaded (needs the KLV API)."""
        if clip in self._titles:
            return self._titles[clip]
        title = None
        if self.klv and self._ext_supported:
            try:
                uuid = await self.client.uuid_for_id(clip)
                if uuid:
                    title = (await self.klv.get_cpl(uuid)).title or None
            except (DCPError, ValueError) as err:
                _LOGGER.debug("No title for %r: %s", clip, err)
        self._titles[clip] = title
        return title

    async def async_refresh_library(self) -> None:
        self._library_at = 0.0
        self._titles.clear()
        await self.async_request_refresh()

    def clip_by_name(self, name: str) -> bytes:
        """Accept a library label, a bare clip id or a CPL title."""
        clips = self.data.clips if self.data else {}
        if name in clips:
            return clips[name]
        for label, raw in clips.items():
            if p.clip_id_to_str(raw) == name or label.split(" – ", 1)[-1] == name:
                return raw
        return p.clip_id_from_str(name)

    def label_for(self, clip: bytes | None) -> str | None:
        if clip is None or not self.data:
            return None
        for label, raw in self.data.clips.items():
            if raw == clip:
                return label
        return p.clip_id_to_str(clip)
