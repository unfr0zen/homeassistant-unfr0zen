"""The ZHA backends: the Thermostat cluster's weekly schedule commands, and
the hot water cluster's attributes.

ZHA has no public way to send a cluster command and read its answer, so
this reaches the zigpy cluster through ZHA's gateway, which is internal to
Home Assistant: everything that depends on it is in `_cluster`.
"""

from __future__ import annotations

from collections.abc import Callable
import contextlib
import datetime as dt
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval

from .backend import MODE_COOL, MODE_HEAT, Backend, HotWaterBackend, day_bit, mode_bits, mode_name, transitions
from .errors import error
from .hotwater import HotWater, Periods, from_text, to_text
from .schedule import Day, Schedule

ENDPOINT = 1
THERMOSTAT = 0x0201
# The hot water cluster: one char string attribute per day, 0x0000 Monday
# to 0x0006 Sunday, "06:00-08:00,17:00-20:00"; on endpoint 1 only with hot water.
HOT_WATER = 0xFC01
NEST_LABS = 0x10D6
CHAR_STRING = 0x42


class ZhaBackend(Backend):
    """One unit on ZHA, by IEEE address."""

    poll = dt.timedelta(minutes=15)

    def __init__(self, hass: HomeAssistant, ieee: str) -> None:
        super().__init__(hass)
        self.ieee = ieee

    def _cluster(self, cluster_id: int = THERMOSTAT) -> Any:
        try:
            from homeassistant.components.zha.helpers import get_zha_gateway
            from zigpy.types import EUI64

            device = get_zha_gateway(self.hass).devices[EUI64.convert(self.ieee)]
            return device.device.endpoints[ENDPOINT].in_clusters[cluster_id]
        except (ImportError, KeyError, AttributeError, ValueError) as err:
            raise error("zha_no_cluster", cluster=f"0x{cluster_id:04X}", ieee=self.ieee, error=repr(err)) from err

    async def async_read(self) -> Schedule:
        cluster = self._cluster()
        modes, week = set(), []
        # The unit answers for one day per request.
        for day in range(7):
            rsp = await cluster.get_weekly_schedule(days_to_return=day_bit(day), mode_to_return=MODE_HEAT | MODE_COOL)
            if not hasattr(rsp, "values"):
                raise error("zha_get_refused", response=rsp)
            bits = int(rsp.mode_for_sequence)
            modes.add(mode_name(bits))
            week.append(_decode(int(rsp.num_transitions_for_sequence), bits, list(rsp.values)))
        # Days of different kinds: the Nest changed mode between requests.
        if len(modes) > 1:
            raise error("zha_mixed_week")
        return Schedule(modes.pop(), tuple(week))

    async def async_write(self, schedule: Schedule, days: dict[int, Day]) -> None:
        cluster = self._cluster()
        bits = mode_bits(schedule.mode)
        for day, points in sorted(days.items()):
            # A range's low (heating) then high (cooling); the others' one.
            values = [
                v
                for p in points
                for v in (p.minute, round(p.temp * 100), *(() if p.high is None else (round(p.high * 100),)))
            ]
            rsp = await cluster.set_weekly_schedule(
                num_transitions_for_sequence=len(points),
                day_of_week_for_sequence=day_bit(day),
                mode_for_sequence=bits,
                values=values,
            )
            status = getattr(rsp, "status", 0)
            if int(status) != 0:
                raise error("zha_set_refused", status=status)


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


class ZhaHotWater(ZhaBackend, HotWaterBackend):
    """The hot water cluster of one unit on ZHA. zigpy knows no attributes
    of it, so requests name raw attribute ids and the string type."""

    def _hot_water(self) -> Any:
        return self._cluster(HOT_WATER)

    async def async_present(self) -> bool:
        try:
            self._hot_water()
        except HomeAssistantError:
            return False
        return True

    _stop_watching: Callable[[], None] | None = None
    _on_update: Callable[[Any], None] | None = None

    async def async_watch(self, present: bool, on_change: Callable[[], None]) -> None:
        async def check(_now: Any) -> None:
            if await self.async_present() != present:
                on_change()

        self._stop_watching = async_track_time_interval(self.hass, check, self.poll)
        # A cluster gained or lost since setup decided.
        await check(None)

    @callback
    def async_stop(self) -> None:
        if self._stop_watching is not None:
            self._stop_watching()
            self._stop_watching = None

    async def async_start(self, on_update: Callable[[Any], None]) -> Callable[[], None]:
        from zigpy.zcl import AttributeReportedEvent

        try:
            cluster = self._hot_water()
        except HomeAssistantError:
            return lambda: None
        self.latest: HotWater | None = None
        self._on_update = on_update

        @callback
        def reported(event: Any) -> None:
            # The unit reports a day that changed, manufacturer-specific.
            if self.latest is None or not 0 <= event.attribute_id < 7 or event.manufacturer_code != NEST_LABS:
                return
            days = list(self.latest.days)
            days[event.attribute_id] = from_text(str(event.value))
            self.latest = HotWater(tuple(days))
            on_update(self.latest)

        return cluster.on_event(AttributeReportedEvent.event_type, reported)

    async def async_read(self) -> HotWater:
        from zigpy.zcl import foundation

        cluster = self._hot_water()
        read = foundation.GeneralCommand.Read_Attributes
        days = []
        # One attribute per request: a full day fills a frame.
        for day in range(7):
            rsp = await cluster.request(
                True, read, foundation.GENERAL_COMMANDS[read].schema, [day], manufacturer=NEST_LABS
            )
            records = getattr(rsp, "status_records", None)
            if not records or records[0].status != foundation.Status.SUCCESS:
                raise error("zha_hot_water_read", day=day, response=rsp)
            days.append(from_text(str(records[0].value.value)))
        self.latest = HotWater(tuple(days))
        return self.latest

    async def async_write(self, current: Any, days: dict[int, Periods]) -> None:
        from zigpy import types as t
        from zigpy.zcl import foundation

        cluster = self._hot_water()

        async def write(day: int, text: str) -> None:
            value = foundation.TypeValue(type=CHAR_STRING, value=t.CharacterString(text))
            rsp = await cluster.write_attributes_raw([foundation.Attribute(day, value)], manufacturer_code=NEST_LABS)
            records = getattr(rsp, "status_records", None)
            statuses = [r.status for r in records] if records is not None else [getattr(rsp, "status", None)]
            if any(s != foundation.Status.SUCCESS for s in statuses):
                raise error("zha_hot_water_refused", periods=repr(text), status=statuses[0])

        async def replace(week: dict[int, Periods]) -> None:
            # The unit checks each day against its whole week, so a period
            # moving across midnight would overlap its old place: several
            # days are cleared first, then given their periods.
            if len(week) > 1:
                for day in week:
                    await write(day, "")
            for day, periods in week.items():
                if periods or len(week) == 1:
                    await write(day, to_text(periods))

        changed = {d: p for d, p in sorted(days.items()) if current is None or p != current.days[d]}
        try:
            await replace(changed)
        except HomeAssistantError:
            with contextlib.suppress(HomeAssistantError):
                # The days as they were, where clearing them took them away.
                if current is not None and len(changed) > 1:
                    await replace({d: current.days[d] for d in changed})
            # Shown as the unit has it, part written or not.
            with contextlib.suppress(HomeAssistantError):
                week = await self.async_read()
                if self._on_update is not None:
                    self._on_update(week)
            raise
