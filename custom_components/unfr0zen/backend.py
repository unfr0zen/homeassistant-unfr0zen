"""How a schedule reaches the Nest: one backend per transport."""

from __future__ import annotations

from collections.abc import Callable
import datetime as dt

from homeassistant.core import HomeAssistant

from .schedule import Day, Schedule, Setpoint

# The Thermostat cluster's schedule bitmaps, as ZCL and Matter share them.
MODE_HEAT = 0x01
MODE_COOL = 0x02


class Backend:
    """Reads and writes one unit's schedule."""

    # How often to read the schedule; None when the unit pushes it.
    poll: dt.timedelta | None = None

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_start(self, on_update: Callable[[Schedule], None]) -> Callable[[], None]:
        """Starts listening for pushed schedules; returns the stop."""
        return lambda: None

    async def async_read(self) -> Schedule:
        """The unit's schedule now."""
        raise NotImplementedError

    async def async_write(self, schedule: Schedule, days: dict[int, Day]) -> None:
        """Replaces the given days; the others stay as they are."""
        raise NotImplementedError


class HotWaterBackend(Backend):
    """The hot water schedule, which only some units have."""

    async def async_present(self) -> bool:
        """Whether the unit has hot water now."""
        raise NotImplementedError

    async def async_watch(self, present: bool, on_change: Callable[[], None]) -> None:
        """Calls `on_change` once the unit stops being `present`."""
        raise NotImplementedError

    def async_stop(self) -> None:
        """Stops what `async_present` and `async_watch` listen to."""


def day_bit(day: int) -> int:
    """A day (0 Monday) as the schedule's day bitmap, which starts on Sunday."""
    return 1 << ((day + 1) % 7)


def mode_bits(mode: str) -> int:
    """The setpoints a schedule's transitions carry: a range's both, its low
    the heating setpoint and its high the cooling one."""
    return {"cool": MODE_COOL, "range": MODE_HEAT | MODE_COOL}.get(mode, MODE_HEAT)


def mode_name(bits: int) -> str:
    """A transition mode bitmap as the schedule's kind: both are a range's."""
    return {MODE_HEAT: "heat", MODE_COOL: "cool", MODE_HEAT | MODE_COOL: "range"}.get(
        bits & (MODE_HEAT | MODE_COOL), "unknown"
    )


def transitions(mode: int, points: list[tuple[int, int | None, int | None]]) -> Day:
    """Setpoints from (minutes, heat, cool) transitions in 0.01 °C."""
    # As reported, to the hundredth: a rounded bound could break a range's gap.
    half = lambda value: value / 100  # noqa: E731
    out = []
    for minute, heat, cool in points:
        if mode == MODE_HEAT | MODE_COOL:
            if heat is not None and cool is not None:
                out.append(Setpoint(minute, half(heat), half(cool)))
            continue
        value = heat if mode == MODE_HEAT else cool if mode == MODE_COOL else None
        if value is not None:
            out.append(Setpoint(minute, half(value)))
    return tuple(sorted(out))
