"""The MQTT backends: nestlocal's own topics, and Zigbee2MQTT's device."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import datetime as dt
import json
from typing import Any

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .backend import Backend
from .schedule import Day, Schedule, days_json, from_json

READ_TIMEOUT = 30
WRITE_TIMEOUT = 30


class _MqttBackend(Backend):
    """A schedule that arrives on a state topic."""

    def __init__(self, hass: HomeAssistant, state_topic: str) -> None:
        super().__init__(hass)
        self.state_topic = state_topic
        self.latest: Schedule | None = None
        self._waiters: list[tuple[bool, asyncio.Future[Schedule]]] = []

    def schedule_of(self, payload: Any) -> Schedule | None:
        """The schedule a state message holds, if any."""
        raise NotImplementedError

    async def async_start(self, on_update: Callable[[Schedule], None]) -> Callable[[], None]:
        await mqtt.async_wait_for_mqtt_client(self.hass)

        @callback
        def received(msg: mqtt.ReceiveMessage) -> None:
            try:
                schedule = self.schedule_of(json.loads(msg.payload))
            except ValueError, KeyError, TypeError, AttributeError:
                return
            if schedule is None:
                return
            changed = schedule != self.latest
            self.latest = schedule
            for only_change, future in list(self._waiters):
                if (changed or not only_change) and not future.done():
                    future.set_result(schedule)
            if changed:
                on_update(schedule)

        return await mqtt.async_subscribe(self.hass, self.state_topic, received)

    async def _await(self, only_change: bool, send: Callable[[], Any], timeout: float) -> Schedule:
        future: asyncio.Future[Schedule] = self.hass.loop.create_future()
        waiter = (only_change, future)
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
        super().__init__(hass, f"{base}/heating_times")
        self.base = base

    def schedule_of(self, payload: Any) -> Schedule | None:
        return from_json(payload)

    async def async_read(self) -> Schedule:
        if self.latest is not None:
            return self.latest
        # The retained topic arrives on subscribing.
        try:
            return await self._await(False, _nothing, READ_TIMEOUT)
        except TimeoutError as err:
            raise HomeAssistantError(f"Nothing on {self.state_topic}") from err

    async def async_write(self, schedule: Schedule, days: dict[int, Day]) -> None:
        try:
            await self._await(
                True, lambda: self._publish(f"{self.base}/set/heating_times", days_json(days)), WRITE_TIMEOUT
            )
        except TimeoutError as err:
            raise HomeAssistantError("The Nest did not take the schedule: see nestlocal's log") from err


class Z2mBackend(_MqttBackend):
    """The heating_times of Zigbee2MQTT's device, which polls the unit."""

    poll = dt.timedelta(minutes=15)

    def __init__(self, hass: HomeAssistant, base: str, name: str) -> None:
        super().__init__(hass, f"{base}/{name}")

    def schedule_of(self, payload: Any) -> Schedule | None:
        value = payload.get("heating_times")
        return from_json(value) if isinstance(value, dict) else None

    async def async_read(self) -> Schedule:
        try:
            return await self._await(
                False, lambda: self._publish(f"{self.state_topic}/get", {"heating_times": ""}), READ_TIMEOUT
            )
        except TimeoutError as err:
            if self.latest is not None:
                return self.latest
            raise HomeAssistantError(f"Zigbee2MQTT sent no heating_times for {self.state_topic}") from err

    async def async_write(self, schedule: Schedule, days: dict[int, Day]) -> None:
        try:
            await self._await(
                True,
                lambda: self._publish(f"{self.state_topic}/set", {"heating_times": days_json(days)}),
                WRITE_TIMEOUT,
            )
        except TimeoutError as err:
            raise HomeAssistantError("The Nest did not take the schedule through Zigbee2MQTT") from err


async def _nothing() -> None:
    return None
