"""The hot water calendar through Zigbee2MQTT."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message

from custom_components.unfr0zen.const import DOMAIN
from custom_components.unfr0zen.mqtt_backend import Z2mHotWater

from .conftest import hot_water, week

DEVICE = "zigbee2mqtt/Hallway"
IEEE = "0x00124b0012345678"
ENTITY = "calendar.hallway_hot_water_schedule"


def devices(*clusters):
    """bridge/devices listing the unit with `clusters` as endpoint 1's inputs."""
    return [
        {"friendly_name": "Coordinator", "ieee_address": "0x0", "endpoints": {}},
        {
            "friendly_name": "Hallway",
            "ieee_address": IEEE,
            "endpoints": {"1": {"clusters": {"input": ["genBasic", "hvacThermostat", *clusters], "output": []}}},
        },
    ]


async def setup(hass, mqtt_mock, listed, state):
    """Sets the entry up; Zigbee2MQTT answers each get and set from `state`."""
    await hass.config.async_set_time_zone("Europe/London")
    sent = []

    async def publish(topic, payload, *args, **kwargs):
        if topic not in (f"{DEVICE}/get", f"{DEVICE}/set"):
            return
        sent.append((topic, json.loads(payload)))
        if topic == f"{DEVICE}/set" and "hot_water_times" in sent[-1][1]:
            state["hot_water_times"] = hot_water(sat=[("09:00", "10:00")])
        hass.loop.call_soon(async_fire_mqtt_message, hass, DEVICE, json.dumps(state))

    mqtt_mock.async_publish.side_effect = publish
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"zigbee2mqtt:{IEEE}",
        title="Hallway",
        data={"transport": "zigbee2mqtt", "base": "zigbee2mqtt", "friendly_name": "Hallway", "ieee": IEEE},
    )
    entry.add_to_hass(hass)
    task = hass.async_create_task(hass.config_entries.async_setup(entry.entry_id))
    while not task.done():
        async_fire_mqtt_message(hass, "zigbee2mqtt/bridge/devices", json.dumps(listed), retain=True)
        await asyncio.sleep(0.01)
    assert await task
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry, sent


async def test_reads_and_writes_hot_water_times(hass, mqtt_mock, hass_ws_client):
    state = {"heating_times": week(), "hot_water_times": hot_water(mon=[("06:00", "08:00")])}
    entry, sent = await setup(hass, mqtt_mock, devices("64513"), state)
    assert entry.runtime_data.hot_water.data.days[0][0].on == 360
    assert (f"{DEVICE}/get", {"hot_water_times": ""}) in sent

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "calendar/event/create",
            "entity_id": ENTITY,
            "event": {
                "summary": "Hot water",
                "dtstart": "2026-11-28T09:00:00+00:00",
                "dtend": "2026-11-28T10:00:00+00:00",
                "rrule": "FREQ=WEEKLY;BYDAY=SA",
            },
        }
    )
    assert (await client.receive_json())["success"]
    assert (f"{DEVICE}/set", {"hot_water_times": {"sat": [{"on": "09:00", "off": "10:00"}]}}) in sent
    assert entry.runtime_data.hot_water.data.days[5][0].off == 600


async def test_no_calendar_without_the_hot_water_cluster(hass, mqtt_mock):
    entry, sent = await setup(hass, mqtt_mock, devices(), {"heating_times": week()})
    assert entry.runtime_data.hot_water is None
    assert hass.states.get(ENTITY) is None
    assert hass.states.get("calendar.hallway_heating_schedule") is not None
    assert all("hot_water_times" not in payload for _, payload in sent)


async def test_a_cluster_listed_before_the_watch_reloads(hass, mqtt_mock):
    b = Z2mHotWater(hass, "zigbee2mqtt", "Hallway", IEEE)
    b._devices(SimpleNamespace(payload=json.dumps(devices())))
    # Setup decided "no hot water"; a device list with the cluster came first.
    b._devices(SimpleNamespace(payload=json.dumps(devices("64513"))))
    changed = MagicMock()
    await b.async_watch(False, changed)
    b.async_stop()
    changed.assert_called_once()


async def test_heating_comes_up_when_the_hot_water_does_not(hass, mqtt_mock):
    # A converter that lists the cluster but answers no hot_water_times.
    with patch("custom_components.unfr0zen.mqtt_backend.READ_TIMEOUT", 0.05):
        entry, _ = await setup(hass, mqtt_mock, devices("64513"), {"heating_times": week()})
    assert entry.runtime_data.hot_water is None
    assert hass.states.get(ENTITY) is None
    assert hass.states.get("calendar.hallway_heating_schedule") is not None
