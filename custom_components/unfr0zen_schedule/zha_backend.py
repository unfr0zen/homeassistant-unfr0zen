"""The ZHA backend: the Thermostat cluster's weekly schedule commands.

ZHA has no public way to send a cluster command and read its answer, so
this reaches the zigpy cluster through ZHA's gateway, which is internal to
Home Assistant: everything that depends on it is in `_cluster`.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .backend import MODE_COOL, MODE_HEAT, Backend, day_bit, mode_bits, mode_name, transitions
from .schedule import Day, Schedule

ENDPOINT = 1
THERMOSTAT = 0x0201


class ZhaBackend(Backend):
    """One unit on ZHA, by IEEE address."""

    poll = dt.timedelta(minutes=15)

    def __init__(self, hass: HomeAssistant, ieee: str) -> None:
        super().__init__(hass)
        self.ieee = ieee

    def _cluster(self) -> Any:
        try:
            from homeassistant.components.zha.helpers import get_zha_gateway
            from zigpy.types import EUI64

            device = get_zha_gateway(self.hass).devices[EUI64.convert(self.ieee)]
            return device.device.endpoints[ENDPOINT].in_clusters[THERMOSTAT]
        except (ImportError, KeyError, AttributeError, ValueError) as err:
            raise HomeAssistantError(f"ZHA has no thermostat cluster for {self.ieee}: {err!r}") from err

    async def async_read(self) -> Schedule:
        cluster = self._cluster()
        mode, week = "heat", []
        # The unit answers for one day per request.
        for day in range(7):
            rsp = await cluster.get_weekly_schedule(days_to_return=day_bit(day), mode_to_return=MODE_HEAT | MODE_COOL)
            if not hasattr(rsp, "values"):
                raise HomeAssistantError(f"The Nest refused Get Weekly Schedule: {rsp}")
            bits = int(rsp.mode_for_sequence)
            mode = mode_name(bits)
            week.append(_decode(int(rsp.num_transitions_for_sequence), bits, list(rsp.values)))
        return Schedule(mode, tuple(week))

    async def async_write(self, schedule: Schedule, days: dict[int, Day]) -> None:
        cluster = self._cluster()
        bits = mode_bits(schedule.mode)
        for day, points in sorted(days.items()):
            values = [v for p in points for v in (p.minute, round(p.temp * 100))]
            rsp = await cluster.set_weekly_schedule(
                num_transitions_for_sequence=len(points),
                day_of_week_for_sequence=day_bit(day),
                mode_for_sequence=bits,
                values=values,
            )
            status = getattr(rsp, "status", 0)
            if int(status) != 0:
                raise HomeAssistantError(f"The Nest refused Set Weekly Schedule: {status}")


def _decode(count: int, bits: int, values: list[int]) -> Day:
    """A day's setpoints from a response's flat values: per transition its
    time, then its heat setpoint and cool setpoint as `bits` has them."""
    width = 1 + bool(bits & MODE_HEAT) + bool(bits & MODE_COOL)
    points = []
    for i in range(count):
        row = values[i * width : (i + 1) * width]
        if len(row) < width:
            break
        heat = row[1] if bits & MODE_HEAT else None
        cool = row[-1] if bits & MODE_COOL else None
        points.append((row[0], heat, cool))
    return transitions(bits, points)
