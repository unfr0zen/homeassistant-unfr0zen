# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright==1.65.0"]
# ///
"""Regenerate the README screenshots from a disposable Home Assistant instance."""

import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import APIRequestContext, Browser, Locator, Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "docs/images"
HEATING = "calendar.nest_thermostat_heating_schedule"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def heating_week(mode):
    week = {}
    for day in DAYS:
        weekend = day in ("sat", "sun")
        times = ["08:00", "13:00", "17:30", "23:00"] if weekend else ["06:30", "08:30", "17:00", "22:30"]
        if mode == "range":
            if weekend:
                times = ["08:00", "23:00"] if day == "sat" else ["06:15", "20:45"]
            ranges = [(20.5, 24), (17, 26)] if weekend else [(20, 24), (16, 28), (20.5, 24), (17, 26)]
            week[day] = [dict(time=t, low=low, high=high) for t, (low, high) in zip(times, ranges, strict=True)]
        else:
            temps = [20.5, 19.5, 21, 16] if weekend else [20, 17, 20.5, 16]
            if mode == "cool":
                temps = [24, 23, 23.5, 25] if weekend else [24, 28, 23.5, 25]
            week[day] = [dict(time=t, temp=temp) for t, temp in zip(times, temps, strict=True)]
    return {"mode": mode, "days": week}


def publish(compose, topic, payload):
    message = payload if isinstance(payload, str) else json.dumps(payload)
    subprocess.run(
        [*compose, "exec", "-T", "mqtt", "mosquitto_pub", "-r", "-t", f"nestlocal/preview/{topic}", "-m", message],
        check=True,
    )


def onboard(api: APIRequestContext, url: str):
    client = url + "/"
    user = dict(name="Preview", username="preview", password=secrets.token_urlsafe(), language="en", client_id=client)
    code = api.post("/api/onboarding/users", data=user, fail_on_status_code=True).json()["auth_code"]
    tokens = api.post(
        "/auth/token", form=dict(grant_type="authorization_code", code=code, client_id=client), fail_on_status_code=True
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    for step in ("core_config", "analytics", "integration"):
        body = dict(client_id=client, redirect_uri=client) if step == "integration" else {}
        api.post(f"/api/onboarding/{step}", data=body, headers=headers, fail_on_status_code=True)
    return dict(**tokens, hassUrl=url, clientId=client, expires=(time.time() + tokens["expires_in"]) * 1000)


def wait_for_item(page, command, key, value):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        items = page.evaluate("type => document.querySelector('home-assistant').hass.callWS({type})", command)
        for item in items:
            if item.get(key) == value:
                return item
        time.sleep(0.25)
    raise TimeoutError(f"{command} did not return {value}")


def prepare(api, page, compose):
    flow_url = "/api/config/config_entries/flow"
    flow = api.post(flow_url, data={"handler": "mqtt"}, fail_on_status_code=True).json()
    broker = dict(broker="mqtt", port=1883, other_settings=dict(set_client_cert=False, set_ca_cert="off"))
    result = api.post(f"{flow_url}/{flow['flow_id']}", data=broker, fail_on_status_code=True).json()
    if result["type"] != "create_entry":
        raise RuntimeError(f"MQTT setup failed: {result.get('errors')}")
    publish(compose, "availability", "online")
    publish(compose, "state", {"schedules": True})
    weekday = [dict(on="06:00", off="07:30"), dict(on="17:30", off="19:00")]
    weekend = [dict(on="07:30", off="09:00"), dict(on="18:00", off="19:30")]
    publish(compose, "hot_water_times", {"days": {day: weekend if day in ("sat", "sun") else weekday for day in DAYS}})
    publish(compose, "heating_times", heating_week("heat"))
    flow = wait_for_item(page, "config_entries/flow/progress", "handler", "unfr0zen")
    api.post(f"{flow_url}/{flow['flow_id']}", data={}, fail_on_status_code=True)
    for entity, state, attrs in [
        ("switch.preview_water_schedule", "on", {}),
        ("binary_sensor.preview_water_active", "on", {}),
        ("select.preview_boost", "off", {"options": ["off", "30", "60", "120"]}),
    ]:
        api.post(f"/api/states/{entity}", data=dict(state=state, attributes=attrs), fail_on_status_code=True)
    wait_for_item(page, "config/entity_registry/list", "entity_id", HEATING)


def screenshot(target: Locator, path: Path):
    target.scroll_into_view_if_needed()
    target.evaluate("el => el.setAttribute('data-screenshot', '')")
    target.page.evaluate("document.fonts.ready")
    target.page.mouse.move(0, 0)
    target.screenshot(
        path=path,
        animations="disabled",
        omit_background=True,
        style="""body { visibility: hidden; background: transparent !important }
                 html { background: transparent !important }
                 [data-screenshot] { visibility: visible; box-shadow: none !important }
                 dialog::backdrop { background: transparent !important; backdrop-filter: none !important }""",
    )
    target.evaluate("el => el.removeAttribute('data-screenshot')")


def capture(page: Page, api, compose, theme, output):
    def shot(target, name):
        screenshot(target, output / f"{name}-{theme}.png")

    def expect_white(target):
        expect(target).to_have_css("color", "rgb(255, 255, 255)")

    for name, mode, instant, target in [
        ("heating", "heat", "2026-11-22T13:18:00Z", {"temperature": 19.5}),
        ("hot-water", "heat", "2026-11-23T06:40:00Z", None),
        ("cooling", "cool", "2026-11-23T22:41:00Z", {"temperature": 25}),
        (
            "heat-cool",
            "range",
            "2026-11-22T16:15:00Z",
            {"target_temp_low": 20.5, "target_temp_high": 24, "hvac_action": "heating"},
        ),
    ]:
        page.clock.set_fixed_time(instant)
        if target:
            publish(compose, "heating_times", heating_week(mode))
            api.post("/api/states/climate.preview", data=dict(state=mode, attributes=target), fail_on_status_code=True)
        # A fresh view reads the fixed clock even when the entity state has not changed.
        page.reload()
        host = page.locator("unfr0zen-hot-water-card" if name == "hot-water" else "unfr0zen-heating-card")
        host.evaluate("(el, width) => el.style.width = width", "392px" if name == "cooling" else "544px")
        expect(host.locator(".week-row")).to_have_count(7)
        expect(host.locator(".title")).to_contain_text(
            {"heating": "Heating", "hot-water": "Hot water", "cooling": "Cooling", "heat-cool": "Heat/cool"}[name]
        )
        if name == "hot-water":
            switch = host.get_by_role("switch")
            expect(switch).to_have_attribute("aria-checked", "true")
            expect(host.locator(".heating-now")).to_be_visible()
            if theme == "dark":
                expect_white(switch)
                expect_white(switch.locator("ha-icon"))
        shot(host.locator("ha-card"), name + "-card")
        if name == "hot-water":
            if theme == "dark":
                for state in ("30", "off"):
                    api.post(
                        "/api/states/select.preview_boost",
                        data=dict(state=state, attributes={"options": ["off", "30", "60", "120"]}),
                        fail_on_status_code=True,
                    )
                    boost = host.locator(".pill.boost.active")
                    if state == "30":
                        expect_white(boost)
                        expect_white(boost.locator("ha-icon"))
                    else:
                        expect(boost).to_have_count(0)
            continue
        if theme == "dark":
            action = "cooling" if mode == "cool" else "heating"
            api.post(
                "/api/states/climate.preview",
                data=dict(state=mode, attributes={**target, "hvac_action": action}),
                fail_on_status_code=True,
            )
            pill = host.locator(f".pill.{action}")
            expect_white(pill)
            expect_white(pill.locator("ha-icon"))
        host.evaluate("el => el.style.width = '544px'")
        host.locator('.week-row[data-day="6"]').click()
        panel = host.locator("dialog[open] .panel")
        expect(panel.locator(".day-name")).to_have_text("Sunday")
        shot(panel, name + "-editor")
        if theme == "dark":
            panel.locator('[data-action="step"][data-by="1"]').first.click()
            save = panel.locator('[data-action="save"]')
            expect(save).to_be_enabled()
            expect_white(save)
            page.keyboard.press("Escape")
            discard = panel.locator('[data-action="discard"]')
            expect_white(discard)
            discard.click()
        else:
            page.keyboard.press("Escape")
        if name == "heating":
            calendar = page.locator("hui-calendar-card")
            calendar.evaluate("el => Object.assign(el.style, {display:'block', width:'700px'})")
            expect(calendar.locator(".fc-list-event").first).to_be_visible()
            shot(calendar.locator("ha-card"), "calendar")


def generate(browser: Browser, playwright, compose, url, output):
    api = playwright.request.new_context(base_url=url)
    tokens = onboard(api, url)
    api.dispose()
    api = playwright.request.new_context(
        base_url=url, extra_http_headers={"Authorization": "Bearer " + tokens["access_token"]}
    )
    for theme in ("light", "dark"):
        with browser.new_context(
            base_url=url,
            viewport={"width": 1300, "height": 900},
            device_scale_factor=2,
            locale="en-GB",
            timezone_id="UTC",
            color_scheme=theme,
        ) as context:
            context.add_init_script("localStorage.setItem('hassTokens', " + json.dumps(json.dumps(tokens)) + ")")
            page = context.new_page()
            page.goto("/lovelace/0")
            page.wait_for_function("document.querySelector('home-assistant')?.hass?.connected")
            if theme == "light":
                prepare(api, page, compose)
            page.evaluate(
                """async entity => {
                const hass = document.querySelector('home-assistant').hass;
                await hass.callWS({type:'config/entity_registry/update', entity_id:entity,
                                   options_domain:'calendar', options:{color:'#ff6f22'}});
                await hass.callWS({type:'frontend/set_user_data', key:'language',
                   value:{language:'en-GB',time_format:'24',first_weekday:'sunday'}});
            }""",
                HEATING,
            )
            capture(page, api, compose, theme, output)
    api.dispose()


def main():
    compose = [
        "docker",
        "compose",
        "-f",
        str(ROOT / "scripts/screenshots/compose.yaml"),
        "-p",
        "unfr0zen-screens-" + secrets.token_hex(4),
    ]
    subprocess.run([sys.executable, "-m", "playwright", "install", "--only-shell", "chromium"], check=True)
    with tempfile.TemporaryDirectory(dir=IMAGES) as tmp:
        output = Path(tmp)
        try:
            subprocess.run([*compose, "up", "-d", "--wait", "--wait-timeout", "180"], check=True)
            address = subprocess.check_output([*compose, "port", "ha", "8123"], text=True).strip()
            with sync_playwright() as playwright, playwright.chromium.launch() as browser:
                generate(browser, playwright, compose, "http://" + address, output)
        finally:
            subprocess.run([*compose, "down", "--volumes"], check=True)
        expected = set(re.findall(r"docs/images/([\w-]+\.png)", (ROOT / "README.md").read_text()))
        if {p.name for p in output.glob("*.png")} != expected:
            raise RuntimeError("Captured filenames differ from the screenshots referenced in README.md")
        for file in sorted(output.glob("*.png")):
            file.replace(IMAGES / file.name)
        print(f"Generated {len(expected)} screenshots in docs/images")


if __name__ == "__main__":
    main()
