"""Show schedule bookkeeping without Home Assistant imports (unit-testable).

A ScheduleBook keeps every show of one server:
- scheduled shows read from the server's scheduler (key "s<scheduleId>"),
- shows started by hand (key "m<timestamp>"),
and follows what really happens (start, expected end, real end) from the
SPL status that the integration polls anyway.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Any

MATCH_WINDOW = timedelta(minutes=15)  # a start this close to a planned show belongs to it
ABORT_MARGIN = timedelta(minutes=2)  # ended this much before the expected end -> "aborted"
NEXT_GRACE = timedelta(minutes=15)  # a planned show not yet started stays "next" this long

SRC_PLANNED = "geplant"
SRC_EXPECTED = "voraussichtlich"
SRC_ACTUAL = "tatsaechlich"
SRC_PAUSE_LIMIT = "pause_zeitlimit"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def parse_server_time(value: str | None, local_tz: tzinfo) -> datetime | None:
    """xsd:dateTime from the server; without zone it is taken as local time."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_tz)
    return parsed


def minute_ceil(value: datetime) -> datetime:
    """Round up to the full minute so sensors do not change every poll."""
    base = value.replace(second=0, microsecond=0)
    return base if base == value else base + timedelta(minutes=1)


@dataclass
class Show:
    key: str
    spl_uuid: str | None
    title: str
    planned_start: datetime
    planned_end: datetime
    schedule_id: int | None = None
    status: str | None = None  # Pending / Success / Failure / Aborted / Unknown (server)
    loop_mode: str | None = None
    loading_policy: str | None = None
    note: str | None = None
    manual: bool = False
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    expected_end: datetime | None = None
    end_source: str = SRC_PLANNED
    limit_at: datetime | None = None  # pause limit reached (sensor end)
    aborted: bool = False

    @property
    def start(self) -> datetime:
        return self.actual_start or self.planned_start

    @property
    def end(self) -> datetime:
        """End for the calendar (the honest one)."""
        if self.actual_end:
            return self.actual_end
        if self.expected_end:
            return self.expected_end
        return max(self.planned_end, self.start)

    @property
    def sensor_end(self) -> datetime:
        """End for sensors: after the pause limit the moment the limit was hit."""
        if self.end_source == SRC_PAUSE_LIMIT and self.limit_at:
            return self.limit_at
        return self.end

    @property
    def running(self) -> bool:
        return self.actual_start is not None and self.actual_end is None

    @property
    def started(self) -> bool:
        return self.actual_start is not None

    @property
    def finished(self) -> bool:
        return self.actual_end is not None

    def summary(self) -> str:
        return f"Handstart: {self.title}" if self.manual else self.title

    def description(self) -> str:
        lines = []
        if not self.manual:
            lines.append(
                f"Geplant: {self.planned_start:%d.%m.%Y %H:%M} – {self.planned_end:%H:%M}"
            )
        if self.actual_start:
            lines.append(f"Gestartet: {self.actual_start:%H:%M}")
        if self.actual_end:
            lines.append(f"Beendet: {self.actual_end:%H:%M}")
        if self.end_source == SRC_PAUSE_LIMIT:
            lines.append("Ende über Pausen-Zeitlimit")
        if self.aborted:
            lines.append("abgebrochen")
        if self.status and not self.manual:
            lines.append(f"Server-Status: {self.status}")
        if self.note:
            lines.append(self.note)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("planned_start", "planned_end", "actual_start", "actual_end", "expected_end", "limit_at"):
            d[k] = _iso(d[k])
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Show:
        d = dict(d)
        for k in ("planned_start", "planned_end", "actual_start", "actual_end", "expected_end", "limit_at"):
            d[k] = _dt(d.get(k))
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})

    def attrs(self) -> dict[str, Any]:
        return {
            "titel": self.summary(),
            "beginn": _iso(self.start),
            "ende": _iso(self.sensor_end),
            "geplant_beginn": None if self.manual else _iso(self.planned_start),
            "geplant_ende": None if self.manual else _iso(self.planned_end),
            "quelle": self.source(),
            "handstart": self.manual,
            "abgebrochen": self.aborted,
            "status": self.status,
            "spl_uuid": self.spl_uuid,
            "schedule_id": self.schedule_id,
            "loop_mode": self.loop_mode,
            "loading_policy": self.loading_policy,
            "notiz": self.note,
        }

    def source(self) -> str:
        if self.end_source == SRC_PAUSE_LIMIT:
            return SRC_PAUSE_LIMIT
        if self.actual_end:
            return SRC_ACTUAL
        if self.running:
            return SRC_EXPECTED
        return SRC_PLANNED


@dataclass
class ScheduleBook:
    shows: dict[str, Show] = field(default_factory=dict)
    current: str | None = None  # key of the running show
    paused_since: datetime | None = None

    # ----------------------------------------------------------------- store
    def to_dict(self) -> dict[str, Any]:
        return {
            "shows": [s.to_dict() for s in self.shows.values()],
            "current": self.current,
            "paused_since": _iso(self.paused_since),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> ScheduleBook:
        book = cls()
        if not d:
            return book
        for raw in d.get("shows", []):
            try:
                show = Show.from_dict(raw)
            except (TypeError, ValueError):
                continue
            book.shows[show.key] = show
        cur = d.get("current")
        book.current = cur if cur in book.shows and book.shows[cur].running else None
        book.paused_since = _dt(d.get("paused_since")) if book.current else None
        return book

    # -------------------------------------------------------- server schedule
    def merge_server(
        self,
        infos: list[dict[str, Any]],
        titles: dict[str, str],
        window_start: datetime,
        now: datetime,
        local_tz: tzinfo,
    ) -> bool:
        """Take over the server's schedules; returns True when anything changed."""
        changed = False
        seen: set[str] = set()
        for info in infos:
            sid = info.get("schedule_id")
            start = parse_server_time(info.get("time_start"), local_tz)
            if sid is None or start is None:
                continue
            key = f"s{sid}"
            seen.add(key)
            uuid = info.get("spl_uuid")
            duration = info.get("duration")
            title = titles.get(uuid or "") or info.get("note") or (uuid[:8] if uuid else "Vorstellung")
            end = start + timedelta(seconds=duration if duration and duration > 0 else 0)
            show = self.shows.get(key)
            if show is None:
                self.shows[key] = Show(
                    key=key, spl_uuid=uuid, title=title, planned_start=start, planned_end=end,
                    schedule_id=sid, status=info.get("status"), loop_mode=info.get("loop_mode"),
                    loading_policy=info.get("loading_policy"), note=info.get("note"),
                )
                changed = True
                continue
            new = {
                "spl_uuid": uuid, "title": title, "planned_start": start, "planned_end": end,
                "status": info.get("status"), "loop_mode": info.get("loop_mode"),
                "loading_policy": info.get("loading_policy"), "note": info.get("note"),
            }
            for attr, value in new.items():
                if getattr(show, attr) != value:
                    setattr(show, attr, value)
                    changed = True
        # Planned shows that vanished from the server (TMS deleted them) and
        # have not happened yet are removed; history stays.
        for key in [k for k, s in self.shows.items() if k.startswith("s") and k not in seen]:
            show = self.shows[key]
            if not show.started and show.planned_start >= window_start and show.planned_start > now:
                del self.shows[key]
                changed = True
        return changed

    def update_titles(self, titles: dict[str, str]) -> bool:
        changed = False
        for show in self.shows.values():
            title = titles.get(show.spl_uuid or "")
            if title and not show.started and show.title != title:
                show.title = title
                changed = True
        return changed

    # ----------------------------------------------------- what really happens
    def track(
        self,
        now: datetime,
        spl_state: str | None,
        spl_uuid: str | None,
        remaining_s: int | None,
        title: str | None,
        external_remaining_s: float | None,
        pause_limit: timedelta,
    ) -> bool:
        """Follow the SPL status. Returns True when a sensor-visible value changed.

        spl_state: "playing" / "paused" / "stopped" / "unknown" (KLV StatusSPL).
        A show starts on "playing", ends on stop/eject (no SPL), or after the pause limit.
        """
        if spl_state is None or spl_state == "unknown":
            return False  # no reliable information this round
        changed = False
        loaded = spl_uuid if spl_state in ("playing", "paused") else None
        show = self.shows.get(self.current) if self.current else None

        if show and (loaded is None or loaded != show.spl_uuid):
            self._finish(show, now, SRC_ACTUAL)
            show = None
            changed = True

        if show is None and loaded and spl_state == "playing":
            show = self._begin(now, loaded, remaining_s, title)
            changed = True

        if show is None:
            return changed

        rem = timedelta(seconds=max(0, remaining_s or 0))
        if spl_state == "playing":
            if self.paused_since is not None:
                self.paused_since = None
                changed = True
            expected = now + rem
        else:  # paused: keep extending until the show is really over
            if self.paused_since is None:
                self.paused_since = now
                changed = True
            if now - self.paused_since >= pause_limit:
                show.limit_at = now
                self._finish(show, self.paused_since, SRC_PAUSE_LIMIT)
                return True
            expected = now + rem + timedelta(seconds=max(0.0, external_remaining_s or 0.0))
        expected = minute_ceil(expected)
        if show.expected_end != expected:
            show.expected_end = expected
            show.end_source = SRC_EXPECTED
            changed = True
        return changed

    def _begin(self, now: datetime, uuid: str, remaining_s: int | None, title: str | None) -> Show:
        best: Show | None = None
        for s in self.shows.values():
            if s.manual or s.started or s.spl_uuid != uuid:
                continue
            if abs(s.planned_start - now) <= MATCH_WINDOW and (
                best is None or abs(s.planned_start - now) < abs(best.planned_start - now)
            ):
                best = s
        expected = minute_ceil(now + timedelta(seconds=max(0, remaining_s or 0)))
        if best is None:
            key = f"m{int(now.timestamp())}"
            best = Show(
                key=key, spl_uuid=uuid, title=title or uuid[:8], planned_start=now,
                planned_end=expected, manual=True,
            )
            self.shows[key] = best
        best.actual_start = now
        best.actual_end = None
        best.expected_end = expected
        best.end_source = SRC_EXPECTED
        self.current = best.key
        self.paused_since = None
        return best

    def _finish(self, show: Show, end: datetime, source: str) -> None:
        expected = show.expected_end
        show.actual_end = max(end, show.start)
        show.end_source = source
        show.aborted = source == SRC_ACTUAL and expected is not None and end < expected - ABORT_MARGIN
        if source == SRC_ACTUAL:
            show.limit_at = None
        self.current = None
        self.paused_since = None

    # ------------------------------------------------------------- housekeeping
    def prune(self, now: datetime, keep: timedelta) -> bool:
        old = [k for k, s in self.shows.items() if k != self.current and s.end < now - keep]
        for k in old:
            del self.shows[k]
        return bool(old)

    # ------------------------------------------------------------------ queries
    def sorted_shows(self) -> list[Show]:
        return sorted(self.shows.values(), key=lambda s: (s.start, s.key))

    def between(self, start: datetime, end: datetime) -> list[Show]:
        return [s for s in self.sorted_shows() if s.start < end and s.end > start]

    @staticmethod
    def day_bounds(now: datetime, day_start_hour: int) -> tuple[datetime, datetime]:
        """Show day: from day_start_hour to day_start_hour next day (late shows count to the day before)."""
        start = now.replace(hour=day_start_hour, minute=0, second=0, microsecond=0)
        if now < start:
            start -= timedelta(days=1)
        return start, start + timedelta(days=1)

    def day_shows(self, now: datetime, day_start_hour: int) -> list[Show]:
        start, end = self.day_bounds(now, day_start_hour)
        return [s for s in self.sorted_shows() if start <= s.start < end]

    def first_of_day(self, now: datetime, day_start_hour: int) -> Show | None:
        shows = self.day_shows(now, day_start_hour)
        return shows[0] if shows else None

    def last_of_day(self, now: datetime, day_start_hour: int) -> Show | None:
        shows = self.day_shows(now, day_start_hour)
        return max(shows, key=lambda s: (s.start, s.key)) if shows else None

    def running_show(self) -> Show | None:
        return self.shows.get(self.current) if self.current else None

    def next_show(self, now: datetime) -> Show | None:
        for s in self.sorted_shows():
            if s.started or s.manual:
                continue
            if s.status in ("Success", "Failure", "Aborted"):
                continue
            if s.planned_start > now - NEXT_GRACE:
                return s
        return None

    def last_finished(self, now: datetime) -> Show | None:
        done = [
            s for s in self.shows.values()
            if s.finished or (s.status in ("Success", "Failure", "Aborted") and s.planned_start <= now)
        ]
        return max(done, key=lambda s: s.end) if done else None

    def calendar_event(self, now: datetime) -> Show | None:
        """Running show, otherwise the next one (calendar entity state)."""
        running = self.running_show()
        if running:
            return running
        upcoming = [s for s in self.sorted_shows() if s.end > now and not s.finished]
        return upcoming[0] if upcoming else None
