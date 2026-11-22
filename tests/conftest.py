"""Fixtures: the integration enabled, and a nestlocal schedule."""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Lets Home Assistant load custom_components/."""
    return


def week(**days):
    """A heating_times object: every day empty but those given, each setpoint
    a time and a temp, or a (low, high) pair for a range schedule."""
    names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

    def point(t, c):
        return {"time": t, "low": c[0], "high": c[1]} if isinstance(c, tuple) else {"time": t, "temp": c}

    return {
        "mode": days.pop("mode", "heat"),
        "days": {n: [point(t, c) for t, c in days.get(n, [])] for n in names},
    }


@pytest.fixture
def expected_lingering_timers() -> bool:
    """MQTT's periodic housekeeping timer outlives a test that uses the broker."""
    return True


@pytest.fixture(autouse=True)
def short_presence_wait():
    """A unit without hot water is known as soon as its retained topic would
    have arrived."""
    try:
        from custom_components.unfr0zen import mqtt_backend
    except ImportError:
        # Tests that read the package's files alone, without importing it.
        yield
        return
    with patch.object(mqtt_backend, "PRESENCE_TIMEOUT", 0.05):
        yield


def hot_water(**days):
    """A hot_water_times object: every day empty but those given."""
    names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    return {"days": {n: [{"on": a, "off": b} for a, b in days.get(n, [])] for n in names}}
