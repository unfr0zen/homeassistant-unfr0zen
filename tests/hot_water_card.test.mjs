import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const words = JSON.parse(await readFile(new URL("../custom_components/unfr0zen/frontend/locales/en.json", import.meta.url)));
const elements = new Map();
globalThis.HTMLElement = class {};
globalThis.window = {};
globalThis.document = { documentElement: { lang: "en" } };
globalThis.customElements = { get: (name) => elements.get(name), define: (name, element) => elements.set(name, element) };
globalThis.fetch = async () => ({ ok: true, json: async () => words });
await import("../custom_components/unfr0zen/frontend/unfr0zen-cards.js");
const Card = elements.get("unfr0zen-hot-water-card");

function card(domain, state, options = [], matter = false) {
  const instance = Object.create(Card.prototype);
  const boost = domain && `${domain}.boost`;
  const calls = [];
  instance._config = { boost, water_heater: "water_heater.nest" };
  instance._hass = {
    states: {
      ...(boost && { [boost]: { state, attributes: { options } } }),
      "water_heater.nest": { state: "high_demand", attributes: {} },
    },
    entities: { "water_heater.nest": { platform: matter ? "matter" : "mqtt" } },
    callService: async (...args) => calls.push(args),
  };
  instance._render = () => {};
  return { instance, calls, boost };
}

for (const minutes of [37, 75, 0]) {
  test(`number boost reports ${minutes} remaining minutes`, () => {
    const { instance } = card("number", String(minutes), [], true);
    assert.deepEqual(instance._boostState(), {
      via: "number", active: minutes > 0, minutes, options: ["30", "60", "120"], off: "0",
    });
    if (minutes) {
      const html = instance._boostHtml(instance._boostState());
      assert.match(html, /class="pill boost active"/);
      assert.ok(html.includes(`Boost · ${minutes === 37 ? "37m" : "1h15"}`));
    }
  });
}

for (const state of ["unavailable", "unknown", "off", "NaN", "Infinity", "37.5", "-1"]) {
  test(`number boost does not activate for ${state}`, () => {
    const { instance } = card("number", state);
    assert.equal(instance._boostState().active, false);
    assert.equal(instance._boostState().minutes, 0);
  });
}

for (const value of [30, 60, 120, 0]) {
  test(`number boost sends ${value} minutes`, async () => {
    const { instance, calls, boost } = card("number", "37");
    instance._menu = true;
    const html = instance._boostHtml(instance._boostState());
    assert.match(html, new RegExp(`data-option="${value}"`));
    await instance._boost({ dataset: { action: "boost", option: String(value) } });
    assert.deepEqual(calls, [["number", "set_value", { value }, { entity_id: boost }]]);
  });
}

test("custom duration is active without a fabricated duration or selectable action", async () => {
  const { instance, calls, boost } = card("select", "Custom duration", ["Custom duration", "Off", "30 min", "60 min", "120 min"], true);
  assert.deepEqual(instance._boostState(), {
    via: "select", active: true, minutes: 0, options: ["30 min", "60 min", "120 min"], off: "Off",
  });
  instance._menu = true;
  assert.doesNotMatch(instance._boostHtml(instance._boostState()), /Custom duration/);
  instance._week = () => ({});
  instance._dayRow = () => "";
  instance._editHtml = () => "";
  instance._axis = () => "";
  assert.match(instance._cardHtml({ state: "off", attributes: {} }), /Boosting/);
  await instance._boost({ dataset: { action: "boost", option: "Off" } });
  assert.deepEqual(calls, [["select", "select_option", { option: "Off" }, { entity_id: boost }]]);
});

for (const [domain, state, option, service, data] of [
  ["select", "30", "60", ["select", "select_option"], { option: "60" }],
  ["select", "off", "30", ["select", "select_option"], { option: "30" }],
  ["switch", "on", "off", ["switch", "turn_off"], {}],
  ["switch", "off", "on", ["switch", "turn_on"], {}],
]) {
  test(`legacy ${domain} ${state} sends ${option}`, async () => {
    const { instance, calls, boost } = card(domain, state, ["off", "30", "60", "120"]);
    assert.equal(instance._boostState().active, state === "on" || state === "30");
    await instance._boost({ dataset: { action: "boost", option } });
    assert.deepEqual(calls, [[...service, data, { entity_id: boost }]]);
  });
}

for (const matter of [false, true]) {
  test(`${matter ? "Matter" : "legacy"} water heater keeps its service path`, async () => {
    const { instance, calls } = card("switch", "on", [], matter);
    if (!matter) delete instance._hass.states["switch.boost"];
    assert.equal(instance._boostState().active, true);
    await instance._boost({ dataset: { action: "boost", option: "60" } });
    await instance._boost({ dataset: { action: "boost", option: "off" } });
    assert.deepEqual(calls, matter ? [
      ["matter", "water_heater_boost", { duration: 3600 }, { entity_id: "water_heater.nest" }],
      ["switch", "turn_off", {}, { entity_id: "switch.boost" }],
    ] : [
      ["water_heater", "set_operation_mode", { operation_mode: "high_demand" }, { entity_id: "water_heater.nest" }],
      ["water_heater", "set_operation_mode", { operation_mode: "eco" }, { entity_id: "water_heater.nest" }],
    ]);
  });
}

test("autodetected card follows a select replaced by a number", () => {
  const { instance } = card("select", "30", ["off", "30", "60", "120"]);
  instance._config = { entity: "calendar.hot_water" };
  const related = { hot_water_boost: "select.boost" };
  instance._hass.states["calendar.hot_water"] = { state: "off", attributes: { related } };
  assert.equal(instance._boostState().via, "select");
  instance._hass.states["number.boost"] = { state: "75", attributes: {} };
  related.hot_water_boost = "number.boost";
  assert.equal(instance._boostState().via, "number");
  assert.equal(instance._boostState().minutes, 75);
  instance._config.boost = "select.boost";
  assert.equal(instance._boostState().via, "select");
});

test("editor accepts numeric and legacy boost entities", () => {
  const field = Card.getConfigForm().schema.find((field) => field.name === "boost");
  assert.deepEqual(field.selector.entity.filter.domain, ["number", "select", "switch"]);
});
