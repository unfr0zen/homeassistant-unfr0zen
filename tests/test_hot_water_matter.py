"""The Matter hot water backend: the Water Heater endpoint's Schedules,
read from and written to the unit as heating's are."""

from unittest.mock import MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
from matter_server.client.models.node import MatterNode
from matter_server.common.helpers.util import dataclass_to_dict, dataclass_to_tag_dict
from matter_server.common.models import MatterNodeData
import pytest

from custom_components.unfr0zen import hot_water_backend_for
from custom_components.unfr0zen.hotwater import HotWater, Period
from custom_components.unfr0zen.matter_backend import MatterHotWater

from .test_matter import OK, Server, reported

WATER_HEATER = 0x050F
EMPTY = ((),) * 7


def node(schedules=None, *, water_heater=True):
    """A unit's node: the thermostat on 1, and with hot water a Water Heater
    endpoint 2 whose Thermostat cluster has `schedules` (None: none)."""
    attributes = {"1/29/0": [{"0": 769, "1": 1}], "1/513/0": 2050, "1/513/81": []}
    if water_heater:
        attributes["2/29/0"] = [{"0": WATER_HEATER, "1": 1}]
        attributes["2/513/0"] = 5500
        if schedules is not None:
            attributes["2/513/81"] = schedules
    return MatterNode(
        MatterNodeData(
            node_id=7,
            date_commissioned="2026-11-01T00:00:00",
            last_interview="2026-11-01T00:00:00",
            interview_version=6,
            available=True,
            is_bridge=False,
            attributes=attributes,
        )
    )


def hot_water(*transitions):
    """The built-in hot water schedule, b"hotwater", as the server reports it."""
    return [{"0": "aG90d2F0ZXI=", "1": 4, "4": list(transitions), "5": True}]


def on(bit, minute):
    return {"0": bit, "1": minute, "3": 4}


def off(bit, minute):
    return {"0": bit, "1": minute, "3": 0}


def backend(hass, server):
    b = MatterHotWater(hass, "device")
    patcher = patch.object(MatterHotWater, "_node", return_value=(server, server.node))
    patcher.start()
    return b, patcher


def server(schedules, *answers, **kwargs):
    return Server(node(schedules), schedules, *answers, **kwargs)


async def test_matter_entries_have_a_hot_water_backend(hass):
    assert isinstance(hot_water_backend_for(hass, {"transport": "matter", "device_id": "x"}), MatterHotWater)


async def test_presence_is_a_water_heater_endpoint_with_a_schedule(hass):
    for n, present in [(node([]), True), (node(water_heater=False), False), (node(None), False)]:
        b = MatterHotWater(hass, "device")
        with patch.object(MatterHotWater, "_node", return_value=(MagicMock(), n)):
            assert await b.async_present() is present


async def test_reads_each_on_until_the_off_after_it(hass):
    # Monday 06:00-08:00 and 23:00 to Tuesday 01:00; Sunday 23:30 to Monday 00:30.
    c = server(hot_water(on(0x02, 360), off(0x02, 480), on(0x02, 1380), off(0x04, 60), on(0x01, 1410), off(0x02, 30)))
    b, p = backend(hass, c)
    hw = await b.async_read()
    p.stop()
    assert hw.days[0] == (Period(360, 480), Period(1380, 60))
    assert hw.days[6] == (Period(1410, 30),)
    assert hw.days[1:6] == ((),) * 5
    c.refresh_attribute.assert_awaited_with(7, "2/513/81")


async def test_writes_the_week_as_on_and_off_transitions(hass):
    c = server([], OK, OK)
    b, p = backend(hass, c)
    await b.async_write(HotWater(EMPTY), {0: (Period(1380, 60),), 6: (Period(1410, 30),)})
    p.stop()
    begin, commit = (call.args for call in c.send_device_command.call_args_list)
    assert begin[:2] == commit[:2] == (7, 2), "the water heater's endpoint"
    assert dataclass_to_dict(begin[2]) == {"requestType": 0, "attributeRequests": [0x51], "timeout": 10000}
    node_id, path, value = c.write_attribute.call_args.args
    assert (node_id, path) == (7, "2/513/81")
    assert dataclass_to_tag_dict(value) == [
        {
            "0": b"hotwater",
            "1": 4,
            "4": [
                {"0": 0x02, "1": 1380, "3": 4},
                {"0": 0x04, "1": 60, "3": 0},
                {"0": 0x01, "1": 1410, "3": 4},
                {"0": 0x02, "1": 30, "3": 0},
            ],
            "5": True,
        }
    ]
    assert c.device == reported(dataclass_to_tag_dict(value))


async def test_overnight_periods_share_transitions_across_days(hass):
    c = server([], OK, OK)
    b, p = backend(hass, c)
    await b.async_write(HotWater(EMPTY), {5: (Period(1380, 60),), 6: (Period(1380, 60),)})
    p.stop()
    assert dataclass_to_tag_dict(c.write_attribute.call_args.args[2])[0]["4"] == [on(0x41, 1380), off(0x03, 60)]


async def test_days_not_written_come_from_the_unit(hass):
    c = server(hot_water(on(0x08, 1020), off(0x08, 1140)), OK, OK)
    b, p = backend(hass, c)
    await b.async_write(HotWater(EMPTY), {0: (Period(360, 420),)})
    p.stop()
    transitions = dataclass_to_tag_dict(c.write_attribute.call_args.args[2])[0]["4"]
    assert transitions == [
        {"0": 0x02, "1": 360, "3": 4},
        {"0": 0x02, "1": 420, "3": 0},
        {"0": 0x08, "1": 1020, "3": 4},
        {"0": 0x08, "1": 1140, "3": 0},
    ]


@pytest.mark.parametrize("off_minute", [59, 60])
async def test_a_write_checks_the_duration_of_periods_joined_on_readback(hass, off_minute):
    # Sunday 23:00 to Monday 00:30, continuing until 01:00.
    c = server([], OK, OK)
    b, p = backend(hass, c)
    read = b.async_read

    async def refreshed():
        if c.write_attribute.await_count:
            c.device = hot_water(on(0x01, 1380), off(0x02, off_minute))
        return await read()

    with patch.object(b, "async_read", refreshed):
        write = b.async_write(HotWater(EMPTY), {6: (Period(1380, 30),), 0: (Period(30, 60),)})
        if off_minute == 60:
            await write
        else:
            with pytest.raises(HomeAssistantError, match="did not take the hot water schedule"):
                await write
    p.stop()


async def test_a_write_the_unit_did_not_apply_fails(hass):
    c = server([], OK, OK, applies=False)
    b, p = backend(hass, c)
    with pytest.raises(HomeAssistantError, match="The Nest did not take the hot water schedule"):
        await b.async_write(HotWater(EMPTY), {2: (Period(360, 420),)})
    p.stop()


async def test_a_refused_commit_is_rolled_back(hass):
    c = server([], OK, {"0": 1, "1": [{"0": 0x51, "1": 0x87}]}, OK)
    b, p = backend(hass, c)
    with pytest.raises(HomeAssistantError, match="refused to commit the schedule: constraint error"):
        await b.async_write(HotWater(EMPTY), {2: (Period(360, 420),)})
    p.stop()
    assert [dataclass_to_dict(call.args[2])["requestType"] for call in c.send_device_command.call_args_list] == [
        0,
        1,
        2,
    ]


async def test_an_endpoint_gained_before_the_watch_reloads(hass):
    b = MatterHotWater(hass, "device")
    changed = MagicMock()
    with patch.object(MatterHotWater, "_node", return_value=(MagicMock(), node([]))):
        await b.async_watch(False, changed)
    b.async_stop()
    changed.assert_called_once()


async def test_an_endpoint_added_later_reloads_at_once(hass):
    from matter_server.common.models import EventType

    b = MatterHotWater(hass, "device")
    changed = MagicMock()
    client, without = MagicMock(), node(water_heater=False)
    client.subscribe_events = MagicMock(return_value=MagicMock())
    with patch.object(MatterHotWater, "_node", return_value=(client, without)):
        await b.async_watch(False, changed)
    changed.assert_not_called()
    subscribed = {c.kwargs["event_filter"]: c for c in client.subscribe_events.call_args_list}
    assert set(subscribed) == {EventType.ENDPOINT_ADDED, EventType.ENDPOINT_REMOVED}
    assert all(c.kwargs["node_filter"] == without.node_id for c in subscribed.values())
    # The unit reports hot water: the Water Heater endpoint arrives.
    added = subscribed[EventType.ENDPOINT_ADDED].args[0]
    with patch.object(MatterHotWater, "_node", return_value=(client, node([]))):
        added(EventType.ENDPOINT_ADDED, {"node_id": without.node_id, "endpoint_id": 2})
        await hass.async_block_till_done()
    changed.assert_called_once()
    b.async_stop()
    for c in subscribed.values():
        c.return_value.assert_called_once()


async def test_no_schedule_is_an_empty_week(hass):
    b, p = backend(hass, server([]))
    assert await b.async_read() == HotWater(EMPTY)
    p.stop()


async def test_an_emptied_week_is_written_as_no_schedule(hass):
    c = server(hot_water(on(0x02, 360), off(0x02, 480)), OK, OK)
    b, p = backend(hass, c)
    await b.async_write(HotWater(EMPTY), {0: ()})
    p.stop()
    assert c.write_attribute.call_args.args == (7, "2/513/81", [])
    assert c.device == []


async def test_a_node_without_the_water_heater_is_an_error(hass):
    b, p = backend(hass, Server(node(water_heater=False), None))
    with pytest.raises(HomeAssistantError, match="has no water heater"):
        await b.async_read()
    p.stop()
