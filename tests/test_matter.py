"""The Matter backend's Thermostat commands, as the Matter client sends them."""

from unittest.mock import AsyncMock, MagicMock, patch

from matter_server.common.helpers.util import dataclass_to_dict

from custom_components.unfr0zen_schedule.matter_backend import MatterBackend, decode
from custom_components.unfr0zen_schedule.schedule import Schedule, Setpoint

EMPTY = {"numberOfTransitionsForSequence": 0, "dayOfWeekForSequence": 0, "modeForSequence": 1, "transitions": []}


def client_answering(days):
    """A client whose GetWeeklySchedule answers from `days` by day bit."""

    async def send(node, endpoint, command):
        return days.get(command.daysToReturn, EMPTY)

    return MagicMock(send_device_command=AsyncMock(side_effect=send))


async def test_reads_one_day_per_request(hass):
    client = client_answering(
        {
            0x02: {
                "numberOfTransitionsForSequence": 1,
                "dayOfWeekForSequence": 2,
                "modeForSequence": 1,
                "transitions": [{"transitionTime": 360, "heatSetpoint": 2000, "coolSetpoint": None}],
            },
        }
    )
    with patch.object(MatterBackend, "_node", return_value=(client, 7)):
        schedule = await MatterBackend(hass, "device").async_read()
    assert schedule.mode == "heat"
    assert schedule.days[0] == (Setpoint(360, 20.0),)
    assert schedule.days[1:] == ((),) * 6
    calls = client.send_device_command.call_args_list
    assert [(c.args[0], c.args[1]) for c in calls] == [(7, 1)] * 7
    first = calls[0].args[2]
    assert (type(first).__name__, first.cluster_id, first.command_id) == ("GetWeeklySchedule", 0x0201, 0x02)
    assert dataclass_to_dict(first) == {"daysToReturn": 0x02, "modeToReturn": 0x03}


async def test_writes_each_day_as_set_weekly_schedule(hass):
    client = MagicMock(send_device_command=AsyncMock(return_value=None))
    with patch.object(MatterBackend, "_node", return_value=(client, 7)):
        await MatterBackend(hass, "device").async_write(Schedule("heat", ((),) * 7), {6: (Setpoint(480, 21.5),)})
    command = client.send_device_command.call_args.args[2]
    assert (type(command).__name__, command.cluster_id, command.command_id) == ("SetWeeklySchedule", 0x0201, 0x01)
    assert dataclass_to_dict(command) == {
        "numberOfTransitionsForSequence": 1,
        "dayOfWeekForSequence": 0x01,
        "modeForSequence": 0x01,
        "transitions": [{"transitionTime": 480, "heatSetpoint": 2150, "coolSetpoint": None}],
    }


def test_a_response_keyed_by_tag_reads_the_same():
    by_tag = {"0": 2, "1": 0x02, "2": 0x02, "3": [{"0": 420, "1": None, "2": 2450}, {"0": 1380, "1": None, "2": 2600}]}
    assert decode(by_tag) == (0x02, (Setpoint(420, 24.5), Setpoint(1380, 26.0)))
