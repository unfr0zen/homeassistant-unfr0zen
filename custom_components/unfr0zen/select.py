"""The thermostat's installed home screen themes."""

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ScheduleConfigEntry
from .display import DisplayEntity, add_display_entities


async def async_setup_entry(
    hass: HomeAssistant, entry: ScheduleConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    add_display_entities(entry, async_add_entities, "select", ThemeSelect)


class ThemeSelect(DisplayEntity, SelectEntity):
    @property
    def options(self) -> list[str]:
        return self.coordinator.data.get("themes", [])

    @property
    def current_option(self) -> str | None:
        return self.coordinator.data.get(self.key)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_write(self.key, option, self._context)
