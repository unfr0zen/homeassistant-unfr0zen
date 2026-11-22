"""What the unfr0zen cards build on: the calendars' attributes, the set
actions, and the cards' script served and loaded."""

import json
from pathlib import Path
import re
from unittest.mock import MagicMock, patch

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message

from custom_components.unfr0zen import ScheduleCoordinator
from custom_components.unfr0zen.calendar import HotWaterCalendar, _related
from custom_components.unfr0zen.hotwater import HotWater, from_text

from .conftest import hot_water, week
from .test_hot_water_mqtt import BASE, HOT_WATER, setup
from .test_hot_water_z2m import devices, setup as setup_z2m
from .test_hot_water_zha import Unit, backend

HEATING_ENTITY = "calendar.nestlocal_02aa01ac_heating_schedule"
HOT_WATER_ENTITY = "calendar.nestlocal_02aa01ac_hot_water_schedule"
HEATING = week(mon=[("22:00", 17), ("06:30", 22)], sat=[("08:00", 21.5)])


@pytest.fixture
async def unit(hass, mqtt_mock):
    """A nestlocal unit with hot water, its calendars not yet on a device."""
    await setup(hass, {"heating_times": HEATING, "hot_water_times": HOT_WATER})
    return mqtt_mock


def answer_writes(hass, mqtt_mock, topic, after):
    """The unit takes each write and republishes the week as `after`, with
    the days written as written."""

    async def publish(t, payload, *args, **kwargs):
        if t == f"{BASE}/set/{topic}":
            week = {**after, "days": {**after["days"], **json.loads(payload)}}
            hass.loop.call_soon(async_fire_mqtt_message, hass, f"{BASE}/{topic}", json.dumps(week))

    mqtt_mock.async_publish.side_effect = publish


def written(mqtt_mock, topic):
    return [json.loads(c.args[1]) for c in mqtt_mock.async_publish.call_args_list if c.args[0] == f"{BASE}/set/{topic}"]


async def call(hass, service, entity, days):
    await hass.services.async_call("unfr0zen", service, {"entity_id": entity, "days": days}, blocking=True)


async def test_the_heating_calendar_gives_its_week_and_limits(hass, unit):
    attributes = hass.states.get(HEATING_ENTITY).attributes
    entry = hass.config_entries.async_entries("unfr0zen")[0]
    assert {k: attributes[k] for k in attributes if k not in ("message", "all_day", "start_time", "end_time")} | {
        "week": None
    } == {
        "kind": "heating",
        "entry_id": entry.entry_id,
        "mode": "heat",
        "writable": True,
        "week": None,
        "min_temp": 4.5,
        "max_temp": 35.0,
        "temp_step": 0.5,
        "max_per_day": 10,
        "friendly_name": "nestlocal/02AA01AC Heating schedule",
        "supported_features": 7,
        "location": "",
        "description": "",
    }
    assert attributes["week"] == {
        "mon": [{"time": "06:30", "temp": 22.0}, {"time": "22:00", "temp": 17.0}],
        "tue": [],
        "wed": [],
        "thu": [],
        "fri": [],
        "sat": [{"time": "08:00", "temp": 21.5}],
        "sun": [],
    }
    assert "related" not in attributes, "no device of the unit's yet"


async def test_the_hot_water_calendar_gives_its_week(hass, unit):
    attributes = hass.states.get(HOT_WATER_ENTITY).attributes
    assert (attributes["kind"], attributes["writable"], attributes["max_per_day"]) == ("hot_water", True, 6)
    assert attributes["entry_id"] == hass.config_entries.async_entries("unfr0zen")[0].entry_id
    assert attributes["week"]["mon"] == [{"on": "06:00", "off": "08:00"}, {"on": "23:00", "off": "01:00"}]
    assert attributes["week"]["wed"] == [{"on": "17:00", "off": "19:00"}]
    assert attributes["week"]["sun"] == []


async def test_both_calendars_name_the_units_related_entities(hass, mqtt_mock):
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    nest = dr.async_get(hass).async_get_or_create(
        config_entry_id=mqtt_entry.entry_id, identifiers={("mqtt", "nestlocal_02aa01ac")}, name="Nest Thermostat"
    )
    entities = er.async_get(hass)
    for domain, key in [
        ("climate", "thermostat"),
        ("switch", "learning_mode"),
        ("switch", "hot_water_schedule"),
        ("binary_sensor", "heating"),
        ("binary_sensor", "hot_water_active"),
        ("select", "hot_water_boost"),
        ("select", "temperature_scale"),
        ("water_heater", "boiler"),
    ]:
        entities.async_get_or_create(
            domain,
            "mqtt",
            f"nestlocal_02aa01ac_{key}",
            config_entry=mqtt_entry,
            device_id=nest.id,
            suggested_object_id=f"nest_{key}",
        )
    await setup(hass, {"heating_times": HEATING, "hot_water_times": HOT_WATER})
    related = {
        "climate": "climate.nest_thermostat",
        "hot_water_switch": "switch.nest_hot_water_schedule",
        "hot_water_active": "binary_sensor.nest_hot_water_active",
        "hot_water_boost": "select.nest_hot_water_boost",
        "water_heater": "water_heater.nest_boiler",
    }
    assert hass.states.get("calendar.nest_thermostat_heating_schedule").attributes["related"] == related
    assert hass.states.get("calendar.nest_thermostat_hot_water_schedule").attributes["related"] == related


async def test_related_entities_not_found_are_null(hass, mqtt_mock):
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=mqtt_entry.entry_id, identifiers={("mqtt", "nestlocal_02aa01ac")}, name="Nest Thermostat"
    )
    await setup(hass, {"heating_times": HEATING})
    assert hass.states.get("calendar.nest_thermostat_heating_schedule").attributes["related"] == {
        "climate": None,
        "hot_water_switch": None,
        "hot_water_active": None,
        "hot_water_boost": None,
        "water_heater": None,
    }


async def test_zigbee2mqtt_found_by_its_suffixed_ids_and_its_limits(hass, mqtt_mock):
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    ieee = "0x00124b0012345678"
    nest = dr.async_get(hass).async_get_or_create(
        config_entry_id=mqtt_entry.entry_id, identifiers={("mqtt", f"zigbee2mqtt_{ieee}")}, name="Hallway"
    )
    for domain, key in [
        ("switch", "hot_water_schedule"),
        ("switch", "hot_water_boost"),
        ("binary_sensor", "hot_water"),
        ("binary_sensor", "heating"),
    ]:
        er.async_get(hass).async_get_or_create(
            domain,
            "mqtt",
            f"{ieee}_{key}_zigbee2mqtt",
            config_entry=mqtt_entry,
            device_id=nest.id,
            suggested_object_id=f"hallway_{key}",
        )
    state = {"heating_times": week(), "hot_water_times": hot_water()}
    await setup_z2m(hass, mqtt_mock, devices("64513"), state)
    attributes = hass.states.get("calendar.hallway_heating_schedule").attributes
    assert (attributes["min_temp"], attributes["max_temp"]) == (9.0, 32.0)
    assert {k: attributes["related"][k] for k in ("hot_water_switch", "hot_water_active", "hot_water_boost")} == {
        "hot_water_switch": "switch.hallway_hot_water_schedule",
        "hot_water_active": "binary_sensor.hallway_hot_water",
        "hot_water_boost": "switch.hallway_hot_water_boost",
    }
    with pytest.raises(ServiceValidationError, match=re.escape("32.5 °C is outside 9-32 °C")):
        await call(
            hass, "set_schedule", "calendar.hallway_heating_schedule", {"mon": [{"time": "06:00", "temp": 32.5}]}
        )


async def test_set_schedule_replaces_the_named_days_in_one_write(hass, unit):
    answer_writes(hass, unit, "heating_times", week(mon=[("07:00", 21)]))
    await call(
        hass,
        "set_schedule",
        HEATING_ENTITY,
        {"mon": [{"time": "22:30", "temp": 17}, {"time": "07:00", "temp": 21.2}], "sat": []},
    )
    assert written(unit, "heating_times") == [
        {"mon": [{"time": "07:00", "temp": 21.2}, {"time": "22:30", "temp": 17.0}], "sat": []}
    ]


@pytest.mark.parametrize(
    ("days", "message"),
    [
        ([], "days must be an object"),
        ({}, "days must be an object"),
        ({"funday": []}, "Unknown day 'funday'"),
        ({"mon": {}}, "mon must be a list"),
        ({"mon": [{"time": "6:30", "temp": 20}]}, "mon: time '6:30' is not HH:MM"),
        ({"mon": [{"time": "06:30"}]}, "temp None is not a number"),
        ({"mon": [{"time": "06:30", "temp": "warm"}]}, "temp 'warm' is not a number"),
        ({"mon": [{"time": "06:30", "temp": 36}]}, "36 °C is outside 4.5-35 °C"),
        ({"mon": [{"time": "06:30", "temp": 20}, {"time": "06:30", "temp": 21}]}, "two setpoints at 06:30"),
        ({"mon": [{"time": f"{h:02d}:00", "temp": 20} for h in range(11)]}, "at most 10 setpoints"),
    ],
)
async def test_set_schedule_refuses_what_the_nest_cannot_take(hass, unit, days, message):
    with pytest.raises(ServiceValidationError, match=re.escape(message)):
        await call(hass, "set_schedule", HEATING_ENTITY, days)
    assert written(unit, "heating_times") == []


async def test_set_schedule_takes_a_range_schedules_lows_and_highs(hass, unit):
    async_fire_mqtt_message(hass, f"{BASE}/heating_times", json.dumps(week(mode="range")))
    await hass.async_block_till_done()
    answer_writes(hass, unit, "heating_times", week(mode="range"))
    tue = [{"time": "07:00", "low": 19, "high": 24}]
    await call(hass, "set_schedule", HEATING_ENTITY, {"tue": tue})
    assert written(unit, "heating_times") == [{"tue": [{"time": "07:00", "low": 19.0, "high": 24.0}]}]
    with pytest.raises(ServiceValidationError, match="not a number"):
        await call(hass, "set_schedule", HEATING_ENTITY, {"tue": [{"time": "07:00", "temp": 20}]})
    with pytest.raises(ServiceValidationError, match=re.escape("1.5 °C or more")):
        await call(hass, "set_schedule", HEATING_ENTITY, {"tue": [{"time": "07:00", "low": 20, "high": 21}]})


async def test_set_hot_water_schedule_replaces_the_named_days(hass, unit):
    answer_writes(hass, unit, "hot_water_times", hot_water(fri=[("22:30", "00:30")]))
    await call(
        hass,
        "set_hot_water_schedule",
        HOT_WATER_ENTITY,
        {"fri": [{"on": "22:30", "off": "00:30"}, {"on": "06:00", "off": "07:00"}], "mon": []},
    )
    assert written(unit, "hot_water_times") == [
        {"mon": [], "fri": [{"on": "06:00", "off": "07:00"}, {"on": "22:30", "off": "00:30"}]}
    ]


@pytest.mark.parametrize(
    ("days", "message"),
    [
        ({"mon": [{"on": "6:00", "off": "08:00"}]}, "on and off must be HH:MM"),
        ({"mon": [{"on": "06:00", "off": "06:00"}]}, "goes off after it comes on"),
        ({"thu": [{"on": f"{h:02d}:00", "off": f"{h:02d}:30"} for h in range(7)]}, "at most 6"),
        ({"thu": [{"on": "06:00", "off": "08:00"}, {"on": "07:00", "off": "09:00"}]}, "overlap on thu at 07:00"),
        # Tuesday 00:30 would start inside Monday's 23:00-01:00, kept as it is.
        ({"tue": [{"on": "00:30", "off": "02:00"}]}, "overlap on tue at 00:30"),
        ({"sun": [{"on": "23:00", "off": "06:30"}]}, "overlap on mon at 06:00"),
    ],
)
async def test_set_hot_water_schedule_refuses_overlaps_and_more(hass, unit, days, message):
    with pytest.raises(ServiceValidationError, match=message):
        await call(hass, "set_hot_water_schedule", HOT_WATER_ENTITY, days)
    assert written(unit, "hot_water_times") == []


async def test_each_action_refuses_the_other_kind(hass, unit):
    with pytest.raises(
        ServiceValidationError, match=re.escape("hot water calendar: use unfr0zen.set_hot_water_schedule")
    ):
        await call(hass, "set_schedule", HOT_WATER_ENTITY, {"mon": []})
    with pytest.raises(ServiceValidationError, match=re.escape("heating calendar: use unfr0zen.set_schedule")):
        await call(hass, "set_hot_water_schedule", HEATING_ENTITY, {"mon": []})


async def test_set_hot_water_schedule_on_zha_clears_first(hass):
    # Tuesday 00:30-01:30 moves to Monday 23:30-01:00 in one call.
    zha = Unit({1: "00:30-01:30"})
    b, p = backend(hass, zha.cluster)
    entry = MockConfigEntry(domain="unfr0zen", data={"transport": "zha", "ieee": "00:12:4b:00:12:34:56:78"})
    entry.add_to_hass(hass)
    coordinator = ScheduleCoordinator(hass, entry, b)
    coordinator.data = HotWater(tuple(from_text(zha.days[d]) for d in range(7)))
    calendar = HotWaterCalendar(coordinator, None)
    calendar.hass, calendar.entity_id = hass, "calendar.landing_hot_water_schedule"
    with p:
        await calendar.async_set_hot_water_schedule({"mon": [{"on": "23:30", "off": "01:00"}], "tue": []})
    assert zha.written == [(0, ""), (1, ""), (0, "23:30-01:00")]
    assert coordinator.data.days[0] == from_text("23:30-01:00")


async def test_the_cards_are_served_and_loaded(hass, hass_client, tmp_path):
    cards = tmp_path / "unfr0zen-cards.js"
    cards.write_text("customElements;")
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(MagicMock(), [])
    with patch("custom_components.unfr0zen.CARDS", cards):
        assert await async_setup_component(hass, "unfr0zen", {})
    version = json.loads((Path(__file__).parents[1] / "custom_components/unfr0zen/manifest.json").read_text())[
        "version"
    ]
    assert hass.data[DATA_EXTRA_MODULE_URL].urls == {f"/unfr0zen/unfr0zen-cards.js?v={version}"}
    response = await (await hass_client()).get("/unfr0zen/unfr0zen-cards.js")
    assert (response.status, await response.text()) == (200, "customElements;")


async def test_without_the_cards_nothing_is_served(hass, hass_client, tmp_path):
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(MagicMock(), [])
    with patch("custom_components.unfr0zen.CARDS", tmp_path / "missing.js"):
        assert await async_setup_component(hass, "unfr0zen", {})
    assert hass.data[DATA_EXTRA_MODULE_URL].urls == set()
    assert (await (await hass_client()).get("/unfr0zen/unfr0zen-cards.js")).status == 404


async def test_an_action_on_the_device_reaches_only_its_calendar_of_that_kind(hass, mqtt_mock):
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    nest = dr.async_get(hass).async_get_or_create(
        config_entry_id=mqtt_entry.entry_id, identifiers={("mqtt", "nestlocal_02aa01ac")}, name="Nest Thermostat"
    )
    await setup(hass, {"heating_times": HEATING, "hot_water_times": HOT_WATER})
    answer_writes(hass, mqtt_mock, "heating_times", HEATING)
    tue = [{"time": "07:00", "temp": 20.0}]
    await hass.services.async_call(
        "unfr0zen", "set_schedule", {"device_id": nest.id, "days": {"tue": tue}}, blocking=True
    )
    assert written(mqtt_mock, "heating_times") == [{"tue": tue}]
    answer_writes(hass, mqtt_mock, "hot_water_times", HOT_WATER)
    await hass.services.async_call(
        "unfr0zen",
        "set_hot_water_schedule",
        {"device_id": nest.id, "days": {"tue": [{"on": "07:00", "off": "08:00"}]}},
        blocking=True,
    )
    assert written(mqtt_mock, "hot_water_times") == [{"tue": [{"on": "07:00", "off": "08:00"}]}]


async def test_an_action_on_a_calendar_of_the_other_kind_is_refused(hass, unit):
    with pytest.raises(ServiceValidationError, match="is a hot water calendar"):
        await call(hass, "set_schedule", HOT_WATER_ENTITY, {"tue": []})


async def test_matter_switches_found_by_their_labels(hass):
    matter_entry = MockConfigEntry(domain="matter")
    matter_entry.add_to_hass(hass)
    nest = dr.async_get(hass).async_get_or_create(
        config_entry_id=matter_entry.entry_id, identifiers={("matter", "deviceid_X-1")}, name="Nest Thermostat"
    )
    entities = er.async_get(hass)
    for endpoint, label in [(15, "Eco cool limit"), (16, "Hot water boost"), (17, "Hot water schedule")]:
        entities.async_get_or_create(
            "switch",
            "matter",
            f"X-1-MatterNodeDevice-{endpoint}-MatterPlug-6-0",
            config_entry=matter_entry,
            device_id=nest.id,
            original_name=f"Switch ({label})",
            # Ids from before the endpoints moved, as Home Assistant keeps them.
            suggested_object_id=f"nest_thermostat_switch_filter_{endpoint}",
        )
    related = _related(hass, nest)
    assert (related["hot_water_switch"], related["hot_water_boost"]) == (
        "switch.nest_thermostat_switch_filter_17",
        "switch.nest_thermostat_switch_filter_16",
    )
