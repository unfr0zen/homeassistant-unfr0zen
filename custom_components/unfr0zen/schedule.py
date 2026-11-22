"""The Nest's weekly schedule and its calendar events.

A day holds setpoints: a time and the temperature the target changes to.
A setpoint holds until the next one, which may be days later. Days are
indexed from Monday (0), as `datetime.weekday()` counts them.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import re
from typing import Any

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
RRULE_DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
TEMP_RANGE = (4.5, 35.0)
MAX_PER_DAY = 10


@dataclass(frozen=True, order=True)
class Setpoint:
    """A change of target at `minute` after midnight to `temp` °C."""

    minute: int
    temp: float


Day = tuple[Setpoint, ...]


@dataclass(frozen=True)
class Schedule:
    """The week: its kind ("heat", "cool" or "range") and seven days."""

    mode: str
    days: tuple[Day, ...]

    @property
    def writable(self) -> bool:
        """Whether the unit takes setpoints for this kind of schedule."""
        return self.mode in ("heat", "cool")


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
    """`temp` in the unit's half degrees, or an error outside `limits`."""
    if not limits[0] <= temp <= limits[1]:
        raise ServiceValidationError(f"{temp:g} °C is outside {limits[0]:g}-{limits[1]:g} °C")
    return round(temp * 2) / 2


def parse_hhmm(value: Any) -> int | None:
    """ "HH:MM" as minutes after midnight; None if it is not that."""
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value) if isinstance(value, str) else None
    return int(match[1]) * 60 + int(match[2]) if match else None


def check_days(value: Any, example: str) -> list[tuple[int, list[Any]]]:
    """A service's days object, as (day index, entries): any of the seven."""
    if not isinstance(value, dict) or not value:
        raise ServiceValidationError(f"days must be an object such as {example}")
    out = []
    for name, entries in value.items():
        if name not in DAYS:
            raise ServiceValidationError(f"Unknown day {name!r}: use {', '.join(DAYS)}")
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            raise ServiceValidationError(f"{name} must be a list such as {example}")
        out.append((DAYS.index(name), entries))
    return out


def parse_days(value: Any, limits: tuple[float, float] = TEMP_RANGE) -> dict[int, Day]:
    """The days a set_schedule call names, each replaced whole."""
    out = {}
    for day, entries in check_days(value, '{"mon": [{"time": "06:30", "temp": 21}]}'):
        points: dict[int, float] = {}
        for entry in entries:
            minute, temp = parse_hhmm(entry.get("time")), entry.get("temp")
            if minute is None:
                raise ServiceValidationError(f"{DAYS[day]}: time {entry.get('time')!r} is not HH:MM")
            if isinstance(temp, bool) or not isinstance(temp, int | float):
                raise ServiceValidationError(f"{DAYS[day]} {hhmm(minute)}: temp {temp!r} is not a number")
            if minute in points:
                raise ServiceValidationError(f"{DAYS[day]}: two setpoints at {hhmm(minute)}")
            points[minute] = check_temp(float(temp), limits)
        if len(points) > MAX_PER_DAY:
            raise ServiceValidationError(f"The Nest takes at most {MAX_PER_DAY} setpoints a day")
        out[day] = tuple(Setpoint(m, c) for m, c in sorted(points.items()))
    return out


def week_json(schedule: Schedule) -> dict[str, list[dict[str, Any]]]:
    """The whole week, as the cards read it."""
    return days_json(dict(enumerate(schedule.days)))


def from_json(value: Any) -> Schedule:
    """The schedule from nestlocal's heating_times object."""
    days = value.get("days", {})
    week = []
    for name in DAYS:
        points = {}
        for p in days.get(name) or []:
            h, m = p["time"].split(":")
            points[int(h) * 60 + int(m)] = float(p["temp"])
        week.append(tuple(Setpoint(t, c) for t, c in sorted(points.items())))
    return Schedule(str(value.get("mode", "heat")).lower(), tuple(week))


def days_json(days: dict[int, Day]) -> dict[str, list[dict[str, Any]]]:
    """The days a heating_times write names, as nestlocal takes them."""
    return {DAYS[d]: [{"time": hhmm(p.minute), "temp": p.temp} for p in points] for d, points in sorted(days.items())}


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
    raise HomeAssistantError(f"No setpoint {value} in the schedule")


def summary(setpoint: Setpoint) -> str:
    """An event's title: the temperature."""
    return f"{setpoint.temp:g} °C"


def parse_event(
    event: dict[str, Any], tz: dt.tzinfo, limits: tuple[float, float] = TEMP_RANGE
) -> tuple[list[int], Setpoint]:
    """The days and setpoint a calendar event asks for.

    The event must repeat weekly; its end is ignored, as the next setpoint
    ends it.
    """
    start = event.get("dtstart")
    if not isinstance(start, dt.datetime):
        raise HomeAssistantError("A setpoint needs a start time, not a whole day")
    start = start.astimezone(tz) if start.tzinfo else start.replace(tzinfo=tz)
    if start.second or start.microsecond:
        raise HomeAssistantError("A setpoint starts on a whole minute")
    match = re.search(r"\d+(?:[.,]\d+)?", event.get("summary") or "")
    if match is None:
        raise HomeAssistantError('Name the event after its temperature, such as "20.5 °C"')
    temp = check_temp(float(match.group().replace(",", ".")), limits)
    days = weekly_days(event.get("rrule") or "", start.weekday())
    return days, Setpoint(start.hour * 60 + start.minute, temp)


def weekly_days(rrule: str, default: int) -> list[int]:
    """The days a weekly RRULE names; anything else is refused."""
    parts = dict(p.split("=", 1) for p in rrule.upper().split(";") if "=" in p)
    if (
        parts.get("FREQ") != "WEEKLY"
        or parts.get("INTERVAL", "1") != "1"
        or set(parts) - {"FREQ", "INTERVAL", "BYDAY", "WKST"}
    ):
        raise HomeAssistantError("The Nest's schedule repeats every week: make the event repeat weekly, with no end")
    if "BYDAY" not in parts:
        return [default]
    days = []
    for name in parts["BYDAY"].split(","):
        if name not in RRULE_DAYS:
            raise HomeAssistantError(f"Unknown day {name} in the event's repeat")
        days.append(RRULE_DAYS.index(name))
    return sorted(set(days))


def with_setpoint(day: Day, setpoint: Setpoint) -> Day:
    """`day` with `setpoint`, replacing any at its time."""
    points = tuple(sorted({p.minute: p for p in (*day, setpoint)}.values()))
    if len(points) > MAX_PER_DAY:
        raise HomeAssistantError(f"The Nest takes at most {MAX_PER_DAY} setpoints a day")
    return points


def without(day: Day, setpoint: Setpoint) -> Day:
    """`day` without the setpoint at `setpoint`'s time."""
    return tuple(p for p in day if p.minute != setpoint.minute)
