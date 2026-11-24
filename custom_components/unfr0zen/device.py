"""Identity of the thermostat behind a schedule entry."""

from collections.abc import Mapping
import json
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import CONF_BASE, CONF_DEVICE, CONF_IEEE, CONF_NAME, CONF_TRANSPORT, DOMAIN, MATTER, NESTLOCAL, Z2M, ZHA

CONNECTION = {NESTLOCAL: "MQTT", Z2M: "Zigbee", ZHA: "Zigbee", MATTER: "Matter"}


def thermostat_device(hass: HomeAssistant, data: Mapping[str, Any]) -> dr.DeviceEntry | None:
    """The unit's device, which the integration it talks through owns."""
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


def thermostat_name(data: Mapping[str, Any], device: dr.DeviceEntry | None) -> str:
    """The device's display name, including any name set in Home Assistant."""
    transport = data[CONF_TRANSPORT]
    name = (device.name_by_user or device.name) if device else None
    if transport == NESTLOCAL and name == data[CONF_BASE]:
        name = None
    if transport == Z2M and (not name or name == data[CONF_NAME]):
        name = data[CONF_NAME].rsplit("/", 1)[-1]
    return name or "Nest Thermostat"


def thermostat_title(data: Mapping[str, Any], device: dr.DeviceEntry | None) -> str:
    return f"{thermostat_name(data, device)} · {CONNECTION[data[CONF_TRANSPORT]]}"


def register_thermostat(hass: HomeAssistant, entry: ConfigEntry) -> dr.DeviceEntry:
    """Register the unfr0zen device and follow its transport's metadata."""
    registry = dr.async_get(hass)
    identifier = (DOMAIN, entry.entry_id)
    existing = registry.async_get_device_by_identifier(identifier, entry.entry_id)

    def sync(source: dr.DeviceEntry | None, existing: dr.DeviceEntry | None) -> dr.DeviceEntry:
        identity = source or existing
        name = existing.name if existing and not source else thermostat_name(entry.data, source)
        serial = identity.serial_number if identity else None
        if not serial and entry.data[CONF_TRANSPORT] == NESTLOCAL:
            serial = entry.data[CONF_BASE].rsplit("/", 1)[-1]
        return registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={identifier},
            name=name,
            serial_number=serial,
            manufacturer=identity.manufacturer if identity else None,
            hw_version=identity.hw_version if identity else None,
            sw_version=(source.sw_version if source else None) or (existing.sw_version if existing else None),
            # HA renders its expandable device row as area, model, entity count.
            model=serial,
            via_device_id=source.id if source else None,
        )

    source = thermostat_device(hass, entry.data)
    device = sync(source, existing)
    if source and source.area_id and not device.area_id:
        registry.async_update_device(device.id, area_id=source.area_id)

    def place_calendars(source: dr.DeviceEntry | None) -> None:
        entities = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(entities, entry.entry_id):
            if entity.domain == "calendar" and entity.device_id in (None, device.id):
                target = source.id if source else None
                if target != entity.device_id:
                    entities.async_update_entity(entity.entity_id, device_id=target)

    place_calendars(source)

    @callback
    def update_title(device: dr.DeviceEntry) -> None:
        title = thermostat_title(entry.data, device)
        if title != entry.title:
            hass.config_entries.async_update_entry(entry, title=title)

    update_title(device)

    @callback
    def changed(event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        current = registry.async_get(device.id)
        if current is None:
            return
        if event.data["device_id"] == current.id:
            update_title(current)
            return
        source = thermostat_device(hass, entry.data)
        if source is None or source.id != event.data["device_id"]:
            return
        sync(source, current)
        if source.id != current.via_device_id:
            place_calendars(source)
        previous = event.data.get("changes", {})
        follows_area = current.area_id is None or current.area_id == previous.get("area_id")
        if follows_area and current.area_id != source.area_id:
            registry.async_update_device(current.id, area_id=source.area_id)

    entry.async_on_unload(hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, changed))
    return device


async def track_mqtt_firmware(hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry) -> None:
    """Read firmware from nestlocal state or the Zigbee2MQTT bridge device list."""
    registry = dr.async_get(hass)
    direct = entry.data[CONF_TRANSPORT] == NESTLOCAL
    topic = f"{entry.data[CONF_BASE]}/" + ("state" if direct else "bridge/devices")

    @callback
    def received(msg: mqtt.ReceiveMessage) -> None:
        try:
            payload = json.loads(msg.payload)
        except ValueError, TypeError:
            return
        if direct and isinstance(payload, dict):
            version = payload.get("current_version")
        elif not direct and isinstance(payload, list):
            version = next(
                (
                    unit.get("software_build_id")
                    for unit in payload
                    if isinstance(unit, dict) and unit.get("ieee_address") == entry.data[CONF_IEEE]
                ),
                None,
            )
        else:
            return
        current = registry.async_get(device.id)
        if current and isinstance(version, str) and version and current.sw_version != version:
            registry.async_update_device(device.id, sw_version=version)

    entry.async_on_unload(await mqtt.async_subscribe(hass, topic, received))
