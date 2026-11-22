"""The Matter backend: the Thermostat cluster's weekly schedule commands,
sent through Home Assistant's Matter server connection.

The client's cluster definitions no longer list these commands, so they are
defined here; the server takes a command by cluster and name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
from functools import cache
from typing import Any, ClassVar

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .backend import MODE_COOL, MODE_HEAT, Backend, day_bit, mode_bits, mode_name, transitions
from .schedule import Day, Schedule

ENDPOINT = 1
THERMOSTAT = 0x0201


@cache
def commands() -> dict[str, type]:
    """The schedule commands, as the Matter client sends commands: named
    as the cluster names them, fields as the client spells them."""
    from chip.clusters.ClusterObjects import ClusterCommand

    @dataclass
    class SetWeeklySchedule(ClusterCommand):
        cluster_id: ClassVar[int] = THERMOSTAT
        command_id: ClassVar[int] = 0x01
        is_client: ClassVar[bool] = True
        response_type: ClassVar[str | None] = None

        numberOfTransitionsForSequence: int = 0
        dayOfWeekForSequence: int = 0
        modeForSequence: int = 0
        transitions: list[dict[str, Any]] = field(default_factory=list)

    @dataclass
    class GetWeeklySchedule(ClusterCommand):
        cluster_id: ClassVar[int] = THERMOSTAT
        command_id: ClassVar[int] = 0x02
        is_client: ClassVar[bool] = True
        response_type: ClassVar[str | None] = "GetWeeklyScheduleResponse"

        daysToReturn: int = 0
        modeToReturn: int = 0

    return {"set": SetWeeklySchedule, "get": GetWeeklySchedule}


class MatterBackend(Backend):
    """One unit on Matter, by its Home Assistant device."""

    poll = dt.timedelta(minutes=15)

    def __init__(self, hass: HomeAssistant, device_id: str) -> None:
        super().__init__(hass)
        self.device_id = device_id

    def _node(self) -> tuple[Any, int]:
        try:
            from homeassistant.components.matter.helpers import get_matter, get_node_from_device_entry

            device = dr.async_get(self.hass).async_get(self.device_id)
            node = get_node_from_device_entry(self.hass, device) if device else None
            if node is None:
                raise KeyError(self.device_id)
            return get_matter(self.hass).matter_client, node.node_id
        except (ImportError, KeyError, IndexError, AttributeError, RuntimeError) as err:
            raise HomeAssistantError(f"Matter has no node for device {self.device_id}: {err!r}") from err

    async def async_read(self) -> Schedule:
        client, node = self._node()
        get = commands()["get"]
        mode, week = "heat", []
        # The unit answers for one day per request.
        for day in range(7):
            rsp = await client.send_device_command(
                node, ENDPOINT, get(daysToReturn=day_bit(day), modeToReturn=MODE_HEAT | MODE_COOL)
            )
            bits, day_points = decode(rsp)
            mode = mode_name(bits)
            week.append(day_points)
        return Schedule(mode, tuple(week))

    async def async_write(self, schedule: Schedule, days: dict[int, Day]) -> None:
        client, node = self._node()
        bits = mode_bits(schedule.mode)
        heat = bits == MODE_HEAT
        for day, points in sorted(days.items()):
            await client.send_device_command(
                node,
                ENDPOINT,
                commands()["set"](
                    numberOfTransitionsForSequence=len(points),
                    dayOfWeekForSequence=day_bit(day),
                    modeForSequence=bits,
                    transitions=[
                        {
                            "transitionTime": p.minute,
                            "heatSetpoint": round(p.temp * 100) if heat else None,
                            "coolSetpoint": None if heat else round(p.temp * 100),
                        }
                        for p in points
                    ],
                ),
            )


def _field(obj: Any, name: str, tag: int) -> Any:
    """A response field, keyed by its name or its TLV tag."""
    if isinstance(obj, dict):
        return obj.get(name, obj.get(str(tag)))
    return getattr(obj, name, None)


def decode(rsp: Any) -> tuple[int, Day]:
    """A GetWeeklyScheduleResponse's mode bitmap and setpoints."""
    if rsp is None:
        raise HomeAssistantError("The Nest sent no weekly schedule")
    bits = int(_field(rsp, "modeForSequence", 2) or 0)
    points = [
        (
            int(_field(t, "transitionTime", 0)),
            _field(t, "heatSetpoint", 1),
            _field(t, "coolSetpoint", 2),
        )
        for t in _field(rsp, "transitions", 3) or []
    ]
    return bits, transitions(bits, points)
