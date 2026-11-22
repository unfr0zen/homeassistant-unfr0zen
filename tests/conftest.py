"""Fixtures: the integration enabled, and a nestlocal schedule."""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Lets Home Assistant load custom_components/."""
    return


def week(**days):
    """A heating_times object: every day empty but those given."""
    names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    return {
        "mode": days.pop("mode", "heat"),
        "days": {n: [{"time": t, "temp": c} for t, c in days.get(n, [])] for n in names},
    }


@pytest.fixture
def expected_lingering_timers() -> bool:
    """MQTT's periodic housekeeping timer outlives a test that uses the broker."""
    return True


@pytest.fixture(autouse=True)
def short_presence_wait():
    """A unit without hot water is known as soon as its retained topic would
    have arrived."""
    with patch("custom_components.unfr0zen.mqtt_backend.PRESENCE_TIMEOUT", 0.05):
        yield


def hot_water(**days):
    """A hot_water_times object: every day empty but those given."""
    names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    return {"days": {n: [{"on": a, "off": b} for a, b in days.get(n, [])] for n in names}}
