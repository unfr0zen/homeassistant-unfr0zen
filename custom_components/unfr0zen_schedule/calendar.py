"""The calendar: each setpoint is a weekly event lasting until the next."""

from __future__ import annotations

import datetime as dt
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEntityFeature, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import ScheduleConfigEntry, ScheduleCoordinator
from .const import CONF_BASE, CONF_DEVICE, CONF_IEEE, CONF_TRANSPORT, MATTER, NESTLOCAL, Z2M, ZHA
from .schedule import (
    RRULE_DAYS,
    Day,
    Occurrence,
    from_uid,
    occurrences,
    parse_event,
    summary,
    uid,
    with_setpoint,
    without,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ScheduleConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """The entry's one calendar."""
    async_add_entities([ScheduleCalendar(entry.runtime_data, _device(hass, entry))])


def _device(hass: HomeAssistant, entry: ScheduleConfigEntry) -> DeviceInfo | None:
    """The unit's device, as the integration it talks through made it."""
    data = entry.data
    transport = data[CONF_TRANSPORT]
    if transport == NESTLOCAL:
        serial = data[CONF_BASE].rsplit("/", 1)[-1]
        return DeviceInfo(identifiers={("mqtt", f"nestlocal_{serial.lower()}")})
    if transport == Z2M:
        return DeviceInfo(identifiers={("mqtt", f"zigbee2mqtt_{data[CONF_IEEE]}")})
    if transport == ZHA:
        return DeviceInfo(identifiers={("zha", data[CONF_IEEE])})
    if transport == MATTER and (device := dr.async_get(hass).async_get(data[CONF_DEVICE])):
        return DeviceInfo(identifiers=device.identifiers)
    return None


class ScheduleCalendar(CoordinatorEntity[ScheduleCoordinator], CalendarEntity):
    """The Nest's weekly schedule."""

    _attr_has_entity_name = True
    _attr_translation_key = "schedule"
    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT | CalendarEntityFeature.DELETE_EVENT | CalendarEntityFeature.UPDATE_EVENT
    )

    def __init__(self, coordinator: ScheduleCoordinator, device: DeviceInfo | None) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.config_entry.unique_id or coordinator.config_entry.entry_id
        self._attr_device_info = device

    @property
    def event(self) -> CalendarEvent | None:
        """The setpoint in effect now."""
        now = dt_util.now()
        found = occurrences(self.coordinator.data, now, now + dt.timedelta(seconds=1), dt_util.get_default_time_zone())
        return _event(found[0]) if found else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: dt.datetime, end_date: dt.datetime
    ) -> list[CalendarEvent]:
        tz = dt_util.get_default_time_zone()
        return [_event(o) for o in occurrences(self.coordinator.data, start_date, end_date, tz)]

    async def async_create_event(self, **kwargs: Any) -> None:
        days, setpoint = parse_event(kwargs, dt_util.get_default_time_zone())
        week = self._writable()
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
        days, setpoint = parse_event(event, dt_util.get_default_time_zone())
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
            raise HomeAssistantError(f"The Nest's {schedule.mode} schedule can't be changed from here")
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
