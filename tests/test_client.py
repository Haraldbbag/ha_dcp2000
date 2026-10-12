"""End-to-end tests of the protocol client against the simulator (no HA needed)."""

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fake_dcp  # noqa: E402,F401  (registers the package stub)
from doremi_dcp2000 import protocol as p  # noqa: E402
from doremi_dcp2000.client import DCP2000Client, DCPCommandError, KLVClient, probe_klv  # noqa: E402


def test_frames_match_docs():
    # Examples from DCP2000 API v1.5, section 2.1.2
    assert p.envelope(p.TYPE_S9P, p.s9p_frame(0x2, 0x01)).hex() == "733970200000000300000000200121"
    assert p.envelope(p.TYPE_ALIVE).hex() == "616c69760000000000000000"
    assert p.parse_reply(bytes.fromhex("100111")).is_ack
    # Extended example: classic 24.31.00.01.02.03 -> E2.04.00.31.00.01.02.03 (+checksum)
    assert p.ext_frame(0x2, 0x31, bytes([0, 1, 2, 3]))[:-1].hex() == "e2040031" + "00010203"
    assert p.ext_frame(0x6, 0xBD, b"\x20")[:-1].hex() == "e60100bd20"
    body = bytes.fromhex("43c8801000")
    assert p.raw_frame("43.C8.80.10.00") == body + bytes([sum(body) & 0xFF])
    assert p.decode_timecode(bytes([0x12, 0x34, 0x56, 0x01])) == "01:56:34:12"
    assert p.klv_request("StatusSPL", 1)[:16].hex() == "060e2b340204010a0e10010101021300"


async def _scenario():
    st, s1, s2 = await fake_dcp.start("127.0.0.1", 15000, 15730)
    async with s1, s2:
        c = DCP2000Client("127.0.0.1", 15000)
        info = await c.device_info()
        assert info.product == "DCP2000" and info.serial == "123456" and info.protocol_version == "1.05"
        assert await c.tcp_protocol_version() == "1.3"
        clips = await c.list_clips()
        assert clips == sorted(fake_dcp.CLIPS)
        assert await c.loaded_id() is None
        assert (await c.status()).state == "stopped"

        await c.cue_clip(b"TRAILER2", 240)
        assert await c.loaded_id() == b"TRAILER2"
        assert await c.current_frame() == 240
        assert await c.last_frame() == 3600
        assert await c.current_uuid() == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        assert await c.uuid_for_id(b"FEATURE\x00") == "12345678-1234-1234-1234-123456789abc"
        assert await c.current_title() == "Trailer Herbst"
        assert (await c.status()).state == "cued"

        await c.play()
        assert (await c.status()).state == "playing"
        f1 = await c.current_frame()
        assert f1 > 240
        await c.pause()
        assert (await c.status()).state == "paused"
        assert await c.timecode() is not None
        await c.step_back()

        assert await c.gpi() == (0, 0b101)
        await c.set_gpo(0x81)
        assert st.gpo == 0x81
        await c.projector(p.PROJ_SHUTTER, 0)
        assert st.log[-1] == "43 c8 80 10 00 " + "%02x" % (sum(bytes.fromhex("43c8801000")) & 0xFF)

        try:
            await c.cue_clip(b"NOPE\x00\x00\x00\x00")
        except DCPCommandError:
            pass
        else:
            raise AssertionError("expected NAK")

        # reconnect after the server drops the socket
        await c._conn._writer.drain()
        c._conn._writer.close()
        assert (await c.status()).state in ("paused", "playing")
        await c.close()

        # KLV
        assert await probe_klv("127.0.0.1", 15730) == 0x04
        assert await probe_klv("127.0.0.1", 15999, timeout=0.5) is None
        k = KLVClient("127.0.0.1", 15730)
        cpls = await k.get_cpl_list()
        assert len(cpls) == 3
        info = await k.get_cpl("12345678-1234-1234-1234-123456789abc")
        assert info.title == "Hauptfilm" and info.fps == 24 and info.kind == "feature"
        assert info.width == 2048 and info.channels == 6 and not info.encrypted
        spl = await k.status_spl()
        assert spl.spl_uuid.startswith("99999999") and spl.spl_percent == 42
        assert spl.cpl_uuid == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        assert await k.get_spl_list() == [spl.spl_uuid]
        assert await k.simple("PlaySPL") == 0
        assert (await k.retrieve_spl(spl.spl_uuid)).startswith("<ShowPlaylist>")
        await k.jump(-180)
        await k.close()


def test_scenario():
    asyncio.run(_scenario())


if __name__ == "__main__":
    test_frames_match_docs()
    test_scenario()
    print("all tests passed")
