"""The Nest's hot water schedule and its calendar events.

A day holds on periods, from an "on" time to an "off" time; an off before
its on runs past midnight into the next day. Days are indexed from Monday
(0), as `datetime.weekday()` counts them.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
from itertools import pairwise
from typing import Any

from .errors import error, invalid
from .schedule import DAYS, check_days, hhmm, parse_hhmm, weekly_days

MAX_PER_DAY = 6
DAY_MINUTES = 24 * 60
SUMMARY = "Hot water"


@dataclass(frozen=True, order=True)
class Period:
    """Hot water on from `on` to `off`, minutes after midnight."""

    on: int
    off: int

    @property
    def end(self) -> int:
        """When it goes off, counting past midnight into the next day."""
        return self.off + DAY_MINUTES if self.off < self.on else self.off


Periods = tuple[Period, ...]


@dataclass(frozen=True)
class HotWater:
    """The week's on periods, seven days."""

    days: tuple[Periods, ...]


@dataclass(frozen=True)
class Occurrence:
    """One period on one date."""

    day: int
    period: Period
    start: dt.datetime
    end: dt.datetime


def _minutes(text: str) -> int:
    h, m = text.split(":")
    if not (0 <= int(h) < 24 and 0 <= int(m) < 60):
        raise ValueError(text)
    return int(h) * 60 + int(m)


def from_json(value: Any) -> HotWater | None:
    """The schedule from nestlocal's hot_water_times object; None without one."""
    if not isinstance(value, dict) or not isinstance(value.get("days"), dict):
        return None
    days = value["days"]
    return HotWater(
        tuple(
            tuple(sorted(Period(_minutes(p["on"]), _minutes(p["off"])) for p in days.get(name) or [])) for name in DAYS
        )
    )


def from_text(text: str) -> Periods:
    """A day as Zigbee carries it: "06:00-08:00,17:00-20:00"."""
    periods = []
    for part in filter(None, (p.strip() for p in text.split(","))):
        on, off = part.split("-")
        periods.append(Period(_minutes(on), _minutes(off)))
    return tuple(sorted(periods))


def to_text(day: Periods) -> str:
    """A day as Zigbee takes it."""
    return ",".join(f"{hhmm(p.on)}-{hhmm(p.off)}" for p in day)


def days_json(days: dict[int, Periods]) -> dict[str, list[dict[str, str]]]:
    """The days a hot_water_times write names, as nestlocal takes them."""
    return {DAYS[d]: [{"on": hhmm(p.on), "off": hhmm(p.off)} for p in day] for d, day in sorted(days.items())}


def check_day(day: int, periods: Periods) -> Periods:
    """`periods`, sorted, or an error if the Nest can't take them."""
    periods = tuple(sorted(periods))
    if len(periods) > MAX_PER_DAY:
        raise invalid("too_many_periods", max=MAX_PER_DAY)
    for a, b in pairwise(periods):
        if a.end > b.on:
            raise invalid("periods_overlap", day=DAYS[day], time=hhmm(b.on))
    return periods


def check_week(days: tuple[Periods, ...] | list[Periods]) -> None:
    """Errors if a day's period past midnight runs into the next day's first."""
    for d in range(7):
        after = days[(d + 1) % 7]
        if days[d] and after and days[d][-1].end - DAY_MINUTES > after[0].on:
            raise invalid("periods_overlap", day=DAYS[(d + 1) % 7], time=hhmm(after[0].on))


def parse_days(value: Any) -> dict[int, Periods]:
    """The days a set_hot_water_schedule call names, each replaced whole."""
    out = {}
    for day, entries in check_days(value, '{"mon": [{"on": "06:00", "off": "08:00"}]}'):
        periods = []
        for entry in entries:
            on, off = parse_hhmm(entry.get("on")), parse_hhmm(entry.get("off"))
            if on is None or off is None:
                raise invalid("period_times", day=DAYS[day], entry=repr(entry))
            if on == off:
                raise invalid("period_empty", day=DAYS[day], time=hhmm(on))
            periods.append(Period(on, off))
        out[day] = check_day(day, tuple(periods))
    return out


def week_json(hot_water: HotWater) -> dict[str, list[dict[str, str]]]:
    """The whole week, as the cards read it."""
    return days_json(dict(enumerate(hot_water.days)))


ONE_MINUTE = dt.timedelta(minutes=1)


def occurrences(hot_water: HotWater, start: dt.datetime, end: dt.datetime, tz: dt.tzinfo) -> list[Occurrence]:
    """Every period that overlaps `start` to `end`, by local time."""
    out = []
    date = start.astimezone(tz).date() - dt.timedelta(days=1)
    last = end.astimezone(tz).date()
    while date <= last:
        for p in hot_water.days[date.weekday()]:
            begin = _at(date, p.on, tz)
            finish = _at(date + dt.timedelta(days=p.end // DAY_MINUTES), p.off, tz)
            if finish > start and begin < end and finish > begin:
                out.append(Occurrence(date.weekday(), p, begin, finish))
        date += dt.timedelta(days=1)
    return out


def _at(date: dt.date, minute: int, tz: dt.tzinfo) -> dt.datetime:
    """The local time on `date`; one the clocks skip comes when they jump
    past it."""
    local = dt.datetime.combine(date, dt.time(minute // 60, minute % 60), tz)
    instant = local.astimezone(dt.UTC)
    if instant.astimezone(tz).replace(tzinfo=None) == local.replace(tzinfo=None):
        return local
    after = instant.astimezone(tz).utcoffset()
    while (instant - ONE_MINUTE).astimezone(tz).utcoffset() == after:
        instant -= ONE_MINUTE
    return instant.astimezone(tz)


def uid(day: int, period: Period) -> str:
    """The event series of one period: its day and on time."""
    return f"hw-{DAYS[day]}-{hhmm(period.on)}"


def from_uid(hot_water: HotWater, value: str) -> tuple[int, Period]:
    """The period an event `uid` names."""
    _, name, time = [*value.split("-", 2), "", ""][:3]
    if name in DAYS:
        day = DAYS.index(name)
        for p in hot_water.days[day]:
            if hhmm(p.on) == time:
                return day, p
    raise error("no_period", uid=value)


def parse_event(event: dict[str, Any], tz: dt.tzinfo) -> tuple[list[int], Period]:
    """The days and period a calendar event asks for: it must repeat
    weekly, and last more than nothing and at most a day."""
    start, end = event.get("dtstart"), event.get("dtend")
    if not isinstance(start, dt.datetime) or not isinstance(end, dt.datetime):
        raise error("period_whole_days")
    start, end = (t.astimezone(tz) if t.tzinfo else t.replace(tzinfo=tz) for t in (start, end))
    if any(t.second or t.microsecond for t in (start, end)):
        raise error("period_whole_minutes")
    # By the clock on the wall, which the unit keeps: the same day and later,
    # or the next day and earlier, whatever the clocks change by.
    on, off = start.hour * 60 + start.minute, end.hour * 60 + end.minute
    days = (end.date() - start.date()).days
    if not ((days == 0 and off > on) or (days == 1 and off < on)):
        raise error("period_order")
    return weekly_days(event.get("rrule") or "", start.weekday()), Period(on, off)


def with_period(day: int, periods: Periods, period: Period) -> Periods:
    """`periods` with `period` added."""
    return check_day(day, (*periods, period))


def without(periods: Periods, period: Period) -> Periods:
    """`periods` without the one starting at `period`'s on time."""
    return tuple(p for p in periods if p.on != period.on)
