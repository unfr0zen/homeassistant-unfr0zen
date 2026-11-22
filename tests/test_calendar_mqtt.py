"""The calendar over nestlocal's MQTT topics, through Home Assistant's API."""

import asyncio
import json
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message

from custom_components.unfr0zen.const import DOMAIN

from .conftest import week

BASE = "nestlocal/02AA01AC"
ENTITY = "calendar.nestlocal_02aa01ac_heating_schedule"


@pytest.fixture
async def calendar(hass: HomeAssistant, mqtt_mock):
    """A Nest that heats Monday 06:00-22:00 and Tuesday from 07:00."""
    await hass.config.async_set_time_zone("Europe/London")
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=f"nestlocal:{BASE}", title=BASE, data={"transport": "nestlocal", "base": BASE}
    )
    entry.add_to_hass(hass)
    setup = hass.async_create_task(hass.config_entries.async_setup(entry.entry_id))
    await retained_on_subscribe(
        hass, setup, f"{BASE}/heating_times", week(mon=[("06:00", 20), ("22:00", 17)], tue=[("07:00", 21)])
    )
    assert entry.state is ConfigEntryState.LOADED
    return mqtt_mock


async def retained_on_subscribe(hass, setup, topic, payload):
    """Sends `topic` as the broker would a retained message, once subscribed."""
    while not setup.done():
        async_fire_mqtt_message(hass, topic, json.dumps(payload), retain=True)
        await asyncio.sleep(0.01)
    assert await setup
    await hass.async_block_till_done()


def answer_writes(hass, mqtt_mock, after):
    """The unit takes each write and republishes the week as `after`, with
    the days written as written."""

    async def publish(topic, payload, *args, **kwargs):
        if topic == f"{BASE}/set/heating_times":
            week = {**after, "days": {**after["days"], **json.loads(payload)}}
            hass.loop.call_soon(async_fire_mqtt_message, hass, f"{BASE}/heating_times", json.dumps(week))

    mqtt_mock.async_publish.side_effect = publish


def written(mqtt_mock):
    return [
        json.loads(c.args[1])
        for c in mqtt_mock.async_publish.call_args_list
        if c.args[0].endswith("/set/heating_times")
    ]


async def events(hass, start, end):
    result = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": ENTITY, "start_date_time": start, "end_date_time": end},
        blocking=True,
        return_response=True,
    )
    return [(e["start"], e["end"], e["summary"]) for e in result[ENTITY]["events"]]


async def test_the_calendar_is_on_the_units_own_device(hass: HomeAssistant, mqtt_mock):
    # The unit's device, as MQTT discovery made it.
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    nest = devices.async_get_or_create(
        config_entry_id=mqtt_entry.entry_id, identifiers={("mqtt", "nestlocal_02aa01ac")}, name="Nest Thermostat"
    )
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=f"nestlocal:{BASE}", title=BASE, data={"transport": "nestlocal", "base": BASE}
    )
    entry.add_to_hass(hass)
    setup = hass.async_create_task(hass.config_entries.async_setup(entry.entry_id))
    await retained_on_subscribe(hass, setup, f"{BASE}/heating_times", week())
    assert er.async_get(hass).async_get("calendar.nest_thermostat_heating_schedule").device_id == nest.id
    assert dr.async_entries_for_config_entry(devices, entry.entry_id) == [], "no device of its own"


async def test_shows_each_setpoint_until_the_next(hass, calendar):
    assert await events(hass, "2026-11-23T21:00:00+00:00", "2026-11-24T08:00:00+00:00") == [
        ("2026-11-23T06:00:00+00:00", "2026-11-23T22:00:00+00:00", "20 °C"),
        ("2026-11-23T22:00:00+00:00", "2026-11-24T07:00:00+00:00", "17 °C"),
        ("2026-11-24T07:00:00+00:00", "2026-11-30T06:00:00+00:00", "21 °C"),
    ]


async def test_follows_a_schedule_changed_on_the_unit(hass, calendar):
    async_fire_mqtt_message(hass, f"{BASE}/heating_times", json.dumps(week(wed=[("08:00", 19)])))
    await hass.async_block_till_done()
    assert [e[2] for e in await events(hass, "2026-11-25T09:00:00+00:00", "2026-11-25T10:00:00+00:00")] == ["19 °C"]


async def test_creating_a_weekly_event_writes_its_days(hass, calendar, hass_ws_client):
    answer_writes(hass, calendar, week(mon=[("06:00", 20), ("18:30", 21.5), ("22:00", 17)]))
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "calendar/event/create",
            "entity_id": ENTITY,
            "event": {
                "summary": "21.5 °C",
                "dtstart": "2026-11-23T18:30:00+00:00",
                "dtend": "2026-11-23T19:00:00+00:00",
                "rrule": "FREQ=WEEKLY;BYDAY=MO,SA",
            },
        }
    )
    assert (await client.receive_json())["success"]
    assert written(calendar) == [
        {
            "mon": [{"time": "06:00", "temp": 20.0}, {"time": "18:30", "temp": 21.5}, {"time": "22:00", "temp": 17.0}],
            "sat": [{"time": "18:30", "temp": 21.5}],
        }
    ]


async def test_moving_an_event_moves_its_setpoint(hass, calendar, hass_ws_client):
    answer_writes(hass, calendar, week(mon=[("06:00", 20)], tue=[("07:00", 21)], wed=[("23:00", 16)]))
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "calendar/event/update",
            "entity_id": ENTITY,
            "uid": "mon-22:00",
            "recurrence_id": "20261123T220000",
            "event": {
                "summary": "16",
                "dtstart": "2026-11-25T23:00:00+00:00",
                "dtend": "2026-11-26T00:00:00+00:00",
                "rrule": "FREQ=WEEKLY",
            },
        }
    )
    assert (await client.receive_json())["success"]
    assert written(calendar) == [{"mon": [{"time": "06:00", "temp": 20.0}], "wed": [{"time": "23:00", "temp": 16.0}]}]


async def test_deleting_an_event_removes_its_setpoint(hass, calendar, hass_ws_client):
    answer_writes(hass, calendar, week(mon=[("06:00", 20), ("22:00", 17)]))
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "calendar/event/delete", "entity_id": ENTITY, "uid": "tue-07:00"})
    assert (await client.receive_json())["success"]
    assert written(calendar) == [{"tue": []}]


async def test_a_one_off_event_is_refused(hass, calendar, hass_ws_client):
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "calendar/event/create",
            "entity_id": ENTITY,
            "event": {"summary": "20", "dtstart": "2026-11-23T18:30:00+00:00", "dtend": "2026-11-23T19:00:00+00:00"},
        }
    )
    reply = await client.receive_json()
    assert not reply["success"]
    assert "repeat weekly" in reply["error"]["message"]
    assert written(calendar) == []


async def test_a_write_the_unit_ignores_fails(hass, calendar, hass_ws_client):
    client = await hass_ws_client(hass)
    with patch("custom_components.unfr0zen.mqtt_backend.WRITE_TIMEOUT", 0.05):
        await client.send_json_auto_id({"type": "calendar/event/delete", "entity_id": ENTITY, "uid": "tue-07:00"})
        reply = await client.receive_json()
    assert not reply["success"]
    assert "did not take" in reply["error"]["message"]


async def test_a_change_without_the_written_days_does_not_complete_a_write(hass, calendar, hass_ws_client):
    # A change made elsewhere arrives while the unit refuses the write.
    other = week(mon=[("06:00", 20), ("22:00", 17)], tue=[("07:00", 21)], wed=[("08:00", 19)])

    async def publish(topic, payload, *args, **kwargs):
        if topic == f"{BASE}/set/heating_times":
            hass.loop.call_soon(async_fire_mqtt_message, hass, f"{BASE}/heating_times", json.dumps(other))

    calendar.async_publish.side_effect = publish
    client = await hass_ws_client(hass)
    with patch("custom_components.unfr0zen.mqtt_backend.WRITE_TIMEOUT", 0.05):
        await client.send_json_auto_id({"type": "calendar/event/delete", "entity_id": ENTITY, "uid": "tue-07:00"})
        reply = await client.receive_json()
    assert not reply["success"]
    assert "did not take" in reply["error"]["message"]


async def test_a_range_schedule_is_read_only(hass, calendar, hass_ws_client):
    async_fire_mqtt_message(hass, f"{BASE}/heating_times", json.dumps(week(mode="range")))
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "calendar/event/create",
            "entity_id": ENTITY,
            "event": {
                "summary": "20",
                "dtstart": "2026-11-23T18:30:00+00:00",
                "dtend": "2026-11-23T19:00:00+00:00",
                "rrule": "FREQ=WEEKLY",
            },
        }
    )
    reply = await client.receive_json()
    assert not reply["success"]
    assert "range" in reply["error"]["message"]
