"""Adding a unit through each transport."""

import asyncio
import json
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message

from custom_components.unfr0zen.const import DOMAIN

from .conftest import week


@pytest.fixture(autouse=True)
def no_setup():
    """The flow alone: a created entry is not set up."""
    with patch("custom_components.unfr0zen.async_setup_entry", return_value=True):
        yield


async def test_mqtt_discovery_adds_the_topic_it_saw(hass, mqtt_mock):
    info = MqttServiceInfo(
        topic="nestlocal/02AA01AC/heating_times",
        payload=json.dumps(week()),
        qos=0,
        retain=True,
        subscribed_topic="+/+/heating_times",
        timestamp=0,
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_MQTT}, data=info
    )
    assert result["step_id"] == "confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"transport": "nestlocal", "base": "nestlocal/02AA01AC"}
    assert result["result"].unique_id == "nestlocal:nestlocal/02AA01AC"


async def test_mqtt_discovery_ignores_other_payloads(hass, mqtt_mock):
    info = MqttServiceInfo(
        topic="other/x/heating_times",
        payload="on",
        qos=0,
        retain=True,
        subscribed_topic="+/+/heating_times",
        timestamp=0,
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_MQTT}, data=info
    )
    assert result["reason"] == "not_a_schedule"


async def test_zigbee2mqtt_lists_the_nests_it_has(hass, mqtt_mock):
    devices = [
        {
            "friendly_name": "Hallway",
            "ieee_address": "0x00124b0012345678",
            "definition": {"model": "Nest Learning Thermostat (3rd gen, unfr0zen)"},
        },
        {"friendly_name": "Lamp", "ieee_address": "0x1", "definition": {"model": "LED1545G12"}},
        {"friendly_name": "Coordinator", "ieee_address": "0x0", "definition": None},
    ]

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "zigbee2mqtt"})
    configure = hass.async_create_task(
        hass.config_entries.flow.async_configure(result["flow_id"], {"base": "zigbee2mqtt"})
    )
    while not configure.done():
        async_fire_mqtt_message(hass, "zigbee2mqtt/bridge/devices", json.dumps(devices), retain=True)
        await asyncio.sleep(0.01)
    result = await configure
    assert result["step_id"] == "zigbee2mqtt_device"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"friendly_name": "Hallway"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "transport": "zigbee2mqtt",
        "base": "zigbee2mqtt",
        "friendly_name": "Hallway",
        "ieee": "0x00124b0012345678",
    }


async def test_zha_picks_a_nest_from_its_devices(hass):
    zha = MockConfigEntry(domain="zha")
    zha.add_to_hass(hass)
    registry = dr.async_get(hass)
    nest = registry.async_get_or_create(
        config_entry_id=zha.entry_id,
        identifiers={("zha", "00:12:4b:00:12:34:56:78")},
        manufacturer="Google Nest",
        model="Nest Learning Thermostat (gen2)",
        name="Landing",
    )
    registry.async_get_or_create(
        config_entry_id=zha.entry_id,
        identifiers={("zha", "00:00:00:00:00:00:00:01")},
        manufacturer="IKEA",
        model="Bulb",
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "zha"})
    assert [o["value"] for o in result["data_schema"].schema["device_id"].config["options"]] == [nest.id]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"device_id": nest.id})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert (result["title"], result["data"]) == ("Landing", {"transport": "zha", "ieee": "00:12:4b:00:12:34:56:78"})


async def test_matter_without_a_nest_aborts(hass):
    MockConfigEntry(domain="matter").add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "matter"})
    assert result["reason"] == "no_device"


async def test_matter_picks_a_nest_from_its_devices(hass):
    matter = MockConfigEntry(domain="matter")
    matter.add_to_hass(hass)
    nest = dr.async_get(hass).async_get_or_create(
        config_entry_id=matter.entry_id,
        identifiers={("matter", "deviceid_ABC-0000000000000007-MatterNodeDevice")},
        manufacturer="Google Nest",
        model="Nest Learning Thermostat",
        name="Lounge",
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "matter"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"device_id": nest.id})
    assert (result["title"], result["data"]) == ("Lounge", {"transport": "matter", "device_id": nest.id})
