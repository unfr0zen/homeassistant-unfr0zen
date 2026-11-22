"""The schedule as calendar events, and events as setpoints."""

import datetime as dt
from zoneinfo import ZoneInfo

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.unfr0zen.schedule import (
    Setpoint,
    days_json,
    from_json,
    matches,
    occurrences,
    parse_days,
    parse_event,
    with_setpoint,
)

from .conftest import week

LONDON = ZoneInfo("Europe/London")


def at(*args):
    return dt.datetime(*args, tzinfo=LONDON)


def test_reads_heating_times_in_time_order():
    s = from_json(week(mon=[("22:00", 18), ("06:30", 20.5)]))
    assert s.mode == "heat"
    assert s.days[0] == (Setpoint(390, 20.5), Setpoint(1320, 18.0))
    assert s.days[1:] == ((),) * 6


def test_a_setpoint_lasts_until_the_next_one_across_midnight():
    s = from_json(week(mon=[("06:00", 20), ("22:00", 17)], tue=[("07:00", 21)]))
    # 2026-11-23 is a Monday.
    found = occurrences(s, at(2026, 11, 23, 21), at(2026, 11, 24, 8), LONDON)
    assert [(o.start, o.end, o.setpoint.temp) for o in found] == [
        (at(2026, 11, 23, 6), at(2026, 11, 23, 22), 20),
        (at(2026, 11, 23, 22), at(2026, 11, 24, 7), 17),
        (at(2026, 11, 24, 7), at(2026, 11, 30, 6), 21),
    ]


def test_a_lone_setpoint_lasts_the_whole_week():
    s = from_json(week(fri=[("08:00", 19)]))
    found = occurrences(s, at(2026, 11, 23), at(2026, 11, 24), LONDON)
    assert [(o.start, o.end) for o in found] == [(at(2026, 11, 20, 8), at(2026, 11, 27, 8))]


def test_an_empty_schedule_has_no_events():
    assert occurrences(from_json(week()), at(2026, 11, 23), at(2026, 11, 30), LONDON) == []


def test_setpoints_keep_their_local_time_across_a_clock_change():
    # The clocks go forward at 01:00 GMT on Sunday 2026-03-29.
    s = from_json(week(sat=[("22:00", 17)], sun=[("07:00", 20)]))
    found = occurrences(s, at(2026, 3, 28, 23), at(2026, 3, 29, 8), LONDON)
    night = found[0]
    assert (night.start, night.end) == (at(2026, 3, 28, 22), at(2026, 3, 29, 7))
    assert night.end.astimezone(dt.UTC) - night.start.astimezone(dt.UTC) == dt.timedelta(hours=8), "an hour lost"
    assert night.end.utcoffset() == dt.timedelta(hours=1)


def test_a_weekly_event_is_a_setpoint_on_each_of_its_days():
    days, point = parse_event(
        {
            "dtstart": at(2026, 11, 23, 6, 30),
            "dtend": at(2026, 11, 23, 7),
            "summary": "20.4 °C",
            "rrule": "FREQ=WEEKLY;BYDAY=MO,WE",
        },
        LONDON,
    )
    assert (days, point) == ([0, 2], Setpoint(390, 20.4))


def test_a_weekly_event_without_days_repeats_on_its_own_day():
    days, _ = parse_event({"dtstart": at(2026, 11, 28, 9), "summary": "19", "rrule": "FREQ=WEEKLY"}, LONDON)
    assert days == [5]


def test_the_start_is_read_in_the_local_zone():
    start = dt.datetime(2026, 11, 23, 23, 30, tzinfo=ZoneInfo("America/New_York"))
    days, point = parse_event({"dtstart": start, "summary": "18", "rrule": "FREQ=WEEKLY"}, LONDON)
    assert (days, point.minute) == ([1], 4 * 60 + 30), "Tuesday 04:30 in London"


@pytest.mark.parametrize(
    ("event", "message"),
    [
        ({"rrule": "FREQ=WEEKLY", "dtstart": dt.date(2026, 11, 23)}, "start time"),
        ({"rrule": ""}, "repeat weekly"),
        ({"rrule": "FREQ=DAILY"}, "repeat weekly"),
        ({"rrule": "FREQ=WEEKLY;COUNT=3"}, "repeat weekly"),
        ({"rrule": "FREQ=WEEKLY;INTERVAL=2"}, "repeat weekly"),
        ({"rrule": "FREQ=WEEKLY;BYDAY=XX"}, "Unknown day"),
        ({"rrule": "FREQ=WEEKLY", "summary": "warm"}, "temperature"),
        ({"rrule": "FREQ=WEEKLY", "summary": "40 °C"}, "outside"),
        ({"rrule": "FREQ=WEEKLY", "dtstart": at(2026, 11, 23, 6, 0, 30)}, "whole minute"),
    ],
)
def test_refuses_what_the_nest_cannot_hold(event, message):
    event = {"dtstart": at(2026, 11, 23, 6), "summary": "20 °C"} | event
    with pytest.raises(HomeAssistantError, match=message):
        parse_event(event, LONDON)


def test_a_day_holds_at_most_ten_setpoints():
    day = tuple(Setpoint(m * 60, 20) for m in range(10))
    assert with_setpoint(day, Setpoint(0, 18))[0] == Setpoint(0, 18), "replaces the one at its time"
    with pytest.raises(HomeAssistantError, match="10"):
        with_setpoint(day, Setpoint(23 * 60, 18))


def test_writes_only_the_named_days():
    assert days_json({4: (Setpoint(390, 20.5),), 6: ()}) == {"fri": [{"time": "06:30", "temp": 20.5}], "sun": []}


def test_a_range_title_is_a_low_and_a_high_each_with_its_sign():
    event = {"dtstart": dt.datetime(2026, 11, 23, 6, 30, tzinfo=ZoneInfo("UTC")), "rrule": "FREQ=WEEKLY"}
    tz = ZoneInfo("UTC")
    assert parse_event(event | {"summary": "19-24 °C"}, tz, mode="range")[1] == Setpoint(390, 19.0, 24.0)
    assert parse_event(event | {"summary": "18,5 – 25"}, tz, mode="range")[1] == Setpoint(390, 18.5, 25.0)  # noqa: RUF001
    with pytest.raises(HomeAssistantError, match="outside"):
        parse_event(event | {"summary": "-10–20 °C"}, tz, mode="range")  # noqa: RUF001
    with pytest.raises(HomeAssistantError, match="outside"):
        parse_event(event | {"summary": "-5 °C"}, tz)


def test_an_error_takes_a_placeholder_named_key():
    from custom_components.unfr0zen.errors import error

    assert str(error("z2m_nothing", key="heating_times", topic="zigbee2mqtt/Hallway")) == (
        "Zigbee2MQTT sent no heating_times for zigbee2mqtt/Hallway"
    )


def test_readback_checks_whole_fahrenheit_and_carried_midnight_values():
    old = from_json(week(mon=[("06:00", 18)], tue=[("00:00", 18)]))
    want = {0: (Setpoint(360, 20.5),)}
    actual = from_json(week(mon=[("06:00", 20.56)], tue=[("00:00", 20.56)]))
    assert matches(actual, old, want, (9, 32))
    wrong = from_json(week(mon=[("06:00", 20.55)], tue=[("00:00", 20.55)]))
    assert not matches(wrong, old, want, (9, 32))


def test_service_passes_fahrenheit_and_celsius_rounding_to_the_thermostat():
    current = from_json(week())
    change = parse_days({"mon": [{"time": "06:00", "temp": 20.8}]})
    assert change[0] == (Setpoint(360, 20.8),)
    for temp in (20.56, 21.0):
        assert matches(from_json(week(mon=[("06:00", temp)])), current, change, (9, 32), partial=True)
    assert not matches(from_json(week(mon=[("06:00", 21.11)])), current, change, (9, 32), partial=True)
