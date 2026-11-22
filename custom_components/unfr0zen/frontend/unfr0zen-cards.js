// unfr0zen's Lovelace cards: a Nest's heating and hot water schedules, as
// the unfr0zen integration's calendars hold them, shown as a week of bars
// and edited a day at a time.
//
//   type: custom:unfr0zen-heating-card     entity: calendar.<nest>_heating_schedule
//   type: custom:unfr0zen-hot-water-card   entity: calendar.<nest>_hot_water_schedule
//
// The calendars' attributes carry the week ("week", by day); a save calls the
// integration's set_schedule / set_hot_water_schedule with the days changed.

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DAY_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const DAY = 24 * 60;
const WEEK = 7 * DAY;
const DOCS = "https://github.com/unfr0zen/homeassistant-unfr0zen";

// ----- Times and the clock -----

/** "HH:MM" as minutes after midnight, or null. */
function toMinutes(text) {
  const m = /^(\d{2}):(\d{2})$/.exec(text ?? "");
  if (!m || Number(m[1]) > 23 || Number(m[2]) > 59) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}

function hhmm(minutes) {
  const m = ((minutes % DAY) + DAY) % DAY;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

/** A length of time as the hot water blocks label it: "2h", "1h30", "45m". */
function duration(minutes) {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (!h) return `${m}m`;
  return m ? `${h}h${String(m).padStart(2, "0")}` : `${h}h`;
}

/** Now in Home Assistant's time zone: the day (0 Monday) and minutes after midnight. */
function zonedNow(timeZone) {
  let parts;
  try {
    parts = new Intl.DateTimeFormat("en-GB", {
      timeZone,
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(new Date());
  } catch {
    parts = new Intl.DateTimeFormat("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      .formatToParts(new Date());
  }
  const part = (type) => parts.find((p) => p.type === type)?.value;
  const day = DAY_SHORT.indexOf(part("weekday"));
  return { day: day < 0 ? 0 : day, minute: Number(part("hour")) * 60 + Number(part("minute")) };
}

/** When a change `ahead` minutes from now, on `day`, at `minute`, happens: "at 22:30", "tomorrow 06:30", "Wed 06:30". */
function when(now, day, minute, ahead) {
  if (day === now.day && ahead < DAY) return `at ${hhmm(minute)}`;
  if (day === (now.day + 1) % 7 && ahead < 2 * DAY) return `tomorrow ${hhmm(minute)}`;
  return `${DAY_SHORT[day]} ${hhmm(minute)}`;
}

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

// Whether Home Assistant shows temperatures in Fahrenheit; the schedules
// are in Celsius whatever it shows.
let fahrenheit = false;

/** A temperature as Home Assistant shows them. */
function shown(temp) {
  return `${Number.isInteger(temp) ? temp : temp.toFixed(1)}°`;
}

/** A schedule's Celsius temperature, in the unit Home Assistant shows. */
function degrees(celsius) {
  return shown(fahrenheit ? Math.round((celsius * 9) / 5 + 32) : celsius);
}

// ----- Heating: setpoints holding until the next -----

/** The week's setpoints in week order: {day, minute, temp}. */
function setpoints(week) {
  const out = [];
  DAYS.forEach((name, day) => {
    for (const p of week?.[name] ?? []) {
      const minute = toMinutes(p.time);
      if (minute !== null) out.push({ day, minute, temp: Number(p.temp) });
    }
  });
  return out.sort((a, b) => a.day * DAY + a.minute - (b.day * DAY + b.minute));
}

/** The setpoint carried into `day` at midnight: the last of the nearest earlier day with one. */
function carried(points, day) {
  for (let back = 1; back <= 7; back++) {
    const d = (day - back + 7) % 7;
    const last = points.filter((p) => p.day === d).at(-1);
    if (last) return last.temp;
  }
  return null;
}

/** A day's segments, {start, end, temp}: from midnight, then each setpoint until the next. */
function heatingSegments(points, day) {
  const today = points.filter((p) => p.day === day);
  const segments = [];
  let start = 0;
  let temp = carried(points, day);
  for (const p of today) {
    if (p.minute > start) segments.push({ start, end: p.minute, temp });
    start = p.minute;
    temp = p.temp;
  }
  segments.push({ start, end: DAY, temp });
  return segments.filter((s) => s.end > s.start);
}

/** The setpoint in effect now, and the next one to another temperature. */
function heatingNow(points, now) {
  if (!points.length) return { current: null, next: null };
  const at = now.day * DAY + now.minute;
  const abs = (p) => p.day * DAY + p.minute;
  const current = [...points].reverse().find((p) => abs(p) <= at) ?? points.at(-1);
  for (let i = 1; i <= points.length; i++) {
    const p = points[(points.indexOf(current) + i) % points.length];
    if (p.temp !== current.temp) {
      const ahead = (abs(p) - at + WEEK) % WEEK || WEEK;
      return { current, next: { ...p, ahead } };
    }
  }
  return { current, next: null };
}

// ----- Hot water: on periods -----

/** The week's on periods as spans of the week: {day, on, off, start, end}; off before on runs past midnight. */
function periods(week) {
  const out = [];
  DAYS.forEach((name, day) => {
    for (const p of week?.[name] ?? []) {
      const on = toMinutes(p.on);
      const off = toMinutes(p.off);
      if (on === null || off === null) continue;
      const start = day * DAY + on;
      out.push({ day, on, off, start, end: start + ((off - on + DAY) % DAY || DAY) });
    }
  });
  return out.sort((a, b) => a.start - b.start);
}

/** A day's blocks, {start, end, length}: its periods, and the end of one that ran past the previous midnight. */
function hotWaterBlocks(spans, day) {
  const blocks = [];
  for (const s of spans) {
    const from = day * DAY;
    for (const shift of [0, WEEK]) {
      const start = Math.max(s.start - shift, from);
      const end = Math.min(s.end - shift, from + DAY);
      if (end > start) blocks.push({ start: start - from, end: end - from, length: s.end - s.start, first: s.start - shift >= from });
    }
  }
  return blocks.sort((a, b) => a.start - b.start);
}

/** Whether hot water is on now, until when, and the next period's start. */
function hotWaterNow(spans, now) {
  if (!spans.length) return { on: false, until: null, next: null };
  const at = now.day * DAY + now.minute;
  const active = spans.find((s) => (s.start <= at && at < s.end) || (s.start <= at + WEEK && at + WEEK < s.end));
  const from = active ? active.end % WEEK : at;
  const upcoming = spans
    // From the active period's end, one starting right then follows on.
    .map((s) => ({ ...s, ahead: (s.start - from + WEEK) % WEEK || (active ? 0 : WEEK) }))
    .sort((a, b) => a.ahead - b.ahead)[0];
  const next = upcoming ? { ...upcoming, ahead: (upcoming.start - at + WEEK) % WEEK || WEEK } : null;
  return { on: Boolean(active), until: active ? active.end % DAY : null, next };
}

// ----- Styles -----

const STYLES = `
  :host {
    /* Home Assistant's own state colours: heating, and hot water as its
       water heater shows following the schedule and boosting. */
    --u0-warm: var(--state-climate-heat-color, #ff6f22);
    --u0-cool: color-mix(in srgb, var(--u0-warm) 22%, var(--u0-card));
    --u0-track: color-mix(in srgb, var(--primary-text-color, #e1e1e1) 7%, var(--u0-card));
    --u0-raised: color-mix(in srgb, var(--primary-text-color, #e1e1e1) 6%, var(--u0-card));
    --u0-line: color-mix(in srgb, var(--primary-text-color, #e1e1e1) 14%, transparent);
    --u0-water: var(--state-water_heater-eco-color, #4caf50);
    --u0-on: var(--state-water_heater-eco-color, #4caf50);
    --u0-boost: var(--state-water_heater-high_demand-color, #ff6f22);
    --u0-card: var(--ha-card-background, var(--card-background-color, #1c1c1e));
    --u0-text: var(--primary-text-color, #e1e1e1);
    --u0-muted: var(--secondary-text-color, #9b9b9b);
    --u0-radius: 14px;
    display: block;
  }
  /* A cooling schedule in the cooling colour. */
  :host(.cooling) {
    --u0-warm: var(--state-climate-cool-color, #2196f3);
    --u0-cool: color-mix(in srgb, var(--u0-warm) 22%, var(--u0-card));
  }
  ha-card, .panel {
    background: var(--u0-card);
    color: var(--u0-text);
    border-radius: var(--ha-card-border-radius, 20px);
    container-type: inline-size;
  }
  ha-card { display: block; padding: 20px 20px 18px; }
  .head { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; }
  .titles { flex: 1; min-width: 0; }
  .title { font-size: 1.35rem; font-weight: 600; line-height: 1.2; }
  .subtitle { color: var(--u0-muted); margin-top: 4px; font-size: 0.95rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pill {
    display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; border-radius: 999px;
    background: var(--u0-raised); font-weight: 600; font-size: 1.05rem; border: 0; color: inherit; font-family: inherit;
  }
  .pill.on { background: var(--u0-on); color: #fff; }
  button.pill { cursor: pointer; }
  .pill ha-icon { --mdc-icon-size: 20px; }
  .controls { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
  .boost-wrap { position: relative; }
  .pill.boost { font-size: 0.95rem; padding: 8px 14px; }
  .pill.boost.active { background: var(--u0-boost); color: #fff; }
  .menu {
    position: absolute; right: 0; top: calc(100% + 6px); z-index: 5; min-width: 140px; padding: 6px;
    background: var(--u0-card); border: 1px solid var(--u0-line); border-radius: 14px;
    box-shadow: 0 10px 30px rgba(0,0,0,.35); display: flex; flex-direction: column;
  }
  .menu-item {
    border: 0; background: transparent; color: var(--u0-text); font: inherit; font-weight: 600; text-align: left;
    padding: 10px 12px; border-radius: 10px; cursor: pointer;
  }
  .menu-item:hover { background: var(--u0-raised); }
  .heating-now { display: inline-flex; align-items: center; gap: 4px; margin-top: 6px; color: var(--u0-warm);
    font-weight: 600; font-size: 0.88rem; }
  .heating-now ha-icon { --mdc-icon-size: 16px; }
  .week { display: grid; grid-template-columns: 44px 1fr; row-gap: 10px; align-items: center; }
  .day { font-weight: 600; color: var(--u0-muted); font-size: 0.95rem; }
  .day.today { color: var(--u0-text); }
  .bar {
    position: relative; height: 30px; border-radius: 9px; overflow: hidden; display: flex;
    background: var(--u0-track);
  }
  .bar.today { outline: 1px solid var(--u0-line); outline-offset: 2px; }
  .seg {
    position: absolute; top: 0; bottom: 0; display: flex; align-items: center; justify-content: center;
    font-weight: 600; font-size: 0.82rem; white-space: nowrap; overflow: hidden;
  }
  .seg.warm { color: #fff; }
  .block {
    position: absolute; top: 3px; bottom: 3px; border-radius: 7px; background: var(--u0-water); color: #fff;
    display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 0.8rem; overflow: hidden;
    white-space: nowrap;
  }
  .now { position: absolute; top: -2px; bottom: -2px; width: 3px; margin-left: -1.5px; background: #fff;
    box-shadow: 0 0 0 1px rgba(0,0,0,.55); border-radius: 2px; z-index: 2; }
  .axis { display: flex; justify-content: space-between; color: var(--u0-muted); font-size: 0.8rem; margin-top: 8px; }
  .axis.indent { margin-left: 44px; }
  .empty { color: var(--u0-muted); font-size: 0.85rem; padding-left: 10px; align-self: center; }
  .note { color: var(--u0-muted); font-size: 0.9rem; margin-top: 16px; text-align: center; }
  .edit {
    margin-top: 18px; width: 100%; display: flex; align-items: center; justify-content: center; gap: 10px;
    padding: 14px; border-radius: var(--u0-radius); border: 0; background: var(--u0-raised); color: inherit;
    font: inherit; font-weight: 600; font-size: 1.05rem; cursor: pointer;
  }
  .edit:hover { background: color-mix(in srgb, var(--u0-text) 10%, var(--u0-card)); }
  .warning { color: var(--error-color, #db4437); }
  @container (max-width: 420px) {
    .seg.small, .block.small { color: transparent; }
    .pill.boost:not(.active) .text { display: none; }
    .pill.boost { padding: 8px 10px; }
    .subtitle { white-space: normal; }
    .title { font-size: 1.2rem; }
    .week { grid-template-columns: 38px 1fr; }
    .axis.indent { margin-left: 38px; }
    ha-card { padding: 16px 14px 14px; }
  }

  dialog {
    padding: 0; border: 0; background: transparent; color: var(--u0-text); max-width: min(560px, calc(100vw - 16px));
    width: 100%; max-height: calc(100vh - 16px);
  }
  dialog::backdrop { background: rgba(0, 0, 0, 0.55); }
  .panel {
    /* An opaque surface, as Home Assistant's dialogs have: a theme's cards
       may be see-through. The colours mixed from the card follow it. */
    --u0-card: var(--ha-dialog-surface-background, var(--mdc-theme-surface, var(--primary-background-color, #1c1c1e)));
    --u0-cool: color-mix(in srgb, var(--u0-warm) 22%, var(--u0-card));
    --u0-track: color-mix(in srgb, var(--primary-text-color, #e1e1e1) 7%, var(--u0-card));
    --u0-raised: color-mix(in srgb, var(--primary-text-color, #e1e1e1) 6%, var(--u0-card));
    padding: 18px; box-shadow: 0 10px 40px rgba(0,0,0,.4);
  }
  .panel-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .panel-title { font-weight: 600; font-size: 1.1rem; }
  .icon-button {
    border: 0; background: transparent; color: var(--u0-muted); width: 40px; height: 40px; border-radius: 50%;
    display: inline-flex; align-items: center; justify-content: center; cursor: pointer;
  }
  .icon-button:hover { background: var(--u0-raised); color: var(--u0-text); }
  .tabs { display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; }
  .tab {
    border: 0; border-radius: 12px; padding: 10px 2px 8px; background: var(--u0-raised); color: var(--u0-muted);
    font: inherit; font-weight: 600; cursor: pointer; display: flex; flex-direction: column; align-items: center; gap: 6px;
  }
  .tab.selected { background: var(--u0-text); color: var(--u0-card); }
  .tab.changed .label::after { content: "•"; margin-left: 2px; color: var(--u0-warm); }
  .mini { position: relative; width: 70%; height: 5px; border-radius: 3px; overflow: hidden; background: var(--u0-track); }
  .mini .seg, .mini .block { top: 0; bottom: 0; border-radius: 0; }
  .day-head { display: flex; justify-content: space-between; align-items: baseline; margin: 18px 0 8px; gap: 8px; }
  .day-name { font-weight: 600; font-size: 1.05rem; }
  .carry { color: var(--u0-muted); font-size: 0.92rem; }
  .rows { display: flex; flex-direction: column; gap: 10px; margin-top: 16px; }
  .row {
    display: flex; align-items: center; gap: 10px; background: var(--u0-raised); border-radius: var(--u0-radius);
    padding: 10px 12px;
  }
  .row.invalid { outline: 1px solid var(--error-color, #db4437); }
  .time {
    display: inline-flex; align-items: center; gap: 8px; border: 1px solid var(--u0-line); border-radius: 12px;
    padding: 6px 10px; background: var(--u0-card);
  }
  .time ha-icon { --mdc-icon-size: 20px; color: var(--u0-muted); }
  .time .part {
    font: inherit; font-size: 1.2rem; font-weight: 700; color: var(--u0-text); background: transparent; border: 0;
    font-variant-numeric: tabular-nums; width: 1.6em; text-align: center; padding: 2px 0; border-radius: 6px;
  }
  .time .part:focus { outline: 2px solid var(--u0-warm); outline-offset: 1px; }
  .colon { font-weight: 700; font-size: 1.2rem; color: var(--u0-muted); margin: 0 1px; }
  .dash { color: var(--u0-muted); }
  .grow { flex: 1; }
  .stepper { display: inline-flex; align-items: center; gap: 6px; }
  .round {
    width: 40px; height: 40px; border-radius: 50%; border: 0; background: var(--u0-card); color: var(--u0-text);
    display: inline-flex; align-items: center; justify-content: center; cursor: pointer; font: inherit;
  }
  .round:disabled { opacity: .35; cursor: default; }
  .temp { min-width: 3.2em; text-align: center; font-weight: 700; font-size: 1.2rem; font-variant-numeric: tabular-nums; }
  .add {
    border: 1.5px dashed var(--u0-line); border-radius: var(--u0-radius); background: transparent; color: var(--u0-muted);
    font: inherit; font-weight: 600; padding: 14px; cursor: pointer; display: flex; justify-content: center; gap: 8px;
  }
  .add:disabled { opacity: .4; cursor: default; }
  .copy-days { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
  .chip {
    border: 1px solid var(--u0-line); background: transparent; color: var(--u0-muted); border-radius: 999px;
    padding: 6px 12px; font: inherit; font-weight: 600; cursor: pointer;
  }
  .chip.selected { background: var(--u0-warm); border-color: var(--u0-warm); color: #fff; }
  .actions { display: flex; justify-content: space-between; align-items: center; margin-top: 18px; gap: 10px; }
  .action {
    border: 0; border-radius: 12px; padding: 12px 18px; background: var(--u0-raised); color: var(--u0-text);
    font: inherit; font-weight: 600; font-size: 1.02rem; cursor: pointer; display: inline-flex; gap: 8px; align-items: center;
  }
  .action.primary { background: var(--u0-warm); color: #fff; }
  .action.primary.water { background: var(--u0-water); }
  .action:disabled, .action.primary:disabled, .action.primary.water:disabled {
    opacity: .45; cursor: default; background: var(--u0-raised); color: var(--u0-muted);
  }
  .errors { color: var(--error-color, #db4437); margin-top: 12px; font-size: 0.92rem; }
  @media (max-width: 420px) {
    .panel { padding: 14px 10px; }
    .tab { padding: 8px 0 6px; font-size: 0.82rem; }
    .row { padding: 8px; gap: 6px; }
    .time .part, .colon { font-size: 1.05rem; }
    .time { gap: 4px; padding: 6px 6px; }
    .round { width: 34px; height: 34px; }
    .temp { font-size: 1.05rem; min-width: 2.8em; }
  }
`;

// ----- The cards -----

/** What both cards share: the calendar, the week, the clock and the day editor. */
class ScheduleCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._editor = null;
  }

  setConfig(config) {
    if (!config?.entity) throw new Error("Choose the unfr0zen calendar (entity)");
    this._config = config;
    this._rendered = null;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    fahrenheit = hass.config?.unit_system?.temperature === "°F";
    const state = hass.states[this._config?.entity];
    const key = [state, ...this._relatedEntities().map((id) => hass.states[id])];
    if (!this._rendered || key.some((s, i) => s !== this._rendered[i])) {
      this._rendered = key;
      this._render();
    }
  }

  connectedCallback() {
    // The now marker and "until" text follow the clock.
    this._tick = setInterval(() => this._render(), 60 * 1000);
  }

  disconnectedCallback() {
    clearInterval(this._tick);
  }

  getCardSize() {
    return 7;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  get _state() {
    return this._hass?.states[this._config?.entity];
  }

  get _now() {
    return zonedNow(this._hass?.config?.time_zone);
  }

  _render() {
    if (!this._config || !this.shadowRoot) return;
    if (!this.shadowRoot.querySelector("ha-card")) {
      this.shadowRoot.innerHTML = `<style>${STYLES}</style><ha-card></ha-card><dialog></dialog>`;
      this.shadowRoot.addEventListener("click", (e) => this._click(e));
      this.shadowRoot.addEventListener("change", (e) => this._change(e));
      // A field's change fires as a press elsewhere blurs it: the editor is
      // redrawn once that press is over, so its click lands.
      this.shadowRoot.addEventListener("pointerdown", () => (this._pressing = true));
      this.shadowRoot.addEventListener("pointerup", () =>
        setTimeout(() => {
          this._pressing = false;
          if (this._redraw) this._renderEditor();
        }),
      );
      this.shadowRoot.addEventListener("focusin", (e) => e.target.matches?.("input") && e.target.select());
      this.shadowRoot.querySelector("dialog").addEventListener("close", () => {
        this._editor = null;
      });
    }
    const card = this.shadowRoot.querySelector("ha-card");
    const state = this._state;
    if (!this._hass) return;
    if (!state) {
      card.innerHTML = `<div class="warning">Entity not found: ${escapeHtml(this._config.entity)}</div>`;
      return;
    }
    this.classList.toggle("cooling", state.attributes.mode === "cool");
    card.innerHTML = this._cardHtml(state);
  }

  _week(state) {
    return state.attributes.week ?? {};
  }

  _axis(indent) {
    return `<div class="axis${indent ? " indent" : ""}"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div>`;
  }

  _editable(state) {
    return state.attributes.writable !== false && state.attributes.mode !== "range";
  }

  _footer(state) {
    if (this._editable(state)) {
      return `<button class="edit" data-action="open"><ha-icon icon="mdi:pencil-outline"></ha-icon>Edit schedule</button>`;
    }
    const why =
      state.attributes.mode === "range"
        ? "A heat-cool schedule can't be edited here."
        : "This schedule can't be changed from Home Assistant.";
    return `<div class="note">${why}</div>`;
  }

  /** A 24-hour time field, "06 : 30", for row `i`'s `field`. */
  _timeHtml(field, value, i, icon, label) {
    const [h, m] = (value ?? "").split(":");
    const part = (name, v, max) =>
      `<input class="part" inputmode="numeric" maxlength="2" value="${escapeHtml(v ?? "")}" data-field="${field}"
        data-part="${name}" data-max="${max}" data-row="${i}" aria-label="${label} ${name === "h" ? "hour" : "minute"}">`;
    return `<label class="time"><ha-icon icon="${icon}"></ha-icon>${part("h", h, 23)}<span class="colon">:</span>${part("m", m, 59)}</label>`;
  }

  // --- The editor ---

  _open() {
    const state = this._state;
    const week = this._week(state);
    const draft = {};
    for (const name of DAYS) draft[name] = this._rowsOf(week[name] ?? []);
    const today = this._now.day;
    this._editor = { day: today, draft, original: JSON.parse(JSON.stringify(draft)), copy: new Set(), copying: false, error: null, saving: false };
    this._renderEditor();
    const dialog = this.shadowRoot.querySelector("dialog");
    if (!dialog.open) dialog.showModal();
  }

  _close() {
    const dialog = this.shadowRoot.querySelector("dialog");
    if (dialog.open) dialog.close();
    this._editor = null;
  }

  /** The draft as it would be saved: the Copy to… days given the shown day's rows. */
  _effective() {
    const e = this._editor;
    const shown = e.draft[DAYS[e.day]];
    return Object.fromEntries(DAYS.map((name) => [name, e.copy.has(name) ? shown : e.draft[name]]));
  }

  /** The days whose rows differ from the calendar's, as they would be saved. */
  _changes() {
    const e = this._editor;
    const week = this._effective();
    const out = {};
    for (const name of DAYS) {
      const rows = week[name];
      if (JSON.stringify(this._sorted(rows)) !== JSON.stringify(this._sorted(e.original[name]))) {
        out[name] = this._sorted(rows).map((r) => this._payloadOf(r));
      }
    }
    return out;
  }

  _renderEditor() {
    const e = this._editor;
    this._redraw = false;
    if (!e) return;
    const dialog = this.shadowRoot.querySelector("dialog");
    const name = DAYS[e.day];
    const rows = e.draft[name];
    const week = this._effective();
    const problems = this._problems(week);
    const changes = this._changes();
    const max = this._state.attributes.max_per_day ?? this._maxPerDay;
    const tabs = DAYS.map((d, i) => {
      const changed = d in changes ? " changed" : "";
      const selected = i === e.day ? " selected" : "";
      return `<button class="tab${selected}${changed}" data-action="day" data-day="${i}">
        <span class="label">${DAY_SHORT[i]}</span><span class="mini">${this._miniHtml(week, i)}</span></button>`;
    }).join("");
    const copy = e.copying
      ? `<div class="copy-days">${DAYS.map((d, i) =>
          i === e.day ? "" : `<button class="chip${e.copy.has(d) ? " selected" : ""}" data-action="copy-day" data-day="${d}">${DAY_SHORT[i]}</button>`,
        ).join("")}</div>`
      : "";
    // A copy that would be refused, by the day it goes to.
    const copied = [...problems]
      .filter(([key]) => e.copy.has(key.split(":")[0]))
      .flatMap(([key, texts]) => texts.map((t) => `${DAY_LONG[DAYS.indexOf(key.split(":")[0])]}: ${t}`));
    const errors = [...new Set([...(problems.get(name) ?? []), ...copied]), ...(e.error ? [e.error] : [])];
    // The time field being typed in, to have it again once redrawn.
    const focused = this.shadowRoot.activeElement?.dataset;
    const field = focused?.part ? `input[data-field="${focused.field}"][data-part="${focused.part}"][data-row="${focused.row}"]` : null;
    dialog.innerHTML = `
      <div class="panel" role="document">
        <div class="panel-head">
          <span class="panel-title">${escapeHtml(this._title())}</span>
          <button class="icon-button" data-action="close" aria-label="Close"><ha-icon icon="mdi:close"></ha-icon></button>
        </div>
        <div class="tabs">${tabs}</div>
        <div class="day-head"><span class="day-name">${DAY_LONG[e.day]}</span><span class="carry">${this._carryText(week, e.day)}</span></div>
        <div class="bar">${this._dayBarHtml(week, e.day, false)}</div>
        ${this._axis(false)}
        <div class="rows">
          ${rows.map((r, i) => this._rowHtml(r, i, problems.get(`${name}:${i}`))).join("")}
          <button class="add" data-action="add" ${rows.length >= max ? "disabled" : ""}>
            <ha-icon icon="mdi:plus"></ha-icon>${this._addLabel}</button>
        </div>
        ${errors.length ? `<div class="errors">${errors.map(escapeHtml).join("<br>")}</div>` : ""}
        ${copy}
        <div class="actions">
          <button class="action" data-action="copy"><ha-icon icon="mdi:content-copy"></ha-icon>Copy to…</button>
          <button class="action primary${this._water ? " water" : ""}" data-action="save"
            ${!Object.keys(changes).length || problems.size || e.saving ? "disabled" : ""}>${e.saving ? "Saving…" : "Save"}</button>
        </div>
      </div>`;
    if (field) dialog.querySelector(field)?.focus();
  }

  _click(event) {
    const target = event.target.closest("[data-action]");
    const action = target?.dataset.action;
    // The boost menu closes on any click outside it.
    if (this._menu && action !== "boost-menu" && action !== "boost") {
      this._menu = false;
      this._render();
    }
    if (!target) return;
    if (action === "open") return this._open();
    if (action === "toggle") return this._toggle();
    if (action === "boost-menu" || action === "boost") return this._boost(target);
    const e = this._editor;
    if (!e) return;
    const rows = e.draft[DAYS[e.day]];
    switch (action) {
      case "close":
        return this._close();
      case "day":
        e.day = Number(target.dataset.day);
        e.copy.clear();
        e.copying = false;
        break;
      case "add":
        rows.push(this._newRow(rows));
        break;
      case "remove":
        rows.splice(Number(target.dataset.row), 1);
        break;
      case "step":
        this._step(rows[Number(target.dataset.row)], Number(target.dataset.by));
        break;
      case "copy":
        e.copying = !e.copying;
        break;
      case "copy-day": {
        const d = target.dataset.day;
        if (e.copy.has(d)) e.copy.delete(d);
        else e.copy.add(d);
        break;
      }
      case "save":
        return this._save();
      default:
        return;
    }
    e.error = null;
    this._renderEditor();
  }

  _change(event) {
    const input = event.target;
    const e = this._editor;
    if (!e || !input.dataset.field) return;
    const row = e.draft[DAYS[e.day]][Number(input.dataset.row)];
    const [h, m] = (row[input.dataset.field] ?? "00:00").split(":");
    const n = Number.parseInt(input.value, 10);
    if (Number.isInteger(n) && n >= 0 && n <= Number(input.dataset.max)) {
      const value = String(n).padStart(2, "0");
      row[input.dataset.field] = input.dataset.part === "h" ? `${value}:${m}` : `${h}:${value}`;
    }
    e.error = null;
    if (this._pressing) this._redraw = true;
    else this._renderEditor();
  }

  async _save() {
    const e = this._editor;
    const days = this._changes();
    if (!Object.keys(days).length) return;
    e.saving = true;
    e.error = null;
    this._renderEditor();
    try {
      await this._hass.callService("unfr0zen", this._service, { days }, { entity_id: this._config.entity });
      this._close();
    } catch (err) {
      e.saving = false;
      e.error = err?.message ?? String(err);
      this._renderEditor();
    }
  }
}

class Unfr0zenHeatingCard extends ScheduleCard {
  static getConfigForm() {
    return {
      schema: [
        { name: "entity", required: true, selector: { entity: { filter: { domain: "calendar", integration: "unfr0zen" } } } },
        { name: "name", selector: { text: {} } },
        { name: "climate", selector: { entity: { filter: { domain: "climate" } } } },
      ],
    };
  }

  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(
      (id) => id.startsWith("calendar.") && hass.states[id].attributes.kind === "heating",
    );
    return { entity: entity ?? "" };
  }

  get _service() {
    return "set_schedule";
  }

  get _maxPerDay() {
    return 10;
  }

  get _addLabel() {
    return "Add a change";
  }

  _title() {
    return this._config.name ?? "Heating";
  }

  _relatedEntity() {
    return this._config?.climate ?? this._state?.attributes.related?.climate ?? null;
  }

  _relatedEntities() {
    return [this._relatedEntity()].filter(Boolean);
  }

  _limits() {
    const a = this._state.attributes;
    return { min: Number(a.min_temp ?? 4.5), max: Number(a.max_temp ?? 35), step: Number(a.temp_step ?? 0.5) };
  }

  /** How warm a temperature looks, 0 (the week's coolest) to 100 (its warmest). */
  _warmth(points) {
    const temps = points.map((p) => p.temp);
    const lo = Math.min(...temps);
    const hi = Math.max(...temps);
    return (temp) => (hi > lo ? Math.round(Math.pow((temp - lo) / (hi - lo), 1.6) * 100) : 70);
  }

  _segmentsHtml(points, day, labels, warmth) {
    return heatingSegments(points, day)
      .filter((s) => s.temp !== null)
      .map((s) => {
        const left = (s.start / DAY) * 100;
        const width = ((s.end - s.start) / DAY) * 100;
        const w = warmth(s.temp);
        const style = `left:${left}%;width:${width}%;background:color-mix(in srgb, var(--u0-warm) ${w}%, var(--u0-cool))`;
        const cls = `seg${w > 45 ? " warm" : ""}${width < 13 ? " small" : ""}`;
        const label = labels && width >= 6 ? degrees(s.temp) : "";
        return `<div class="${cls}" style="${style}">${label}</div>`;
      })
      .join("");
  }

  _cardHtml(state) {
    const week = this._week(state);
    const points = setpoints(week);
    const now = this._now;
    const warmth = this._warmth(points);
    const { current, next } = heatingNow(points, now);
    let subtitle = "No schedule";
    if (current) {
      subtitle = `Set to ${degrees(current.temp)}`;
      if (next) subtitle += ` · ${degrees(next.temp)} ${when(now, next.day, next.minute, next.ahead)}`;
    }
    if (state.attributes.mode === "range") subtitle = "Heat-cool schedule";
    const rows = DAYS.map((_, day) => {
      const today = day === now.day;
      const marker = today ? `<div class="now" style="left:${(now.minute / DAY) * 100}%"></div>` : "";
      const bar = points.length
        ? this._segmentsHtml(points, day, true, warmth)
        : `<span class="empty">No setpoints</span>`;
      return `<div class="day${today ? " today" : ""}">${DAY_SHORT[day]}</div>
        <div class="bar${today ? " today" : ""}">${bar}${marker}</div>`;
    }).join("");
    return `
      <div class="head">
        <div class="titles"><div class="title">${escapeHtml(this._title())}</div>
          <div class="subtitle">${escapeHtml(subtitle)}</div></div>
        ${this._pillHtml(current)}
      </div>
      <div class="week">${rows}</div>
      ${this._axis(true)}
      ${this._footer(state)}`;
  }

  _pillHtml(current) {
    const climate = this._relatedEntity() && this._hass.states[this._relatedEntity()];
    let text = current ? degrees(current.temp) : "–";
    if (climate) {
      const a = climate.attributes;
      // The climate entity's own, in Home Assistant's unit.
      if (a.temperature != null) text = shown(Number(a.temperature));
      else if (a.target_temp_low != null && a.target_temp_high != null)
        text = `${shown(Number(a.target_temp_low))}–${shown(Number(a.target_temp_high))}`;
    }
    return `<span class="pill"><ha-icon icon="mdi:gauge"></ha-icon>${text}</span>`;
  }

  // --- Editor rows: a time and a temperature ---

  _rowsOf(list) {
    return list.map((p) => ({ time: p.time, temp: Number(p.temp) }));
  }

  _sorted(rows) {
    return [...rows].sort((a, b) => (toMinutes(a.time) ?? 0) - (toMinutes(b.time) ?? 0));
  }

  _payloadOf(row) {
    return { time: row.time, temp: row.temp };
  }

  _draftPoints(draft) {
    return setpoints(draft);
  }

  _miniHtml(draft, day) {
    const points = this._draftPoints(draft);
    return points.length ? this._segmentsHtml(points, day, false, this._warmth(points)) : "";
  }

  _dayBarHtml(draft, day) {
    const points = this._draftPoints(draft);
    return points.length ? this._segmentsHtml(points, day, true, this._warmth(points)) : "";
  }

  _carryText(draft, day) {
    const temp = carried(this._draftPoints(draft), day);
    return temp === null ? "" : `From midnight: ${degrees(temp)}`;
  }

  _rowHtml(row, i, problem) {
    const { min, max } = this._limits();
    return `<div class="row${problem ? " invalid" : ""}">
      ${this._timeHtml("time", row.time, i, "mdi:clock-outline", "Time")}
      <span class="grow"></span>
      <span class="stepper">
        <button class="round" data-action="step" data-row="${i}" data-by="-1" aria-label="Cooler" ${row.temp <= min ? "disabled" : ""}><ha-icon icon="mdi:minus"></ha-icon></button>
        <span class="temp">${degrees(row.temp)}</span>
        <button class="round" data-action="step" data-row="${i}" data-by="1" aria-label="Warmer" ${row.temp >= max ? "disabled" : ""}><ha-icon icon="mdi:plus"></ha-icon></button>
      </span>
      <button class="icon-button" data-action="remove" data-row="${i}" aria-label="Remove"><ha-icon icon="mdi:trash-can-outline"></ha-icon></button>
    </div>`;
  }

  _step(row, by) {
    const { min, max, step } = this._limits();
    const next = Math.round((row.temp + by * step) / step) * step;
    row.temp = Math.min(max, Math.max(min, Number(next.toFixed(1))));
  }

  _newRow(rows) {
    const sorted = this._sorted(rows);
    const last = sorted.at(-1);
    const after = last ? Math.min((toMinutes(last.time) ?? 0) + 60, DAY - 1) : 12 * 60;
    const { min, max } = this._limits();
    const temp = last ? last.temp : Math.min(max, Math.max(min, 20));
    return { time: hhmm(after), temp };
  }

  /** What the server would refuse, by day ("mon") and row ("mon:2"). */
  _problems(draft) {
    const problems = new Map();
    const { min, max } = this._limits();
    const limit = this._state.attributes.max_per_day ?? this._maxPerDay;
    const add = (key, text) => problems.set(key, [...(problems.get(key) ?? []), text]);
    for (const name of DAYS) {
      const rows = draft[name];
      if (rows.length > limit) add(name, `At most ${limit} changes a day.`);
      const seen = new Map();
      rows.forEach((r, i) => {
        const m = toMinutes(r.time);
        if (m === null) {
          add(`${name}:${i}`, "time");
          add(name, "Each change needs a time.");
        } else if (seen.has(m)) {
          add(`${name}:${i}`, "duplicate");
          add(name, `Two changes at ${r.time}.`);
        } else seen.set(m, i);
        if (!(r.temp >= min && r.temp <= max)) add(name, `Temperatures are ${degrees(min)}–${degrees(max)}.`);
      });
    }
    return problems;
  }
}

class Unfr0zenHotWaterCard extends ScheduleCard {
  static getConfigForm() {
    return {
      schema: [
        { name: "entity", required: true, selector: { entity: { filter: { domain: "calendar", integration: "unfr0zen" } } } },
        { name: "name", selector: { text: {} } },
        { name: "switch", selector: { entity: { filter: { domain: "switch" } } } },
        { name: "boost", selector: { entity: { filter: { domain: "select" } } } },
        { name: "water_heater", selector: { entity: { filter: { domain: "water_heater" } } } },
        { name: "active", selector: { entity: { filter: { domain: "binary_sensor" } } } },
      ],
    };
  }

  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(
      (id) => id.startsWith("calendar.") && hass.states[id].attributes.kind === "hot_water",
    );
    return { entity: entity ?? "" };
  }

  get _service() {
    return "set_hot_water_schedule";
  }

  get _maxPerDay() {
    return 6;
  }

  get _addLabel() {
    return "Add a period";
  }

  get _water() {
    return true;
  }

  _title() {
    return this._config.name ?? "Hot water";
  }

  /** The entities beside the calendar: its switch, boost, water heater and "heating now" sensor. */
  _related() {
    const r = this._state?.attributes.related ?? {};
    const c = this._config ?? {};
    return {
      switch: c.switch ?? r.hot_water_switch ?? null,
      boost: c.boost ?? r.hot_water_boost ?? null,
      waterHeater: c.water_heater ?? r.water_heater ?? null,
      active: c.active ?? r.hot_water_active ?? null,
    };
  }

  _relatedEntities() {
    return Object.values(this._related()).filter(Boolean);
  }

  _blocksHtml(spans, day, labels) {
    return hotWaterBlocks(spans, day)
      .map((b) => {
        const left = (b.start / DAY) * 100;
        const width = ((b.end - b.start) / DAY) * 100;
        const label = labels && b.first && width >= 5 ? duration(b.length) : "";
        return `<div class="block${width < 9 ? " small" : ""}" style="left:${left}%;width:${width}%">${label}</div>`;
      })
      .join("");
  }

  _cardHtml(state) {
    const spans = periods(this._week(state));
    const now = this._now;
    const status = hotWaterNow(spans, now);
    const boost = this._boostState();
    let subtitle = "No periods";
    if (spans.length) {
      const next = status.next ? `next ${when(now, status.next.day, status.next.on, status.next.ahead)}` : "";
      subtitle = status.on ? `On until ${hhmm(status.until)}${next ? ` · ${next}` : ""}` : `Off${next ? ` · ${next}` : ""}`;
    }
    if (boost.active) subtitle = boost.minutes ? `Boosting · ${duration(boost.minutes)}` : "Boosting";
    const active = this._related().active;
    const heating = active && this._hass.states[active]?.state === "on"
      ? `<div class="heating-now"><ha-icon icon="mdi:fire"></ha-icon>Heating now</div>`
      : "";
    const rows = DAYS.map((_, day) => {
      const today = day === now.day;
      const marker = today ? `<div class="now" style="left:${(now.minute / DAY) * 100}%"></div>` : "";
      return `<div class="day${today ? " today" : ""}">${DAY_SHORT[day]}</div>
        <div class="bar${today ? " today" : ""}">${this._blocksHtml(spans, day, true)}${marker}</div>`;
    }).join("");
    return `
      <div class="head">
        <div class="titles"><div class="title">${escapeHtml(this._title())}</div>
          <div class="subtitle">${escapeHtml(subtitle)}</div>${heating}</div>
        <div class="controls">${this._boostHtml(boost)}${this._pillHtml(state, status)}</div>
      </div>
      <div class="week">${rows}</div>
      ${this._axis(true)}
      ${this._footer(state)}`;
  }

  /** On/off: the schedule switch's, else the water heater's (anything but "off"), else the calendar's. */
  _pillHtml(state, status) {
    const { switch: sw, waterHeater } = this._related();
    const entity = (sw && this._hass.states[sw]) || (waterHeater && this._hass.states[waterHeater]);
    if (entity) {
      const on = sw && this._hass.states[sw] ? entity.state === "on" : entity.state !== "off";
      return `<button class="pill${on ? " on" : ""}" data-action="toggle" title="Hot water">
        <ha-icon icon="mdi:water-boiler"></ha-icon>${on ? "On" : "Off"}</button>`;
    }
    const on = state.state === "on" || status.on;
    return `<span class="pill${on ? " on" : ""}"><ha-icon icon="mdi:water-boiler"></ha-icon>${on ? "On" : "Off"}</span>`;
  }

  async _toggle() {
    const { switch: sw, waterHeater } = this._related();
    if (sw && this._hass.states[sw]) {
      await this._hass.callService("switch", "toggle", {}, { entity_id: sw });
    } else if (waterHeater && this._hass.states[waterHeater]) {
      const off = this._hass.states[waterHeater].state === "off";
      await this._hass.callService("water_heater", "set_operation_mode", { operation_mode: off ? "eco" : "off" }, { entity_id: waterHeater });
    }
  }

  /** The boost: through its select (minutes, "off" for none) or switch (a fixed
   * boost), else the water heater's high demand mode. */
  _boostState() {
    const { boost, waterHeater } = this._related();
    const select = boost && this._hass.states[boost];
    if (select && boost.startsWith("switch.")) return { via: "switch", active: select.state === "on", minutes: 0, options: [] };
    if (select) {
      const minutes = Number.parseInt(select.state, 10);
      const options = (select.attributes.options ?? []).filter((o) => o !== "off");
      return { via: "select", active: Number.isInteger(minutes) && minutes > 0, minutes: minutes || 0, options };
    }
    const heater = waterHeater && this._hass.states[waterHeater];
    if (heater) return { via: "water_heater", active: heater.state === "high_demand", minutes: 0, options: [] };
    return { via: null, active: false };
  }

  _boostHtml(boost) {
    if (!boost.via) return "";
    const label = boost.active ? `Boost${boost.minutes ? ` · ${duration(boost.minutes)}` : ""}` : "Boost";
    const items =
      boost.via === "select"
        ? boost.options.map((o) => ({ option: o, text: Number(o) >= 60 ? `${Number(o) / 60} h` : `${o} min` }))
        : [{ option: "on", text: "Boost now" }];
    // Ending a boost is offered only while one runs.
    if (boost.active) items.push({ option: "off", text: "Off" });
    const menu = this._menu
      ? `<div class="menu" role="menu">${items
          .map((i) => `<button class="menu-item" data-action="boost" data-option="${escapeHtml(i.option)}">${escapeHtml(i.text)}</button>`)
          .join("")}</div>`
      : "";
    return `<span class="boost-wrap"><button class="pill boost${boost.active ? " active" : ""}" data-action="boost-menu"
      aria-haspopup="menu" aria-expanded="${Boolean(this._menu)}" aria-label="${label}"><ha-icon icon="mdi:rocket-launch-outline"></ha-icon><span class="text">${label}</span></button>${menu}</span>`;
  }

  async _boost(target) {
    if (target.dataset.action === "boost-menu") {
      this._menu = !this._menu;
      return this._render();
    }
    this._menu = false;
    this._render();
    const option = target.dataset.option;
    const { boost, waterHeater } = this._related();
    const via = this._boostState().via;
    if (via === "select") {
      await this._hass.callService("select", "select_option", { option }, { entity_id: boost });
    } else if (via === "switch") {
      await this._hass.callService("switch", option === "off" ? "turn_off" : "turn_on", {}, { entity_id: boost });
    } else {
      const mode = option === "off" ? "eco" : "high_demand";
      await this._hass.callService("water_heater", "set_operation_mode", { operation_mode: mode }, { entity_id: waterHeater });
    }
  }

  // --- Editor rows: an on and an off ---

  _rowsOf(list) {
    return list.map((p) => ({ on: p.on, off: p.off }));
  }

  _sorted(rows) {
    return [...rows].sort((a, b) => (toMinutes(a.on) ?? 0) - (toMinutes(b.on) ?? 0));
  }

  _payloadOf(row) {
    return { on: row.on, off: row.off };
  }

  _miniHtml(draft, day) {
    return this._blocksHtml(periods(draft), day, false);
  }

  _dayBarHtml(draft, day) {
    return this._blocksHtml(periods(draft), day, true);
  }

  _carryText(draft, day) {
    const spill = hotWaterBlocks(periods(draft), day).find((b) => !b.first && b.start === 0);
    return spill ? `On from midnight until ${hhmm(spill.end)}` : "";
  }

  _rowHtml(row, i, problem) {
    return `<div class="row${problem ? " invalid" : ""}">
      ${this._timeHtml("on", row.on, i, "mdi:water-boiler", "On")}
      <span class="dash">–</span>
      ${this._timeHtml("off", row.off, i, "mdi:water-boiler-off", "Off")}
      <span class="grow"></span>
      <button class="icon-button" data-action="remove" data-row="${i}" aria-label="Remove"><ha-icon icon="mdi:trash-can-outline"></ha-icon></button>
    </div>`;
  }

  _newRow(rows) {
    const last = this._sorted(rows).at(-1);
    const on = last ? Math.min((toMinutes(last.off) ?? 0) + 60, DAY - 60) : 6 * 60;
    return { on: hhmm(on), off: hhmm(on + 60) };
  }

  /** What the server would refuse, by day ("mon") and row ("mon:2"), overlaps checked across the week. */
  _problems(draft) {
    const problems = new Map();
    const limit = this._state.attributes.max_per_day ?? this._maxPerDay;
    const add = (key, text) => problems.set(key, [...(problems.get(key) ?? []), text]);
    const spans = [];
    DAYS.forEach((name, day) => {
      const rows = draft[name];
      if (rows.length > limit) add(name, `At most ${limit} periods a day.`);
      rows.forEach((r, i) => {
        const on = toMinutes(r.on);
        const off = toMinutes(r.off);
        if (on === null || off === null) {
          add(name, "Each period needs an on and an off time.");
          add(`${name}:${i}`, "time");
          return;
        }
        if (on === off) {
          add(name, `A period from ${r.on} to itself.`);
          add(`${name}:${i}`, "empty");
          return;
        }
        const start = day * DAY + on;
        spans.push({ name, i, start, end: start + ((off - on + DAY) % DAY) });
      });
    });
    spans.sort((a, b) => a.start - b.start);
    spans.forEach((s, k) => {
      const next = spans[(k + 1) % spans.length];
      const nextStart = k + 1 < spans.length ? next.start : next.start + WEEK;
      if (spans.length > 1 && s.end > nextStart) {
        add(`${s.name}:${s.i}`, "overlap");
        add(`${next.name}:${next.i}`, "overlap");
        add(s.name, "Periods overlap.");
        if (next.name !== s.name) add(next.name, "Periods overlap.");
      }
    });
    return problems;
  }
}

customElements.get("unfr0zen-heating-card") || customElements.define("unfr0zen-heating-card", Unfr0zenHeatingCard);
customElements.get("unfr0zen-hot-water-card") || customElements.define("unfr0zen-hot-water-card", Unfr0zenHotWaterCard);

window.customCards = window.customCards || [];
for (const card of [
  { type: "unfr0zen-heating-card", name: "unfr0zen heating", description: "A Nest's heating schedule: the week, and editing it a day at a time." },
  { type: "unfr0zen-hot-water-card", name: "unfr0zen hot water", description: "A Nest's hot water schedule: the on periods, and editing them." },
]) {
  if (!window.customCards.some((c) => c.type === card.type)) {
    window.customCards.push({ ...card, preview: true, documentationURL: DOCS });
  }
}
