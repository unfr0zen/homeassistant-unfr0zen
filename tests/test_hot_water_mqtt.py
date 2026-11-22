"""The hot water calendar over nestlocal's MQTT topics."""

import asyncio
import json
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
    async_fire_time_changed,
)

from custom_components.unfr0zen.const import DOMAIN
from custom_components.unfr0zen.hotwater import from_json
from custom_components.unfr0zen.mqtt_backend import NestlocalHotWater

from .conftest import hot_water, week

BASE = "nestlocal/02AA01AC"
ENTITY = "calendar.nestlocal_02aa01ac_hot_water_schedule"
HEATING = week(mon=[("06:00", 20)])
HOT_WATER = hot_water(mon=[("06:00", "08:00"), ("23:00", "01:00")], wed=[("17:00", "19:00")])


async def setup(hass, retained):
    """Sets the entry up while the broker holds `retained` (topic: payload)."""
    await hass.config.async_set_time_zone("Europe/London")
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=f"nestlocal:{BASE}", title=BASE, data={"transport": "nestlocal", "base": BASE}
    )
    entry.add_to_hass(hass)
    task = hass.async_create_task(hass.config_entries.async_setup(entry.entry_id))
    while not task.done():
        for topic, payload in retained.items():
            async_fire_mqtt_message(hass, f"{BASE}/{topic}", json.dumps(payload), retain=True)
        await asyncio.sleep(0.01)
    assert await task
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


@pytest.fixture
async def calendar(hass: HomeAssistant, mqtt_mock):
    await setup(hass, {"heating_times": HEATING, "hot_water_times": HOT_WATER})
    return mqtt_mock


def answer_writes(hass, mqtt_mock, after):
    """The unit takes each write and republishes the week as `after`, with
    the days written as written."""

    async def publish(topic, payload, *args, **kwargs):
        if topic == f"{BASE}/set/hot_water_times":
            week = {**after, "days": {**after["days"], **json.loads(payload)}}
            hass.loop.call_soon(async_fire_mqtt_message, hass, f"{BASE}/hot_water_times", json.dumps(week))

    mqtt_mock.async_publish.side_effect = publish


def written(mqtt_mock):
    return [
        json.loads(c.args[1])
        for c in mqtt_mock.async_publish.call_args_list
        if c.args[0] == f"{BASE}/set/hot_water_times"
    ]


async def ws(hass, hass_ws_client, message):
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"entity_id": ENTITY} | message)
    return await client.receive_json()


async def test_shows_each_period_from_on_to_off(hass, calendar):
    result = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": ENTITY,
            "start_date_time": "2026-11-23T07:00:00+00:00",
            "end_date_time": "2026-11-24T00:30:00+00:00",
        },
        blocking=True,
        return_response=True,
    )
    assert [(e["start"], e["end"], e["summary"]) for e in result[ENTITY]["events"]] == [
        ("2026-11-23T06:00:00+00:00", "2026-11-23T08:00:00+00:00", "Hot water"),
        ("2026-11-23T23:00:00+00:00", "2026-11-24T01:00:00+00:00", "Hot water"),
    ]


async def test_creating_a_period_past_midnight_writes_its_days(hass, calendar, hass_ws_client):
    answer_writes(hass, calendar, hot_water(fri=[("22:30", "00:30")]))
    reply = await ws(
        hass,
        hass_ws_client,
        {
            "type": "calendar/event/create",
            "event": {
                "summary": "Bath",
                "dtstart": "2026-11-27T22:30:00+00:00",
                "dtend": "2026-11-28T00:30:00+00:00",
                "rrule": "FREQ=WEEKLY;BYDAY=FR,TU",
            },
        },
    )
    assert reply["success"], reply
    assert written(calendar) == [{"tue": [{"on": "22:30", "off": "00:30"}], "fri": [{"on": "22:30", "off": "00:30"}]}]


async def test_moving_a_period_moves_it(hass, calendar, hass_ws_client):
    answer_writes(hass, calendar, hot_water())
    reply = await ws(
        hass,
        hass_ws_client,
        {
            "type": "calendar/event/update",
            "uid": "hw-wed-17:00",
            "recurrence_id": "20261125T170000",
            "event": {
                "summary": "Hot water",
                "dtstart": "2026-11-25T18:00:00+00:00",
                "dtend": "2026-11-25T19:30:00+00:00",
                "rrule": "FREQ=WEEKLY",
            },
        },
    )
    assert reply["success"], reply
    assert written(calendar) == [{"wed": [{"on": "18:00", "off": "19:30"}]}]


async def test_deleting_a_period_removes_it(hass, calendar, hass_ws_client):
    answer_writes(hass, calendar, hot_water())
    reply = await ws(hass, hass_ws_client, {"type": "calendar/event/delete", "uid": "hw-mon-06:00"})
    assert reply["success"], reply
    assert written(calendar) == [{"mon": [{"on": "23:00", "off": "01:00"}]}]


async def test_an_overlapping_period_is_refused(hass, calendar, hass_ws_client):
    reply = await ws(
        hass,
        hass_ws_client,
        {
            "type": "calendar/event/create",
            "event": {
                "summary": "Hot water",
                "dtstart": "2026-11-23T07:00:00+00:00",
                "dtend": "2026-11-23T09:00:00+00:00",
                "rrule": "FREQ=WEEKLY",
            },
        },
    )
    assert not reply["success"]
    assert "overlap on mon at 07:00" in reply["error"]["message"]
    assert written(calendar) == []


async def test_is_on_while_a_period_runs(hass, calendar, freezer):
    freezer.move_to("2026-11-23T23:30:00+00:00")
    async_fire_mqtt_message(hass, f"{BASE}/hot_water_times", json.dumps(hot_water(mon=[("23:00", "01:00")])))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "on"
    freezer.move_to("2026-11-24T02:00:00+00:00")
    async_fire_mqtt_message(hass, f"{BASE}/hot_water_times", json.dumps(hot_water(mon=[("23:00", "01:30")])))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "off"


async def test_no_calendar_without_hot_water_until_it_appears(hass, mqtt_mock):
    entry = await setup(hass, {"heating_times": HEATING})
    assert hass.states.get(ENTITY) is None
    assert hass.states.get("calendar.nestlocal_02aa01ac_heating_schedule") is not None
    # The unit gains hot water control: the entry reloads with the calendar.
    async_fire_mqtt_message(hass, f"{BASE}/hot_water_times", json.dumps(HOT_WATER))
    # The broker replays both retained topics to the reloaded entry.
    for _ in range(100):
        if hass.states.get(ENTITY) is not None:
            break
        async_fire_mqtt_message(hass, f"{BASE}/heating_times", json.dumps(HEATING), retain=True)
        async_fire_mqtt_message(hass, f"{BASE}/hot_water_times", json.dumps(HOT_WATER), retain=True)
        await asyncio.sleep(0.01)
    assert hass.states.get(ENTITY) is not None
    assert entry.state is ConfigEntryState.LOADED


async def test_the_calendar_goes_when_hot_water_does(hass, calendar):
    assert hass.states.get(ENTITY) is not None
    async_fire_mqtt_message(hass, f"{BASE}/hot_water_times", "")
    for _ in range(100):
        await asyncio.sleep(0.01)
        async_fire_mqtt_message(hass, f"{BASE}/heating_times", json.dumps(HEATING), retain=True)
        state = hass.states.get(ENTITY)
        if state is None or state.state == "unavailable":
            break
    state = hass.states.get(ENTITY)
    assert state is None or state.state == "unavailable"
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.hot_water is None


async def test_between_periods_the_calendar_turns_on_at_the_next(hass, calendar, freezer):
    freezer.move_to("2026-11-25T12:00:00+00:00")
    async_fire_mqtt_message(hass, f"{BASE}/hot_water_times", json.dumps(hot_water(wed=[("17:00", "19:00")])))
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY)
    assert state.state == "off"
    assert state.attributes["start_time"] == "2026-11-25 17:00:00", "the next period"
    # No message from the unit: the calendar's own timer turns it on.
    freezer.move_to("2026-11-25T17:00:01+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "on"


async def test_hot_water_gone_before_the_watch_reloads(hass, mqtt_mock):
    b = NestlocalHotWater(hass, BASE)
    b.seen(from_json(HOT_WATER))
    # Setup decided "hot water"; an empty message came before it watched.
    b.seen(None)
    changed = MagicMock()
    await b.async_watch(True, changed)
    b.async_stop()
    changed.assert_called_once()


async def test_an_unchanged_presence_does_not_reload(hass, mqtt_mock):
    b = NestlocalHotWater(hass, BASE)
    b.seen(from_json(HOT_WATER))
    changed = MagicMock()
    await b.async_watch(True, changed)
    b.seen(from_json(hot_water()))
    b.async_stop()
    changed.assert_not_called()
