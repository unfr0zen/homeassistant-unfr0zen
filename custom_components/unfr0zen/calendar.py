"""The calendars: each heating setpoint is a weekly event lasting until the
next; each hot water period a weekly event from on to off."""

from __future__ import annotations

import datetime as dt
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEntityFeature, CalendarEvent
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_platform, entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
import voluptuous as vol

from . import ScheduleConfigEntry, ScheduleCoordinator, hotwater
from .const import (
    CONF_BASE,
    CONF_DEVICE,
    CONF_IEEE,
    CONF_TRANSPORT,
    MATTER,
    NESTLOCAL,
    TEMP_LIMITS,
    TEMP_STEP,
    Z2M,
    ZHA,
    ZIGBEE_MATTER_LIMITS,
)
from .errors import error, invalid
from .schedule import (
    MAX_PER_DAY,
    RRULE_DAYS,
    Day,
    Occurrence,
    from_uid,
    occurrences,
    parse_days,
    parse_event,
    summary,
    uid,
    week_json,
    with_setpoint,
    without,
)

# Far enough ahead to find the next event of any weekly schedule.
AHEAD = dt.timedelta(days=8)


async def async_setup_entry(
    hass: HomeAssistant, entry: ScheduleConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """The entry's schedule, and hot water on a unit that has it."""
    device = _device(hass, entry)
    runtime = entry.runtime_data
    entities: list[CalendarEntity] = [ScheduleCalendar(runtime.schedule, device)]
    if runtime.hot_water is not None:
        entities.append(HotWaterCalendar(runtime.hot_water, device))
    async_add_entities(entities)
    # The cards' way to write a whole day; registered once for every entry.
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service("set_schedule", {vol.Required("days"): cv.match_all}, _set_schedule)
    platform.async_register_entity_service(
        "set_hot_water_schedule", {vol.Required("days"): cv.match_all}, _set_hot_water_schedule
    )


def _named(entity: _UnitCalendar, call: ServiceCall) -> bool:
    """Whether the call names the entity itself, not its device or area."""
    return entity.entity_id in cv.ensure_list(call.data.get(ATTR_ENTITY_ID))


async def _set_schedule(entity: _UnitCalendar, call: ServiceCall) -> None:
    # A device's other calendar is passed over unless named.
    if entity.kind == ScheduleCalendar.kind or _named(entity, call):
        await entity.async_set_schedule(call.data["days"])


async def _set_hot_water_schedule(entity: _UnitCalendar, call: ServiceCall) -> None:
    if entity.kind == HotWaterCalendar.kind or _named(entity, call):
        await entity.async_set_hot_water_schedule(call.data["days"])


def _device(hass: HomeAssistant, entry: ScheduleConfigEntry) -> dr.DeviceEntry | None:
    """The unit's device, which the integration it talks through owns."""
    data = entry.data
    registry = dr.async_get(hass)
    transport = data[CONF_TRANSPORT]
    if transport == MATTER:
        return registry.async_get(data[CONF_DEVICE])
    if transport == NESTLOCAL:
        identifier = ("mqtt", f"nestlocal_{data[CONF_BASE].rsplit('/', 1)[-1].lower()}")
    elif transport == Z2M:
        identifier = ("mqtt", f"zigbee2mqtt_{data[CONF_IEEE]}")
    else:
        identifier = (ZHA, data[CONF_IEEE])
    # Identifiers are unique within the config entry that owns the device.
    for owner in hass.config_entries.async_entries(identifier[0]):
        if device := registry.async_get_device_by_identifier(identifier, owner.entry_id):
            return device
    return None


class _UnitCalendar(CoordinatorEntity[ScheduleCoordinator], CalendarEntity):
    """What both calendars give the cards: their entry and the unit's
    related entities."""

    kind = ""

    def _base_attributes(self) -> dict[str, Any]:
        attributes: dict[str, Any] = {"kind": self.kind, "entry_id": self.coordinator.config_entry.entry_id}
        if self.device_entry is not None:
            attributes["related"] = _related(self.hass, self.device_entry)
        return attributes

    async def async_set_schedule(self, days: dict[str, Any]) -> None:
        raise invalid("hot_water_calendar", entity_id=self.entity_id)

    async def async_set_hot_water_schedule(self, days: dict[str, Any]) -> None:
        raise invalid("heating_calendar", entity_id=self.entity_id)


def _related(hass: HomeAssistant, device: dr.DeviceEntry) -> dict[str, str | None]:
    """The unit's climate entity, hot water schedule switch, hot water
    sensor, boost and water heater, on its device; nestlocal's discovery ids
    end in their keys, Zigbee2MQTT's in their keys and `_zigbee2mqtt`, and
    Matter's switches are named after their endpoints' labels."""

    def keyed(domain: str, *keys: str) -> str | None:
        for key in keys:
            for e in entries:
                if e.domain == domain and e.unique_id.endswith((f"_{key}", f"_{key}_zigbee2mqtt")):
                    return e.entity_id
        return None

    def labelled(domain: str, label: str) -> str | None:
        # Matter's are named after their endpoint's label: "Switch (Hot water schedule)".
        return next(
            (e.entity_id for e in entries if e.domain == domain and (e.original_name or "").endswith(f"({label})")),
            None,
        )

    entries = er.async_entries_for_device(er.async_get(hass), device.id)
    return {
        "climate": next((e.entity_id for e in entries if e.domain == "climate"), None),
        "hot_water_switch": keyed("switch", "hot_water_schedule") or labelled("switch", "Hot water schedule"),
        # nestlocal's hot_water_active, Zigbee2MQTT's hot_water.
        "hot_water_active": keyed("binary_sensor", "hot_water_active", "hot_water"),
        # nestlocal's select of minutes, Zigbee2MQTT's switch of a fixed boost.
        "hot_water_boost": keyed("select", "hot_water_boost")
        or keyed("switch", "hot_water_boost")
        or labelled("switch", "Hot water boost"),
        "water_heater": next((e.entity_id for e in entries if e.domain == "water_heater"), None),
    }


class ScheduleCalendar(_UnitCalendar):
    """The Nest's weekly schedule."""

    kind = "heating"

    _attr_has_entity_name = True
    _attr_translation_key = "schedule"
    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT | CalendarEntityFeature.DELETE_EVENT | CalendarEntityFeature.UPDATE_EVENT
    )

    def __init__(self, coordinator: ScheduleCoordinator, device: dr.DeviceEntry | None) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.config_entry.unique_id or coordinator.config_entry.entry_id
        # Shown on the unit's own device, without making one of its own;
        # by the entry's title until that device exists.
        self.device_entry = device
        if device is None:
            self._attr_name = f"{coordinator.config_entry.title} Heating schedule"
        self._limits = TEMP_LIMITS.get(coordinator.config_entry.data[CONF_TRANSPORT], ZIGBEE_MATTER_LIMITS)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        schedule = self.coordinator.data
        return self._base_attributes() | {
            "mode": schedule.mode,
            "writable": schedule.writable,
            "week": week_json(schedule),
            "min_temp": self._limits[0],
            "max_temp": self._limits[1],
            "temp_step": TEMP_STEP,
            "max_per_day": MAX_PER_DAY,
        }

    async def async_set_schedule(self, days: dict[str, Any]) -> None:
        """Replaces the named days whole."""
        self._writable()
        changes = parse_days(days, self._limits, self.coordinator.data.mode)
        await self.coordinator.async_write(changes)

    @property
    def event(self) -> CalendarEvent | None:
        """The setpoint in effect now, or the next; Home Assistant times the
        state's next change from it."""
        now = dt_util.now()
        found = occurrences(self.coordinator.data, now, now + AHEAD, dt_util.get_default_time_zone())
        return _event(min(found, key=lambda o: o.start)) if found else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: dt.datetime, end_date: dt.datetime
    ) -> list[CalendarEvent]:
        tz = dt_util.get_default_time_zone()
        return [_event(o) for o in occurrences(self.coordinator.data, start_date, end_date, tz)]

    async def async_create_event(self, **kwargs: Any) -> None:
        week = self._writable()
        days, setpoint = parse_event(kwargs, dt_util.get_default_time_zone(), self._limits, self.coordinator.data.mode)
        await self.coordinator.async_write({d: with_setpoint(week[d], setpoint) for d in days})

    async def async_update_event(
        self,
        uid: str,
        event: dict[str, Any],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        # One occurrence is its whole series: the schedule repeats every week.
        week = self._writable()
        day, old = from_uid(self.coordinator.data, uid)
        days, setpoint = parse_event(event, dt_util.get_default_time_zone(), self._limits, self.coordinator.data.mode)
        changes = {day: without(week[day], old)}
        for d in days:
            changes[d] = with_setpoint(changes.get(d, week[d]), setpoint)
        await self.coordinator.async_write(changes)

    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        week = self._writable()
        day, old = from_uid(self.coordinator.data, uid)
        await self.coordinator.async_write({day: without(week[day], old)})

    def _writable(self) -> tuple[Day, ...]:
        schedule = self.coordinator.data
        if not schedule.writable:
            raise error("not_writable", mode=schedule.mode)
        return schedule.days


def _event(o: Occurrence) -> CalendarEvent:
    return CalendarEvent(
        start=o.start,
        end=o.end,
        summary=summary(o.setpoint),
        uid=uid(o.day, o.setpoint),
        rrule=f"FREQ=WEEKLY;BYDAY={RRULE_DAYS[o.day]}",
        recurrence_id=o.start.strftime("%Y%m%dT%H%M%S"),
    )


class HotWaterCalendar(_UnitCalendar):
    """The Nest's weekly hot water schedule."""

    kind = "hot_water"

    _attr_has_entity_name = True
    _attr_translation_key = "hot_water"
    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT | CalendarEntityFeature.DELETE_EVENT | CalendarEntityFeature.UPDATE_EVENT
    )

    def __init__(self, coordinator: ScheduleCoordinator, device: dr.DeviceEntry | None) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}-hot_water"
        self.device_entry = device
        if device is None:
            self._attr_name = f"{entry.title} Hot water schedule"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._base_attributes() | {
            "writable": True,
            "week": hotwater.week_json(self.coordinator.data),
            "max_per_day": hotwater.MAX_PER_DAY,
        }

    async def async_set_hot_water_schedule(self, days: dict[str, Any]) -> None:
        """Replaces the named days whole."""
        await self._write(hotwater.parse_days(days))

    async def _write(self, changes: dict[int, hotwater.Periods]) -> None:
        """Writes `changes` once the week they make has no overlap."""
        week = list(self.coordinator.data.days)
        for d, periods in changes.items():
            week[d] = periods
        hotwater.check_week(week)
        await self.coordinator.async_write(changes)

    @property
    def event(self) -> CalendarEvent | None:
        """The period hot water is on in now, or the next: between periods
        the state is off until the next one starts."""
        now = dt_util.now()
        found = hotwater.occurrences(self.coordinator.data, now, now + AHEAD, dt_util.get_default_time_zone())
        return _hot_water_event(min(found, key=lambda o: o.start)) if found else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: dt.datetime, end_date: dt.datetime
    ) -> list[CalendarEvent]:
        tz = dt_util.get_default_time_zone()
        return [_hot_water_event(o) for o in hotwater.occurrences(self.coordinator.data, start_date, end_date, tz)]

    async def async_create_event(self, **kwargs: Any) -> None:
        days, period = hotwater.parse_event(kwargs, dt_util.get_default_time_zone())
        week = self.coordinator.data.days
        await self._write({d: hotwater.with_period(d, week[d], period) for d in days})

    async def async_update_event(
        self,
        uid: str,
        event: dict[str, Any],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        # One occurrence is its whole series: the schedule repeats every week.
        week = self.coordinator.data.days
        day, old = hotwater.from_uid(self.coordinator.data, uid)
        days, period = hotwater.parse_event(event, dt_util.get_default_time_zone())
        changes = {day: hotwater.without(week[day], old)}
        for d in days:
            changes[d] = hotwater.with_period(d, changes.get(d, week[d]), period)
        await self._write(changes)

    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        day, old = hotwater.from_uid(self.coordinator.data, uid)
        await self._write({day: hotwater.without(self.coordinator.data.days[day], old)})


def _hot_water_event(o: hotwater.Occurrence) -> CalendarEvent:
    return CalendarEvent(
        start=o.start,
        end=o.end,
        summary=hotwater.SUMMARY,
        uid=hotwater.uid(o.day, o.period),
        rrule=f"FREQ=WEEKLY;BYDAY={RRULE_DAYS[o.day]}",
        recurrence_id=o.start.strftime("%Y%m%dT%H%M%S"),
    )
