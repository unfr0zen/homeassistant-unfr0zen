"""Display settings advertised by the thermostat."""

from collections.abc import Callable
import json
import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import CONF_BASE, CONF_IEEE, CONF_NAME, CONF_TRANSPORT, NESTLOCAL, Z2M, ZHA
from .device import thermostat_device

# ZHA identifies these standard Analog Output and Binary Output clusters by endpoint.
ZHA_OUTPUTS = {
    "screen_timeout": (40, 13),
    "screen_always_on": (41, 16),
    "screen_brightness": (42, 13),
    "screen_brightness_auto": (43, 16),
}
DOMAINS = {
    "theme": "select",
    "screen_always_on": "switch",
    "screen_brightness": "number",
    "screen_brightness_auto": "switch",
    "screen_timeout": "number",
}


class DisplaySettings(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, logging.getLogger(__name__), name="Thermostat display", config_entry=entry)
        self.data = {}
        self.entry = entry
        self.transport = entry.data[CONF_TRANSPORT]
        self.online = True
        self.sources: dict[str, str] = {}

    async def async_start(self) -> None:
        if self.transport in (NESTLOCAL, Z2M):
            base = self.entry.data[CONF_BASE]
            direct = self.transport == NESTLOCAL
            self.topic = base if direct else f"{base}/{self.entry.data[CONF_NAME]}"
            state = f"{base}/state" if direct else self.topic
            self.entry.async_on_unload(await mqtt.async_subscribe(self.hass, state, self._received))
            self.entry.async_on_unload(
                await mqtt.async_subscribe(self.hass, f"{self.topic}/availability", self._availability)
            )
            return
        self._watch_entities()

    @callback
    def _received(self, msg: mqtt.ReceiveMessage) -> None:
        if not msg.payload:
            self.async_set_updated_data({})
            return
        try:
            payload = json.loads(msg.payload)
        except ValueError, TypeError:
            return
        if not isinstance(payload, dict):
            return
        values = {key: payload[key] for key in DOMAINS if key in payload}
        if "themes" in payload:
            catalog = payload["themes"]
            values["themes"] = (
                [item["label"] for item in catalog if isinstance(item, dict) and isinstance(item.get("label"), str)]
                if isinstance(catalog, list)
                else []
            )
        if self.transport == Z2M:
            values = self.data | values
        if values != self.data:
            self.async_set_updated_data(values)

    @callback
    def _availability(self, msg: mqtt.ReceiveMessage) -> None:
        state = msg.payload
        if self.transport == Z2M:
            try:
                payload = json.loads(state)
            except TypeError, ValueError:
                payload = None
            if isinstance(payload, dict):
                state = payload.get("state")
        self.online = state == "online"
        self.async_update_listeners()

    def _watch_entities(self) -> None:
        """Follow writable controls on the exact ZHA or Matter device, including renames."""
        unsubscribe: Callable[[], None] | None = None

        @callback
        def read(event: Event | None = None) -> None:
            values = {}
            for key, entity_id in self.sources.items():
                state = self.hass.states.get(entity_id)
                if state is None or state.state == STATE_UNAVAILABLE:
                    continue
                values[key] = state.state
                if key == "theme":
                    values["themes"] = state.attributes.get("options", [])
            if self.data != values:
                self.async_set_updated_data(values)

        @callback
        def bind(event: Event | None = None) -> None:
            nonlocal unsubscribe
            source = thermostat_device(self.hass, self.entry.data)
            entities = er.async_entries_for_device(er.async_get(self.hass), source.id) if source else ()
            sources = {}
            for entity in entities:
                if entity.disabled or entity.platform != self.transport:
                    continue
                for key, domain in DOMAINS.items():
                    if entity.domain != domain:
                        continue
                    label = "home screen theme" if key == "theme" else key.replace("_", " ")
                    name = (entity.original_name or "").casefold()
                    matches = name == label or name.endswith(f"({label})")
                    if self.transport == ZHA and key in ZHA_OUTPUTS:
                        ep, cluster = ZHA_OUTPUTS[key]
                        matches = matches or entity.unique_id == f"{self.entry.data[CONF_IEEE]}-{ep}-{cluster}"
                    if matches:
                        sources[key] = entity.entity_id
            if sources == self.sources:
                return
            if unsubscribe:
                unsubscribe()
            self.sources = sources
            unsubscribe = async_track_state_change_event(self.hass, list(sources.values()), read) if sources else None
            read()

        self.entry.async_on_unload(lambda: unsubscribe() if unsubscribe else None)
        self.entry.async_on_unload(self.hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, bind))
        self.entry.async_on_unload(self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, bind))
        bind()

    async def async_write(self, key: str, value: str | int | bool, context: Context | None) -> None:
        if not self.online or key not in self.data:
            raise HomeAssistantError("Display setting unavailable")
        if self.transport in (NESTLOCAL, Z2M):
            if isinstance(value, bool):
                value = "ON" if value else "OFF"
            if self.transport == NESTLOCAL:
                await mqtt.async_publish(self.hass, f"{self.topic}/set/{key}", str(value), qos=1, retain=False)
            else:
                await mqtt.async_publish(self.hass, f"{self.topic}/set", json.dumps({key: value}), retain=False)
            return
        target = {"entity_id": self.sources[key]}
        domain = DOMAINS[key]
        if domain == "switch":
            service = "turn_on" if value else "turn_off"
        elif domain == "number":
            service = "set_value"
            target["value"] = value
        else:
            service = "select_option"
            target["option"] = value
        await self.hass.services.async_call(domain, service, target, blocking=True, context=context)


class DisplayEntity(CoordinatorEntity[DisplaySettings]):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, key: str) -> None:
        super().__init__(entry.runtime_data.display)
        self.key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}-{key}"
        self.device_entry = entry.runtime_data.device

    @property
    def available(self) -> bool:
        return self.coordinator.online and self.key in self.coordinator.data


def add_display_entities(
    entry: ConfigEntry, add: AddConfigEntryEntitiesCallback, domain: str, entity: type[DisplayEntity]
) -> None:
    """Create each control once its transport reports support."""
    display = entry.runtime_data.display
    added: set[str] = set()

    @callback
    def discover() -> None:
        keys = [k for k in display.data if DOMAINS.get(k) == domain and k not in added]
        if keys:
            added.update(keys)
            add([entity(entry, key) for key in keys])

    entry.async_on_unload(display.async_add_listener(discover))
    discover()
