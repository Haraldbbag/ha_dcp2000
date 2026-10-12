"""Minimal DCP-2000 simulator (Sony 9-pin on 5000, KLV on 11730).

Run:  python3 tests/fake_dcp.py [host] [s9p_port] [klv_port]
Useful to try the Home Assistant integration without a real server.
"""

from __future__ import annotations

import asyncio
import pathlib
import struct
import sys
import uuid

import types

# Import the protocol modules without running the HA-dependent package __init__.
_PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "doremi_dcp2000"
if "doremi_dcp2000" not in sys.modules:
    _mod = types.ModuleType("doremi_dcp2000")
    _mod.__path__ = [str(_PKG)]
    sys.modules["doremi_dcp2000"] = _mod
from doremi_dcp2000 import protocol as p  # noqa: E402

CLIPS = {
    b"SPOT1\x00\x00\x00": ("Werbung Sommer", uuid.UUID("11111111-2222-3333-4444-555555555555"), 1440),
    b"TRAILER2": ("Trailer Herbst", uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"), 3600),
    b"FEATURE\x00": ("Hauptfilm", uuid.UUID("12345678-1234-1234-1234-123456789abc"), 172800),
    b"KDMLESS\x00": ("Ohne KDM", uuid.UUID("deadbeef-0000-0000-0000-000000000000"), 1000),
}
FPS = {b"TRAILER2": 25}  # everything else 24
NO_KDM = {b"KDMLESS\x00"}
# Real DCP-2000 firmware answers 60.A2 (current frame) with 0; mimic that.
BROKEN_CURRENT_FRAME = True
PLAY_DELAY = 4.0  # seconds before PlaySPL is answered


class State:
    def __init__(self) -> None:
        self.clip: bytes | None = None
        self.frame = 0
        self.playing = False
        self.still = False
        self.auto = False
        self.gpo = 0
        self.gpi = 0b00000101
        self.list_pos = 0
        self.ejected = False
        self.klv_log: list[str] = []
        self.spl_state = 1  # 1 stop, 2 play, 3 pause
        self.log: list[str] = []

    def tick(self) -> None:
        if self.playing and not self.still and self.clip:
            self.frame = min(self.frame + 6, CLIPS[self.clip][2])

    def status_bytes(self) -> bytes:
        b = bytearray(10)
        if self.ejected:
            b[1] |= 0x10
            return bytes(b)
        if self.playing:
            b[1] |= 0x01
            b[2] |= 0x80
        else:
            b[1] |= 0x20
        if self.still:
            b[2] |= 0x02 | 0x80
        if self.clip:
            b[2] |= 0x01
            b[3] |= 0x03
        if self.auto:
            b[3] |= 0x80
        return bytes(b)


def reply(func: int, cmd2: int, data: bytes = b"") -> bytes:
    return p.s9p_frame(func, cmd2, data)


def ext_reply(cmd2: int, data: bytes) -> bytes:
    return p.ext_frame(0x7, cmd2, data)


ACK = reply(0x1, 0x01)
NAK = reply(0x1, 0x12, b"\x01")


def handle_s9p(st: State, frame: bytes) -> bytes:
    r = p.parse_reply(frame)
    if not r.checksum_ok:
        return reply(0x1, 0x12, b"\x04")
    key = (r.func, r.cmd2)
    st.log.append(frame.hex(" "))
    if r.extended:
        sub = r.data[:1]
        if r.cmd2 == 0xBD and sub == b"\x20":
            clip = r.data[1:9] if len(r.data) > 1 else st.clip
            if clip not in CLIPS:
                return ext_reply(0xBD, b"\x20")
            return ext_reply(0xBD, b"\x20" + CLIPS[clip][1].bytes)
        if r.cmd2 == 0xBE and sub == b"\x10":
            clip = r.data[1:9] if len(r.data) > 1 else st.clip
            if clip not in CLIPS:
                return ext_reply(0xBE, b"\x10")
            return ext_reply(0xBE, b"\x10" + CLIPS[clip][0].encode() + b"\x00")
        return NAK
    if key == p.CMD_STOP:
        st.playing = st.still = False
        return ACK
    if key == p.CMD_PLAY:
        st.playing, st.still = True, False
        return ACK
    if key == p.CMD_STILL:
        st.still = True
        return ACK
    if key == p.CMD_STEP_BACK:
        st.frame = max(0, st.frame - 1)
        return ACK
    if key == p.CMD_SET_FRAME:
        st.frame = int.from_bytes(r.data, "big")
        return ACK
    if key == p.CMD_AUTO_ON:
        st.auto = True
        return ACK
    if key == p.CMD_IN_PRESET:
        if r.data[:8] not in CLIPS:
            return NAK
        if r.data[:8] in NO_KDM:
            st.clip, st.playing, st.ejected = None, False, True
            return ACK
        st.clip, st.frame, st.playing, st.still, st.ejected = r.data[:8], 0, False, True, False
        return ACK
    if key in (p.CMD_OUT_PRESET, p.CMD_PREVIEW_IN, p.CMD_PREVIEW_OUT):
        return ACK
    if key == p.CMD_STATUS_SENSE:
        start, count = r.data[0] >> 4, r.data[0] & 0x0F
        return reply(0x7, 0x20, st.status_bytes()[start:start + count])
    if key == p.CMD_CURRENT_FRAME:
        return reply(0x7, 0xA2, (0 if BROKEN_CURRENT_FRAME else st.frame).to_bytes(4, "big"))
    if key == p.CMD_LAST_FRAME:
        return reply(0x7, 0xB5, (CLIPS[st.clip][2] if st.clip else 0).to_bytes(4, "big"))
    if key == p.CMD_CURRENT_TIME_SENSE:
        tc = list(reversed(p.frames_to_timecode(st.frame, FPS.get(st.clip, 24))))
        return reply(0x7, 0x04, bytes(int(str(v), 16) for v in tc))
    if key == p.CMD_LOADED_ID:
        return reply(0x8, 0xC1, st.clip or b"\x00" * 8)
    if key == p.CMD_PRODUCT_NAME:
        return reply(0x8, 0xB1, b"DCP2000\x00")
    if key == p.CMD_SERIAL:
        return reply(0x6, 0xB2, b"123456")
    if key == p.CMD_PROTOCOL_VERSION:
        return reply(0x2, 0xB0, b"\x01\x05")
    if key in (p.CMD_LIST_FIRST, p.CMD_LIST_NEXT):
        if key == p.CMD_LIST_FIRST:
            st.list_pos = 0
        ids = sorted(CLIPS)
        if st.list_pos >= len(ids):
            return NAK
        st.list_pos += 1
        return reply(0x8, 0x14, ids[st.list_pos - 1])
    if key == p.CMD_CLIP_INFO and r.data[:1] == b"\x02":
        clip = r.data[1:9]
        if clip not in CLIPS:
            return NAK
        return reply(0x7, 0xBB, b"\x02" + CLIPS[clip][2].to_bytes(8, "big"))
    if key == p.CMD_GPI_SENSE:
        return reply(0x7, 0xBF, bytes([0x00, st.gpi]))
    if key == p.CMD_GPO_SET:
        st.gpo = r.data[1]
        return ACK
    if key == p.CMD_PROJECTOR:
        return ACK
    return NAK


async def serve_s9p(st: State, reader, writer) -> None:
    try:
        while True:
            header = await reader.readexactly(12)
            typ, length, _ = p.HEADER.unpack(header)
            data = await reader.readexactly(length) if length else b""
            if typ == p.TYPE_ALIVE:
                continue
            if typ == p.TYPE_VERSION:
                writer.write(p.HEADER.pack(p.TYPE_VERSION, 1, 3))
            else:
                st.tick()
                writer.write(p.envelope(p.TYPE_S9P, handle_s9p(st, data)))
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
        writer.close()


def klv_resp(b14: int, b15: int, b16: int, value: bytes) -> bytes:
    return p.klv_key(b14, b15 + 1, b16) + p.ber4(len(value)) + value


def batch(items: list[bytes]) -> bytes:
    return struct.pack(">II", len(items), 16) + b"".join(items)


async def serve_klv(st: State, reader, writer) -> None:
    spl = uuid.UUID("99999999-8888-7777-6666-555555555555")
    spl2 = uuid.UUID("12121212-3434-5656-7878-909090909090")
    by_uuid = {c[1]: (k, c) for k, c in CLIPS.items()}
    try:
        while True:
            key = await reader.readexactly(16)
            if key[5] != 0x05:  # wrong key -> real server hangs up
                break
            length, _ = p.parse_ber_length(await reader.readexactly(4))
            value = await reader.readexactly(length)
            b14, b15, b16, rid = key[13], key[14], key[15], value[:4]
            name = next((n for n, v in p.KLV_MESSAGES.items() if v == (b14, b15, b16)), None)
            if name is None:
                break
            ok = b"\x00"
            st.klv_log.append(name)
            if name == "PlaySPL":
                await asyncio.sleep(PLAY_DELAY)  # real server: pre-show checks first
                # DCP-2000: first PlaySPL after loading parks the show paused
                st.spl_state = 3 if st.spl_state == 1 else 2
            elif name == "PauseSPL":
                st.spl_state = 3
            elif name in ("LoadSPL", "LoadSPLbyUUID", "EjectSPL"):
                st.spl_state = 1
            if name == "GetCPLList":
                out = rid + batch([c[1].bytes for c in CLIPS.values()]) + ok
            elif name == "GetSPLList":
                out = rid + batch([spl.bytes, spl2.bytes]) + ok
            elif name == "GetSPLInfo":
                su = value[4:20]
                title = {spl.bytes: "Abendvorstellung", spl2.bytes: "Matinee"}[su]
                out = rid + su + title.encode().ljust(128, b"\x00") + (5400 * 24).to_bytes(4, "big") + ok
            elif name == "GetKDMList":
                out = rid + batch([]) + ok
            elif name == "GetCPLInfo":
                cu = uuid.UUID(bytes=value[4:20])
                clip, (title, _, dur) = by_uuid[cu]
                out = (rid + cu.bytes + b"\x01" + title.encode().ljust(128, b"\x00") + b"\x01"
                       + struct.pack(">III", dur, FPS.get(clip, 24) * 1000, 1000)
                       + b"\x02" + struct.pack(">HH", 2048, 858)
                       + b"\x00\x03\x06\x18\x00" + batch([]) + ok)
            elif name == "StatusSPL":
                cpl = CLIPS[st.clip][1].bytes if st.clip else b"\x00" * 16
                out = (rid + bytes([st.spl_state]) + spl.bytes
                       + struct.pack(">II", 2268, 5400) + cpl + b"\x00" * 32
                       + struct.pack(">II", 10, 100) + ok)
            elif name == "RetrSPL":
                out = rid + b"<ShowPlaylist><Id>urn:uuid:" + str(spl).encode() + b"</Id></ShowPlaylist>" + ok
            elif name == "GetAPIProtocolVersion":
                out = rid + bytes([2, 5, 0]) + ok
            elif name == "GetProductInfo":
                out = (rid + b"DCP2000".ljust(16, b"\x00") + b"123456".ljust(16, b"\x00")
                       + uuid.uuid4().bytes + bytes([4, 2, 1, 0, 1, 0, 0, 7]) + ok)
            elif name == "LoadSPLbyUUID" and value[4:20] not in (spl.bytes, spl2.bytes):
                out = rid + b"\x01"  # failed
            else:
                out = rid + ok
            writer.write(klv_resp(b14, b15, b16, out))
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, StopIteration, KeyError):
        pass
    writer.close()


async def start(host: str = "127.0.0.1", port: int = 5000, klv_port: int = 11730):
    st = State()
    s1 = await asyncio.start_server(lambda r, w: serve_s9p(st, r, w), host, port)
    s2 = await asyncio.start_server(lambda r, w: serve_klv(st, r, w), host, klv_port)
    return st, s1, s2


async def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    klv = int(sys.argv[3]) if len(sys.argv) > 3 else 11730
    _, s1, s2 = await start(host, port, klv)
    print(f"Fake DCP-2000 on {host}:{port} (KLV {klv})")
    async with s1, s2:
        await asyncio.gather(s1.serve_forever(), s2.serve_forever())


if __name__ == "__main__":
    asyncio.run(main())
