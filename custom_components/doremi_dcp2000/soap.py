"""Read-only client for the Doremi SOAP web services (port 10000).

Only the calls needed to read the show schedule are allowed. Everything that
changes the server (CreateSchedule, DeleteSchedule, Start/StopScheduler,
ShowControl, Projectors, ...) is refused here before anything is sent.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import aiohttp

from .client import DCPError

_LOGGER = logging.getLogger(__name__)

NS_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
NS_V1_0 = "http://www.doremilabs.com/dc/dcp/ws/v1_0"
PATH = "/dc/dcp/ws/v1/"

ALLOWED: dict[str, frozenset[str]] = {
    "SessionManagement": frozenset({"Login", "Logout", "WhoAmI"}),
    "ScheduleManagement": frozenset({"GetScheduleList", "GetScheduleInfo", "GetSchedulerStatus"}),
    "SPLManagement": frozenset({"GetSPLListInfo", "GetSPLInfo"}),
}


class SoapError(DCPError):
    """Transport problem or unexpected answer."""


class SoapFault(SoapError):
    """The server answered with a SOAP fault."""

    def __init__(self, fault: str) -> None:
        super().__init__(fault)
        self.fault = fault


class SoapAuthError(SoapFault):
    """Login refused (wrong user or password)."""


class SoapNotAuthenticated(SoapFault):
    """Session expired or unknown."""


class SoapNotAllowed(SoapError):
    """The integration never sends this call."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ET.Element, name: str) -> ET.Element | None:
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text(el: ET.Element, name: str) -> str | None:
    c = _child(el, name)
    return c.text.strip() if c is not None and c.text is not None else None


def strip_uuid(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().lower().removeprefix("urn:uuid:")


def format_datetime(value: datetime) -> str:
    """xsd:dateTime without zone (server local time, like the WSDL examples)."""
    return value.replace(tzinfo=None, microsecond=0).isoformat()


def build_envelope(operation: str, params: list[tuple[str, Any]]) -> bytes:
    body = "".join(
        f"<{name}>{escape(str(value))}</{name}>" for name, value in params if value is not None
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<soapenv:Envelope xmlns:soapenv="{NS_ENV}" xmlns:v1="{NS_V1_0}">'
        f"<soapenv:Header/><soapenv:Body><v1:{operation}>{body}</v1:{operation}>"
        "</soapenv:Body></soapenv:Envelope>"
    ).encode()


def parse_response(raw: bytes, operation: str) -> ET.Element:
    """Return the <OperationResponse> element or raise the matching fault."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as err:
        raise SoapError(f"{operation}: invalid XML ({err})") from err
    body = _child(root, "Body")
    if body is None:
        raise SoapError(f"{operation}: no SOAP body")
    for el in body:
        name = _local(el.tag)
        if name == "Fault":
            fault = (_text(el, "faultstring") or _text(el, "Reason") or "unknown fault").strip()
            low = fault.lower()
            if "not authenticated" in low:
                raise SoapNotAuthenticated(fault)
            if "authentication failed" in low:
                raise SoapAuthError(fault)
            raise SoapFault(fault)
        if name == f"{operation}Response":
            return el
    raise SoapError(f"{operation}: no {operation}Response in answer")


def parse_schedule_info(el: ET.Element) -> dict[str, Any]:
    info = _child(el, "scheduleInfo")
    if info is None:
        raise SoapError("GetScheduleInfo: no scheduleInfo")
    duration = _text(info, "duration")
    sid = _text(info, "scheduleId")
    return {
        "schedule_id": int(sid) if sid and sid.lstrip("-").isdigit() else None,
        "spl_uuid": strip_uuid(_text(info, "splId")),
        "time_start": _text(info, "timeStart"),
        "duration": int(duration) if duration and duration.lstrip("-").isdigit() else None,
        "status": _text(info, "statusMode") or "Unknown",
        "loop_mode": _text(info, "loopMode"),
        "loading_policy": _text(info, "loadingPolicy"),
        "note": _text(info, "annotationText"),
    }


class DoremiSoapClient:
    """One session per server, reused; re-login once when it expired."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        username: str,
        password: str,
        timeout: float = 5.0,
    ) -> None:
        self._http = session
        self.host = host
        self._base = f"http://{host}:{port}{PATH}"
        self._username = username
        self._password = password
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session_id: str | None = None
        self._lock = asyncio.Lock()

    async def _post(self, service: str, operation: str, params: list[tuple[str, Any]]) -> ET.Element:
        if operation not in ALLOWED.get(service, ()):
            raise SoapNotAllowed(f"{service}/{operation} is not allowed (read-only client)")
        data = build_envelope(operation, params)
        try:
            async with self._http.post(
                self._base + service,
                data=data,
                headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
                timeout=self._timeout,
            ) as resp:
                raw = await resp.read()
                status = resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise SoapError(f"{operation}: {err or type(err).__name__}") from err
        if status not in (200, 500):  # faults come with HTTP 500
            raise SoapError(f"{operation}: HTTP {status}")
        return parse_response(raw, operation)

    async def login(self) -> str:
        el = await self._post(
            "SessionManagement",
            "Login",
            [("username", self._username), ("password", self._password)],
        )
        sid = _text(el, "sessionId")
        if not sid:
            raise SoapError("Login: no sessionId")
        self._session_id = sid
        return sid

    async def logout(self) -> None:
        sid, self._session_id = self._session_id, None
        if sid:
            try:
                await self._post("SessionManagement", "Logout", [("sessionId", sid)])
            except SoapError as err:
                _LOGGER.debug("Logout from %s failed: %s", self.host, err)

    async def _call(self, service: str, operation: str, params: list[tuple[str, Any]] | None = None) -> ET.Element:
        """Call with the session; on 'not authenticated' log in again once and repeat (read only)."""
        async with self._lock:
            if not self._session_id:
                await self.login()
            try:
                return await self._post(service, operation, [("sessionId", self._session_id), *(params or [])])
            except SoapNotAuthenticated:
                _LOGGER.debug("Session on %s expired, logging in again", self.host)
                self._session_id = None
                await self.login()
                return await self._post(service, operation, [("sessionId", self._session_id), *(params or [])])

    async def who_am_i(self) -> dict[str, Any]:
        el = await self._call("SessionManagement", "WhoAmI")
        return {"name": _text(el, "name"), "group": _text(el, "group"), "user_level": _text(el, "userLevel")}

    async def scheduler_status(self) -> str:
        el = await self._call("ScheduleManagement", "GetSchedulerStatus")
        return _text(el, "schedulerStatus") or "Unknown"

    async def schedule_list(self, date_from: datetime | None = None, date_to: datetime | None = None) -> list[int]:
        params = [
            ("dateFrom", format_datetime(date_from) if date_from else None),
            ("dateTo", format_datetime(date_to) if date_to else None),
        ]
        el = await self._call("ScheduleManagement", "GetScheduleList", params)
        lst = _child(el, "scheduleList")
        ids: list[int] = []
        for c in lst if lst is not None else []:
            if _local(c.tag) == "scheduleId" and c.text and c.text.strip().lstrip("-").isdigit():
                ids.append(int(c.text.strip()))
        return ids

    async def schedule_info(self, schedule_id: int) -> dict[str, Any]:
        el = await self._call("ScheduleManagement", "GetScheduleInfo", [("scheduleId", schedule_id)])
        info = parse_schedule_info(el)
        if info["schedule_id"] is None:
            info["schedule_id"] = schedule_id
        return info

    async def spl_list_info(self) -> dict[str, dict[str, Any]]:
        el = await self._call("SPLManagement", "GetSPLListInfo")
        result: dict[str, dict[str, Any]] = {}
        for arr in el:
            for item in arr:
                if _local(item.tag) != "splInfo":
                    continue
                uuid = strip_uuid(_text(item, "uuid"))
                if not uuid:
                    continue
                dur = _text(item, "duration")
                result[uuid] = {
                    "title": _text(item, "title"),
                    "duration": int(dur) if dur and dur.isdigit() else None,
                }
        return result
