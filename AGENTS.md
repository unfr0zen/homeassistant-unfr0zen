# README screenshots

When a UI change affects a README screenshot, regenerate the light and dark PNGs in `docs/images/`. From the repository root, with Docker running and Compose installed, run

```sh
uv run --script scripts/screenshots.py
```

The [generator](scripts/screenshots.py) loads the integration from this checkout into a temporary Home Assistant instance and seeds an example thermostat over MQTT. Use the generated light and dark images to review the change.

For a new screenshot, extend `capture()` in the generator. Reuse the [preview dashboard](scripts/screenshots/dashboard.yaml) for dashboard cards and the [Compose fixture](scripts/screenshots/compose.yaml) for the services. Add the pair to the [README](README.md) using the existing `<picture>` pattern with a dark source and a light fallback. The generator checks the output filenames against the README.
