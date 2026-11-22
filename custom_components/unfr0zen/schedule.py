"""The Nest's weekly schedule and its calendar events.

A day holds setpoints: a time and the temperature the target changes to,
or in a range schedule (the Nest's heat-cool) the low and the high it
keeps between. A setpoint holds until the next one, which may be days
later. Days are indexed from Monday (0), as `datetime.weekday()` counts
them.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import math
import re
from typing import Any

from .errors import error, invalid

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
RRULE_DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
TEMP_RANGE = (4.5, 35.0)
MAX_PER_DAY = 10
# The least a range's high is above its low, °C, as the Nest keeps them.
RANGE_GAP = 1.5


@dataclass(frozen=True, order=True)
class Setpoint:
    """A change of target at `minute` after midnight to `temp` °C; in a
    range schedule `temp` is the low and `high` the high."""

    minute: int
    temp: float
    high: float | None = None


Day = tuple[Setpoint, ...]


@dataclass(frozen=True)
class Schedule:
    """The week: its kind ("heat", "cool" or "range") and seven days."""

    mode: str
    days: tuple[Day, ...]
    handle: bytes = b"nest"

    @property
    def writable(self) -> bool:
        """Whether the unit takes setpoints for this kind of schedule."""
        return self.mode in ("heat", "cool", "range")


@dataclass(frozen=True)
class Occurrence:
    """One setpoint on one date: from its time until the next setpoint."""

    day: int
    setpoint: Setpoint
    start: dt.datetime
    end: dt.datetime


def hhmm(minute: int) -> str:
    """A minute of the day as "HH:MM"."""
    return f"{minute // 60:02d}:{minute % 60:02d}"


def check_temp(temp: float, limits: tuple[float, float] = TEMP_RANGE) -> float:
    """Validate a Celsius input; the thermostat rounds to its display scale."""
    if not limits[0] <= temp <= limits[1]:
        raise invalid("temp_outside", temp=f"{temp:g}", min=f"{limits[0]:g}", max=f"{limits[1]:g}")
    return temp


def check_range(low: float, high: float, limits: tuple[float, float] = TEMP_RANGE) -> tuple[float, float]:
    """Check both Celsius setpoints and the minimum gap between them."""
    low, high = check_temp(low, limits), check_temp(high, limits)
    if high - low < RANGE_GAP - 1e-9:
        raise invalid("range_gap", low=f"{low:g}", high=f"{high:g}", gap=f"{RANGE_GAP:g}")
    return low, high


def parse_hhmm(value: Any) -> int | None:
    """ "HH:MM" as minutes after midnight; None if it is not that."""
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value) if isinstance(value, str) else None
    return int(match[1]) * 60 + int(match[2]) if match else None


def check_days(value: Any, example: str) -> list[tuple[int, list[Any]]]:
    """A service's days object, as (day index, entries): any of the seven."""
    if not isinstance(value, dict) or not value:
        raise invalid("days_object", example=example)
    out = []
    for name, entries in value.items():
        if name not in DAYS:
            raise invalid("unknown_day", day=repr(name), days=", ".join(DAYS))
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            raise invalid("day_list", day=name, example=example)
        out.append((DAYS.index(name), entries))
    return out


def parse_days(value: Any, limits: tuple[float, float] = TEMP_RANGE, mode: str = "heat") -> dict[int, Day]:
    """The days a set_schedule call names, each replaced whole: setpoints of
    a time and a temp, or for a range schedule a time, a low and a high."""
    example = (
        '{"mon": [{"time": "06:30", "low": 19, "high": 24}]}'
        if mode == "range"
        else '{"mon": [{"time": "06:30", "temp": 21}]}'
    )

    def number(day: int, minute: int, key: str, value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise invalid("not_a_number", day=DAYS[day], time=hhmm(minute), field=key, value=repr(value))
        return float(value)

    out = {}
    for day, entries in check_days(value, example):
        points: dict[int, Setpoint] = {}
        for entry in entries:
            minute = parse_hhmm(entry.get("time"))
            if minute is None:
                raise invalid("bad_time", day=DAYS[day], value=repr(entry.get("time")))
            if minute in points:
                raise invalid("two_setpoints", day=DAYS[day], time=hhmm(minute))
            if mode == "range":
                low, high = (number(day, minute, k, entry.get(k)) for k in ("low", "high"))
                points[minute] = Setpoint(minute, *check_range(low, high, limits))
            else:
                points[minute] = Setpoint(minute, check_temp(number(day, minute, "temp", entry.get("temp")), limits))
        if len(points) > MAX_PER_DAY:
            raise invalid("too_many_setpoints", max=MAX_PER_DAY)
        out[day] = tuple(points[m] for m in sorted(points))
    return out


def week_json(schedule: Schedule) -> dict[str, list[dict[str, Any]]]:
    """The whole week, as the cards read it."""
    return days_json(dict(enumerate(schedule.days)))


def from_json(value: Any) -> Schedule:
    """The schedule from nestlocal's heating_times object."""
    days = value.get("days", {})
    week = []
    for name in DAYS:
        points: dict[int, Setpoint] = {}
        for p in days.get(name) or []:
            h, m = p["time"].split(":")
            minute = int(h) * 60 + int(m)
            if "low" in p:
                points[minute] = Setpoint(minute, float(p["low"]), float(p["high"]))
            else:
                points[minute] = Setpoint(minute, float(p["temp"]))
        week.append(tuple(points[t] for t in sorted(points)))
    return Schedule(str(value.get("mode", "heat")).lower(), tuple(week))


def days_json(days: dict[int, Day]) -> dict[str, list[dict[str, Any]]]:
    """The days a heating_times write names, as nestlocal takes them."""
    return {DAYS[d]: [setpoint_json(p) for p in points] for d, points in sorted(days.items())}


def setpoint_json(setpoint: Setpoint) -> dict[str, Any]:
    """A setpoint as nestlocal and the cards have it."""
    if setpoint.high is not None:
        return {"time": hhmm(setpoint.minute), "low": setpoint.temp, "high": setpoint.high}
    return {"time": hhmm(setpoint.minute), "temp": setpoint.temp}


def matches(
    actual: Schedule, current: Schedule, changes: dict[int, Day], limits: tuple[float, float], *, partial: bool = False
) -> bool:
    """Readback can contain whole Fahrenheit setpoints and a carried midnight target on an empty day."""
    if (actual.mode, actual.handle) != (current.mode, current.handle):
        return False
    expected = [changes.get(d, day) for d, day in enumerate(current.days)]
    if not any(expected):
        return not any(actual.days)

    def stored(c: float) -> float:
        f = max(math.ceil(limits[0] * 1.8 + 32), min(math.floor(limits[1] * 1.8 + 32), math.floor(c * 1.8 + 32.5)))
        return round((f - 32) / 1.8, 2)

    def same(p: Setpoint, q: Setpoint) -> bool:
        def half(c: float) -> float:
            return math.floor(c * 2 + 0.5) / 2

        return p == q or any(
            q == Setpoint(p.minute, rounded(p.temp), rounded(p.high) if p.high is not None else None)
            for rounded in (stored, half)
        )

    for d, wanted in enumerate(expected):
        if partial and d not in changes:
            continue
        got = actual.days[d]
        if len(got) == len(wanted) and all(same(p, q) for p, q in zip(wanted, got, strict=True)):
            continue
        # Changing the preceding day's final target can update an inherited midnight.
        if (
            (not wanted or (d not in changes and len(wanted) == 1 and wanted[0].minute == 0))
            and len(got) == 1
            and got[0].minute == 0
        ):
            previous = next(
                (
                    day[-1]
                    for n in range(1, 8)
                    if (day := expected[(d - n) % 7]) and (len(day) > 1 or day[0].minute != 0 or (d - n) % 7 in changes)
                ),
                None,
            )
            if previous and same(Setpoint(0, previous.temp, previous.high), got[0]):
                continue
        return False
    return True


def occurrences(schedule: Schedule, start: dt.datetime, end: dt.datetime, tz: dt.tzinfo) -> list[Occurrence]:
    """Every setpoint in effect between `start` and `end`, by local time."""
    flat = [(d, p) for d in range(7) for p in schedule.days[d]]
    if not flat:
        return []
    first = start.astimezone(tz).date() - dt.timedelta(days=7)
    last = end.astimezone(tz).date()
    out = []
    date = first
    while date <= last:
        for i, p in enumerate(schedule.days[date.weekday()]):
            begin = _at(date, p.minute, tz)
            finish = _next_start(schedule, date, i, tz)
            if finish > start and begin < end:
                out.append(Occurrence(date.weekday(), p, begin, finish))
        date += dt.timedelta(days=1)
    return out


def _at(date: dt.date, minute: int, tz: dt.tzinfo) -> dt.datetime:
    return dt.datetime.combine(date, dt.time(minute // 60, minute % 60), tz)


def _next_start(schedule: Schedule, date: dt.date, index: int, tz: dt.tzinfo) -> dt.datetime:
    """When the setpoint after day `date`'s `index`th starts."""
    points = schedule.days[date.weekday()]
    if index + 1 < len(points):
        return _at(date, points[index + 1].minute, tz)
    for ahead in range(1, 8):
        later = date + dt.timedelta(days=ahead)
        if points := schedule.days[later.weekday()]:
            return _at(later, points[0].minute, tz)
    raise AssertionError("unreachable: the day itself has a setpoint")


def uid(day: int, setpoint: Setpoint) -> str:
    """The event series of one setpoint: its day and time."""
    return f"{DAYS[day]}-{hhmm(setpoint.minute)}"


def from_uid(schedule: Schedule, value: str) -> tuple[int, Setpoint]:
    """The setpoint an event `uid` names."""
    name, _, time = value.partition("-")
    if name in DAYS:
        day = DAYS.index(name)
        for p in schedule.days[day]:
            if hhmm(p.minute) == time:
                return day, p
    raise error("no_setpoint", uid=value)


def summary(setpoint: Setpoint) -> str:
    """An event's title: the temperature, or a range's low and high."""
    if setpoint.high is not None:
        return f"{setpoint.temp:g}–{setpoint.high:g} °C"  # noqa: RUF001
    return f"{setpoint.temp:g} °C"


def parse_event(
    event: dict[str, Any], tz: dt.tzinfo, limits: tuple[float, float] = TEMP_RANGE, mode: str = "heat"
) -> tuple[list[int], Setpoint]:
    """The days and setpoint a calendar event asks for: its title the
    temperature, or for a range schedule the low and the high ("19-24 °C").

    The event must repeat weekly; its end is ignored, as the next setpoint
    ends it.
    """
    start = event.get("dtstart")
    if not isinstance(start, dt.datetime):
        raise error("setpoint_whole_day")
    start = start.astimezone(tz) if start.tzinfo else start.replace(tzinfo=tz)
    if start.second or start.microsecond:
        raise error("setpoint_whole_minute")
    title = event.get("summary") or ""
    days = weekly_days(event.get("rrule") or "", start.weekday())
    minute = start.hour * 60 + start.minute
    if mode == "range":
        # A low, a dash and a high, each with its own sign: "19-24 °C", "-1 - 5".
        match = re.match(rf"\s*({_NUMBER})\s*[-–—~]\s*({_NUMBER})", title)  # noqa: RUF001
        if match is None:
            raise error("title_range")
        low, high = (float(n.replace(",", ".")) for n in match.groups())
        return days, Setpoint(minute, *check_range(low, high, limits))
    match = re.search(_NUMBER, title)
    if match is None:
        raise error("title_temp")
    return days, Setpoint(minute, check_temp(float(match[0].replace(",", ".")), limits))


# A temperature in an event's title: a sign, digits, and a decimal point or comma.
_NUMBER = r"-?\d+(?:[.,]\d+)?"


def weekly_days(rrule: str, default: int) -> list[int]:
    """The days a weekly RRULE names; anything else is refused."""
    parts = dict(p.split("=", 1) for p in rrule.upper().split(";") if "=" in p)
    if (
        parts.get("FREQ") != "WEEKLY"
        or parts.get("INTERVAL", "1") != "1"
        or set(parts) - {"FREQ", "INTERVAL", "BYDAY", "WKST"}
    ):
        raise error("weekly_only")
    if "BYDAY" not in parts:
        return [default]
    days = []
    for name in parts["BYDAY"].split(","):
        if name not in RRULE_DAYS:
            raise error("unknown_repeat_day", day=name)
        days.append(RRULE_DAYS.index(name))
    return sorted(set(days))


def with_setpoint(day: Day, setpoint: Setpoint) -> Day:
    """`day` with `setpoint`, replacing any at its time."""
    points = tuple(sorted({p.minute: p for p in (*day, setpoint)}.values()))
    if len(points) > MAX_PER_DAY:
        raise error("too_many_setpoints", max=MAX_PER_DAY)
    return points


def without(day: Day, setpoint: Setpoint) -> Day:
    """`day` without the setpoint at `setpoint`'s time."""
    return tuple(p for p in day if p.minute != setpoint.minute)
