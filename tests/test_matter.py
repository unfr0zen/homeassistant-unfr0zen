"""The Matter backend's Schedules attribute and atomic writes, as the Matter
client caches and sends them."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from matter_server.client.models.node import MatterNode
from matter_server.common.helpers.util import dataclass_to_dict, dataclass_to_tag_dict
from matter_server.common.models import EventType, MatterNodeData
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unfr0zen import ScheduleCoordinator
from custom_components.unfr0zen.matter_backend import MatterBackend
from custom_components.unfr0zen.schedule import Schedule, Setpoint

OK = {"statusCode": 0, "attributeStatus": [{"attributeID": 0x51, "statusCode": 0}]}


HEAT_TYPES = [{"0": 4, "1": 1, "2": 0x02}]
COOL_TYPES = [{"0": 3, "1": 1, "2": 0x02}]


def node(schedules=None, types=None, *, thermostat=True):
    """A unit's node as the server reports it; Schedules tag-keyed, its
    handle base64 as the server sends octet strings; ScheduleTypes the kinds
    of schedule the unit takes."""
    attributes = {"0/29/0": [{"0": 22, "1": 1}]}
    if thermostat:
        attributes |= {"1/29/0": [{"0": 769, "1": 1}], "1/513/0": 2050}
    if schedules is not None:
        attributes["1/513/81"] = schedules
    if types is not None:
        attributes["1/513/73"] = types
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


def nest(mode, *transitions):
    return [{"0": "bmVzdA==", "1": mode, "4": list(transitions), "5": True}]


def reported(value):
    """A written value as the server reports it: tag-keyed, octet strings base64."""
    if isinstance(value, list):
        return [reported(v) for v in value]
    if isinstance(value, dict):
        return {k: reported(v) for k, v in value.items()}
    return base64.b64encode(value).decode() if isinstance(value, bytes) else value


class Server:
    """The Matter server between Home Assistant and the unit.

    `device` is the Schedules the unit holds (None: the attribute it lacks);
    the node is the server's cache, which keeps whatever was written, taken
    or not, until a read of the device (refresh_attribute) overwrites it. A
    write is the unit's only once a commit answers success, and only if
    `applies`; AtomicRequests get `answers` in turn.
    """

    def __init__(self, node, device, *answers, applies=True):
        self.node, self.device, self.applies = node, device, applies
        self.answers = list(answers)
        self.pending = None
        self.send_device_command = AsyncMock(side_effect=self._command)
        self.write_attribute = AsyncMock(side_effect=self._write)
        self.refresh_attribute = AsyncMock(side_effect=self._refresh)
        self.subscribe_events = MagicMock(return_value="unsubscribe")

    async def _command(self, node_id, endpoint, command):
        answer = self.answers.pop(0)
        status = answer.get("statusCode", answer.get("0"))
        if command.requestType == 1 and status == 0 and self.pending is not None and self.applies:
            self.device = self.pending
        if command.requestType in (1, 2):
            self.pending = None
        return answer

    async def _write(self, node_id, path, value):
        self.pending = reported(dataclass_to_tag_dict(value))
        self.node.update_attribute(path, self.pending)

    async def _refresh(self, node_id, path):
        if self.device is not None:
            self.node.update_attribute(path, self.device)


def server(schedules, *answers, types=None, **kwargs):
    """A server whose cache and unit both hold `schedules`."""
    return Server(node(schedules, types), schedules, *answers, **kwargs)


def backend(hass, c):
    b = MatterBackend(hass, "device")
    patcher = patch.object(MatterBackend, "_node", return_value=(c, c.node))
    patcher.start()
    return b, patcher


WEEK = Schedule("heat", ((Setpoint(390, 20.5), Setpoint(1320, 17.0)),) + ((),) * 6)


async def test_reads_the_cached_schedule(hass):
    # Monday 06:30 20.5, Monday 22:00 17, and 08:00 21 on Saturday and Sunday.
    c = server(nest(4, {"0": 2, "1": 390, "5": 2050}, {"0": 2, "1": 1320, "5": 1700}, {"0": 0x41, "1": 480, "5": 2100}))
    b, p = backend(hass, c)
    schedule = await b.async_read()
    p.stop()
    assert schedule.mode == "heat"
    assert schedule.days[0] == (Setpoint(390, 20.5), Setpoint(1320, 17.0))
    assert schedule.days[5] == schedule.days[6] == (Setpoint(480, 21.0),)
    assert schedule.days[1:5] == ((),) * 4


async def test_a_cool_schedule_reads_its_cooling_setpoints(hass):
    b, p = backend(hass, server(nest(3, {"0": 8, "1": 600, "4": 2400})))
    schedule = await b.async_read()
    p.stop()
    assert (schedule.mode, schedule.days[2]) == ("cool", (Setpoint(600, 24.0),))


async def test_no_schedule_and_no_schedule_types_is_of_no_kind_it_writes(hass):
    b, p = backend(hass, server([]))
    schedule = await b.async_read()
    p.stop()
    assert schedule == Schedule("unknown", ((),) * 7)
    assert not schedule.writable


async def test_an_auto_schedule_is_a_range_of_lows_and_highs(hass):
    # Monday 06:30 19-24 °C: heating the low, cooling the high.
    c = server(nest(1, {"0": 2, "1": 390, "4": 2400, "5": 1900}), OK, OK)
    b, p = backend(hass, c)
    schedule = await b.async_read()
    assert schedule.mode == "range"
    assert schedule.days[0] == (Setpoint(390, 19.0, 24.0),)
    await b.async_write(schedule, {5: (Setpoint(480, 18.0, 25.0),)})
    p.stop()
    assert dataclass_to_tag_dict(c.write_attribute.call_args.args[2])[0]["4"] == [
        {"0": 0x02, "1": 390, "4": 2400, "5": 1900},
        {"0": 0x40, "1": 480, "4": 2500, "5": 1800},
    ]


async def test_a_repeated_range_week_fits_the_units_transition_limit(hass):
    c = server(nest(1), OK, OK)
    b, p = backend(hass, c)
    day = tuple(Setpoint(hour * 60, 19.0, 24.0) for hour in range(10))
    await b.async_write(await b.async_read(), dict.fromkeys(range(7), day))
    p.stop()
    assert dataclass_to_tag_dict(c.write_attribute.call_args.args[2])[0]["4"] == [
        {"0": 0x7F, "1": point.minute, "4": 2400, "5": 1900} for point in day
    ]


async def test_a_nestlocal_without_schedules_is_named(hass):
    b, p = backend(hass, server(None))
    with pytest.raises(HomeAssistantError, match="nestlocal too old for the Matter schedule"):
        await b.async_read()
    p.stop()


async def test_a_change_on_the_unit_pushes_the_schedule(hass):
    c = server(nest(4))
    n = c.node
    b, p = backend(hass, c)
    pushed = []
    assert await b.async_start(pushed.append) == "unsubscribe"
    updated = c.subscribe_events.call_args.args[0]
    assert c.subscribe_events.call_args.kwargs == {
        "event_filter": EventType.ATTRIBUTE_UPDATED,
        "node_filter": 7,
        "attr_path_filter": "1/513/81",
    }
    value = nest(4, {"0": 0x10, "1": 420, "5": 1900})
    n.update_attribute("1/513/81", value)
    updated(EventType.ATTRIBUTE_UPDATED, value)
    p.stop()
    assert pushed[0].days[3] == (Setpoint(420, 19.0),)


async def test_writes_the_whole_week_in_an_atomic_write(hass):
    c = server(nest(4, {"0": 2, "1": 390, "5": 2050}, {"0": 2, "1": 1320, "5": 1700}), OK, OK)
    b, p = backend(hass, c)
    await b.async_write(WEEK, {6: (Setpoint(480, 21.5),)})
    p.stop()
    begin, commit = (call.args for call in c.send_device_command.call_args_list)
    assert begin[:2] == commit[:2] == (7, 1)
    assert (type(begin[2]).__name__, begin[2].cluster_id, begin[2].command_id) == ("AtomicRequest", 0x0201, 0xFE)
    assert dataclass_to_dict(begin[2]) == {"requestType": 0, "attributeRequests": [0x51], "timeout": 10000}
    assert dataclass_to_dict(commit[2]) == {"requestType": 1, "attributeRequests": [0x51], "timeout": None}
    node_id, path, value = c.write_attribute.call_args.args
    assert (node_id, path) == (7, "1/513/81")
    assert dataclass_to_tag_dict(value) == [
        {
            "0": b"nest",
            "1": 4,
            "4": [
                {"0": 0x02, "1": 390, "5": 2050},
                {"0": 0x02, "1": 1320, "5": 1700},
                {"0": 0x01, "1": 480, "5": 2150},
            ],
            "5": True,
        }
    ]


async def test_a_cool_week_writes_cooling_setpoints(hass):
    c = server(nest(3), OK, OK)
    b, p = backend(hass, c)
    await b.async_write(Schedule("cool", ((),) * 7), {1: (Setpoint(600, 24.0),)})
    p.stop()
    assert dataclass_to_tag_dict(c.write_attribute.call_args.args[2]) == [
        {"0": b"nest", "1": 3, "4": [{"0": 0x04, "1": 600, "4": 2400}], "5": True}
    ]


@pytest.mark.parametrize(
    ("status", "message"),
    [(0x87, "constraint error"), (0x89, "resource exhausted"), (0x9C, "busy"), (0xCB, "invalid in state")],
)
async def test_a_refused_commit_is_named_and_rolled_back(hass, status, message):
    refused = {"0": 1, "1": [{"0": 0x51, "1": status}]}
    c = server(nest(4), OK, refused, OK)
    b, p = backend(hass, c)
    with pytest.raises(HomeAssistantError, match=f"refused to commit the schedule: {message}"):
        await b.async_write(WEEK, {0: ()})
    p.stop()
    requests = [dataclass_to_dict(call.args[2])["requestType"] for call in c.send_device_command.call_args_list]
    assert requests == [0, 1, 2]
    assert c.device == nest(4), "the unit keeps its week"


async def test_a_failed_attribute_write_is_rolled_back(hass):
    c = server(nest(4), OK, OK)
    c.write_attribute.side_effect = RuntimeError("timeout")
    b, p = backend(hass, c)
    with pytest.raises(HomeAssistantError, match="Matter: timeout"):
        await b.async_write(WEEK, {0: ()})
    p.stop()
    requests = [dataclass_to_dict(call.args[2])["requestType"] for call in c.send_device_command.call_args_list]
    assert requests == [0, 2]


async def test_a_refused_begin_writes_nothing(hass):
    c = server(nest(4), {"statusCode": 1, "attributeStatus": [{"attributeID": 0x51, "statusCode": 0x9C}]})
    b, p = backend(hass, c)
    with pytest.raises(HomeAssistantError, match="refused to begin the schedule write: busy"):
        await b.async_write(WEEK, {0: ()})
    p.stop()
    c.write_attribute.assert_not_called()
    assert c.send_device_command.call_count == 1


async def test_days_not_written_come_from_the_unit_not_a_refused_cache(hass):
    # The cache still holds a week the unit refused: Tuesday 10:00 at 30.
    unit = nest(4, {"0": 2, "1": 390, "5": 2050})
    c = Server(node(nest(4, {"0": 4, "1": 600, "5": 3000})), unit, OK, OK)
    b, p = backend(hass, c)
    await b.async_write(WEEK, {6: (Setpoint(480, 21.5),)})
    p.stop()
    assert dataclass_to_tag_dict(c.write_attribute.call_args.args[2]) == [
        {"0": b"nest", "1": 4, "4": [{"0": 0x02, "1": 390, "5": 2050}, {"0": 0x01, "1": 480, "5": 2150}], "5": True}
    ]
    assert c.device == reported(dataclass_to_tag_dict(c.write_attribute.call_args.args[2]))


async def test_days_not_written_keep_the_setpoints_the_unit_reports(hass):
    # 69 °F on a Fahrenheit unit: not a half degree Celsius.
    c = server(nest(4, {"0": 4, "1": 420, "5": 2056}), OK, OK)
    b, p = backend(hass, c)
    await b.async_write(await b.async_read(), {0: (Setpoint(390, 20.5),)})
    p.stop()
    assert dataclass_to_tag_dict(c.write_attribute.call_args.args[2])[0]["4"] == [
        {"0": 0x02, "1": 390, "5": 2050},
        {"0": 0x04, "1": 420, "5": 2056},
    ]


async def test_an_edit_of_a_schedule_whose_mode_changed_writes_nothing(hass):
    # Checked against a heating week; the unit now has a cooling one.
    c = server(nest(3, {"0": 2, "1": 390, "5": 2050}))
    b, p = backend(hass, c)
    with pytest.raises(HomeAssistantError, match="changed while being edited"):
        await b.async_write(WEEK, {6: (Setpoint(480, 21.5),)})
    p.stop()
    c.send_device_command.assert_not_called()
    c.write_attribute.assert_not_called()


async def test_a_commit_the_unit_did_not_apply_fails(hass):
    c = server(nest(4), OK, OK, applies=False)
    b, p = backend(hass, c)
    with pytest.raises(HomeAssistantError, match="The Nest did not take the schedule"):
        await b.async_write(WEEK, {6: (Setpoint(480, 21.5),)})
    p.stop()
    assert c.device == nest(4)
    assert c.node.get_attribute_value(1, 513, 81)[0].transitions == [], "the cache holds the unit's week again"


@pytest.mark.parametrize(
    ("types", "mode"),
    [
        (HEAT_TYPES, "heat"),
        (COOL_TYPES, "cool"),
        ([{"0": 1, "1": 1, "2": 2}], "range"),
        # Auto of no schedules: the unit has a schedule it doesn't say.
        ([{"0": 1, "1": 0, "2": 0}], "unknown"),
    ],
)
async def test_no_schedule_is_an_empty_week_of_the_kind_the_unit_takes(hass, types, mode):
    b, p = backend(hass, server([], types=types))
    schedule = await b.async_read()
    p.stop()
    assert schedule == Schedule(mode, ((),) * 7)
    assert schedule.writable == (mode != "unknown")


async def test_an_emptied_week_is_written_as_no_schedule(hass):
    c = server(nest(4, {"0": 2, "1": 390, "5": 2050}), OK, OK, types=HEAT_TYPES)
    b, p = backend(hass, c)
    await b.async_write(WEEK, {0: ()})
    p.stop()
    assert c.write_attribute.call_args.args == (7, "1/513/81", [])
    assert c.device == [], "the unit holds no schedule, read back as the empty week"


async def test_a_week_left_with_setpoints_keeps_its_schedule(hass):
    c = server([], OK, OK, types=HEAT_TYPES)
    b, p = backend(hass, c)
    await b.async_write(Schedule("heat", ((),) * 7), {4: (Setpoint(420, 19.0),)})
    p.stop()
    assert dataclass_to_tag_dict(c.write_attribute.call_args.args[2]) == [
        {"0": b"nest", "1": 4, "4": [{"0": 0x20, "1": 420, "5": 1900}], "5": True}
    ]


async def test_a_node_without_the_thermostat_endpoint_is_unavailable(hass):
    c = Server(node(thermostat=False), None)
    b, p = backend(hass, c)
    with pytest.raises(HomeAssistantError, match="no endpoint 1 of the Nest"):
        await b.async_read()
    entry = MockConfigEntry(domain="unfr0zen", data={"transport": "matter", "device_id": "device"})
    entry.add_to_hass(hass)
    with pytest.raises(UpdateFailed, match="no endpoint 1 of the Nest"):
        await ScheduleCoordinator(hass, entry, b)._async_update_data()
    p.stop()


async def test_editing_the_active_named_week_preserves_its_name_and_the_other_weeks(hass):
    value = [
        *nest(4, {"0": 2, "1": 420, "5": 1900}),
        {"0": "czE=", "1": 4, "2": "Weekend", "4": [{"0": 2, "1": 390, "5": 2250}], "5": False},
    ]
    c = server(value, OK, OK, OK, OK)
    c.node.node_data.attributes["1/513/79"] = "czE="
    c.node.update_attribute("1/513/79", "czE=")

    async def refresh(node_id, path):
        if path == "1/513/81":
            c.node.update_attribute(path, c.device)

    c.refresh_attribute.side_effect = refresh
    b, patcher = backend(hass, c)
    try:
        current = await b.async_read()
        assert current.days[0] == (Setpoint(390, 22.5),)
        await b.async_write(current, {0: (Setpoint(390, 21.5),)})
        assert value[0] in c.device
        named = next(item for item in c.device if item["0"] == "czE=")
        assert named["2"] == "Weekend" and named["4"][0]["5"] == 2150
        await b.async_write(await b.async_read(), {0: ()})
        assert c.device[0]["0"] == "czE=" and c.device[0]["2"] == "Weekend"
        assert c.device[0]["4"] == [{"0": 127, "1": 0}]
        assert (await b.async_read()).handle == b"s1"

    finally:
        patcher.stop()
