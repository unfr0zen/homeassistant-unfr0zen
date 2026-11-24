"""Wake screen and update status over Zigbee2MQTT, ZHA and Matter."""

import json
from unittest.mock import AsyncMock, patch

from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message

from custom_components.unfr0zen.matter_backend import MatterBackend
from custom_components.unfr0zen.zha_backend import ZhaBackend

from .conftest import week
from .test_hot_water_z2m import DEVICE, IEEE, devices, setup


async def test_zigbee2mqtt_wake_and_update_without_discovery(hass, mqtt_mock):
    units = devices()
    units[1]["software_build_id"] = "5.9"
    entry, sent = await setup(hass, mqtt_mock, units, {"heating_times": week()})
    assert dr.async_get(hass).async_get(entry.runtime_data.device.id).sw_version == "5.9"
    await hass.services.async_call("button", "press", {"entity_id": "button.hallway_wake_screen"}, blocking=True)
    assert (f"{DEVICE}/set", {"wake_screen": "wake"}) in sent
    async_fire_mqtt_message(
        hass,
        DEVICE,
        json.dumps(
            {"update": {"installed_version": 1000, "latest_version": 1001, "state": "updating", "progress": 37}}
        ),
    )
    async_fire_mqtt_message(hass, DEVICE, '{"local_temperature":20}')
    await hass.async_block_till_done()
    state = hass.states.get("update.hallway_unfr0zen")
    assert state.state == "on"
    assert state.attributes["update_percentage"] == 37
    assert state.attributes["installed_version"] == "1000"


@pytest.mark.parametrize("domain", ["zha", "matter"])
async def test_native_firmware_status_and_identify(hass, domain):
    owner = MockConfigEntry(domain=domain)
    owner.add_to_hass(hass)
    registry = dr.async_get(hass)
    source = registry.async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={(domain, IEEE)}, name="Hall", sw_version="1.0"
    )
    data = {"ieee": IEEE} if domain == "zha" else {"device_id": source.id}
    entry = MockConfigEntry(domain="unfr0zen", data={"transport": domain, **data})
    entry.add_to_hass(hass)
    with patch("custom_components.unfr0zen.calendar.async_start_calendars", side_effect=PlatformNotReady("offline")):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.device.sw_version == "1.0"
    update = "update.hall_unfr0zen"
    assert hass.states.get(update).state == "unavailable"
    entities = er.async_get(hass)
    ota = entities.async_get_or_create(
        "update",
        domain,
        "thermostat-firmware",
        config_entry=owner,
        device_id=source.id,
        original_device_class="firmware",
    )
    hass.states.async_set(ota.entity_id, "on", {"installed_version": "1.0", "latest_version": "2.0"})
    await hass.async_block_till_done()
    assert hass.states.get(update).state == "on"
    assert hass.states.get(update).attributes["latest_version"] == "2.0"
    press = {"entity_id": "button.hall_wake_screen"}
    if domain == "zha":
        cluster = AsyncMock()
        with patch.object(ZhaBackend, "_cluster", return_value=cluster) as get_cluster:
            await hass.services.async_call("button", "press", press, blocking=True)
        get_cluster.assert_called_once_with(3)
        cluster.identify.assert_awaited_once_with(identify_time=5)
    else:
        from chip.clusters.Objects import Identify

        from .test_matter import node

        client = AsyncMock()
        with patch.object(MatterBackend, "_node", return_value=(client, node())):
            await hass.services.async_call("button", "press", press, blocking=True)
        client.send_device_command.assert_awaited_once_with(7, 1, Identify.Commands.Identify(identifyTime=5))
    entities.async_remove(ota.entity_id)
    await hass.async_block_till_done()
    assert hass.states.get(update).state == "unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)
