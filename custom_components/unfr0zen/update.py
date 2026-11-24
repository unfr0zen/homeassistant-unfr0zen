"""Read-only status of the thermostat's unfr0zen firmware update."""

from collections.abc import Callable, Mapping
import json
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.update import UpdateDeviceClass, UpdateEntity, UpdateEntityFeature
from homeassistant.const import STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from . import ScheduleConfigEntry
from .const import CONF_BASE, CONF_NAME, CONF_TRANSPORT, NESTLOCAL, Z2M
from .device import thermostat_device


async def async_setup_entry(
    hass: HomeAssistant, entry: ScheduleConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([ThermostatUpdate(entry)])


class ThermostatUpdate(UpdateEntity):
    _attr_has_entity_name = True
    _attr_name = "unfr0zen"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_supported_features = UpdateEntityFeature.PROGRESS
    _attr_should_poll = False
    _attr_available = False

    def __init__(self, entry: ScheduleConfigEntry) -> None:
        self.entry = entry
        self.device_entry = entry.runtime_data.device
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}-firmware_update"

    @callback
    def _show(self, values: Mapping[str, Any] | None) -> None:
        self._attr_available = values is not None
        values = values or {}
        installed = values.get("installed_version")
        latest = values.get("latest_version")
        self._attr_installed_version = str(installed) if isinstance(installed, (str, int)) else None
        self._attr_latest_version = str(latest) if isinstance(latest, (str, int)) else None
        self._attr_release_url = values.get("release_url")
        self._attr_in_progress = values.get("in_progress", False)
        self._attr_update_percentage = values.get("update_percentage")
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        data = self.entry.data
        if data[CONF_TRANSPORT] in (NESTLOCAL, Z2M):
            direct = data[CONF_TRANSPORT] == NESTLOCAL
            topic = f"{data[CONF_BASE]}/update" if direct else f"{data[CONF_BASE]}/{data[CONF_NAME]}"

            @callback
            def received(msg: mqtt.ReceiveMessage) -> None:
                if not msg.payload:
                    self._show(None)
                    return
                try:
                    payload = json.loads(msg.payload)
                except ValueError, TypeError:
                    return
                if not isinstance(payload, dict):
                    return
                if direct:
                    self._show(payload if "installed_version" in payload else None)
                elif "update" in payload:
                    ota = payload["update"]
                    self._show(
                        ota
                        | {
                            "in_progress": ota.get("state") == "updating",
                            "update_percentage": ota.get("progress"),
                        }
                        if isinstance(ota, dict)
                        else None
                    )

            self.async_on_remove(await mqtt.async_subscribe(self.hass, topic, received))
            return

        # ZHA and Matter expose their firmware update on the source device.
        source_id: str | None = None
        unsubscribe: Callable[[], None] | None = None

        @callback
        def refresh(event: Event | None = None) -> None:
            state = self.hass.states.get(source_id) if source_id else None
            self._show(state.attributes if state and state.state != STATE_UNAVAILABLE else None)

        @callback
        def bind(event: Event | None = None) -> None:
            nonlocal source_id, unsubscribe
            if (
                event
                and event.event_type == er.EVENT_ENTITY_REGISTRY_UPDATED
                and not event.data["entity_id"].startswith("update.")
            ):
                return
            source = thermostat_device(self.hass, self.entry.data)
            entities = er.async_entries_for_device(er.async_get(self.hass), source.id) if source else ()
            entity_id = next(
                (
                    e.entity_id
                    for e in entities
                    if e.domain == "update" and e.original_device_class == UpdateDeviceClass.FIRMWARE and not e.disabled
                ),
                None,
            )
            if entity_id == source_id:
                return
            if unsubscribe:
                unsubscribe()
            source_id = entity_id
            unsubscribe = async_track_state_change_event(self.hass, entity_id, refresh) if entity_id else None
            refresh()

        self.async_on_remove(lambda: unsubscribe() if unsubscribe else None)
        self.async_on_remove(self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, bind))
        self.async_on_remove(self.hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, bind))
        bind()
