"""The Nest's own weekly schedule as a Home Assistant calendar."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import datetime as dt
import logging
from pathlib import Path
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError, PlatformNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.loader import async_get_integration

from .backend import Backend, HotWaterBackend
from .const import (
    CARDS_URL,
    CONF_BASE,
    CONF_DEVICE,
    CONF_IEEE,
    CONF_NAME,
    CONF_TRANSPORT,
    DOMAIN,
    FRONTEND_URL,
    MATTER,
    NESTLOCAL,
    Z2M,
    ZHA,
)
from .device import register_thermostat, track_mqtt_firmware
from .display import DisplaySettings
from .matter_backend import MatterBackend, MatterHotWater
from .mqtt_backend import NestlocalBackend, NestlocalHotWater, Z2mBackend, Z2mHotWater
from .zha_backend import ZhaBackend, ZhaHotWater

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BUTTON, Platform.UPDATE, Platform.SELECT, Platform.SWITCH, Platform.NUMBER, Platform.CALENDAR]

# How long an entry whose hot water failed to start waits to try again.
HOT_WATER_RETRY = dt.timedelta(minutes=5)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# The cards, built beside the integration.
CARDS = Path(__file__).parent / "frontend" / "unfr0zen-cards.js"


class ScheduleCoordinator(DataUpdateCoordinator[Any]):
    """A weekly schedule of the unit, heating or hot water, read through its
    backend; its data has one entry per day in `days`."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, backend: Backend, name: str = DOMAIN) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=name, update_interval=backend.poll)
        self.backend = backend

    async def _async_update_data(self) -> Any:
        try:
            return await self.backend.async_read()
        except HomeAssistantError as err:
            raise UpdateFailed(str(err)) from err

    async def async_write(self, days: dict[int, Any]) -> None:
        """Replaces `days`; pushed backends report the result themselves."""
        days = {d: points for d, points in days.items() if points != self.data.days[d]}
        if not days:
            return
        await self.backend.async_write(self.data, days)
        if self.backend.poll is not None:
            self.async_set_updated_data(await self.backend.async_read())


@dataclass
class Runtime:
    """An entry's schedules: hot water only on a unit that has it."""

    schedule: ScheduleCoordinator
    device: dr.DeviceEntry
    display: DisplaySettings
    hot_water: ScheduleCoordinator | None = None


type ScheduleConfigEntry = ConfigEntry[Runtime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serves the cards and has the frontend load them, without a resource
    the user adds."""
    if not await hass.async_add_executor_job(CARDS.is_file):
        _LOGGER.debug("No cards at %s", CARDS)
        return True
    if getattr(hass, "http", None) is None:
        return True
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    # The folder: the cards fetch their locales beside them.
    await hass.http.async_register_static_paths([StaticPathConfig(FRONTEND_URL, str(CARDS.parent), False)])
    # Without the frontend there is nothing to load them into.
    if DATA_EXTRA_MODULE_URL in hass.data:
        version = (await async_get_integration(hass, DOMAIN)).version
        add_extra_js_url(hass, f"{CARDS_URL}?v={version}")
    return True


def backend_for(hass: HomeAssistant, data: dict) -> Backend:
    """The backend an entry's transport uses."""
    transport = data[CONF_TRANSPORT]
    if transport == NESTLOCAL:
        return NestlocalBackend(hass, data[CONF_BASE])
    if transport == Z2M:
        return Z2mBackend(hass, data[CONF_BASE], data[CONF_NAME])
    if transport == ZHA:
        return ZhaBackend(hass, data[CONF_IEEE])
    if transport == MATTER:
        return MatterBackend(hass, data[CONF_DEVICE])
    raise ValueError(f"unknown transport {transport}")


def hot_water_backend_for(hass: HomeAssistant, data: dict) -> HotWaterBackend | None:
    """The hot water backend an entry's transport uses."""
    transport = data[CONF_TRANSPORT]
    if transport == NESTLOCAL:
        return NestlocalHotWater(hass, data[CONF_BASE])
    if transport == Z2M:
        return Z2mHotWater(hass, data[CONF_BASE], data[CONF_NAME], data[CONF_IEEE])
    if transport == ZHA:
        return ZhaHotWater(hass, data[CONF_IEEE])
    if transport == MATTER:
        return MatterHotWater(hass, data[CONF_DEVICE])
    return None


async def _start(entry: ScheduleConfigEntry, coordinator: ScheduleCoordinator) -> None:
    try:
        stop = await coordinator.backend.async_start(coordinator.async_set_updated_data)
    except HomeAssistantError as err:
        raise PlatformNotReady(str(err)) from err
    coordinator.backend.request_refresh = coordinator.async_request_refresh
    try:
        await coordinator.async_refresh()
        if not coordinator.last_update_success:
            raise PlatformNotReady(str(coordinator.last_exception))
    except BaseException:
        coordinator.backend.request_refresh = None
        stop()
        raise
    entry.async_on_unload(stop)


async def async_setup_entry(hass: HomeAssistant, entry: ScheduleConfigEntry) -> bool:
    """Register the thermostat before its independent entity platforms start."""
    device = register_thermostat(hass, entry)
    if entry.data[CONF_TRANSPORT] in (NESTLOCAL, Z2M):
        if not await mqtt.async_wait_for_mqtt_client(hass):
            raise ConfigEntryNotReady("Waiting for MQTT")
        await track_mqtt_firmware(hass, entry, device)
    backend = backend_for(hass, dict(entry.data))
    display = DisplaySettings(hass, entry)
    await display.async_start()
    entry.runtime_data = Runtime(ScheduleCoordinator(hass, entry, backend), device, display)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_start_calendars(hass: HomeAssistant, entry: ScheduleConfigEntry) -> None:
    """Start the calendars when their transport can supply a schedule."""
    runtime = entry.runtime_data
    hot_water = hot_water_backend_for(hass, dict(entry.data))
    presence = None
    if hot_water is not None:
        # Whether the unit has hot water is learned while the heating starts.
        presence = hass.async_create_task(hot_water.async_present())
    try:
        await _start(entry, runtime.schedule)
    except BaseException:
        if presence is not None:
            presence.cancel()
            await asyncio.gather(presence, return_exceptions=True)
        if hot_water is not None:
            hot_water.async_stop()
        raise
    if hot_water is not None and presence is not None:
        entry.async_on_unload(hot_water.async_stop)
        present = await presence
        # The calendar comes and goes with the unit's hot water.
        await hot_water.async_watch(present, lambda: hass.config_entries.async_schedule_reload(entry.entry_id))
        if present:
            runtime.hot_water = ScheduleCoordinator(hass, entry, hot_water, f"{DOMAIN} hot water")
            try:
                await _start(entry, runtime.hot_water)
            except PlatformNotReady as err:
                # The heating goes on without it; the entry retries later.
                _LOGGER.warning(
                    "%s: hot water schedule unavailable, retrying in %s: %s", entry.title, HOT_WATER_RETRY, err
                )
                runtime.hot_water = None
                entry.async_on_unload(
                    async_call_later(
                        hass, HOT_WATER_RETRY, lambda _: hass.config_entries.async_schedule_reload(entry.entry_id)
                    )
                )


async def async_unload_entry(hass: HomeAssistant, entry: ScheduleConfigEntry) -> bool:
    """Stops the calendar; the backend's listener stops with the entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
