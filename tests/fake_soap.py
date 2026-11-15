"""Minimal simulator of the Doremi SOAP web services (scheduler part, port 10000).

Run:  python3 tests/fake_soap.py [host] [port]
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from xml.etree import ElementTree as ET

from aiohttp import web

NS_SCH = "http://www.doremilabs.com/dc/dcp/ws/v1/schemas/schedulemanagement"
NS_SPL = "http://www.doremilabs.com/dc/dcp/ws/v1/schemas/splmanagement"
NS_V1 = "http://www.doremilabs.com/dc/dcp/ws/v1_0"

USER, PASSWORD = "manager", "password"
SPL_A = "11111111-aaaa-bbbb-cccc-000000000001"
SPL_B = "11111111-aaaa-bbbb-cccc-000000000002"


class SoapState:
    def __init__(self) -> None:
        self.sessions: set[str] = set()
        self.calls: list[str] = []
        self.scheduler = "Running"
        self.reject_dates = False
        self.schedules: dict[int, dict] = {
            101: {"spl": SPL_A, "start": "2026-11-20T19:30:00", "duration": 7200, "status": "Pending"},
            102: {"spl": SPL_B, "start": "2026-11-20T22:00:00", "duration": 6000, "status": "Pending",
                  "note": "Spätvorstellung"},
        }
        self.spls = {SPL_A: ("Abendvorstellung", 7200), SPL_B: ("Spätfilm", 6000)}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _fault(text: str) -> web.Response:
    body = (
        '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"><SOAP-ENV:Body>'
        f"<SOAP-ENV:Fault><faultcode>SOAP-ENV:Client</faultcode><faultstring>{text}</faultstring>"
        "</SOAP-ENV:Fault></SOAP-ENV:Body></SOAP-ENV:Envelope>"
    )
    return web.Response(status=500, text=body, content_type="text/xml")


def _ok(op: str, inner: str = "") -> web.Response:
    body = (
        '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:ns1="{NS_V1}" xmlns:sch="{NS_SCH}" xmlns:spl="{NS_SPL}"><SOAP-ENV:Body>'
        f"<ns1:{op}Response>{inner}</ns1:{op}Response></SOAP-ENV:Body></SOAP-ENV:Envelope>"
    )
    return web.Response(text=body, content_type="text/xml")


def make_app(st: SoapState) -> web.Application:
    async def handle(request: web.Request) -> web.Response:
        service = request.match_info["service"]
        root = ET.fromstring(await request.read())
        body = next(c for c in root if _local(c.tag) == "Body")
        req = body[0]
        op = _local(req.tag)
        args = {_local(c.tag): (c.text or "") for c in req}
        st.calls.append(f"{service}/{op}")
        if op == "Login":
            if args.get("username") != USER or args.get("password") != PASSWORD:
                return _fault("Authentication failed")
            sid = str(uuid.uuid4())
            st.sessions.add(sid)
            return _ok(op, f"<sessionId>{sid}</sessionId>")
        if args.get("sessionId") not in st.sessions:
            return _fault("Not authenticated")
        if op == "Logout":
            st.sessions.discard(args["sessionId"])
            return _ok(op)
        if op == "WhoAmI":
            return _ok(op, "<name>manager</name><group>Manager</group><userLevel>2</userLevel>")
        if op == "GetSchedulerStatus":
            return _ok(op, f"<schedulerStatus>{st.scheduler}</schedulerStatus>")
        if op == "GetScheduleList":
            if st.reject_dates and ("dateFrom" in args or "dateTo" in args):
                return _fault("Invalid argument")
            ids = "".join(f"<sch:scheduleId>{i}</sch:scheduleId>" for i in sorted(st.schedules))
            return _ok(op, f"<scheduleList>{ids}</scheduleList>")
        if op == "GetScheduleInfo":
            sid = int(args.get("scheduleId", "0"))
            s = st.schedules.get(sid)
            if s is None:
                return _fault("Not found")
            note = f"<sch:annotationText>{s['note']}</sch:annotationText>" if s.get("note") else ""
            return _ok(
                op,
                "<scheduleInfo>"
                f"<sch:scheduleId>{sid}</sch:scheduleId><sch:splId>urn:uuid:{s['spl']}</sch:splId>"
                f"<sch:timeStart>{s['start']}</sch:timeStart><sch:duration>{s['duration']}</sch:duration>"
                f"<sch:statusMode>{s['status']}</sch:statusMode><sch:loopMode>PlayOnce</sch:loopMode>"
                f"<sch:loadingPolicy>Default</sch:loadingPolicy>{note}</scheduleInfo>",
            )
        if op == "GetSPLListInfo":
            items = "".join(
                f"<spl:splInfo><spl:uuid>urn:uuid:{u}</spl:uuid><spl:title>{t}</spl:title>"
                f"<spl:duration>{d}</spl:duration></spl:splInfo>"
                for u, (t, d) in st.spls.items()
            )
            return _ok(op, f"<splList>{items}</splList>")
        return _fault(f"Unsupported operation {op}")

    app = web.Application()
    app.router.add_post("/dc/dcp/ws/v1/{service}", handle)
    return app


async def start(host: str = "127.0.0.1", port: int = 10000) -> tuple[SoapState, web.AppRunner]:
    st = SoapState()
    runner = web.AppRunner(make_app(st))
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return st, runner


async def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 10000
    await start(host, port)
    print(f"Fake Doremi web services on {host}:{port}")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
