"""The Matter backend: the Thermostat cluster's Schedules attribute (MSCH),
through Home Assistant's Matter server connection.

nestlocal keeps one built-in schedule, the Nest's week, with one transition
per setpoint per day, on the thermostat endpoint; a unit with hot water has a
Water Heater endpoint whose Thermostat cluster carries the hot water week the
same way, its transitions turning it on (Heat) and off (Off). Pushes come from the node's attribute cache, which the
server keeps subscribed; reads and writes go to the unit, as the server's
cache can keep a value written in a write the unit then refused. A write
replaces the whole list inside an atomic write (AtomicRequest begin, write,
commit), and is checked against what the unit then holds.
"""

from __future__ import annotations

from collections.abc import Callable
import contextlib
import datetime as dt
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval

from .backend import Backend, HotWaterBackend, day_bit
from .errors import error
from .hotwater import DAY_MINUTES, HotWater, Period, Periods
from .schedule import Schedule, Setpoint, matches

ENDPOINT = 1
THERMOSTAT = 0x0201
SCHEDULES = 0x0051
SCHEDULES_PATH = f"{ENDPOINT}/{THERMOSTAT}/{SCHEDULES}"
HANDLE = b"nest"
HOT_WATER_HANDLE = b"hotwater"
# An empty week is no schedule at all: a schedule without transitions makes
# the Matter server drop the node's endpoints when it restarts.
NO_SCHEDULE: list[Any] = []
MODE_OFF, MODE_HEAT = 0, 4
# How long the unit holds the atomic write open, ms.
ATOMIC_TIMEOUT = 10000

BEGIN_WRITE, COMMIT_WRITE, ROLLBACK_WRITE = 0, 1, 2
# A schedule's kind as its system mode: a range schedule is Auto.
SYSTEM_MODE = {"heat": 4, "cool": 3, "range": 1}
STATUS = {0x87: "constraint error", 0x89: "resource exhausted", 0x9C: "busy", 0xCB: "invalid in state"}


class MatterBackend(Backend):
    """One unit's heating on Matter, by its Home Assistant device."""

    poll = dt.timedelta(minutes=15)
    kind = "schedule"

    def __init__(self, hass: HomeAssistant, device_id: str) -> None:
        super().__init__(hass)
        self.device_id = device_id

    def _node(self) -> tuple[Any, Any]:
        """The Matter client and the unit's node."""
        try:
            from homeassistant.components.matter.helpers import get_matter, get_node_from_device_entry

            device = dr.async_get(self.hass).async_get(self.device_id)
            node = get_node_from_device_entry(self.hass, device) if device else None
            if node is None:
                raise KeyError(self.device_id)
            return get_matter(self.hass).matter_client, node
        except (ImportError, KeyError, IndexError, AttributeError, RuntimeError) as err:
            raise error("matter_no_node", device=self.device_id, error=repr(err)) from err

    def _endpoint(self, node: Any) -> int:
        """The endpoint whose Thermostat cluster holds the schedule."""
        return ENDPOINT

    def _decode(self, node: Any, endpoint: int) -> Any:
        return read(node, endpoint)

    def _encode(self, current: Any, week: list[Any]) -> list[Any]:
        from chip.clusters.Objects import Thermostat

        if not any(week):
            return NO_SCHEDULE
        days_for_point: dict[Setpoint, int] = {}
        for d, points in enumerate(week):
            for point in points:
                days_for_point[point] = days_for_point.get(point, 0) | day_bit(d)
        return [
            Thermostat.Structs.ScheduleStruct(
                scheduleHandle=current.handle,
                systemMode=SYSTEM_MODE[current.mode],
                builtIn=current.handle == HANDLE,
                transitions=[
                    Thermostat.Structs.ScheduleTransitionStruct(
                        dayOfWeek=bits,
                        transitionTime=p.minute,
                        # A range's low heats and its high cools.
                        heatingSetpoint=round(p.temp * 100) if current.mode in ("heat", "range") else None,
                        coolingSetpoint=round((p.high if current.mode == "range" else p.temp) * 100)
                        if current.mode in ("cool", "range")
                        else None,
                    )
                    for p, bits in days_for_point.items()
                ],
            )
        ]

    def _matches(self, actual: Schedule, current: Schedule, days: dict[int, Any]) -> bool:
        return matches(actual, current, days, (9.0, 32.0))

    async def async_start(self, on_update: Callable[[Any], None]) -> Callable[[], None]:
        from matter_server.common.models import EventType

        client, node = self._node()
        endpoint = self._endpoint(node)

        @callback
        def updated(event: Any, data: Any) -> None:
            with contextlib.suppress(HomeAssistantError):
                on_update(self._decode(node, endpoint))

        @callback
        def back(event: Any, data: Any) -> None:
            # The server has the node again: its cache is current, so a read
            # that failed while it was away need not wait for the next poll.
            if getattr(data, "available", False):
                updated(event, data)

        stops = [
            client.subscribe_events(
                updated,
                event_filter=EventType.ATTRIBUTE_UPDATED,
                node_filter=node.node_id,
                attr_path_filter=_path(endpoint),
            ),
            client.subscribe_events(back, event_filter=EventType.NODE_UPDATED, node_filter=node.node_id),
        ]
        if self.kind == "schedule" and f"{endpoint}/{THERMOSTAT}/79" in node.node_data.attributes:
            stops.append(
                client.subscribe_events(
                    updated,
                    event_filter=EventType.ATTRIBUTE_UPDATED,
                    node_filter=node.node_id,
                    attr_path_filter=f"{endpoint}/{THERMOSTAT}/79",
                )
            )

        def unsubscribe() -> None:
            for stop in stops:
                stop()

        return unsubscribe

    async def async_wake_screen(self) -> None:
        from chip.clusters.Objects import Identify

        client, node = self._node()
        await _call(client.send_device_command(node.node_id, ENDPOINT, Identify.Commands.Identify(identifyTime=5)))

    async def async_read(self) -> Any:
        client, node = self._node()
        endpoint = self._endpoint(node)
        active_path = f"{endpoint}/{THERMOSTAT}/79"
        if self.kind == "schedule" and active_path in node.node_data.attributes:
            await _call(client.refresh_attribute(node.node_id, active_path))
        await _call(client.refresh_attribute(node.node_id, _path(endpoint)))
        return self._decode(node, endpoint)

    async def async_write(self, current: Any, days: dict[int, Any]) -> None:
        from chip.clusters.Objects import Thermostat

        client, node = self._node()
        endpoint = self._endpoint(node)

        async def atomic(request: int) -> Any:
            return await client.send_device_command(
                node.node_id,
                endpoint,
                Thermostat.Commands.AtomicRequest(
                    requestType=request,
                    attributeRequests=[SCHEDULES],
                    timeout=ATOMIC_TIMEOUT if request == BEGIN_WRITE else None,
                ),
            )

        async def checked_read() -> Any:
            fresh = await self.async_read()
            if getattr(fresh, "mode", None) != getattr(current, "mode", None) or getattr(
                fresh, "handle", None
            ) != getattr(current, "handle", None):
                raise error(f"changed_while_editing_{self.kind}")
            return fresh

        await checked_read()
        _check("begin", await _call(atomic(BEGIN_WRITE)))
        try:
            current = await checked_read()
            week = [days.get(d, current.days[d]) for d in range(7)]
            value = self._encode(current, week)
            if isinstance(current, Schedule):
                schedules = node.get_attribute_value(endpoint, Thermostat, Thermostat.Attributes.Schedules) or []
                active = next((s for s in schedules if s.scheduleHandle == current.handle), None)
                # Matter requires at least one transition. With alternatives present,
                # target the active handle explicitly and keep every other schedule.
                if not value and (current.handle != HANDLE or any(s.scheduleHandle != HANDLE for s in schedules)):
                    value = [
                        Thermostat.Structs.ScheduleStruct(
                            scheduleHandle=current.handle,
                            systemMode=SYSTEM_MODE[current.mode],
                            builtIn=current.handle == HANDLE,
                            transitions=[Thermostat.Structs.ScheduleTransitionStruct(dayOfWeek=127, transitionTime=0)],
                        )
                    ]
                if value and active is not None:
                    value[0].name = active.name
                value = [*value, *(s for s in schedules if s.scheduleHandle != current.handle)]
            await _call(client.write_attribute(node.node_id, _path(endpoint), value))
            _check("commit", await _call(atomic(COMMIT_WRITE)))
        except HomeAssistantError:
            with contextlib.suppress(Exception):
                await atomic(ROLLBACK_WRITE)
            raise
        # A write the server didn't pass on commits nothing: check the unit.
        if not self._matches(await self.async_read(), current, days):
            raise error(f"matter_not_taken_{self.kind}")


class MatterHotWater(MatterBackend, HotWaterBackend):
    """One unit's hot water on Matter: the Thermostat cluster of its Water
    Heater endpoint, which it has only with hot water control."""

    kind = "hot_water"
    _stop_watching: Callable[[], None] | None = None

    def _endpoint(self, node: Any) -> int:
        if (endpoint := water_heater(node)) is None:
            raise error("matter_no_water_heater", device=self.device_id)
        return endpoint

    def _decode(self, node: Any, endpoint: int) -> HotWater:
        return read_hot_water(node, endpoint)

    def _encode(self, current: Any, week: list[Periods]) -> list[Any]:
        from chip.clusters.Objects import Thermostat

        days_for_mark: dict[tuple[int, int], int] = {}
        for d, periods in enumerate(week):
            for p in periods:
                # Off the next day for a period past midnight.
                for day, minute, mode in ((d, p.on, MODE_HEAT), (d + p.end // DAY_MINUTES, p.off, MODE_OFF)):
                    mark = (minute, mode)
                    days_for_mark[mark] = days_for_mark.get(mark, 0) | day_bit(day % 7)
        transitions = [
            Thermostat.Structs.ScheduleTransitionStruct(dayOfWeek=bits, transitionTime=minute, systemMode=mode)
            for (minute, mode), bits in days_for_mark.items()
        ]
        if not transitions:
            return NO_SCHEDULE
        return [
            Thermostat.Structs.ScheduleStruct(
                scheduleHandle=HOT_WATER_HANDLE, systemMode=MODE_HEAT, builtIn=True, transitions=transitions
            )
        ]

    def _matches(self, actual: HotWater, current: HotWater, days: dict[int, Periods]) -> bool:
        week = [days.get(d, day) for d, day in enumerate(current.days)]

        # The unit can join touching periods, including across Sunday midnight.
        def intervals(days: tuple[Periods, ...] | list[Periods]) -> list[tuple[int, int]]:
            end_of_week = 7 * DAY_MINUTES
            spans = []
            for day, periods in enumerate(days):
                for p in periods:
                    start, end = day * DAY_MINUTES + p.on, day * DAY_MINUTES + p.end
                    spans.append((start, min(end, end_of_week)))
                    if end > end_of_week:
                        spans.append((0, end - end_of_week))
            joined = []
            for start, end in sorted(spans):
                if joined and joined[-1][1] == start:
                    start = joined.pop()[0]
                joined.append((start, end))
            return joined

        return intervals(actual.days) == intervals(week)

    async def async_present(self) -> bool:
        try:
            _, node = self._node()
        except HomeAssistantError:
            return False
        return water_heater(node) is not None

    async def async_watch(self, present: bool, on_change: Callable[[], None]) -> None:
        async def check(_now: Any) -> None:
            if await self.async_present() != present:
                on_change()

        stops = [async_track_time_interval(self.hass, check, self.poll)]
        # nestlocal adds the Water Heater endpoint as soon as the unit reports
        # hot water: look again when the node's endpoints change, the poll
        # above being the fallback.
        with contextlib.suppress(HomeAssistantError):
            from matter_server.common.models import EventType

            client, node = self._node()

            @callback
            def endpoints_changed(event: Any, data: Any) -> None:
                self.hass.async_create_task(check(None))

            for event in (EventType.ENDPOINT_ADDED, EventType.ENDPOINT_REMOVED):
                stops.append(client.subscribe_events(endpoints_changed, event_filter=event, node_filter=node.node_id))

        def stop() -> None:
            for s in stops:
                s()

        self._stop_watching = stop
        # An endpoint gained or lost since setup decided.
        await check(None)

    @callback
    def async_stop(self) -> None:
        if self._stop_watching is not None:
            self._stop_watching()
            self._stop_watching = None


def _path(endpoint: int) -> str:
    return f"{endpoint}/{THERMOSTAT}/{SCHEDULES}"


def water_heater(node: Any) -> int | None:
    """The node's Water Heater endpoint that carries a schedule, if any."""
    from chip.clusters.Objects import Thermostat, WaterHeaterManagement
    from matter_server.client.models.device_types import WaterHeater

    for number, endpoint in sorted(node.endpoints.items()):
        if (WaterHeater in endpoint.device_types or endpoint.has_cluster(WaterHeaterManagement)) and (
            endpoint.has_attribute(Thermostat, Thermostat.Attributes.Schedules)
        ):
            return number
    return None


def _schedule(node: Any, endpoint: int, handle: bytes) -> Any:
    """The built-in schedule `handle` in the node's cache, or None: an
    empty week."""
    from chip.clusters.Objects import Thermostat

    if endpoint not in node.endpoints:
        raise error("matter_no_endpoint", endpoint=endpoint)
    if not node.endpoints[endpoint].has_attribute(Thermostat, Thermostat.Attributes.Schedules):
        raise error("nestlocal_too_old")
    schedules = node.get_attribute_value(endpoint, Thermostat, Thermostat.Attributes.Schedules) or []
    return next((s for s in schedules if s.scheduleHandle == handle), None)


def read_hot_water(node: Any, endpoint: int) -> HotWater:
    """The hot water week in the node's cache: each on paired with the off
    after it, which may be the next day."""
    found = _schedule(node, endpoint, HOT_WATER_HANDLE)
    marks = sorted(
        (d * DAY_MINUTES + int(t.transitionTime), int(t.systemMode or MODE_OFF) != MODE_OFF)
        for t in (found.transitions if found is not None else [])
        for d in range(7)
        if int(t.dayOfWeek) & day_bit(d)
    )
    week: list[list[Period]] = [[] for _ in range(7)]
    for i, (at, on) in enumerate(marks):
        if not on:
            continue
        off = next((m for m, o in marks[i + 1 :] + marks[:i] if not o), None)
        if off is not None:
            day, minute = divmod(at, DAY_MINUTES)
            week[day].append(Period(minute, off % DAY_MINUTES))
    return HotWater(tuple(tuple(sorted(day)) for day in week))


def read(node: Any, endpoint: int = ENDPOINT) -> Schedule:
    """The heating schedule in the node's attribute cache."""
    from chip.clusters.Objects import Thermostat

    handle = HANDLE
    if endpoint in node.endpoints and node.endpoints[endpoint].has_attribute(
        Thermostat, Thermostat.Attributes.ActiveScheduleHandle
    ):
        active = node.get_attribute_value(endpoint, Thermostat, Thermostat.Attributes.ActiveScheduleHandle)
        if isinstance(active, bytes):
            handle = active
        else:
            # While paused, the unit lists the selected logical week first.
            schedules = node.get_attribute_value(endpoint, Thermostat, Thermostat.Attributes.Schedules) or []
            if schedules:
                handle = schedules[0].scheduleHandle
    nest = _schedule(node, endpoint, handle)
    modes = {v: k for k, v in SYSTEM_MODE.items()}
    if nest is None:
        # An empty week, of the kind ScheduleTypes offers a schedule of.
        types = node.get_attribute_value(endpoint, Thermostat, Thermostat.Attributes.ScheduleTypes) or []
        offered = (t for t in types if int(t.systemMode) in modes and int(t.numberOfSchedules or 0) > 0)
        mode = next((modes[int(t.systemMode)] for t in offered), "unknown")
        return Schedule(mode, ((),) * 7, handle)
    mode = modes.get(int(nest.systemMode), "unknown")
    week: list[dict[int, Setpoint]] = [{} for _ in range(7)]
    for t in nest.transitions:
        # As reported: a rounded setpoint written back would change a day
        # the edit left alone. A range's low heats and its high cools.
        minute = int(t.transitionTime)
        if mode == "range":
            if t.heatingSetpoint is None or t.coolingSetpoint is None:
                continue
            point = Setpoint(minute, t.heatingSetpoint / 100, t.coolingSetpoint / 100)
        else:
            value = t.heatingSetpoint if mode == "heat" else t.coolingSetpoint
            if value is None:
                continue
            point = Setpoint(minute, value / 100)
        for d in range(7):
            if int(t.dayOfWeek) & day_bit(d):
                week[d][minute] = point
    return Schedule(mode, tuple(tuple(day[m] for m in sorted(day)) for day in week), handle)


async def _call(request: Any) -> Any:
    """A client request, its failures as Home Assistant errors."""
    try:
        return await request
    except HomeAssistantError:
        raise
    except Exception as err:
        raise error("matter_failed", error=err) from err


def _field(obj: Any, name: str, tag: int) -> Any:
    """A response field, keyed by its name or its TLV tag."""
    if isinstance(obj, dict):
        return obj.get(name, obj.get(str(tag)))
    return getattr(obj, name, None)


def _check(step: str, response: Any) -> None:
    """Raises unless an AtomicResponse says success."""
    if response is None:
        raise error(f"{step}_no_answer")
    if int(_field(response, "statusCode", 0) or 0) == 0:
        return
    statuses = _field(response, "attributeStatus", 1) or []
    code = int(_field(statuses[0], "statusCode", 1) or 1) if statuses else 1
    raise error(f"{step}_refused", status=STATUS.get(code, f"status 0x{code:02X}"))
