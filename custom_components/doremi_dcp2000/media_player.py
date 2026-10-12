"""Media player for the Doremi DCP-2000."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

import logging

from . import protocol as p
from .client import DCPError
from .coordinator import DCPConfigEntry, DCPCoordinator
from .entity import DCPEntity

_LOGGER = logging.getLogger(__name__)

STATE_MAP = {
    "playing": MediaPlayerState.PLAYING,
    "paused": MediaPlayerState.PAUSED,
    "cued": MediaPlayerState.IDLE,
    "cueing": MediaPlayerState.BUFFERING,
    "stopped": MediaPlayerState.IDLE,
    "ejected": MediaPlayerState.IDLE,
    "starting": MediaPlayerState.BUFFERING,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: DCPConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([DCPMediaPlayer(entry.runtime_data)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "cue_clip",
        {
            vol.Required("clip"): cv.string,
            vol.Optional("start_frame", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
            vol.Optional("autoplay", default=False): cv.boolean,
        },
        "async_cue_clip",
    )
    platform.async_register_entity_service(
        "queue_clip", {vol.Required("clip"): cv.string}, "async_queue_clip"
    )
    platform.async_register_entity_service(
        "set_frame", {vol.Required("frame"): vol.All(vol.Coerce(int), vol.Range(min=0))},
        "async_set_frame",
    )
    platform.async_register_entity_service("step_back", None, "async_step_back")
    platform.async_register_entity_service(
        "measure_frame_rate", None, "async_measure_frame_rate",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        "projector_channel",
        {vol.Required("channel"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255))},
        "async_projector_channel",
    )
    platform.async_register_entity_service(
        "set_gpo", {vol.Required("value"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255))},
        "async_set_gpo",
    )
    platform.async_register_entity_service(
        "send_command",
        {vol.Required("command"): cv.string},
        "async_send_command",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        "list_content", None, "async_list_content", supports_response=SupportsResponse.ONLY
    )
    # --- SPL (KLV API) ---
    platform.async_register_entity_service(
        "spl_load",
        {
            vol.Exclusive("xml", "spl"): cv.string,
            vol.Exclusive("spl_uuid", "spl"): cv.string,
            vol.Optional("store", default=False): cv.boolean,
        },
        "async_spl_load",
    )
    platform.async_register_entity_service(
        "spl_command",
        {vol.Required("command"): vol.In(
            ["play", "pause", "eject", "skip_forward", "skip_backward", "loop_on", "loop_off"]
        )},
        "async_spl_command",
    )
    platform.async_register_entity_service(
        "spl_jump", {vol.Required("seconds"): vol.Coerce(int)}, "async_spl_jump"
    )
    # --- dashboard card ---
    platform.async_register_entity_service(
        "load_content",
        {
            vol.Required("kind"): vol.In(["cpl", "spl"]),
            vol.Required("id"): cv.string,
            vol.Optional("start", default="pause"): vol.In(["play", "pause"]),
        },
        "async_load_content",
    )
    platform.async_register_entity_service(
        "seek",
        {
            vol.Exclusive("timecode", "pos"): cv.string,
            vol.Exclusive("position", "pos"): vol.All(vol.Coerce(float), vol.Range(min=0)),
        },
        "async_seek",
    )
    platform.async_register_entity_service(
        "skip_to_event",
        {
            vol.Exclusive("event_id", "event"): cv.string,
            vol.Exclusive("index", "event"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        },
        "async_skip_to_event",
    )
    platform.async_register_entity_service("eject", None, "async_eject")
    platform.async_register_entity_service(
        "get_library", None, "async_get_library", supports_response=SupportsResponse.ONLY
    )
    platform.async_register_entity_service(
        "spl_retrieve", {vol.Required("spl_uuid"): cv.string}, "async_spl_retrieve",
        supports_response=SupportsResponse.ONLY,
    )


class DCPMediaPlayer(DCPEntity, MediaPlayerEntity):
    _attr_name = None
    _attr_media_content_type = MediaType.MOVIE

    def __init__(self, coordinator: DCPCoordinator) -> None:
        super().__init__(coordinator, "player")
        features = (
            MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.SEEK
            | MediaPlayerEntityFeature.SELECT_SOURCE
            | MediaPlayerEntityFeature.PLAY_MEDIA
        )
        if coordinator.klv:
            features |= (
                MediaPlayerEntityFeature.NEXT_TRACK | MediaPlayerEntityFeature.PREVIOUS_TRACK
            )
        self._attr_supported_features = features

    # -- helpers ------------------------------------------------------------

    async def _run(self, coro: Awaitable[Any], refresh: bool = True) -> Any:
        try:
            result = await coro
        except DCPError as err:
            raise HomeAssistantError(f"DCP-2000: {err}") from err
        if refresh:
            await self.coordinator.async_request_refresh()
        return result

    def _require_klv(self):
        if self.coordinator.klv is None:
            raise ServiceValidationError(
                "The SPL/KLV API is not enabled for this server (integration options)."
            )
        return self.coordinator.klv

    # -- state --------------------------------------------------------------

    @property
    def state(self) -> MediaPlayerState:
        if not self.coordinator.last_update_success:
            return MediaPlayerState.OFF
        return STATE_MAP.get(self.coordinator.data.state, MediaPlayerState.IDLE)

    @property
    def available(self) -> bool:
        return True  # show OFF instead of unavailable when the server is down

    @property
    def media_content_id(self) -> str | None:
        return self.coordinator.data.uuid or self.coordinator.data.clip_name

    def _element(self) -> dict | None:
        d = self.coordinator.data
        if self.coordinator.spl_loaded and d.spl_index is not None and d.spl_items:
            return d.spl_items[d.spl_index]
        return None

    @property
    def media_duration(self) -> float | None:
        item = self._element()
        if item:
            return item["duration"] or self.coordinator.data.spl.element_duration or None
        return self.coordinator.data.duration

    @property
    def media_position(self) -> float | None:
        d = self.coordinator.data
        if self._element() is not None and d.frame is not None:
            return d.frame / self.coordinator.current_fps()
        return d.position

    @property
    def media_title(self) -> str | None:
        item = self._element()
        d = self.coordinator.data
        return (item and item["title"]) or d.title or d.clip_name

    @property
    def media_position_updated_at(self):
        return self.coordinator.data.position_updated_at

    @property
    def source(self) -> str | None:
        return self.coordinator.label_for(self.coordinator.data.clip_id)

    @property
    def source_list(self) -> list[str]:
        return sorted(self.coordinator.data.clips)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        attrs: dict[str, Any] = {
            "clip_id": d.clip_name,
            "cpl_uuid": d.uuid,
            "frame": d.frame,
            "last_frame": d.last_frame,
            "timecode": d.timecode,
            "frame_rate": d.fps,
            "frame_rate_source": d.fps_source,
            "transport": d.state,
            "auto_mode": d.status.auto_mode,
            "status_bytes": d.status.raw.hex(" "),
        }
        if d.cpl:
            attrs |= {
                "content_kind": d.cpl.kind,
                "resolution": f"{d.cpl.width}x{d.cpl.height}",
                "audio_channels": d.cpl.channels,
                "encrypted": d.cpl.encrypted,
            }
        if d.spl:
            attrs |= {
                "spl_uuid": d.spl.spl_uuid,
                "spl_title": self.coordinator.spl_label(d.spl.spl_uuid),
                "spl_state": d.spl.state,
                "spl_progress": d.spl.spl_percent,
                "spl_position": d.spl.position,
                "spl_duration": d.spl.duration,
                "spl_index": d.spl_index,
                "spl_items": [
                    {
                        "event_id": it["event_id"],
                        "title": it["title"],
                        "kind": it["kind"],
                        "start": it["start"],
                        "duration": it["duration"],
                        "fps": it["fps"],
                        "cues": it["cues"],
                    }
                    for it in d.spl_items
                ] if d.spl.spl_uuid else [],
            }
        attrs["edit_rate"] = self.coordinator.current_fps()
        attrs["mode"] = (
            "spl" if self.coordinator.spl_loaded else "clip" if d.clip_id else "empty"
        )
        return attrs

    # -- transport ----------------------------------------------------------

    async def async_media_play(self) -> None:
        d = self.coordinator.data
        if self.coordinator.klv and d.spl and d.spl.spl_uuid and d.spl.state != "playing":
            await self._run(self.coordinator.async_spl_play())
            return
        await self._run(self.coordinator.client.play(), refresh=False)
        self.coordinator.mark_start_requested()
        await self.coordinator.async_request_refresh()

    async def async_media_pause(self) -> None:
        if self.coordinator.spl_loaded:
            await self._run(self.coordinator.klv.simple("PauseSPL"))
            return
        await self._run(self.coordinator.client.pause())

    async def async_media_stop(self) -> None:
        await self._run(self.coordinator.client.stop())

    async def async_media_seek(self, position: float) -> None:
        await self._run(self.coordinator.async_seek(position=position), refresh=False)

    async def async_select_source(self, source: str) -> None:
        await self.async_cue_clip(source)

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        await self.async_cue_clip(media_id, autoplay=True)

    async def async_media_next_track(self) -> None:
        await self._run(self._require_klv().simple("SkipForward"))

    async def async_media_previous_track(self) -> None:
        if not self.coordinator.spl_loaded:  # single clip: back to its start
            await self._run(self.coordinator.client.locate(0))
            return
        await self._run(self._require_klv().simple("SkipBackward"))

    # -- services -----------------------------------------------------------

    async def async_cue_clip(self, clip: str, start_frame: int = 0, autoplay: bool = False) -> None:
        client = self.coordinator.client
        raw = self.coordinator.clip_by_name(clip)

        async def _do():
            await client.cue_clip(raw)
            try:  # cheap and exact: learn the edit rate while we own the transport
                await self.coordinator.async_measure_fps(force=False, delays=(1.0, 2.0))
            except DCPError as err:
                _LOGGER.debug("Frame rate measurement skipped: %s", err)
            if start_frame:
                await client.locate(start_frame)
            if autoplay:
                await client.play()
                self.coordinator.mark_start_requested()

        await self._run(_do())

    async def async_queue_clip(self, clip: str) -> None:
        await self._run(self.coordinator.client.queue_next_clip(self.coordinator.clip_by_name(clip)))

    async def async_set_frame(self, frame: int) -> None:
        await self._run(self.coordinator.client.set_frame(frame))

    async def async_measure_frame_rate(self) -> ServiceResponse:
        rate = await self._run(self.coordinator.async_measure_fps())
        return {"clip_id": self.coordinator.data.clip_name, "frame_rate": rate}

    async def async_step_back(self) -> None:
        await self._run(self.coordinator.client.step_back())

    async def async_projector_channel(self, channel: int) -> None:
        await self._run(self.coordinator.client.projector(p.PROJ_CHANNEL, channel), refresh=False)

    async def async_set_gpo(self, value: int) -> None:
        await self._run(self.coordinator.client.set_gpo(value), refresh=False)
        self.coordinator.gpo_state = value
        self.coordinator.async_update_listeners()

    async def async_send_command(self, command: str) -> ServiceResponse:
        try:
            frame = p.raw_frame(command)
        except ValueError as err:
            raise ServiceValidationError(f"Invalid hex command: {command}") from err
        reply = await self._run(self.coordinator.client.send_frame(frame))
        return {
            "sent": frame.hex(" "),
            "reply": reply.code,
            "data": reply.data.hex(" "),
            "text": p.decode_text(reply.data),
        }

    async def async_list_content(self) -> ServiceResponse:
        coordinator = self.coordinator
        clips = [
            {"clip_id": p.clip_id_to_str(raw), "label": label}
            for label, raw in sorted(coordinator.data.clips.items())
        ]
        result: dict[str, Any] = {"clips": clips}
        if coordinator.klv:
            klv = coordinator.klv
            result["cpls"] = await self._run(klv.get_cpl_list(), refresh=False)
            result["spls"] = await self._run(klv.get_spl_list(), refresh=False)
            result["kdms"] = await self._run(klv.get_kdm_list(), refresh=False)
        return result

    async def async_spl_load(
        self, xml: str | None = None, spl_uuid: str | None = None, store: bool = False
    ) -> None:
        klv = self._require_klv()
        if spl_uuid:  # UUID, title or select-list label
            await self._run(klv.load_spl_uuid(self.coordinator.spl_by_name(spl_uuid)))
        elif xml:
            self.coordinator.remember_spl_xml(xml)
            await self._run(klv.store_spl(xml) if store else klv.load_spl(xml))
        else:
            raise ServiceValidationError("Provide either xml or spl_uuid.")

    async def async_spl_command(self, command: str) -> None:
        klv = self._require_klv()
        actions = {
            "play": lambda: self._spl_play(klv),
            "pause": lambda: klv.simple("PauseSPL"),
            "eject": lambda: klv.simple("EjectSPL"),
            "skip_forward": lambda: klv.simple("SkipForward"),
            "skip_backward": lambda: klv.simple("SkipBackward"),
            "loop_on": lambda: klv.loop_mode(1),
            "loop_off": lambda: klv.loop_mode(0),
        }
        await self._run(actions[command]())

    async def _spl_play(self, klv):
        return await self.coordinator.async_spl_play()

    async def async_spl_jump(self, seconds: int) -> None:
        await self._run(self._require_klv().jump(seconds))

    async def async_load_content(self, kind: str, id: str, start: str = "pause") -> None:
        if kind == "spl":
            self._require_klv()
        await self._run(self.coordinator.async_load_content(kind, id, start), refresh=False)

    async def async_seek(self, timecode: str | None = None, position: float | None = None) -> None:
        if timecode is None and position is None:
            raise ServiceValidationError("Provide timecode or position.")
        await self._run(self.coordinator.async_seek(timecode, position), refresh=False)

    async def async_skip_to_event(self, event_id: str | None = None, index: int | None = None) -> None:
        self._require_klv()
        await self._run(self.coordinator.async_skip_to_event(event_id, index), refresh=False)

    async def async_eject(self) -> None:
        await self._run(self.coordinator.async_eject(), refresh=False)

    async def async_get_library(self) -> ServiceResponse:
        return self.coordinator.library()

    async def async_spl_retrieve(self, spl_uuid: str) -> ServiceResponse:
        xml = await self._run(self._require_klv().retrieve_spl(spl_uuid), refresh=False)
        return {"spl_uuid": spl_uuid, "xml": xml}
