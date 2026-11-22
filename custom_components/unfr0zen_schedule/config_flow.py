"""Finding the unit: by its transport, then its topic or device."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import SelectOptionDict, SelectSelector, SelectSelectorConfig
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo
import voluptuous as vol

from .const import (
    CONF_BASE,
    CONF_DEVICE,
    CONF_IEEE,
    CONF_NAME,
    CONF_TRANSPORT,
    DOMAIN,
    MANUFACTURER,
    MATTER,
    MODEL,
    NESTLOCAL,
    Z2M,
    ZHA,
)

# How long to wait for a retained message when asked to look at a topic.
RETAINED_TIMEOUT = 5


class ScheduleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Adds one unit's schedule."""

    VERSION = 1

    def __init__(self) -> None:
        self._base = ""
        self._z2m: dict[str, str] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=[NESTLOCAL, Z2M, ZHA, MATTER])

    async def async_step_mqtt(self, discovery_info: MqttServiceInfo) -> ConfigFlowResult:
        """nestlocal's retained heating_times, seen by MQTT discovery."""
        base = discovery_info.topic.removesuffix("/heating_times")
        try:
            json.loads(discovery_info.payload)["days"]
        except ValueError, KeyError, TypeError:
            return self.async_abort(reason="not_a_schedule")
        await self.async_set_unique_id(f"{NESTLOCAL}:{base}")
        self._abort_if_unique_id_configured()
        self._base = base
        self.context["title_placeholders"] = {"base": base}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="confirm", description_placeholders={"base": self._base})
        return self.async_create_entry(title=self._base, data={CONF_TRANSPORT: NESTLOCAL, CONF_BASE: self._base})

    async def async_step_nestlocal(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            base = user_input[CONF_BASE].strip().rstrip("/")
            if not await mqtt.async_wait_for_mqtt_client(self.hass):
                return self.async_abort(reason="mqtt_not_ready")
            if await _retained(self, f"{base}/heating_times") is None:
                errors["base"] = "no_schedule"
            else:
                await self.async_set_unique_id(f"{NESTLOCAL}:{base}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=base, data={CONF_TRANSPORT: NESTLOCAL, CONF_BASE: base})
        return self.async_show_form(
            step_id="nestlocal",
            data_schema=vol.Schema({vol.Required(CONF_BASE, default="nestlocal/"): str}),
            errors=errors,
        )

    async def async_step_zigbee2mqtt(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            base = user_input[CONF_BASE].strip().rstrip("/")
            if not await mqtt.async_wait_for_mqtt_client(self.hass):
                return self.async_abort(reason="mqtt_not_ready")
            devices = await _retained(self, f"{base}/bridge/devices")
            self._base = base
            self._z2m = {
                d["friendly_name"]: d["ieee_address"]
                for d in (devices or [])
                if str((d.get("definition") or {}).get("model", "")).startswith(MODEL)
            }
            if self._z2m:
                return await self.async_step_zigbee2mqtt_device()
            errors["base"] = "no_device"
        return self.async_show_form(
            step_id="zigbee2mqtt",
            data_schema=vol.Schema({vol.Required(CONF_BASE, default="zigbee2mqtt"): str}),
            errors=errors,
        )

    async def async_step_zigbee2mqtt_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            name = user_input[CONF_NAME]
            ieee = self._z2m[name]
            await self.async_set_unique_id(f"{Z2M}:{ieee}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=name, data={CONF_TRANSPORT: Z2M, CONF_BASE: self._base, CONF_NAME: name, CONF_IEEE: ieee}
            )
        return self.async_show_form(
            step_id="zigbee2mqtt_device",
            data_schema=vol.Schema({vol.Required(CONF_NAME): vol.In(sorted(self._z2m))}),
        )

    async def async_step_zha(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        devices = self._devices(ZHA)
        if not devices:
            return self.async_abort(reason="no_device")
        if user_input is not None:
            device = devices[user_input[CONF_DEVICE]]
            ieee = next(i for domain, i in device.identifiers if domain == ZHA)
            await self.async_set_unique_id(f"{ZHA}:{ieee}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=_name(device), data={CONF_TRANSPORT: ZHA, CONF_IEEE: ieee})
        return self._pick_device(ZHA, devices)

    async def async_step_matter(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        devices = self._devices(MATTER)
        if not devices:
            return self.async_abort(reason="no_device")
        if user_input is not None:
            device = devices[user_input[CONF_DEVICE]]
            await self.async_set_unique_id(f"{MATTER}:{device.id}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=_name(device), data={CONF_TRANSPORT: MATTER, CONF_DEVICE: device.id})
        return self._pick_device(MATTER, devices)

    @callback
    def _devices(self, domain: str) -> dict[str, dr.DeviceEntry]:
        """The integration's devices that are an unfr0zen Nest."""
        registry = dr.async_get(self.hass)
        return {
            device.id: device
            for entry in self.hass.config_entries.async_entries(domain)
            for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
            if device.manufacturer == MANUFACTURER
            and (device.model or "").startswith(MODEL)
            and any(d == domain for d, _ in device.identifiers)
        }

    @callback
    def _pick_device(self, step: str, devices: dict[str, dr.DeviceEntry]) -> ConfigFlowResult:
        options = [SelectOptionDict(value=i, label=_name(d)) for i, d in devices.items()]
        return self.async_show_form(
            step_id=step,
            data_schema=vol.Schema({vol.Required(CONF_DEVICE): SelectSelector(SelectSelectorConfig(options=options))}),
        )


def _name(device: dr.DeviceEntry) -> str:
    return device.name_by_user or device.name or device.id


async def _retained(flow: ScheduleConfigFlow, topic: str) -> Any:
    """The JSON a retained `topic` holds, or None if nothing came."""
    future: asyncio.Future[Any] = flow.hass.loop.create_future()

    @callback
    def received(msg: mqtt.ReceiveMessage) -> None:
        if not future.done():
            try:
                future.set_result(json.loads(msg.payload))
            except ValueError:
                future.set_result(None)

    unsubscribe = await mqtt.async_subscribe(flow.hass, topic, received)
    try:
        async with asyncio.timeout(RETAINED_TIMEOUT):
            return await future
    except TimeoutError:
        return None
    finally:
        unsubscribe()
