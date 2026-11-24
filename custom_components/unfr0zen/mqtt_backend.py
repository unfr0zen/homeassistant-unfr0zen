"""The MQTT backends: nestlocal's own topics, and Zigbee2MQTT's device."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import datetime as dt
import json
from typing import Any

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback

from . import hotwater, schedule
from .backend import Backend, HotWaterBackend
from .errors import error

READ_TIMEOUT = 30
WRITE_TIMEOUT = 30
# How long setup waits for the retained message that says the unit has hot
# water, beside the heating's own: a broker sends both on subscribing.
PRESENCE_TIMEOUT = 2
# The hot water cluster (0xFC01), as Zigbee2MQTT lists a cluster it has no name for.
HOT_WATER_CLUSTER = "64513"


class _MqttBackend(Backend):
    """A schedule that arrives on a state topic: the heating one, by default."""

    key = "heating_times"
    kind = "schedule"
    parse = staticmethod(schedule.from_json)
    encode = staticmethod(schedule.days_json)

    def __init__(self, hass: HomeAssistant, state_topic: str) -> None:
        super().__init__(hass)
        self.state_topic = state_topic
        self.latest: Any = None
        # Each a test of a message's schedule and the future it resolves.
        self._waiters: list[tuple[Callable[[Any], bool], asyncio.Future[Any]]] = []
        self._on_update: Callable[[Any], None] = lambda value: None
        self._unsubscribe: Callable[[], None] | None = None

    def value_of(self, payload: Any) -> Any:
        """The schedule a state message holds, if any; `payload` is None
        for an empty message."""
        raise NotImplementedError

    def seen(self, value: Any) -> None:
        """Called with each message's schedule, or None without one."""

    async def _subscribe(self) -> None:
        if self._unsubscribe is None:
            await mqtt.async_wait_for_mqtt_client(self.hass)
            self._unsubscribe = await mqtt.async_subscribe(self.hass, self.state_topic, self._received)

    @callback
    def _stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    async def async_start(self, on_update: Callable[[Any], None]) -> Callable[[], None]:
        self._on_update = on_update
        await self._subscribe()
        return self._stop

    @callback
    def _received(self, msg: mqtt.ReceiveMessage) -> None:
        try:
            value = self.value_of(json.loads(msg.payload) if msg.payload else None)
        except ValueError, KeyError, TypeError, AttributeError:
            return
        self.seen(value)
        if value is None:
            return
        changed = value != self.latest
        self.latest = value
        for wanted, future in list(self._waiters):
            if wanted(value) and not future.done():
                future.set_result(value)
        if changed:
            self._on_update(value)

    async def _await(self, wanted: Callable[[Any], bool], send: Callable[[], Any], timeout: float) -> Any:
        """The first schedule after `send` that `wanted` takes."""
        future: asyncio.Future[Any] = self.hass.loop.create_future()
        waiter = (wanted, future)
        self._waiters.append(waiter)
        try:
            await send()
            async with asyncio.timeout(timeout):
                return await future
        finally:
            self._waiters.remove(waiter)

    async def _publish(self, topic: str, payload: Any) -> None:
        await mqtt.async_publish(self.hass, topic, json.dumps(payload))


class NestlocalBackend(_MqttBackend):
    """nestlocal's <base>/heating_times and <base>/set/heating_times."""

    def __init__(self, hass: HomeAssistant, base: str) -> None:
        super().__init__(hass, f"{base}/{self.key}")
        self.base = base
        # False once the unit's state says its firmware has no schedules:
        # one it ran before may have left a retained schedule it no longer
        # updates. Firmware with them says "schedules": true.
        self.supported = True
        self._state_unsubscribe: Callable[[], None] | None = None
        # The last schedule message: one that came before the state said
        # the firmware has schedules is taken once it does.
        self._last: mqtt.ReceiveMessage | None = None

    async def _subscribe(self) -> None:
        if self._state_unsubscribe is None:
            await mqtt.async_wait_for_mqtt_client(self.hass)
            self._state_unsubscribe = await mqtt.async_subscribe(self.hass, f"{self.base}/state", self._state)
        await super()._subscribe()

    @callback
    def _stop(self) -> None:
        if self._state_unsubscribe is not None:
            self._state_unsubscribe()
            self._state_unsubscribe = None
        super()._stop()

    @callback
    def _state(self, msg: mqtt.ReceiveMessage) -> None:
        try:
            supported = json.loads(msg.payload).get("schedules") is True
        except ValueError, AttributeError, TypeError:
            return
        if supported == self.supported:
            return
        self.supported = supported
        if supported and self._last is not None:
            super()._received(self._last)
        elif not supported:
            self.latest = None
            self.seen(None)
        if self.request_refresh is not None:
            self.hass.async_create_task(self.request_refresh())

    @callback
    def _received(self, msg: mqtt.ReceiveMessage) -> None:
        self._last = msg
        super()._received(msg)

    def value_of(self, payload: Any) -> Any:
        return self.parse(payload) if self.supported else None

    async def async_read(self) -> Any:
        if not self.supported:
            raise error("nestlocal_too_old_mqtt")
        if self.latest is not None:
            return self.latest
        # The retained topic arrives on subscribing.
        try:
            return await self._await(_any, _nothing, READ_TIMEOUT)
        except TimeoutError as err:
            raise error("mqtt_nothing") from err

    async def async_wake_screen(self) -> None:
        await mqtt.async_publish(self.hass, f"{self.base}/set/wake_screen", "PRESS", qos=1, retain=False)

    async def async_write(self, current: Any, days: dict[int, Any]) -> None:
        try:
            await self._await(
                _having(current, days, (4.5, 35.0)),
                lambda: self._publish(f"{self.base}/set/{self.key}", self.encode(days)),
                WRITE_TIMEOUT,
            )
        except TimeoutError as err:
            raise error(f"mqtt_not_taken_{self.kind}") from err


class NestlocalHotWater(NestlocalBackend, HotWaterBackend):
    """nestlocal's <base>/hot_water_times: retained while the unit has hot
    water control, empty or absent without."""

    key = "hot_water_times"
    kind = "hot_water"
    parse = staticmethod(hotwater.from_json)
    encode = staticmethod(hotwater.days_json)

    def __init__(self, hass: HomeAssistant, base: str) -> None:
        super().__init__(hass, base)
        self._first: asyncio.Future[None] = hass.loop.create_future()
        # Whether the last message had a schedule, from subscribing on.
        self._seen: bool | None = None
        self._present: bool | None = None
        self._on_change: Callable[[], None] | None = None

    def seen(self, value: Any) -> None:
        if not self._first.done():
            self._first.set_result(None)
        self._seen = value is not None
        self._reconcile()

    def _reconcile(self) -> None:
        if self._on_change is not None and self._seen is not None and self._seen != self._present:
            on_change, self._on_change = self._on_change, None
            on_change()

    async def async_present(self) -> bool:
        await self._subscribe()
        try:
            async with asyncio.timeout(PRESENCE_TIMEOUT):
                await asyncio.shield(self._first)
        except TimeoutError:
            pass
        return self.latest is not None

    async def async_watch(self, present: bool, on_change: Callable[[], None]) -> None:
        self._present, self._on_change = present, on_change
        await self._subscribe()
        # A message since setup decided.
        self._reconcile()

    @callback
    def async_stop(self) -> None:
        self._stop()


class Z2mBackend(_MqttBackend):
    """The heating_times of Zigbee2MQTT's device, which polls the unit."""

    poll = dt.timedelta(minutes=15)

    def __init__(self, hass: HomeAssistant, base: str, name: str) -> None:
        super().__init__(hass, f"{base}/{name}")

    def value_of(self, payload: Any) -> Any:
        return self.parse(payload[self.key]) if isinstance(payload.get(self.key), dict) else None

    async def async_read(self) -> Any:
        try:
            return await self._await(
                _any, lambda: self._publish(f"{self.state_topic}/get", {self.key: ""}), READ_TIMEOUT
            )
        except TimeoutError as err:
            if self.latest is not None:
                return self.latest
            raise error("z2m_nothing", key=self.key) from err

    async def async_wake_screen(self) -> None:
        await self._publish(f"{self.state_topic}/set", {"wake_screen": "wake"})

    async def async_write(self, current: Any, days: dict[int, Any]) -> None:
        try:
            # Zigbee2MQTT reports each day as written: the write is done when
            # every day is as asked.
            await self._await(
                _having(current, days, (9.0, 32.0)),
                lambda: self._publish(f"{self.state_topic}/set", {self.key: self.encode(days)}),
                WRITE_TIMEOUT,
            )
        except TimeoutError as err:
            raise error(f"z2m_not_taken_{self.kind}") from err


class Z2mHotWater(Z2mBackend, HotWaterBackend):
    """The device's hot_water_times, present while Zigbee2MQTT lists the
    hot water cluster on the unit's endpoint 1."""

    key = "hot_water_times"
    kind = "hot_water"
    parse = staticmethod(hotwater.from_json)
    encode = staticmethod(hotwater.days_json)

    def __init__(self, hass: HomeAssistant, base: str, name: str, ieee: str) -> None:
        super().__init__(hass, base, name)
        self.devices_topic = f"{base}/bridge/devices"
        self.ieee = ieee
        self._listed: asyncio.Future[bool] = hass.loop.create_future()
        # Whether the last device list had the cluster, from subscribing on.
        self._last: bool | None = None
        self._present: bool | None = None
        self._on_change: Callable[[], None] | None = None
        self._unsubscribe_devices: Callable[[], None] | None = None

    def _has(self, devices: Any) -> bool:
        for device in devices if isinstance(devices, list) else []:
            if device.get("ieee_address") == self.ieee:
                endpoint = (device.get("endpoints") or {}).get("1") or {}
                clusters = (endpoint.get("clusters") or {}).get("input") or []
                return HOT_WATER_CLUSTER in (str(c) for c in clusters)
        return False

    @callback
    def _devices(self, msg: mqtt.ReceiveMessage) -> None:
        try:
            present = self._has(json.loads(msg.payload))
        except ValueError, TypeError, AttributeError:
            return
        self._last = present
        if not self._listed.done():
            self._listed.set_result(present)
        self._reconcile()

    def _reconcile(self) -> None:
        if self._on_change is not None and self._last is not None and self._last != self._present:
            on_change, self._on_change = self._on_change, None
            on_change()

    async def async_present(self) -> bool:
        await mqtt.async_wait_for_mqtt_client(self.hass)
        if self._unsubscribe_devices is None:
            self._unsubscribe_devices = await mqtt.async_subscribe(self.hass, self.devices_topic, self._devices)
        try:
            async with asyncio.timeout(PRESENCE_TIMEOUT):
                return await asyncio.shield(self._listed)
        except TimeoutError:
            return False

    async def async_watch(self, present: bool, on_change: Callable[[], None]) -> None:
        self._present, self._on_change = present, on_change
        if not self._listed.done():
            self._listed.set_result(present)
        # A device list since setup decided.
        self._reconcile()

    @callback
    def async_stop(self) -> None:
        if self._unsubscribe_devices is not None:
            self._unsubscribe_devices()
            self._unsubscribe_devices = None
        self._stop()


async def _nothing() -> None:
    return None


def _any(value: Any) -> bool:
    return True


def _having(current: Any, days: dict[int, Any], limits: tuple[float, float]) -> Callable[[Any], bool]:
    """A test of whether a schedule has `days` as given."""
    if isinstance(current, schedule.Schedule):
        return lambda value: schedule.matches(value, current, days, limits, partial=True)
    return lambda value: all(tuple(value.days[d]) == tuple(periods) for d, periods in days.items())
