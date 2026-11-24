"""Wake the thermostat display."""

from homeassistant.components import mqtt
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ScheduleConfigEntry
from .const import CONF_BASE, CONF_TRANSPORT, NESTLOCAL


async def async_setup_entry(
    hass: HomeAssistant, entry: ScheduleConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([WakeScreen(entry)])


class WakeScreen(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "wake_screen"
    _attr_icon = "mdi:gesture-tap-button"

    def __init__(self, entry: ScheduleConfigEntry) -> None:
        self.entry = entry
        self.device_entry = entry.runtime_data.device
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}-wake_screen"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.entry.data[CONF_TRANSPORT] == NESTLOCAL:
            self._attr_available = False

            @callback
            def availability(msg: mqtt.ReceiveMessage) -> None:
                self._attr_available = msg.payload == "online"
                self.async_write_ha_state()

            self.async_on_remove(
                await mqtt.async_subscribe(self.hass, f"{self.entry.data[CONF_BASE]}/availability", availability)
            )

    async def async_press(self) -> None:
        await self.entry.runtime_data.schedule.backend.async_wake_screen()
