"""The ZHA backend's Thermostat cluster commands, against zigpy's own schemas."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
import pytest
from zigpy.zcl.clusters.hvac import Thermostat
from zigpy.zcl.foundation import GENERAL_COMMANDS, GeneralCommand, Status

from custom_components.unfr0zen.schedule import Schedule, Setpoint
from custom_components.unfr0zen.zha_backend import ZhaBackend

SET = Thermostat.ServerCommandDefs.set_weekly_schedule
GET = Thermostat.ServerCommandDefs.get_weekly_schedule
RESPONSE = Thermostat.ClientCommandDefs.get_weekly_schedule_response
DEFAULT = GENERAL_COMMANDS[GeneralCommand.Default_Response].schema


def cluster_answering(days):
    """A cluster whose Get Weekly Schedule answers from `days` by day bit."""
    cluster = MagicMock()

    async def get(days_to_return, mode_to_return):
        values = days.get(int(days_to_return), [])
        return RESPONSE.schema(
            num_transitions_for_sequence=len(values) // 2,
            day_of_week_for_sequence=days_to_return,
            mode_for_sequence=0x01,
            values=values,
        )

    cluster.get_weekly_schedule = AsyncMock(side_effect=get)
    cluster.set_weekly_schedule = AsyncMock(return_value=DEFAULT(command_id=SET.id, status=Status.SUCCESS))
    return cluster


async def test_reads_one_day_per_request(hass):
    # Monday is bit 1, Sunday bit 0.
    cluster = cluster_answering({0x02: [360, 2000, 1320, 1700], 0x01: [480, 2150]})
    backend = ZhaBackend(hass, "00:12:4b:00:12:34:56:78")
    with patch.object(ZhaBackend, "_cluster", return_value=cluster):
        schedule = await backend.async_read()
    assert schedule.mode == "heat"
    assert schedule.days[0] == (Setpoint(360, 20.0), Setpoint(1320, 17.0))
    assert schedule.days[6] == (Setpoint(480, 21.5),)
    assert [int(c.kwargs["days_to_return"]) for c in cluster.get_weekly_schedule.call_args_list] == [
        0x02,
        0x04,
        0x08,
        0x10,
        0x20,
        0x40,
        0x01,
    ]
    frame = GET.schema(**cluster.get_weekly_schedule.call_args_list[0].kwargs).serialize()
    assert frame == bytes([0x02, 0x03])


async def test_writes_each_day_as_a_set_weekly_schedule_frame(hass):
    cluster = cluster_answering({})
    backend = ZhaBackend(hass, "00:12:4b:00:12:34:56:78")
    week = Schedule("heat", ((),) * 7)
    with patch.object(ZhaBackend, "_cluster", return_value=cluster):
        await backend.async_write(week, {0: (Setpoint(390, 20.5), Setpoint(1320, 17.0)), 6: ()})
    frames = [SET.schema(**c.kwargs).serialize() for c in cluster.set_weekly_schedule.call_args_list]
    assert frames == [
        bytes([2, 0x02, 0x01])
        + (390).to_bytes(2, "little")
        + (2050).to_bytes(2, "little")
        + (1320).to_bytes(2, "little")
        + (1700).to_bytes(2, "little"),
        bytes([0, 0x01, 0x01]),
    ]


async def test_a_cool_schedule_writes_cool_setpoints(hass):
    cluster = cluster_answering({})
    with patch.object(ZhaBackend, "_cluster", return_value=cluster):
        await ZhaBackend(hass, "x").async_write(Schedule("cool", ((),) * 7), {2: (Setpoint(600, 24.0),)})
    assert cluster.set_weekly_schedule.call_args.kwargs | {} == {
        "num_transitions_for_sequence": 1,
        "day_of_week_for_sequence": 0x08,
        "mode_for_sequence": 0x02,
        "values": [600, 2400],
    }


async def test_a_refused_write_fails(hass):
    cluster = cluster_answering({})
    cluster.set_weekly_schedule.return_value = DEFAULT(command_id=SET.id, status=Status.INVALID_FIELD)
    with patch.object(ZhaBackend, "_cluster", return_value=cluster), pytest.raises(HomeAssistantError, match="refused"):
        await ZhaBackend(hass, "x").async_write(Schedule("heat", ((),) * 7), {0: ()})


async def test_without_zha_the_cluster_is_an_error(hass):
    with pytest.raises(HomeAssistantError, match="ZHA has no cluster 0x0201"):
        ZhaBackend(hass, "00:12:4b:00:12:34:56:78")._cluster()
