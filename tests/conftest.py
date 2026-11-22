"""Fixtures: the integration enabled, and a nestlocal schedule."""

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
