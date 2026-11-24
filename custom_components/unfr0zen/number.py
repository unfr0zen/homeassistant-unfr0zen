"""Thermostat display brightness and timeout."""

from homeassistant.components.number import NumberEntity
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ScheduleConfigEntry
from .display import DisplayEntity, add_display_entities


async def async_setup_entry(
    hass: HomeAssistant, entry: ScheduleConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    add_display_entities(entry, async_add_entities, "number", DisplayNumber)


class DisplayNumber(DisplayEntity, NumberEntity):
    def __init__(self, entry: ScheduleConfigEntry, key: str) -> None:
        super().__init__(entry, key)
        if key == "screen_timeout":
            self._attr_native_min_value, self._attr_native_max_value, self._attr_native_step = 10, 600, 5
            self._attr_native_unit_of_measurement = UnitOfTime.SECONDS
        else:
            self._attr_native_min_value, self._attr_native_max_value, self._attr_native_step = 0, 100, 1
            self._attr_native_unit_of_measurement = PERCENTAGE

    @property
    def native_value(self) -> float | None:
        try:
            return float(self.coordinator.data[self.key])
        except TypeError, ValueError, KeyError:
            return None

    async def async_set_native_value(self, value: float) -> None:
        if not float(value).is_integer():
            raise ServiceValidationError("The thermostat requires a whole number")
        await self.coordinator.async_write(self.key, int(value), self._context)
