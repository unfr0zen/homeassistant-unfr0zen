"""The hot water schedule as calendar events, and events as periods."""

import datetime as dt
from zoneinfo import ZoneInfo

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.unfr0zen.hotwater import (
    HotWater,
    Period,
    check_day,
    days_json,
    from_json,
    from_text,
    from_uid,
    occurrences,
    parse_event,
    to_text,
    uid,
    with_period,
)

from .conftest import hot_water

LONDON = ZoneInfo("Europe/London")


def at(*args):
    return dt.datetime(*args, tzinfo=LONDON)


def test_reads_hot_water_times_in_on_order():
    hw = from_json(hot_water(mon=[("17:00", "20:00"), ("06:00", "08:00")]))
    assert hw.days[0] == (Period(360, 480), Period(1020, 1200))
    assert hw.days[1:] == ((),) * 6


def test_no_schedule_is_no_hot_water():
    assert from_json(None) is None
    assert from_json({}) is None


def test_zigbee_text_round_trips():
    day = from_text("06:00-08:00,23:00-01:00")
    assert day == (Period(360, 480), Period(1380, 60))
    assert to_text(day) == "06:00-08:00,23:00-01:00"
    assert from_text("") == ()


def test_a_period_past_midnight_ends_the_next_day():
    # 2026-11-23 is a Monday.
    hw = from_json(hot_water(sun=[("23:00", "01:00")], mon=[("06:00", "08:00")]))
    found = occurrences(hw, at(2026, 11, 23, 0, 30), at(2026, 11, 23, 7), LONDON)
    assert [(o.start, o.end) for o in found] == [
        (at(2026, 11, 22, 23), at(2026, 11, 23, 1)),
        (at(2026, 11, 23, 6), at(2026, 11, 23, 8)),
    ]


def test_a_weekly_event_is_a_period_on_each_of_its_days():
    days, period = parse_event(
        {
            "dtstart": at(2026, 11, 23, 23, 0),
            "dtend": at(2026, 11, 24, 1, 0),
            "summary": "anything",
            "rrule": "FREQ=WEEKLY;BYDAY=MO,FR",
        },
        LONDON,
    )
    assert (days, period) == ([0, 4], Period(1380, 60))


@pytest.mark.parametrize(
    ("event", "message"),
    [
        ({"rrule": ""}, "repeat weekly"),
        ({"rrule": "FREQ=WEEKLY;UNTIL=20270101"}, "repeat weekly"),
        ({"dtend": at(2026, 11, 23, 6)}, "ends after it starts"),
        ({"dtend": at(2026, 11, 24, 6)}, "ends after it starts"),
        ({"dtend": at(2026, 11, 24, 7)}, "ends after it starts"),
        ({"dtstart": dt.date(2026, 11, 23)}, "not whole days"),
        ({"dtend": at(2026, 11, 23, 7, 0, 5)}, "whole minutes"),
    ],
)
def test_refuses_what_the_nest_cannot_hold(event, message):
    event = {"dtstart": at(2026, 11, 23, 6), "dtend": at(2026, 11, 23, 8), "rrule": "FREQ=WEEKLY"} | event
    with pytest.raises(HomeAssistantError, match=message):
        parse_event(event, LONDON)


def test_a_day_holds_at_most_six_periods_none_overlapping():
    day = tuple(Period(h * 60, h * 60 + 30) for h in range(6))
    with pytest.raises(HomeAssistantError, match="at most 6"):
        with_period(0, day, Period(1200, 1230))
    with pytest.raises(HomeAssistantError, match="overlap on mon at 06:30"):
        with_period(0, (Period(360, 420),), Period(390, 450))
    with pytest.raises(HomeAssistantError, match="overlap"):
        check_day(0, (Period(1320, 120), Period(1380, 1410))), "only the last may run past midnight"
    assert with_period(0, (Period(1020, 1200),), Period(360, 480)) == (Period(360, 480), Period(1020, 1200))


def test_uids_name_the_day_and_on_time():
    hw = from_json(hot_water(tue=[("06:00", "08:00")]))
    assert uid(1, Period(360, 480)) == "hw-tue-06:00"
    assert from_uid(hw, "hw-tue-06:00") == (1, Period(360, 480))
    with pytest.raises(HomeAssistantError):
        from_uid(hw, "hw-tue-07:00")


def test_writes_only_the_named_days():
    assert days_json({0: (Period(1380, 60),), 6: ()}) == {"mon": [{"on": "23:00", "off": "01:00"}], "sun": []}


def test_the_span_is_read_by_the_clock_on_the_wall():
    # The clocks go forward at 01:00 GMT on Sunday 2026-03-29: Saturday 02:00
    # to Sunday 02:30 is under a day elapsed but over a day on the wall.
    with pytest.raises(HomeAssistantError, match="within a day"):
        parse_event({"dtstart": at(2026, 3, 28, 2), "dtend": at(2026, 3, 29, 2, 30), "rrule": "FREQ=WEEKLY"}, LONDON)
    # Saturday 23:00 to Sunday 03:00 is three hours elapsed, four on the wall.
    _, period = parse_event(
        {"dtstart": at(2026, 3, 28, 23), "dtend": at(2026, 3, 29, 3), "rrule": "FREQ=WEEKLY"}, LONDON
    )
    assert period == Period(1380, 180)


def test_a_period_in_the_hour_the_clocks_skip_still_ends_after_it_starts():
    # Sunday 28 March 2027: 01:00 to 02:00 does not happen.
    week = HotWater(((),) * 6 + ((Period(90, 135),),))
    start = dt.datetime(2027, 3, 28, tzinfo=LONDON)
    [o] = occurrences(week, start, start + dt.timedelta(days=1), LONDON)
    assert o.end > o.start
    # On as the clocks go forward, at 02:00 BST.
    assert (o.start.hour, o.start.minute, o.end.hour, o.end.minute) == (2, 0, 2, 15)
