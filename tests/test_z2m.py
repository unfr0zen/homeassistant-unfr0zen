"""The calendar through Zigbee2MQTT's device state."""

import json

from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message

from custom_components.unfr0zen.const import DOMAIN

from .conftest import week

DEVICE = "zigbee2mqtt/Hallway"
ENTITY = "calendar.hallway_schedule"


async def test_reads_and_writes_heating_times_through_zigbee2mqtt(hass, mqtt_mock, hass_ws_client):
    await hass.config.async_set_time_zone("Europe/London")
    state = {"local_temperature": 20.5, "heating_times": week(mon=[("06:00", 20)])}

    async def publish(topic, payload, *args, **kwargs):
        if topic == f"{DEVICE}/get":
            assert json.loads(payload) == {"heating_times": ""}
        elif topic == f"{DEVICE}/set":
            assert json.loads(payload) == {"heating_times": {"tue": [{"time": "07:30", "temp": 19.0}]}}
            state["heating_times"] = week(mon=[("06:00", 20)], tue=[("07:30", 19)])
        else:
            return
        # Other state first, as Zigbee2MQTT publishes every change of the device.
        hass.loop.call_soon(async_fire_mqtt_message, hass, DEVICE, json.dumps({"local_temperature": 21}))
        hass.loop.call_soon(async_fire_mqtt_message, hass, DEVICE, json.dumps(state))

    mqtt_mock.async_publish.side_effect = publish
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="zigbee2mqtt:0x00124b0012345678",
        title="Hallway",
        data={
            "transport": "zigbee2mqtt",
            "base": "zigbee2mqtt",
            "friendly_name": "Hallway",
            "ieee": "0x00124b0012345678",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(ENTITY).attributes["message"] == "20 °C"

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "calendar/event/create",
            "entity_id": ENTITY,
            "event": {
                "summary": "19 °C",
                "dtstart": "2026-11-24T07:30:00+00:00",
                "dtend": "2026-11-24T08:00:00+00:00",
                "rrule": "FREQ=WEEKLY;BYDAY=TU",
            },
        }
    )
    assert (await client.receive_json())["success"]
    assert entry.runtime_data.schedule.data.days[1][0].temp == 19
