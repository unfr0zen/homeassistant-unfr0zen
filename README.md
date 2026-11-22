# unfr0zen Nest schedule

A Home Assistant integration that shows an [unfr0zen](https://github.com/unfr0zen/unfr0zen) Nest's own weekly
heating schedule as a calendar, and changes it from the calendar. The Nest keeps running its schedule: the calendar
reads it and writes it back, whichever way Home Assistant reaches the unit.

## Install

With HACS: add this repository as a custom repository (type Integration), install **unfr0zen Nest schedule**, restart
Home Assistant. By hand: copy `custom_components/unfr0zen_schedule` into your configuration's `custom_components/`
and restart.

Then add it under **Settings → Devices & services → Add integration → unfr0zen Nest schedule** and pick how Home
Assistant reaches the Nest. A unit publishing over MQTT is found on its own.

## Transports

| Transport | Reads | Writes | Needs |
|-|-|-|-|
| nestlocal's MQTT topics | `<prefix>/<serial>/heating_times`, as the unit publishes each change | `<prefix>/<serial>/set/heating_times` | the MQTT integration |
| Zigbee2MQTT | the device's `heating_times`, asked for every 15 minutes | the device's `set` topic | the unfr0zen converter, the MQTT integration |
| ZHA | Get Weekly Schedule, one day per request, every 15 minutes | Set Weekly Schedule, one day per command | ZHA |
| Matter | GetWeeklySchedule, one day per request, every 15 minutes | SetWeeklySchedule, one day per command | the Matter integration |

The ZHA backend reaches the Thermostat cluster through ZHA's internal gateway, as ZHA offers no action that returns a
command's answer; a Home Assistant release that changes those internals shows as an error naming the device.

## The calendar

- Each setpoint is an event that repeats every week on its day, from its time until the next setpoint, which may be
  on a later day. Its title is the temperature, such as `20.5 °C`.
- A new event must repeat weekly with no end (it may name several days): it adds a setpoint at its start time on each
  of its days, at the temperature in its title. Its end time is ignored, as the next setpoint ends it.
- Editing or deleting one occurrence changes the whole series: the Nest's schedule is the same every week.
- Refused, with the reason: one-off events, other repeats (daily, every other week, a count or an end date), all-day
  events, starts off a whole minute, temperatures outside 4.5-35 °C, more than 10 setpoints a day, and any change to a
  heat-cool (range) schedule, which the calendar shows empty.
- Temperatures are °C, in the Nest's half degrees.
- Writing the schedule turns off the Nest's Auto-Schedule, or the unit would rewrite it.

## Development

```sh
uv venv -p python3.14 .venv
uv pip install -p .venv -r requirements_test.txt
.venv/bin/python -m pytest
.venv/bin/ruff format --check . && .venv/bin/ruff check .
```

## License

GPL-3.0-or-later, as unfr0zen.
