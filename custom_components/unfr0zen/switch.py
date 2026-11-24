"""Thermostat display switches."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ScheduleConfigEntry
from .display import DisplayEntity, add_display_entities


async def async_setup_entry(
    hass: HomeAssistant, entry: ScheduleConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    add_display_entities(entry, async_add_entities, "switch", DisplaySwitch)


class DisplaySwitch(DisplayEntity, SwitchEntity):
    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.get(self.key)
        if isinstance(value, bool):
            return value
        return {"on": True, "off": False}.get(str(value).lower())

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_write(self.key, True, self._context)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_write(self.key, False, self._context)
