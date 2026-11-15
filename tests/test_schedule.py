"""Tests of the read-only SOAP client and the schedule bookkeeping (no HA needed).

Run:  python3 tests/test_schedule.py   (needs aiohttp)
"""

import asyncio
from datetime import datetime, timedelta
import pathlib
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fake_dcp  # noqa: E402,F401  (registers the package stub)
import fake_soap  # noqa: E402

import aiohttp  # noqa: E402

from doremi_dcp2000 import soap as s  # noqa: E402
from doremi_dcp2000.schedule_logic import (  # noqa: E402
    SRC_ACTUAL,
    SRC_EXPECTED,
    SRC_PAUSE_LIMIT,
    ScheduleBook,
)

TZ = ZoneInfo("Europe/Berlin")
LIMIT = timedelta(minutes=180)


def t(hh: int, mm: int = 0, ss: int = 0, day: int = 20) -> datetime:
    return datetime(2026, 11, day, hh, mm, ss, tzinfo=TZ)


# ------------------------------------------------------------------ SOAP


async def _soap_scenario() -> None:
    st, runner = await fake_soap.start("127.0.0.1", 15100)
    try:
        async with aiohttp.ClientSession() as http:
            c = s.DoremiSoapClient(http, "127.0.0.1", 15100, "manager", "password")
            assert await c.scheduler_status() == "Running"
            assert st.calls[:2] == ["SessionManagement/Login", "ScheduleManagement/GetSchedulerStatus"]
            assert (await c.who_am_i())["name"] == "manager"
            ids = await c.schedule_list(t(0), t(0, day=30))
            assert ids == [101, 102]
            info = await c.schedule_info(102)
            assert info == {
                "schedule_id": 102, "spl_uuid": fake_soap.SPL_B, "time_start": "2026-11-20T22:00:00",
                "duration": 6000, "status": "Pending", "loop_mode": "PlayOnce",
                "loading_policy": "Default", "note": "Spätvorstellung",
            }
            titles = await c.spl_list_info()
            assert titles[fake_soap.SPL_A]["title"] == "Abendvorstellung"
            # expired session -> one new login, call repeated
            st.sessions.clear()
            before = len(st.calls)
            assert await c.scheduler_status() == "Running"
            assert st.calls[before:] == [
                "ScheduleManagement/GetSchedulerStatus", "SessionManagement/Login",
                "ScheduleManagement/GetSchedulerStatus",
            ]
            # not found
            try:
                await c.schedule_info(999)
            except s.SoapFault as err:
                assert "not found" in err.fault.lower()
            else:
                raise AssertionError("expected fault")
            # write / control calls never leave the client
            before = len(st.calls)
            for service, op in (
                ("ScheduleManagement", "CreateSchedule"), ("ScheduleManagement", "DeleteSchedule"),
                ("ScheduleManagement", "StartScheduler"), ("ScheduleManagement", "StopScheduler"),
                ("ShowControl", "Play"), ("Projectors", "PowerOn"),
            ):
                try:
                    await c._post(service, op, [])
                except s.SoapNotAllowed:
                    pass
                else:
                    raise AssertionError(f"{op} must be refused")
            assert len(st.calls) == before
            await c.logout()
            assert not st.sessions
            # wrong password
            bad = s.DoremiSoapClient(http, "127.0.0.1", 15100, "manager", "wrong")
            try:
                await bad.login()
            except s.SoapAuthError:
                pass
            else:
                raise AssertionError("expected auth error")
            # nothing listening -> SoapError
            off = s.DoremiSoapClient(http, "127.0.0.1", 15199, "manager", "password", timeout=1)
            try:
                await off.scheduler_status()
            except s.SoapError:
                pass
            else:
                raise AssertionError("expected connection error")
    finally:
        await runner.cleanup()


def test_soap():
    asyncio.run(_soap_scenario())


def test_auth_fault_texts():
    for text in ("Authentication failed", "authentication failure"):
        raw = (
            '<e:Envelope xmlns:e="http://schemas.xmlsoap.org/soap/envelope/"><e:Body><e:Fault>'
            f"<faultcode>e:Client</faultcode><faultstring>{text}</faultstring></e:Fault></e:Body></e:Envelope>"
        ).encode()
        try:
            s.parse_response(raw, "Login")
        except s.SoapAuthError:
            pass
        else:
            raise AssertionError(text)


def test_envelope():
    env = s.build_envelope("GetScheduleList", [("sessionId", "abc"), ("dateFrom", None), ("x", "<&>")])
    assert b"<v1:GetScheduleList><sessionId>abc</sessionId><x>&lt;&amp;&gt;</x></v1:GetScheduleList>" in env
    assert s.format_datetime(t(19, 30)) == "2026-11-20T19:30:00"


# ------------------------------------------------------------ bookkeeping


def _infos():
    return [
        {"schedule_id": 101, "spl_uuid": "a", "time_start": "2026-11-20T19:30:00", "duration": 7200,
         "status": "Pending", "loop_mode": "PlayOnce", "loading_policy": "Default", "note": None},
        {"schedule_id": 102, "spl_uuid": "b", "time_start": "2026-11-21T00:30:00", "duration": 5400,
         "status": "Pending", "loop_mode": None, "loading_policy": None, "note": None},
        {"schedule_id": 103, "spl_uuid": "c", "time_start": "2026-11-21T19:00:00", "duration": 3600,
         "status": "Pending", "loop_mode": None, "loading_policy": None, "note": None},
    ]


TITLES = {"a": "Abendfilm", "b": "Spätfilm", "c": "Morgen"}


def _book(now: datetime) -> ScheduleBook:
    b = ScheduleBook()
    assert b.merge_server(_infos(), TITLES, t(4, day=19), now, TZ)
    return b


def test_day_sensors():
    now = t(12)
    b = _book(now)
    first = b.first_of_day(now, 4)
    last = b.last_of_day(now, 4)
    assert first.title == "Abendfilm" and first.start == t(19, 30)
    # 00:30 counts to the show day of the 20th (day starts 4:00)
    assert last.title == "Spätfilm" and last.sensor_end == t(2, 0, day=21)
    assert b.next_show(now).key == "s101"
    # after midnight still the same show day
    assert b.last_of_day(t(1, day=21), 4).key == "s102"
    # at 5:00 the new day only has the 19:00 show
    assert b.first_of_day(t(5, day=21), 4).key == "s103"
    assert len(b.day_shows(now, 4)) == 2
    # unchanged merge reports no change
    assert not b.merge_server(_infos(), TITLES, t(4, day=19), now, TZ)


def test_scheduled_show_runs_late_and_ends():
    b = _book(t(12))
    # start 3 min late -> belongs to the planned show
    assert b.track(t(19, 33), "playing", "a", 7200, "Abendfilm", None, LIMIT)
    run = b.running_show()
    assert run.key == "s101" and not run.manual and run.actual_start == t(19, 33)
    assert run.sensor_end == t(21, 33) and run.source() == SRC_EXPECTED
    assert b.next_show(t(19, 34)).key == "s102"
    # playing on: no change while the minute-rounded end stays
    assert not b.track(t(19, 33, 2), "playing", "a", 7198, None, None, LIMIT)
    # pause 10 min: end keeps moving later
    b.track(t(20, 0), "paused", "a", 5580, None, None, LIMIT)
    b.track(t(20, 10), "paused", "a", 5580, None, None, LIMIT)
    assert b.running_show().sensor_end == t(21, 43)
    b.track(t(20, 11), "playing", "a", 5520, None, None, LIMIT)
    assert b.paused_since is None
    # stop at the end
    assert b.track(t(21, 43), "stopped", None, None, None, None, LIMIT)
    show = b.shows["s101"]
    assert show.finished and show.actual_end == t(21, 43) and show.source() == SRC_ACTUAL
    assert not show.aborted and b.running_show() is None
    assert b.last_finished(t(21, 44)).key == "s101"


def test_manual_start_aborted():
    b = _book(t(12))
    assert b.track(t(15, 0), "playing", "x", 600, "Kinderfilm", None, LIMIT)
    run = b.running_show()
    assert run.manual and run.summary() == "Handstart: Kinderfilm" and run.sensor_end == t(15, 10)
    # eject after 2 min -> aborted, calendar end real
    b.track(t(15, 2), "stopped", None, None, None, None, LIMIT)
    show = b.shows[run.key]
    assert show.aborted and show.end == t(15, 2)
    # manual shows are never "next"
    assert b.next_show(t(15, 3)).key == "s101"


def test_external_player_and_pause_limit():
    b = _book(t(12))
    b.track(t(19, 30), "playing", "a", 600, None, None, LIMIT)  # pre-show of a Blu-ray show
    # server pauses, Blu-ray plays 90 min
    b.track(t(19, 40), "paused", "a", 60, None, 5400, LIMIT)
    assert b.running_show().sensor_end == t(21, 11)
    # 30 min later the Blu-ray has 60 min left
    b.track(t(20, 10), "paused", "a", 60, None, 3600, LIMIT)
    assert b.running_show().sensor_end == t(21, 11)
    # forgotten playlist: pause limit (180 min) ends the show
    b.track(t(22, 39), "paused", "a", 60, None, None, LIMIT)
    assert b.running_show() is not None
    assert b.track(t(22, 40), "paused", "a", 60, None, None, LIMIT)
    show = b.shows["s101"]
    assert show.finished and show.source() == SRC_PAUSE_LIMIT
    assert show.end == t(19, 40)  # calendar: begin of the pause
    assert show.sensor_end == t(22, 40)  # sensors: moment the limit hit (time trigger fires)
    # still paused afterwards: no new show
    assert not b.track(t(22, 45), "paused", "a", 60, None, None, LIMIT)
    assert b.running_show() is None


def test_store_roundtrip_and_prune():
    b = _book(t(12))
    b.track(t(19, 30), "playing", "a", 7200, None, None, LIMIT)
    b.track(t(19, 31), "paused", "a", 7140, None, None, LIMIT)
    copy = ScheduleBook.from_dict(b.to_dict())
    assert copy.current == "s101" and copy.paused_since == t(19, 31)
    assert copy.running_show().sensor_end == b.running_show().sensor_end
    # deleted on the server before it happened -> removed; history stays
    infos = [i for i in _infos() if i["schedule_id"] != 103]
    assert copy.merge_server(infos, TITLES, t(4, day=19), t(19, 32), TZ)
    assert "s103" not in copy.shows and "s101" in copy.shows
    copy.track(t(21, 0), "stopped", None, None, None, None, LIMIT)
    assert copy.prune(t(12, day=27) + timedelta(days=183), timedelta(days=183))
    assert not copy.shows


def test_server_time_with_zone():
    b = ScheduleBook()
    info = dict(_infos()[0], time_start="2026-11-20T18:30:00Z")
    b.merge_server([info], TITLES, t(4, day=19), t(12), TZ)
    assert b.shows["s101"].planned_start == t(19, 30)


if __name__ == "__main__":
    test_auth_fault_texts()
    test_envelope()
    test_soap()
    test_day_sensors()
    test_scheduled_show_runs_late_and_ends()
    test_manual_start_aborted()
    test_external_player_and_pause_limit()
    test_store_roundtrip_and_prune()
    test_server_time_with_zone()
    print("all schedule tests passed")
