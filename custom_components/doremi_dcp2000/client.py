"""Async TCP clients for the Doremi DCP-2000 (no Home Assistant imports)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import itertools
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

    async def _exchange(self, data: bytes, read) -> object:
        async with self._lock:
            for attempt in (1, 2):
                await self._connect()
                assert self._writer and self._reader
                try:
                    self._writer.write(data)
                    await self._writer.drain()
                    if read is None:
                        return None
                    return await asyncio.wait_for(read(self._reader), self.timeout)
                except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as err:
                    await self.close()
                    if attempt == 2:
                        raise DCPConnectionError(
                            f"{self.host}:{self.port}: {err or type(err).__name__}"
                        ) from err
                    _LOGGER.debug("Retrying after error: %r", err)
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

    async def cue_clip(self, clip_id: bytes, start_frame: int | None = None) -> None:
        """Load a clip using auto mode (In Preset by ID + full-length Out Preset)."""
        await self.command(p.CMD_AUTO_ON)
        await self.command(p.CMD_IN_PRESET, clip_id)
        await self.command(p.CMD_OUT_PRESET)
        if start_frame:
            await self.set_frame(start_frame)

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

    async def timecode(self) -> str | None:
        return p.decode_timecode((await self.command(p.CMD_CURRENT_TIME_SENSE, b"\x01")).data)

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
    """CPL & SPL API (KLV encoded)."""

    def __init__(self, host: str, port: int, reg: int = 0x04,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        self._conn = _Connection(host, port, timeout)
        self.reg = reg
        self._ids = itertools.count(1)

    async def close(self) -> None:
        await self._conn.close()

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
        return p.KlvMessage(key[13], key[14], value)

    async def request(self, name: str, payload: bytes = b"") -> p.KlvMessage:
        req_id = next(self._ids) & 0xFFFFFFFF
        set_kind, msg_kind = p.KLV_MESSAGES[name]

        async def read(reader):
            while True:
                msg = await self._read_klv(reader)
                if msg.set_kind == set_kind and msg.msg_kind in (msg_kind + 1, msg_kind + 2):
                    return msg
                _LOGGER.debug("Skipping KLV %02X.%02X", msg.set_kind, msg.msg_kind)

        return await self._conn._exchange(p.klv_request(name, req_id, payload, self.reg), read)

    @staticmethod
    def _check(msg: p.KlvMessage) -> int | None:
        """Return the 'General Response info' byte (meaning undocumented)."""
        code = msg.value[-1] if len(msg.value) > 4 else None
        if code not in (None, 0):
            _LOGGER.warning("KLV %02X.%02X returned response code %s",
                            msg.set_kind, msg.msg_kind, code)
        return code

    async def get_cpl_list(self) -> list[str]:
        msg = await self.request("GetCPLList")
        return p.parse_uuid_batch(msg.value, 4)[0]

    async def get_spl_list(self) -> list[str]:
        msg = await self.request("GetSPLList")
        return p.parse_uuid_batch(msg.value, 4)[0]

    async def get_kdm_list(self) -> list[str]:
        msg = await self.request("GetKDMList")
        return p.parse_uuid_batch(msg.value, 4)[0]

    async def get_cpl(self, cpl_uuid: str) -> p.CplInfo:
        import uuid as uuidlib

        msg = await self.request("GetCPL", uuidlib.UUID(cpl_uuid).bytes)
        return p.parse_get_cpl(msg.value)

    async def get_kdm(self, kdm_uuid: str) -> dict:
        import uuid as uuidlib

        msg = await self.request("GetKDM", uuidlib.UUID(kdm_uuid).bytes)
        return p.parse_get_kdm(msg.value)

    async def status_spl(self) -> p.SplStatus:
        return p.parse_status_spl((await self.request("StatusSPL")).value)

    async def retrieve_spl(self, spl_uuid: str) -> str:
        import uuid as uuidlib

        msg = await self.request("RetrSPL", uuidlib.UUID(spl_uuid).bytes)
        value = msg.value
        # The draft puts the request id *after* the XML; tolerate both orders.
        start = value.find(b"<")
        body = value[start:] if start >= 0 else value
        end = body.rfind(b">")
        return body[: end + 1].decode("utf-8", errors="replace")

    async def simple(self, name: str, payload: bytes = b"") -> int | None:
        return self._check(await self.request(name, payload))

    async def load_spl(self, xml: str) -> None:
        await self.simple("LoadSPL", xml.encode("utf-8"))

    async def store_spl(self, xml: str) -> None:
        await self.simple("StoreSPL", xml.encode("utf-8"))

    async def loop_mode(self, enabled: bool) -> None:
        await self.simple("LoopModeSPL", b"\x01" if enabled else b"\x00")

    async def jump(self, seconds: int) -> None:
        name = "JumpForward" if seconds >= 0 else "JumpBackward"
        await self.simple(name, abs(int(seconds)).to_bytes(4, "big"))


async def probe_klv(host: str, port: int, timeout: float = 3.0) -> int | None:
    """Return the working registry designator byte, or None if KLV is unavailable."""
    for reg in p.KLV_KEY_REG_DESIGNATORS:
        client = KLVClient(host, port, reg, timeout)
        try:
            await client.request("StatusSPL")
            return reg
        except DCPError as err:
            _LOGGER.debug("KLV probe reg=0x%02X failed: %s", reg, err)
        finally:
            await client.close()
    return None
