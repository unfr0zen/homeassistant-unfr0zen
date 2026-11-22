"""The Nest's own weekly schedule as a Home Assistant calendar."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .backend import Backend
from .const import CONF_BASE, CONF_DEVICE, CONF_IEEE, CONF_NAME, CONF_TRANSPORT, DOMAIN, MATTER, NESTLOCAL, Z2M, ZHA
from .matter_backend import MatterBackend
from .mqtt_backend import NestlocalBackend, Z2mBackend
from .schedule import Day, Schedule
from .zha_backend import ZhaBackend

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CALENDAR]

type ScheduleConfigEntry = ConfigEntry[ScheduleCoordinator]


class ScheduleCoordinator(DataUpdateCoordinator[Schedule]):
    """The unit's schedule, read through its backend."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, backend: Backend) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=backend.poll)
        self.backend = backend

    async def _async_update_data(self) -> Schedule:
        try:
            return await self.backend.async_read()
        except HomeAssistantError as err:
            raise UpdateFailed(str(err)) from err

    async def async_write(self, days: dict[int, Day]) -> None:
        """Replaces `days`; pushed backends report the result themselves."""
        days = {d: points for d, points in days.items() if points != self.data.days[d]}
        if not days:
            return
        await self.backend.async_write(self.data, days)
        if self.backend.poll is not None:
            self.async_set_updated_data(await self.backend.async_read())


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


async def async_setup_entry(hass: HomeAssistant, entry: ScheduleConfigEntry) -> bool:
    """Starts the backend and the calendar."""
    backend = backend_for(hass, dict(entry.data))
    coordinator = ScheduleCoordinator(hass, entry, backend)
    entry.async_on_unload(await backend.async_start(coordinator.async_set_updated_data))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ScheduleConfigEntry) -> bool:
    """Stops the calendar; the backend's listener stops with the entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
