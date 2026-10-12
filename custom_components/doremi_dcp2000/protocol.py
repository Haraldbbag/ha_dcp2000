"""Wire protocol helpers for the Doremi DCP-2000 (no Home Assistant imports).

Two protocols are implemented, both taken from the Doremi documentation:

* Sony 9-pin (Odetics flavour) embedded in a 12 byte TCP header on port 5000
  ("DCP-2000 API Description" v1.0 / v1.5).
* KLV encoded CPL / SPL messages ("CPL & SPL API Description", draft 2006).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import struct
import uuid as uuidlib

# --------------------------------------------------------------------------
# TCP envelope
# --------------------------------------------------------------------------

HEADER = struct.Struct(">4sii")
TYPE_S9P = b"s9p "
TYPE_ALIVE = b"aliv"
TYPE_VERSION = b"ver "


def envelope(msg_type: bytes, payload: bytes = b"", param2: int = 0) -> bytes:
    """Wrap a payload in the 12 byte cnxn_msg header."""
    return HEADER.pack(msg_type, len(payload), param2) + payload


# --------------------------------------------------------------------------
# Sony 9-pin frames
# --------------------------------------------------------------------------


def checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def s9p_frame(cmd1_func: int, cmd2: int, data: bytes = b"") -> bytes:
    """Build a classic Sony 9-pin frame (max. 15 data bytes)."""
    if len(data) > 0x0F:
        return ext_frame(cmd1_func, cmd2, data)
    body = bytes([(cmd1_func << 4) | len(data), cmd2]) + data
    return body + bytes([checksum(body)])


def ext_frame(cmd1_func: int, cmd2: int, data: bytes = b"") -> bytes:
    """Build a Doremi "Extended Sony 9 Pins" frame.

    Format: 0xE<func> | count lo | count hi | cmd2 | data… | checksum
    """
    n = len(data)
    body = bytes([0xE0 | (cmd1_func & 0x0F), n & 0xFF, (n >> 8) & 0xFF, cmd2]) + data
    return body + bytes([checksum(body)])


def raw_frame(hex_bytes: str) -> bytes:
    """Parse '20 01' / '20.01' / '2001' and append the checksum."""
    cleaned = hex_bytes.replace(".", " ").replace(":", " ").replace(",", " ")
    body = bytes.fromhex(cleaned)
    return body + bytes([checksum(body)])


@dataclass(slots=True)
class Reply:
    func: int
    cmd2: int
    data: bytes
    extended: bool = False
    checksum_ok: bool = True

    @property
    def is_ack(self) -> bool:
        return self.func == 0x1 and self.cmd2 == 0x01

    @property
    def is_nak(self) -> bool:
        return self.func == 0x1 and self.cmd2 == 0x12

    @property
    def code(self) -> str:
        return f"{self.func:X}x.{self.cmd2:02X}"


def parse_reply(payload: bytes) -> Reply:
    if len(payload) < 2:
        raise ValueError(f"reply too short: {payload.hex()}")
    b0 = payload[0]
    cs_ok = checksum(payload[:-1]) == payload[-1]
    if (b0 >> 4) == 0xE:
        if len(payload) < 5:
            raise ValueError(f"extended reply too short: {payload.hex()}")
        func = b0 & 0x0F
        cmd2 = payload[3]
        data = payload[4:-1]
        return Reply(func, cmd2, data, True, cs_ok)
    func = b0 >> 4
    count = b0 & 0x0F
    cmd2 = payload[1]
    data = payload[2 : 2 + count]
    return Reply(func, cmd2, data, False, cs_ok)


NAK_BITS = {
    7: "timeout",
    6: "framing error",
    5: "overrun error",
    4: "parity error",
    2: "checksum error",
    0: "undefined command",
}


def nak_reason(reply: Reply) -> str:
    if not reply.data:
        return "unknown"
    bits = reply.data[0]
    names = [name for bit, name in NAK_BITS.items() if bits & (1 << bit)]
    return ", ".join(names) or f"0x{bits:02X}"


# --- Command catalogue (func, cmd2, data) -----------------------------------

CMD_STOP = (0x2, 0x00)
CMD_PLAY = (0x2, 0x01)
CMD_EJECT = (0x2, 0x0F)
CMD_STILL = (0x2, 0x13)
CMD_STEP_BACK = (0x2, 0x24)
CMD_SET_FRAME = (0x2, 0x9D)  # 24.9D aa bb cc dd
CMD_IN_PRESET = (0x4, 0x14)
CMD_OUT_PRESET = (0x4, 0x15)
CMD_IN_RESET = (0x4, 0x20)
CMD_OUT_RESET = (0x4, 0x21)
CMD_AUTO_OFF = (0x4, 0x40)
CMD_AUTO_ON = (0x4, 0x41)
CMD_CURRENT_TIME_SENSE = (0x6, 0x0C)
CMD_STATUS_SENSE = (0x6, 0x20)
CMD_CURRENT_FRAME = (0x6, 0xA2)
CMD_PROTOCOL_VERSION = (0x6, 0xB0)
CMD_PRODUCT_NAME = (0x6, 0xB1)
CMD_SERIAL = (0x6, 0xB2)
CMD_FIRST_FRAME = (0x6, 0xB4)
CMD_LAST_FRAME = (0x6, 0xB5)
CMD_LOADED_ID = (0x6, 0xC1)
CMD_GPI_SENSE = (0x6, 0xBF)
CMD_GPO_SET = (0x4, 0xBF)
CMD_PROJECTOR = (0x4, 0xC8)
CMD_PREVIEW_IN = (0xA, 0x04)
CMD_PREVIEW_OUT = (0xA, 0x05)
CMD_PREVIEW_IN_RESET = (0xA, 0x06)
CMD_PREVIEW_OUT_RESET = (0xA, 0x07)
CMD_ERASE_ID = (0xA, 0x10)
CMD_LIST_FIRST = (0xA, 0x14)
CMD_LIST_NEXT = (0xA, 0x15)
CMD_DEVICE_ID = (0xA, 0x21)
CMD_CLIP_INFO = (0x6, 0xBB)  # 69.BB.<sub>.<id>; sub 0x02 = length in frames (seen in Doremi tools)
# Extended
EXT_UUID_ID = (0x6, 0xBD)  # sub 0x20: ID -> UUID, 0x21: UUID -> ID
EXT_TITLE = (0x6, 0xBE)  # sub 0x10: content title text

# Generic projector namespace 43.C8.80.<cmd>.<arg>
PROJ_SHUTTER = 0x10
PROJ_POWER = 0x11
PROJ_CHANNEL = 0x12


# --------------------------------------------------------------------------
# Payload decoders
# --------------------------------------------------------------------------


def bcd(value: int) -> int:
    return ((value >> 4) & 0x0F) * 10 + (value & 0x0F)


Timecode = tuple[int, int, int, int]  # hours, minutes, seconds, frames

# Frame rates found in DCI / SMPTE DCPs (integer edit rates only).
STANDARD_RATES = (24, 25, 30, 48, 50, 60, 96, 100, 120)


def decode_timecode_parts(data: bytes) -> Timecode | None:
    """Sony LTC time data: frames, seconds, minutes, hours (BCD)."""
    if len(data) < 4:
        return None
    # Bit 6 is the drop-frame flag in video timecode; D-Cinema never uses drop
    # frame, so treat it as a tens bit (HFR content can show frames >= 40).
    frames = bcd(data[0] & 0x7F)
    seconds = bcd(data[1] & 0x7F)
    minutes = bcd(data[2] & 0x7F)
    hours = bcd(data[3] & 0x3F)
    return hours, minutes, seconds, frames


def format_timecode(tc: Timecode | None) -> str | None:
    if tc is None:
        return None
    return "{:02d}:{:02d}:{:02d}:{:02d}".format(*tc)


def decode_timecode(data: bytes) -> str | None:
    return format_timecode(decode_timecode_parts(data))


def timecode_to_frames(tc: Timecode, fps: float) -> int:
    hours, minutes, seconds, frames = tc
    return round(((hours * 60 + minutes) * 60 + seconds) * fps) + frames


def frames_to_timecode(frame: int, fps: int) -> Timecode:
    seconds, frames = divmod(frame, fps)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return hours, minutes, seconds, frames


def match_rate(frame: int, tc: Timecode, tolerance: int = 2) -> int | None:
    """Which standard rate turns `frame` into timecode `tc` (± tolerance frames)?"""
    hits = [
        r for r in STANDARD_RATES
        if tc[3] < r and abs(timecode_to_frames(tc, r) - frame) <= tolerance
    ]
    return hits[0] if len(hits) == 1 else None


def probe_frame(last_frame: int | None) -> int | None:
    """A frame index whose timecode differs for every standard rate."""
    limit = (last_frame or 1000) - 1
    for frame in (1000, 997, 601, 487, 311, 241, 131, 121):
        if frame <= limit and len({frames_to_timecode(frame, r) for r in STANDARD_RATES}) == len(STANDARD_RATES):
            return frame
    return None


def rate_at_least(min_frames: int) -> int:
    """Smallest standard rate that can show a frames field of `min_frames`."""
    return next((r for r in STANDARD_RATES if r > min_frames), STANDARD_RATES[-1])


def decode_u32(data: bytes) -> int | None:
    if len(data) < 4:
        return None
    return int.from_bytes(data[:4], "big")


def decode_text(data: bytes) -> str:
    return data.split(b"\x00", 1)[0].decode("latin-1", errors="replace").strip()


def decode_clip_id(data: bytes) -> bytes | None:
    """Return the raw 8 byte clip id, or None when no clip is loaded."""
    raw = bytes(data[:8])
    if not raw or raw.strip(b"\x00 ") == b"":
        return None
    return raw


def clip_id_to_str(raw: bytes) -> str:
    return raw.rstrip(b"\x00 ").decode("latin-1", errors="replace")


def clip_id_from_str(value: str) -> bytes:
    raw = value.encode("latin-1")[:8]
    return raw.ljust(8, b"\x00")


def decode_uuid(data: bytes) -> str | None:
    """UUIDs may come back as 16 raw bytes or as text."""
    if len(data) >= 16 and len(data) < 32:
        return str(uuidlib.UUID(bytes=bytes(data[:16])))
    text = decode_text(data)
    if text.lower().startswith("urn:uuid:"):
        text = text[9:]
    try:
        return str(uuidlib.UUID(text))
    except ValueError:
        return text or None


@dataclass(slots=True)
class TransportStatus:
    """Decoded Sony status bytes (0-based, as used by the Doremi docs)."""

    raw: bytes = b""

    def bit(self, byte: int, bit: int) -> bool:
        return len(self.raw) > byte and bool(self.raw[byte] & (1 << bit))

    @property
    def playing(self) -> bool:
        return self.bit(1, 0)

    @property
    def stopped(self) -> bool:
        return self.bit(1, 5)

    @property
    def still(self) -> bool:
        return self.bit(2, 1)

    @property
    def cue_complete(self) -> bool:
        return self.bit(2, 0)

    @property
    def servo_lock(self) -> bool:
        return self.bit(2, 7)

    @property
    def in_preset(self) -> bool:
        return self.bit(3, 0)

    @property
    def out_preset(self) -> bool:
        return self.bit(3, 1)

    @property
    def auto_mode(self) -> bool:
        return self.bit(3, 7)

    @property
    def preroll(self) -> bool:
        return self.bit(4, 0)

    @property
    def ejected(self) -> bool:
        return self.bit(1, 4)

    @property
    def state(self) -> str:
        if self.ejected and not self.playing:
            return "ejected"
        if self.playing and not self.still:
            return "playing"
        if self.still or (self.servo_lock and not self.stopped):
            return "paused"
        if self.preroll:
            return "cueing"
        if self.cue_complete:
            return "cued"
        return "stopped"


# --------------------------------------------------------------------------
# KLV ("DCP-2000 Messages", Doremi doc CSL.TD.000109.DRM v2.5, TCP 11730)
# --------------------------------------------------------------------------

KLV_KEY_PREFIX = bytes.fromhex("060e2b3402")
# Byte 6: 0x05 in v2.5 (fixed length pack); the 2006 draft used 0x04.
KLV_KEY_REG_DESIGNATORS = (0x05, 0x04)

# name -> (byte 14, byte 15, byte 16) of the request key; response = byte 15 + 1
KLV_MESSAGES: dict[str, tuple[int, int, int]] = {
    "GetCPLList": (0x01, 0x01, 0x00),
    "GetCPLInfo": (0x01, 0x03, 0x00),
    "GetKDMList": (0x02, 0x01, 0x00),
    "GetSPLList": (0x03, 0x01, 0x00),
    "GetSPLInfo": (0x03, 0x03, 0x00),
    "LoadSPL": (0x03, 0x07, 0x00),
    "LoadSPLbyUUID": (0x03, 0x09, 0x00),
    "PlaySPL": (0x03, 0x0B, 0x00),
    "PauseSPL": (0x03, 0x0D, 0x00),
    "EjectSPL": (0x03, 0x0F, 0x00),
    "SkipForward": (0x03, 0x11, 0x00),
    "SkipBackward": (0x03, 0x13, 0x00),
    "JumpForward": (0x03, 0x15, 0x00),
    "JumpBackward": (0x03, 0x17, 0x00),
    "LoopModeSPL": (0x03, 0x19, 0x00),
    "StatusSPL": (0x03, 0x1B, 0x00),
    "RetrSPL": (0x03, 0x1D, 0x00),
    "StoreSPL": (0x03, 0x1F, 0x00),
    "SkipToEvent": (0x03, 0x21, 0x00),
    "GetLoopModeSPL": (0x03, 0x23, 0x00),
    "JumpToTimeCode": (0x03, 0x2D, 0x00),
    "GetProductInfo": (0x05, 0x01, 0x00),
    "GetAPIProtocolVersion": (0x05, 0x05, 0x00),
}

RESPONSE_CODES = {0: "ok", 1: "failed", 2: "invalid parameter", 3: "busy"}
PLAYBACK_STATES = {0: "unknown", 1: "stopped", 2: "playing", 3: "paused"}
LOOP_MODES = {"once": 0, "loop": 1, "rewind": 2, "eject": 3}
CONTENT_KINDS = {
    0: "unknown", 1: "feature", 2: "trailer", 3: "test", 4: "teaser", 5: "rating",
    6: "advertisement", 7: "short", 8: "transitional", 9: "psa", 10: "policy", 0x80: "live",
}
ENCODINGS = {0: "unknown", 1: "MPEG2", 2: "JPEG2000", 3: "PCM"}
ENCRYPTION = {0: "none", 1: "AES-128-CBC"}


def klv_key(b14: int, b15: int, b16: int = 0, reg: int = 0x05) -> bytes:
    return (
        KLV_KEY_PREFIX + bytes([reg, 0x01, 0x0A, 0x0E, 0x10, 0x01, 0x01, 0x01, b14, b15, b16])
    )


def ber4(length: int) -> bytes:
    return bytes([0x83]) + length.to_bytes(3, "big")


def klv_request(name: str, request_id: int, payload: bytes = b"", reg: int = 0x05) -> bytes:
    b14, b15, b16 = KLV_MESSAGES[name]
    value = request_id.to_bytes(4, "big") + payload
    return klv_key(b14, b15, b16, reg) + ber4(len(value)) + value


def parse_ber_length(buf: bytes) -> tuple[int, int]:
    """Return (length, bytes consumed)."""
    first = buf[0]
    if first < 0x80:
        return first, 1
    n = first & 0x7F
    return int.from_bytes(buf[1 : 1 + n], "big"), 1 + n


def is_klv_key(key: bytes) -> bool:
    return len(key) == 16 and key[:5] == KLV_KEY_PREFIX and key[9] == 0x10


@dataclass(slots=True)
class KlvMessage:
    b14: int
    b15: int
    b16: int
    value: bytes

    @property
    def request_id(self) -> int:
        return int.from_bytes(self.value[:4], "big")

    @property
    def response(self) -> int | None:
        return self.value[-1] if len(self.value) > 4 else None


def parse_uuid_batch(data: bytes, offset: int = 0) -> tuple[list[str], int]:
    count = int.from_bytes(data[offset : offset + 4], "big")
    size = int.from_bytes(data[offset + 4 : offset + 8], "big")
    offset += 8
    items = []
    for _ in range(count):
        chunk = data[offset : offset + size]
        offset += size
        items.append(str(uuidlib.UUID(bytes=chunk[:16])) if size >= 16 else chunk.hex())
    return items, offset


@dataclass(slots=True)
class CplInfo:
    uuid: str
    title: str
    kind: str
    duration: int
    edit_rate: tuple[int, int]
    picture_encoding: str
    width: int
    height: int
    picture_encryption: str
    sound_encoding: str
    channels: int
    sound_bits: int
    sound_encryption: str
    storage: int = 1
    key_ids: list[str] = field(default_factory=list)

    @property
    def fps(self) -> float:
        num, den = self.edit_rate
        return round(num / den, 3) if num and den else 0.0

    @property
    def encrypted(self) -> bool:
        return self.picture_encryption != "none" or self.sound_encryption != "none"


def parse_get_cpl(value: bytes) -> CplInfo:
    v = value[4:]  # skip request id
    o = 0
    cpl = str(uuidlib.UUID(bytes=v[o : o + 16])); o += 16
    storage = v[o]; o += 1
    title = v[o : o + 128].split(b"\x00", 1)[0].decode("utf-8", errors="replace"); o += 128
    kind = v[o]; o += 1
    duration, er_a, er_b = struct.unpack_from(">III", v, o); o += 12
    pic_enc = v[o]; o += 1
    width, height = struct.unpack_from(">HH", v, o); o += 4
    pic_crypt, snd_enc, channels, bits, snd_crypt = v[o : o + 5]; o += 5
    keys: list[str] = []
    if len(v) >= o + 8:
        keys, o = parse_uuid_batch(v, o)
    return CplInfo(
        uuid=cpl, title=title, kind=CONTENT_KINDS.get(kind, f"0x{kind:02X}"),
        duration=duration, edit_rate=(er_a, er_b),
        picture_encoding=ENCODINGS.get(pic_enc, str(pic_enc)), width=width, height=height,
        picture_encryption=ENCRYPTION.get(pic_crypt, str(pic_crypt)),
        sound_encoding=ENCODINGS.get(snd_enc, str(snd_enc)), channels=channels,
        sound_bits=bits, sound_encryption=ENCRYPTION.get(snd_crypt, str(snd_crypt)),
        storage=storage, key_ids=keys,
    )


@dataclass(slots=True)
class SplStatus:
    state: str
    spl_uuid: str | None
    position: int  # seconds
    duration: int  # seconds
    cpl_uuid: str | None
    element_position: int
    element_duration: int
    event_id: str | None = None
    element_id: str | None = None

    @property
    def remaining(self) -> int | None:
        return max(0, self.duration - self.position) if self.duration else None

    @property
    def spl_percent(self) -> int | None:
        return round(100 * self.position / self.duration) if self.duration else None


def _uuid_or_none(raw: bytes) -> str | None:
    return None if raw.strip(b"\x00") == b"" else str(uuidlib.UUID(bytes=raw))


def parse_status_spl(value: bytes) -> SplStatus:
    v = value[4:]
    state = v[0]
    spl = _uuid_or_none(v[1:17])
    position, duration = struct.unpack_from(">II", v, 17)
    cpl = _uuid_or_none(v[25:41])
    event = _uuid_or_none(v[41:57]) if len(v) >= 57 else None
    element = _uuid_or_none(v[57:73]) if len(v) >= 73 else None
    el_pos, el_dur = struct.unpack_from(">II", v, 73) if len(v) >= 81 else (0, 0)
    return SplStatus(PLAYBACK_STATES.get(state, f"0x{state:02X}"), spl, position, duration,
                     cpl, el_pos, el_dur, event, element)


def format_hms(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el, name: str):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text(el, name: str) -> str | None:
    c = _child(el, name) if el is not None else None
    return c.text.strip() if c is not None and c.text else None


def _uuid_text(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    return value[9:].lower() if value.lower().startswith("urn:uuid:") else value.lower()


def parse_spl_xml(xml: str) -> list[dict]:
    """Events of a (Doremi or SMPTE draft) Show Playlist in play order."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml.strip().rstrip("\x00").encode("utf-8"))
    items: list[dict] = []
    start = 0.0
    for event in root.iter():
        if _local(event.tag) != "Event":
            continue
        elements = _child(event, "ElementList")
        if elements is None:
            continue
        main = None
        holder = _child(elements, "MainElement")
        for c in (holder if holder is not None else elements):
            if _local(c.tag) in ("Composition", "Pattern"):
                main = c
                break
        if main is None:
            continue
        kind = _local(main.tag).lower()
        rate = (_text(main, "EditRate") or "24 1").split()
        try:
            fps = int(rate[0]) / int(rate[1] if len(rate) > 1 else 1)
        except (ValueError, ZeroDivisionError):
            fps = 24.0
        frames = _text(main, "IntrinsicDuration") or _text(main, "Duration") or "0"
        try:
            duration = int(frames) / fps
        except ValueError:
            duration = 0.0
        cues = [
            _text(c, "Action")
            for c in elements
            if _local(c.tag) == "AutomationCue" and _text(c, "Action")
        ]
        items.append({
            "event_id": _uuid_text(_text(event, "Id")),
            "element_id": _uuid_text(_text(main, "Id")),
            "kind": kind,
            "cpl_uuid": _uuid_text(_text(main, "CompositionPlaylistId")),
            "title": _text(main, "AnnotationText") or kind,
            "fps": round(fps, 3),
            "start": round(start, 2),
            "duration": round(duration, 2),
            "cues": cues,
        })
        start += duration
    return items


@dataclass(slots=True)
class ProductInfo:
    name: str
    serial: str
    product_id: str
    software: str
    hardware: str


def parse_product_info(value: bytes) -> ProductInfo:
    v = value[4:]
    name = v[0:16].split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    serial = v[16:32].split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    pid = str(uuidlib.UUID(bytes=v[32:48]))
    sw = ".".join(str(x) for x in v[48:52])
    hw = ".".join(str(x) for x in v[52:56])
    return ProductInfo(name, serial, pid, sw, hw)
