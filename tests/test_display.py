"""The display controls on the unfr0zen device follow the thermostat's reports."""

import json
from unittest.mock import patch

from homeassistant.core import ServiceCall
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message

from .conftest import week
from .test_calendar_mqtt import BASE
from .test_hot_water_mqtt import setup
from .test_hot_water_z2m import DEVICE, IEEE, devices, setup as setup_z2m


async def test_mqtt_display_reports_and_device_controls(hass, mqtt_mock):
    values = {
        "theme": "Stock",
        "themes": [{"id": "stock", "label": "Stock"}, {"id": "paper", "label": "Paper dial"}],
        "screen_timeout": 30,
        "screen_always_on": False,
        "screen_brightness": 0,
        "screen_brightness_auto": True,
        "schedules": True,
    }
    entry = await setup(hass, {"heating_times": week(), "state": values})
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    expected = [
        ("select", "theme", "Stock", "select_option", {"option": "Paper dial"}, "Paper dial"),
        ("switch", "screen_always_on", "off", "turn_on", {}, "ON"),
        ("number", "screen_brightness", "0", "set_value", {"value": 45}, "45"),
        ("switch", "screen_brightness_auto", "on", "turn_off", {}, "OFF"),
        ("number", "screen_timeout", "30", "set_value", {"value": 90}, "90"),
    ]
    for domain, key, initial, service, fields, payload in expected:
        entity = registry.async_get_entity_id(domain, "unfr0zen", f"{entry.unique_id}-{key}")
        assert registry.async_get(entity).device_id == entry.runtime_data.device.id
        state = hass.states.get(entity)
        assert float(state.state) == float(initial) if domain == "number" else state.state == initial
        if domain == "select":
            assert state.attributes["options"] == ["Stock", "Paper dial"]
        await hass.services.async_call(domain, service, {"entity_id": entity, **fields}, blocking=True)
        assert mqtt_mock.async_publish.call_args.args[:2] == (f"{BASE}/set/{key}", payload)
    theme = registry.async_get_entity_id("select", "unfr0zen", f"{entry.unique_id}-theme")
    async_fire_mqtt_message(
        hass,
        f"{BASE}/state",
        json.dumps(values | {"theme": "Paper dial", "themes": [{"id": "paper", "label": "Paper dial"}]}),
    )
    await hass.async_block_till_done()
    assert hass.states.get(theme).state == "Paper dial"
    assert hass.states.get(theme).attributes["options"] == ["Paper dial"]
    async_fire_mqtt_message(hass, f"{BASE}/availability", "offline")
    await hass.async_block_till_done()
    assert hass.states.get(theme).state == "unavailable"


async def test_z2m_display_discovers_partial_reports_and_writes(hass, mqtt_mock):
    entry, sent = await setup_z2m(hass, mqtt_mock, devices(), {"heating_times": week()})
    async_fire_mqtt_message(hass, DEVICE, '{"screen_always_on":"OFF","screen_timeout":60}')
    async_fire_mqtt_message(hass, DEVICE, '{"screen_brightness_auto":"ON","screen_brightness":80}')
    await hass.async_block_till_done()
    entity = "number.hallway_screen_timeout"
    assert float(hass.states.get(entity).state) == 60
    await hass.services.async_call("number", "set_value", {"entity_id": entity, "value": 120}, blocking=True)
    assert (f"{DEVICE}/set", {"screen_timeout": 120}) in sent
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.hallway_screen_always_on"}, blocking=True)
    assert (f"{DEVICE}/set", {"screen_always_on": "ON"}) in sent
    assert er.async_get(hass).async_get_entity_id("select", "unfr0zen", f"{entry.unique_id}-theme") is None


@pytest.mark.parametrize("transport", ["zha", "matter"])
async def test_native_controls_follow_only_the_paired_devices_writable_entities(hass, transport):
    owner = MockConfigEntry(domain=transport)
    owner.add_to_hass(hass)
    source = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={(transport, IEEE)}, name="Hall"
    )
    data = {"ieee": IEEE} if transport == "zha" else {"device_id": source.id}
    entry = MockConfigEntry(domain="unfr0zen", data={"transport": transport, **data})
    entry.add_to_hass(hass)
    with patch("custom_components.unfr0zen.calendar.async_start_calendars", side_effect=PlatformNotReady("offline")):
        assert await hass.config_entries.async_setup(entry.entry_id)
    assert hass.states.get("number.hall_screen_brightness") is None
    registry = er.async_get(hass)
    dimmer = registry.async_get_or_create(
        "number",
        transport,
        f"{IEEE}-42-13",
        config_entry=owner,
        device_id=source.id,
        original_name="Analog output" if transport == "zha" else "Number (Screen brightness)",
        suggested_object_id="source_dimmer",
    )
    hass.states.async_set(dimmer.entity_id, "60")
    await hass.async_block_till_done()
    visible = registry.async_get_entity_id("number", "unfr0zen", f"{entry.entry_id}-screen_brightness")
    assert float(hass.states.get(visible).state) == 60
    received = []

    async def set_value(call: ServiceCall):
        received.append(dict(call.data))

    hass.services.async_register("number", "set_value", set_value)
    # Exercise the proxy write directly; the source and proxy share HA's number service.
    await entry.runtime_data.display.async_write("screen_brightness", 20, None)
    assert received == [{"entity_id": dimmer.entity_id, "value": 20}]
    registry.async_remove(dimmer.entity_id)
    await hass.async_block_till_done()
    assert hass.states.get(visible).state == "unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)
