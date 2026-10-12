"""Async TCP clients for the Doremi DCP-2000 (no Home Assistant imports)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import itertools
import uuid as uuidlib
import logging

from . import protocol as p

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 3.0


class DCPError(Exception):
    """Base error."""


class DCPConnectionError(DCPError):
    """Connection could not be established or was lost."""


class DCPCommandError(DCPError):
    """Device answered with NAK or an unexpected reply."""


class _Connection:
    """A persistent TCP connection that serialises request/response pairs."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def _connect(self) -> None:
        if self.connected:
            return
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout
            )
        except (OSError, asyncio.TimeoutError) as err:
            self._reader = self._writer = None
            raise DCPConnectionError(f"{self.host}:{self.port}: {err}") from err
        _LOGGER.debug("Connected to %s:%s", self.host, self.port)

    async def close(self) -> None:
        writer, self._reader, self._writer = self._writer, None, None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _exchange(self, data: bytes, read, timeout: float | None = None) -> object:
        """Send and read one answer.

        A dropped/stale connection is retried once (the request never reached
        the server). A timeout is *not* retried: the server may still be
        executing the command (e.g. PlaySPL runs its pre-show checks before it
        answers), and sending it again would duplicate it.
        """
        async with self._lock:
            for attempt in (1, 2):
                await self._connect()
                assert self._writer and self._reader
                try:
                    self._writer.write(data)
                    await self._writer.drain()
                    if read is None:
                        return None
                    return await asyncio.wait_for(read(self._reader), timeout or self.timeout)
                except asyncio.TimeoutError as err:
                    await self.close()
                    raise DCPConnectionError(
                        f"{self.host}:{self.port}: no answer within {timeout or self.timeout:.0f} s"
                    ) from err
                except (OSError, asyncio.IncompleteReadError) as err:
                    await self.close()
                    if attempt == 2:
                        reason = str(err) or type(err).__name__
                        raise DCPConnectionError(f"{self.host}:{self.port}: {reason}") from err
                    _LOGGER.debug("Reconnecting after error: %r", err)
        raise DCPConnectionError("unreachable")


@dataclass(slots=True)
class DeviceInfo:
    product: str
    serial: str
    protocol_version: str


class DCP2000Client:
    """Sony 9-pin over TCP (port 5000)."""

    def __init__(self, host: str, port: int = 5000, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._conn = _Connection(host, port, timeout)

    @property
    def host(self) -> str:
        return self._conn.host

    @property
    def connected(self) -> bool:
        return self._conn.connected

    async def close(self) -> None:
        await self._conn.close()

    # -- low level ----------------------------------------------------------

    @staticmethod
    async def _read_envelope(reader: asyncio.StreamReader) -> tuple[bytes, int, bytes]:
        while True:
            header = await reader.readexactly(p.HEADER.size)
            msg_type, length, _ = p.HEADER.unpack(header)
            if length < 0 or length > 1_000_000:
                raise asyncio.IncompleteReadError(header, None)
            payload = await reader.readexactly(length) if length else b""
            if msg_type in (p.TYPE_S9P, p.TYPE_VERSION):
                return msg_type, length, payload
            _LOGGER.debug("Ignoring message type %r", msg_type)

    async def send_frame(self, frame: bytes) -> p.Reply:
        async def read(reader):
            msg_type, _, payload = await self._read_envelope(reader)
            if msg_type != p.TYPE_S9P:
                raise DCPCommandError(f"unexpected reply type {msg_type!r}")
            return p.parse_reply(payload)

        _LOGGER.debug("-> %s", frame.hex(" "))
        reply = await self._conn._exchange(p.envelope(p.TYPE_S9P, frame), read)
        _LOGGER.debug("<- %s.%02X %s", reply.code, reply.cmd2, reply.data.hex(" "))
        if reply.is_nak:
            raise DCPCommandError(f"NAK ({p.nak_reason(reply)}) for {frame.hex(' ')}")
        return reply

    async def command(self, cmd: tuple[int, int], data: bytes = b"") -> p.Reply:
        return await self.send_frame(p.s9p_frame(cmd[0], cmd[1], data))

    async def ext_command(self, cmd: tuple[int, int], data: bytes = b"") -> p.Reply:
        return await self.send_frame(p.ext_frame(cmd[0], cmd[1], data))

    async def keepalive(self) -> None:
        await self._conn._exchange(p.envelope(p.TYPE_ALIVE), None)

    async def tcp_protocol_version(self) -> str:
        async def read(reader):
            header = await reader.readexactly(p.HEADER.size)
            _, v1, v2 = p.HEADER.unpack(header)
            return f"{v1}.{v2}"

        return await self._conn._exchange(p.envelope(p.TYPE_VERSION), read)

    # -- transport ----------------------------------------------------------

    async def play(self) -> None:
        await self.command(p.CMD_PLAY)

    async def pause(self) -> None:
        await self.command(p.CMD_STILL)

    async def stop(self) -> None:
        await self.command(p.CMD_STOP)

    async def step_back(self) -> None:
        await self.command(p.CMD_STEP_BACK)

    async def set_frame(self, frame: int) -> None:
        await self.command(p.CMD_SET_FRAME, max(0, int(frame)).to_bytes(4, "big"))

    async def cue_clip(self, clip_id: bytes, start_frame: int | None = None,
                       timeout: float = 60.0) -> None:
        """Load a clip using auto mode (In Preset by ID + full-length Out Preset).

        Waits until the server reports the clip as loaded; raises DCPCommandError
        when it ejects instead (e.g. encrypted CPL without valid KDM).
        """
        await self.command(p.CMD_AUTO_ON)
        await self.command(p.CMD_IN_PRESET, clip_id)
        await self.command(p.CMD_OUT_PRESET)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            await asyncio.sleep(0.3)
            status = await self.status()
            if not status.preroll:
                loaded = await self.loaded_id()
                if loaded == clip_id:
                    break
                if status.ejected or (loaded is None and not status.cue_complete):
                    raise DCPCommandError(
                        f"server could not load clip {p.clip_id_to_str(clip_id)!r} "
                        "(missing/expired KDM or broken content?)"
                    )
            if loop.time() > deadline:
                raise DCPCommandError(f"timeout loading clip {p.clip_id_to_str(clip_id)!r}")
        if start_frame:
            await self.locate(start_frame)

    async def locate(self, frame: int, timeout: float = 10.0) -> bool:
        """Locate and confirm via timecode.

        Right after a clip has been (re)cued the server ignores locates and may
        still reset to the in point, so repeat until the timecode shows the
        target frame (for any standard rate, ±2 frames) twice in a row.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            await self.set_frame(frame)
            hits = 0
            for _ in range(5):
                await asyncio.sleep(0.25)
                tc = await self.timecode_parts()
                if tc is not None and any(
                    abs(p.timecode_to_frames(tc, r) - frame) <= 2 for r in p.STANDARD_RATES
                ):
                    hits += 1
                    if hits >= 2:
                        return True
                else:
                    hits = 0
        _LOGGER.debug("Locate to %s not confirmed", frame)
        return False

    async def measure_fps(self, last_frame: int | None = None) -> int | None:
        """Find the edit rate by locating to a probe frame and reading the timecode.

        Only call while not playing. The previous position is restored.
        """
        probe = p.probe_frame(last_frame)
        if probe is None:
            return None
        before = await self.timecode_parts()
        await self.set_frame(probe)
        # The server needs a moment to locate; wait until the timecode settles.
        rate = None
        previous = None
        for _ in range(15):
            await asyncio.sleep(0.2)
            after = await self.timecode_parts()
            if after and after == previous and after != before:
                rate = p.match_rate(probe, after)
                break
            previous = after
        else:
            if previous and previous != before:
                rate = p.match_rate(probe, previous)
        if before is not None:
            await self.set_frame(p.timecode_to_frames(before, rate or 24))
        return rate

    async def queue_next_clip(self, clip_id: bytes) -> None:
        """Preview In/Out preset: play this clip after the current one."""
        await self.command(p.CMD_PREVIEW_IN, clip_id)
        await self.command(p.CMD_PREVIEW_OUT)

    # -- sense --------------------------------------------------------------

    async def status(self) -> p.TransportStatus:
        reply = await self.command(p.CMD_STATUS_SENSE, b"\x0a")
        return p.TransportStatus(reply.data)

    async def current_frame(self) -> int | None:
        return p.decode_u32((await self.command(p.CMD_CURRENT_FRAME)).data)

    async def last_frame(self) -> int | None:
        return p.decode_u32((await self.command(p.CMD_LAST_FRAME)).data)

    async def timecode_parts(self) -> p.Timecode | None:
        reply = await self.command(p.CMD_CURRENT_TIME_SENSE, b"\x01")
        return p.decode_timecode_parts(reply.data)

    async def timecode(self) -> str | None:
        return p.format_timecode(await self.timecode_parts())

    async def loaded_id(self) -> bytes | None:
        return p.decode_clip_id((await self.command(p.CMD_LOADED_ID)).data)

    async def current_uuid(self) -> str | None:
        reply = await self.ext_command(p.EXT_UUID_ID, b"\x20")
        return p.decode_uuid(reply.data[1:]) if len(reply.data) > 1 else None

    async def uuid_for_id(self, clip_id: bytes) -> str | None:
        reply = await self.ext_command(p.EXT_UUID_ID, b"\x20" + clip_id)
        return p.decode_uuid(reply.data[1:]) if len(reply.data) > 1 else None

    async def current_title(self) -> str | None:
        reply = await self.ext_command(p.EXT_TITLE, b"\x10")
        return p.decode_text(reply.data[1:]) or None if len(reply.data) > 1 else None

    async def title_for_id(self, clip_id: bytes) -> str | None:
        """Content title of any clip (E6.09.00.BE.10 <id>, as used by Doremi's tools)."""
        reply = await self.ext_command(p.EXT_TITLE, b"\x10" + clip_id)
        return p.decode_text(reply.data[1:]) or None if len(reply.data) > 1 else None

    async def frames_for_id(self, clip_id: bytes) -> int | None:
        """Length in frames of any clip (69.BB.02 <id>, undocumented)."""
        reply = await self.command(p.CMD_CLIP_INFO, b"\x02" + clip_id)
        if len(reply.data) >= 2 and reply.data[0] == 0x02:
            return int.from_bytes(reply.data[1:], "big")
        return None

    async def product_name(self) -> str:
        return p.decode_text((await self.command(p.CMD_PRODUCT_NAME)).data)

    async def serial_number(self) -> str:
        return p.decode_text((await self.command(p.CMD_SERIAL)).data)

    async def protocol_version(self) -> str:
        data = (await self.command(p.CMD_PROTOCOL_VERSION, b"\x00")).data
        if len(data) >= 2:
            return f"{data[0]}.{data[1]:02d}"
        return data.hex()

    async def device_info(self) -> DeviceInfo:
        product = await self.product_name()
        try:
            serial = await self.serial_number()
        except DCPCommandError:
            serial = ""
        try:
            version = await self.protocol_version()
        except DCPCommandError:
            version = ""
        return DeviceInfo(product or "DCP-2000", serial, version)

    async def list_clips(self, limit: int = 1000) -> list[bytes]:
        clips: list[bytes] = []
        cmd = p.CMD_LIST_FIRST
        for _ in range(limit):
            try:
                reply = await self.command(cmd)
            except DCPCommandError:
                break  # NAK = end of list on some firmware
            cmd = p.CMD_LIST_NEXT
            if reply.is_ack:
                break  # end of list
            clip = p.decode_clip_id(reply.data)
            if clip is None or clip in clips:
                break
            clips.append(clip)
        return clips

    # -- GPIO / projector ---------------------------------------------------

    async def gpi(self) -> tuple[int, int]:
        """Return (changed bits, state bits)."""
        data = (await self.command(p.CMD_GPI_SENSE, b"\x00")).data
        if len(data) >= 3:  # 73.BF.<00?>... tolerate echo of the sub byte
            return data[-2], data[-1]
        if len(data) == 2:
            return data[0], data[1]
        raise DCPCommandError(f"unexpected GPI reply {data.hex()}")

    async def set_gpo(self, value: int) -> None:
        await self.command(p.CMD_GPO_SET, bytes([0x00, value & 0xFF]))

    async def projector(self, cmd: int, arg: int) -> None:
        await self.command(p.CMD_PROJECTOR, bytes([0x80, cmd & 0xFF, arg & 0xFF]))


class KLVClient:
    """DCP-2000 Messages API (KLV over TCP 11730, Doremi doc v2.5)."""

    def __init__(self, host: str, port: int, reg: int = 0x05,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        self._conn = _Connection(host, port, timeout)
        self.reg = reg
        self._ids = itertools.count(1)

    async def close(self) -> None:
        await self._conn.close()

    @property
    def busy(self) -> bool:
        """A (possibly slow) request is in flight on the KLV connection."""
        return self._conn._lock.locked()

    @staticmethod
    async def _read_klv(reader: asyncio.StreamReader) -> p.KlvMessage:
        key = await reader.readexactly(16)
        if not p.is_klv_key(key):
            raise DCPCommandError(f"not a Doremi KLV key: {key.hex()}")
        first = await reader.readexactly(1)
        if first[0] & 0x80:
            rest = await reader.readexactly(first[0] & 0x7F)
            length = int.from_bytes(rest, "big")
        else:
            length = first[0]
        value = await reader.readexactly(length)
        return p.KlvMessage(key[13], key[14], key[15], value)

    # Commands the server may answer only after its checks/ingest have finished.
    SLOW = {"PlaySPL": 90.0, "LoadSPL": 60.0, "LoadSPLbyUUID": 60.0, "StoreSPL": 60.0,
            "SkipForward": 30.0, "SkipBackward": 30.0}

    async def request(self, name: str, payload: bytes = b"") -> p.KlvMessage:
        req_id = (next(self._ids) & 0x7FFFFFFF) or 1  # must be non-zero
        b14, b15, b16 = p.KLV_MESSAGES[name]

        async def read(reader):
            while True:
                msg = await self._read_klv(reader)
                if (msg.b14, msg.b15) == (b14, b15 + 1) and msg.request_id == req_id:
                    return msg
                _LOGGER.debug("Skipping KLV %02X.%02X", msg.b14, msg.b15)

        msg = await self._conn._exchange(
            p.klv_request(name, req_id, payload, self.reg), read, self.SLOW.get(name)
        )
        if msg.response:
            raise DCPCommandError(
                f"{name}: {p.RESPONSE_CODES.get(msg.response, msg.response)}"
            )
        return msg

    async def api_version(self) -> str:
        v = (await self.request("GetAPIProtocolVersion")).value[4:]
        return ".".join(str(x) for x in v[:3])

    async def product_info(self) -> p.ProductInfo:
        return p.parse_product_info((await self.request("GetProductInfo")).value)

    async def get_cpl_list(self) -> list[str]:
        return p.parse_uuid_batch((await self.request("GetCPLList")).value, 4)[0]

    async def get_spl_list(self) -> list[str]:
        return p.parse_uuid_batch((await self.request("GetSPLList")).value, 4)[0]

    async def get_kdm_list(self) -> list[str]:
        return p.parse_uuid_batch((await self.request("GetKDMList")).value, 4)[0]

    async def get_cpl(self, cpl_uuid: str) -> p.CplInfo:
        msg = await self.request("GetCPLInfo", uuidlib.UUID(cpl_uuid).bytes)
        return p.parse_get_cpl(msg.value)

    async def get_spl_info(self, spl_uuid: str) -> dict:
        v = (await self.request("GetSPLInfo", uuidlib.UUID(spl_uuid).bytes)).value[4:]
        return {
            "spl_uuid": str(uuidlib.UUID(bytes=v[0:16])),
            "title": v[16:144].split(b"\x00", 1)[0].decode("utf-8", errors="replace"),
            "duration_frames": int.from_bytes(v[144:148], "big"),
        }

    async def status_spl(self) -> p.SplStatus:
        return p.parse_status_spl((await self.request("StatusSPL")).value)

    async def retrieve_spl(self, spl_uuid: str) -> str:
        value = (await self.request("RetrSPL", uuidlib.UUID(spl_uuid).bytes)).value
        return value[4:-1].rstrip(b"\x00").decode("utf-8", errors="replace")

    async def simple(self, name: str, payload: bytes = b"") -> None:
        await self.request(name, payload)

    async def load_spl(self, xml: str) -> None:
        await self.simple("LoadSPL", xml.encode("utf-8"))

    async def load_spl_uuid(self, spl_uuid: str) -> None:
        await self.simple("LoadSPLbyUUID", uuidlib.UUID(spl_uuid).bytes)

    async def store_spl(self, xml: str) -> None:
        await self.simple("StoreSPL", xml.encode("utf-8"))

    async def loop_mode(self, mode: int | bool) -> None:
        await self.simple("LoopModeSPL", bytes([int(mode)]))

    async def jump(self, seconds: int) -> None:
        name = "JumpForward" if seconds >= 0 else "JumpBackward"
        await self.simple(name, abs(int(seconds)).to_bytes(4, "big"))


async def probe_klv(host: str, port: int, timeout: float = 3.0) -> int | None:
    """Return the working key registry byte, or None if the KLV API is unavailable."""
    for reg in p.KLV_KEY_REG_DESIGNATORS:
        client = KLVClient(host, port, reg, timeout)
        try:
            version = await client.api_version()
            _LOGGER.debug("KLV API %s on %s:%s (reg 0x%02X)", version, host, port, reg)
            return reg
        except DCPError as err:
            _LOGGER.debug("KLV probe reg=0x%02X failed: %s", reg, err)
        finally:
            await client.close()
    return None
