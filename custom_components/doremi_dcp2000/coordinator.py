"""Polling coordinator for the Doremi DCP-2000."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
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
    START_TIMEOUT,
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
    fps_source: str = "default"
    gpi: int | None = None
    spl: p.SplStatus | None = None
    clips: dict[str, bytes] = field(default_factory=dict)
    spls: dict[str, str] = field(default_factory=dict)  # label -> SPL UUID
    position_updated_at: datetime | None = None
    starting: bool = False

    @property
    def state(self) -> str:
        state = self.status.state
        if state == "ejected" and self.spl and self.spl.spl_uuid:
            # An SPL is loaded but not started: the 9-pin side shows "eject".
            state = {"playing": "playing", "paused": "paused"}.get(self.spl.state, "cued")
        if self.starting and state != "playing":
            return "starting"
        return state

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


def clip_label(clip: bytes, title: str | None) -> str:
    """Source-list entry: 'Title (clip id)' so the list sorts by title."""
    clip_id = p.clip_id_to_str(clip)
    return f"{title} ({clip_id})" if title else clip_id


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
        self._start_requested: float | None = None
        self.gpo_state: int = 0  # the device cannot report outputs; we remember them
        self._library_at = 0.0
        self._ext_supported = True
        self._klv_warned = False
        self._titles: dict[bytes, str | None] = {}
        # Per-clip knowledge that survives restarts: {clip_id: {title, fps, fps_source}}
        self._store: Store[dict] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self._cache: dict[str, dict] = {}
        self._spl_cache: dict[str, dict] = {}  # uuid -> {title, frames}
        self.data = DCPData(fps=self.default_fps)

    async def async_load_cache(self) -> None:
        stored = await self._store.async_load() or {}
        self._cache = stored.get("clips", {})
        self._spl_cache = stored.get("spls", {})

    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {"clips": self._cache, "spls": self._spl_cache}, 10
        )

    def _remember(self, clip: bytes, **values) -> None:
        key = p.clip_id_to_str(clip)
        entry = self._cache.setdefault(key, {})
        if all(entry.get(k) == v for k, v in values.items()):
            return
        entry.update(values)
        self._save()

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
        self._update_starting(data)
        data.position_updated_at = dt_util.utcnow()
        try:
            tc = await c.timecode_parts()
        except DCPCommandError:
            tc = None
        data.timecode = p.format_timecode(tc)
        data.clip_id = await c.loaded_id()

        if data.clip_id != prev.clip_id or (data.clip_id and data.last_frame is None):
            await self._load_clip_details(data)
        elif data.clip_id is None:
            data.last_frame = data.uuid = data.title = data.cpl = None
            data.fps, data.fps_source = self.default_fps, "default"

        # Passive learning: a frames field >= fps proves a higher rate.
        if tc and data.clip_id and data.fps_source in ("default", "learned") and tc[3] >= data.fps:
            data.fps, data.fps_source = float(p.rate_at_least(tc[3])), "learned"
            self._remember(data.clip_id, fps=data.fps, fps_source="learned")

        # 60.A2 (current frame) returns 0 on some firmware -> derive from timecode.
        frame = await c.current_frame()
        if not frame and tc is not None:
            frame = p.timecode_to_frames(tc, data.fps)
        data.frame = frame

        if self.gpio_enabled:
            try:
                _, data.gpi = await c.gpi()
            except DCPCommandError as err:
                _LOGGER.debug("GPI sense failed: %s", err)
                data.gpi = None

        if self.klv and not self.klv.busy:  # don't queue behind a slow PlaySPL
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
        data.fps, data.fps_source = self.default_fps, "default"
        if data.clip_id is None:
            data.last_frame = None
            return
        cached = self._cache.get(p.clip_id_to_str(data.clip_id), {})
        if cached.get("fps"):
            data.fps, data.fps_source = float(cached["fps"]), cached.get("fps_source", "cache")
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
                if data.cpl.fps:
                    data.fps, data.fps_source = data.cpl.fps, "cpl"
                data.title = data.title or data.cpl.title
            except (DCPError, ValueError) as err:
                _LOGGER.debug("GetCPL failed: %s", err)
        if data.title:
            self._titles[data.clip_id] = data.title
            self._remember(data.clip_id, title=data.title)
            data.clips = {k: v for k, v in data.clips.items() if v != data.clip_id}
            data.clips[clip_label(data.clip_id, data.title)] = data.clip_id

    async def _refresh_library(self, data: DCPData) -> None:
        try:
            clips = await self.client.list_clips()
        except DCPCommandError as err:
            _LOGGER.debug("Listing clips failed: %s", err)
            clips = []
        if self.klv:
            await self._enrich_from_klv(clips)
            await self._refresh_spls(data)
        names: dict[str, bytes] = {}
        for clip in clips:
            title = await self._clip_title(clip)
            names[clip_label(clip, title)] = clip
        data.clips = names
        self._library_at = time.monotonic()

    async def _refresh_spls(self, data: DCPData) -> None:
        """Show playlists with their titles (GetSPLList + GetSPLInfo, cached)."""
        try:
            uuids = await self.klv.get_spl_list()
        except DCPError as err:
            _LOGGER.debug("GetSPLList failed: %s", err)
            return
        changed = False
        for uuid in uuids:
            if uuid in self._spl_cache:
                continue
            try:
                info = await self.klv.get_spl_info(uuid)
            except DCPError as err:
                _LOGGER.debug("GetSPLInfo %s failed: %s", uuid, err)
                continue
            self._spl_cache[uuid] = {"title": info["title"], "frames": info["duration_frames"]}
            changed = True
        for gone in set(self._spl_cache) - set(uuids):
            del self._spl_cache[gone]
            changed = True
        if changed:
            self._save()
        labels: dict[str, str] = {}
        titles = [self._spl_cache.get(u, {}).get("title") or u for u in uuids]
        for uuid, title in zip(uuids, titles):
            label = title if titles.count(title) == 1 else f"{title} ({uuid[:8]})"
            labels[label] = uuid
        data.spls = dict(sorted(labels.items(), key=lambda kv: kv[0].lower()))

    def remember_spl_xml(self, xml: str) -> str | None:
        """Keep the title of an SPL loaded from XML (it is not in the SPL list)."""
        import re

        uid = re.search(r"<Id>\s*(?:urn:uuid:)?([0-9a-fA-F-]{36})\s*</Id>", xml)
        title = re.search(r"<ShowTitleText[^>]*>([^<]*)</ShowTitleText>", xml)
        if uid and title:
            self._spl_cache.setdefault(uid.group(1).lower(), {})["title"] = title.group(1).strip()
            return uid.group(1).lower()
        return None

    def spl_by_name(self, name: str) -> str:
        """Accept an SPL label, title or UUID."""
        spls = self.data.spls if self.data else {}
        if name in spls:
            return spls[name]
        for label, uuid in spls.items():
            if self._spl_cache.get(uuid, {}).get("title") == name:
                return uuid
        return name.removeprefix("urn:uuid:")

    def spl_label(self, uuid: str | None) -> str | None:
        if not uuid or not self.data:
            return None
        for label, value in self.data.spls.items():
            if value == uuid:
                return label
        return self._spl_cache.get(uuid, {}).get("title") or uuid

    async def _enrich_from_klv(self, clips: list[bytes]) -> None:
        """Exact edit rate, title and kind for every CPL via GetCPLInfo."""
        missing = [
            c for c in clips
            if self._cache.get(p.clip_id_to_str(c), {}).get("fps_source") != "cpl"
        ]
        if not missing:
            return
        try:
            by_prefix = {u.replace("-", "")[:8]: u for u in await self.klv.get_cpl_list()}
        except DCPError as err:
            _LOGGER.debug("GetCPLList failed: %s", err)
            return
        for clip in missing:
            uuid = by_prefix.get(p.clip_id_to_str(clip).lower())
            if not uuid and self._ext_supported:
                try:  # clip ids are normally the first 8 hex digits of the CPL UUID
                    uuid = await self.client.uuid_for_id(clip)
                except DCPCommandError:
                    uuid = None
            if not uuid:
                continue
            try:
                info = await self.klv.get_cpl(uuid)
            except (DCPError, ValueError) as err:
                _LOGGER.debug("GetCPLInfo %s failed: %s", uuid, err)
                continue
            values = {"uuid": uuid, "kind": info.kind, "frames": info.duration}
            if info.title:
                values["title"] = info.title
                self._titles[clip] = info.title
            if info.fps:
                values |= {"fps": info.fps, "fps_source": "cpl"}
            self._remember(clip, **values)

    async def _clip_title(self, clip: bytes) -> str | None:
        """Title of a clip that is not loaded; cached per clip."""
        if clip in self._titles:
            return self._titles[clip]
        title = self._cache.get(p.clip_id_to_str(clip), {}).get("title")
        if title:
            self._titles[clip] = title
            return title
        if self._ext_supported:
            try:
                title = await self.client.title_for_id(clip)
                frames = await self.client.frames_for_id(clip)
            except DCPCommandError as err:
                _LOGGER.debug("No title for %r: %s", clip, err)
                frames = None
            if title:
                self._remember(clip, title=title, **({"frames": frames} if frames else {}))
        if not title and self.klv and self._ext_supported:
            try:
                uuid = await self.client.uuid_for_id(clip)
                if uuid:
                    title = (await self.klv.get_cpl(uuid)).title or None
            except (DCPError, ValueError) as err:
                _LOGGER.debug("No KLV title for %r: %s", clip, err)
        self._titles[clip] = title
        return title

    def mark_start_requested(self) -> None:
        """Play was sent; the server may need up to ~30 s of checks before it runs."""
        self._start_requested = time.monotonic()
        if self.data:
            self.data.starting = True
            self.async_update_listeners()

    def _update_starting(self, data: DCPData) -> None:
        if self._start_requested is None:
            data.starting = False
            return
        waited = time.monotonic() - self._start_requested
        spl_loaded = bool(data.spl and data.spl.spl_uuid)
        if data.status.playing or (data.status.ejected and not spl_loaded):
            self._start_requested = None
        elif waited > START_TIMEOUT:
            _LOGGER.warning("%s did not start playback within %d s (status %s)",
                            self.client.host, START_TIMEOUT, data.status.raw.hex(" "))
            self._start_requested = None
        data.starting = self._start_requested is not None

    async def async_spl_play(self) -> None:
        """Start the loaded SPL.

        On the DCP-2000 the first PlaySPL after loading only runs the pre-show
        checks and parks the show paused at its start; a second PlaySPL starts
        it. Send the second one automatically in that case.
        """
        klv = self.klv
        if klv is None:
            raise DCPCommandError("KLV API not enabled")
        before = await klv.status_spl()
        self.mark_start_requested()
        await klv.simple("PlaySPL")
        if before.state != "paused":
            # The state flips to "paused" only shortly after the answer.
            after = before
            for _ in range(20):
                after = await klv.status_spl()
                if after.state in ("playing", "paused"):
                    break
                await asyncio.sleep(0.5)
            if after.state == "paused":
                _LOGGER.debug("SPL parked after checks, sending PlaySPL again")
                await klv.simple("PlaySPL")
        await self.async_request_refresh()

    async def async_refresh_library(self) -> None:
        self._library_at = 0.0
        self._titles.clear()
        await self.async_request_refresh()

    async def async_measure_fps(
        self, force: bool = True, delays: tuple[float, ...] = (0.0,)
    ) -> int | None:
        """Measure the edit rate of the loaded clip (not while playing).

        With force=False a clip whose rate is already measured/known is skipped.
        """
        c = self.client
        clip = await c.loaded_id()
        if clip is None:
            raise DCPCommandError("no clip loaded")
        cached = self._cache.get(p.clip_id_to_str(clip), {})
        if not force and cached.get("fps_source") in ("measured", "cpl"):
            return int(cached["fps"])
        if (await c.status()).state == "playing":
            raise DCPCommandError("frame rate can only be measured while paused/cued")
        last = await c.last_frame()
        rate = None
        for delay in delays:
            # Right after cueing the server ignores locates for a moment.
            await asyncio.sleep(delay)
            rate = await c.measure_fps(last)
            if rate:
                break
        _LOGGER.debug("Measured %s fps for %s", rate, p.clip_id_to_str(clip))
        if rate:
            self._remember(clip, fps=float(rate), fps_source="measured")
            if self.data and self.data.clip_id == clip:
                self.data.fps, self.data.fps_source = float(rate), "measured"
                self.async_update_listeners()
        return rate

    def cached_fps(self, clip: bytes) -> float | None:
        fps = self._cache.get(p.clip_id_to_str(clip), {}).get("fps")
        return float(fps) if fps else None

    def clip_by_name(self, name: str) -> bytes:
        """Accept a library label, a bare clip id or a CPL title."""
        clips = self.data.clips if self.data else {}
        if name in clips:
            return clips[name]
        for label, raw in clips.items():
            if p.clip_id_to_str(raw) == name or self._titles.get(raw) == name:
                return raw
        return p.clip_id_from_str(name)

    def label_for(self, clip: bytes | None) -> str | None:
        if clip is None or not self.data:
            return None
        for label, raw in self.data.clips.items():
            if raw == clip:
                return label
        return p.clip_id_to_str(clip)
