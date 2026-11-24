// unfr0zen's Lovelace cards: a Nest's heating and hot water schedules, as
// the unfr0zen integration's calendars hold them, shown as a week of bars
// and edited a day at a time.
//
//   type: custom:unfr0zen-heating-card     entity: calendar.<nest>_heating_schedule
//   type: custom:unfr0zen-hot-water-card   entity: calendar.<nest>_hot_water_schedule
//
// The calendars' attributes carry the week ("week", by day); a save calls the
// integration's set_schedule / set_hot_water_schedule with the days changed.
//
// The cards' words are in locales/<language>.json beside this file, English
// the base: a language's file need only have the strings it translates.
// Day names and numbers come from the browser, in the same language.

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY = 24 * 60;
const WEEK = 7 * DAY;
const DOCS = "https://github.com/unfr0zen/homeassistant-unfr0zen";
/** A drag's times fall on this grid, and keep slots at least this long, in minutes. */
const SNAP = 15;
/** The boost lengths the unit's screen offers, in minutes: a Matter boost, which takes any up to the longest, offers these. */
const BOOST_MINUTES = ["30", "60", "120"];

// ----- Words -----

/** The languages with a locales file; another falls back to its base language, then English. */
const LANGUAGES = ["cs", "da", "de", "en", "es", "fr", "it", "nb", "nl", "pl", "pt-BR", "sv", "zh-Hans"];
/** Base languages whose file goes by a region or script. */
const LANGUAGE_FILE = { pt: "pt-BR", zh: "zh-Hans", no: "nb" };
const VERSION = new URL(import.meta.url).searchParams.get("v") ?? "";
const STRINGS = {};
let language = "en";

/** A language's words, or null when they couldn't be had: tried again later. */
async function fetchStrings(lang) {
  try {
    const response = await fetch(new URL(`locales/${lang}.json?v=${VERSION}`, import.meta.url));
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

/** The locales file for a language tag such as "de", "pt-BR" or "en-GB", if there is one. */
function languageFile(tag) {
  if (!tag) return "en";
  if (LANGUAGES.includes(tag)) return tag;
  const base = tag.split("-")[0];
  if (LANGUAGES.includes(base)) return base;
  return LANGUAGE_FILE[base] ?? "en";
}

const loading = new Map();

/** Loads a language's words once; a failed load is tried again on the next call. */
function load(lang) {
  if (!loading.has(lang)) {
    loading.set(
      lang,
      fetchStrings(lang).then((words) => {
        if (words) STRINGS[lang] = words;
        else loading.delete(lang);
      }),
    );
  }
  return loading.get(lang);
}

/** Has the cards speak `tag`'s language; resolves once its words are loaded. */
function useLanguage(tag) {
  const lang = languageFile(tag);
  return Promise.all([load("en"), lang === "en" ? null : load(lang)]).then(() => {
    // Its words, or the language it had until they come.
    if (language !== lang && STRINGS[lang]) {
      language = lang;
      pickerEntries();
    }
  });
}

/** A string in the cards' language, its {placeholders} filled in from `values`. */
/** Whether a calendar's entity can't reach its thermostat: the card shows it so, not an empty week. */
function unavailable(state) {
  return !state || state.state === "unavailable";
}

function t(key, values = {}) {
  const text = STRINGS[language]?.[key] ?? STRINGS.en?.[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (all, name) => (name in values ? String(values[name]) : all));
}

/** The cards in the card picker, in the cards' language. */
function pickerEntries() {
  window.customCards = window.customCards || [];
  for (const card of [
    { type: "unfr0zen-heating-card", name: t("picker_heating_name"), description: t("picker_heating_description") },
    { type: "unfr0zen-hot-water-card", name: t("picker_hot_water_name"), description: t("picker_hot_water_description") },
  ]) {
    const entry = window.customCards.find((c) => c.type === card.type);
    if (entry) Object.assign(entry, card);
    else window.customCards.push({ ...card, preview: true, documentationURL: DOCS });
  }
}

/** A day's name (0 Monday) in the cards' language, "short" or "long". */
function dayName(day, style = "short") {
  // 2024-01-01 was a Monday.
  const date = new Date(Date.UTC(2024, 0, 1 + day));
  return new Intl.DateTimeFormat(language, { weekday: style, timeZone: "UTC" }).format(date);
}

await useLanguage(document.documentElement.lang || navigator.language);

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

// Whether Home Assistant shows times on the 12-hour clock; the schedules
// are on the 24-hour one whatever it shows.
let twelveHour = false;
let clockLocale;

/** Has the cards show times as a Home Assistant locale does: on its 12 or 24
 * hours, else as its language or the browser has them. */
function useClock(locale) {
  if (locale === clockLocale) return;
  clockLocale = locale;
  const format = locale?.time_format;
  const tag = format === "system" ? undefined : locale?.language;
  twelveHour =
    format === "12" || (format !== "24" && new Intl.DateTimeFormat(tag, { hour: "numeric" }).resolvedOptions().hour12 === true);
}

/** The 12-hour clock in the cards' language: an hour and its AM or PM, with its minutes if asked for. */
function twelve(minutes) {
  return new Intl.DateTimeFormat(language, { hour: "numeric", minute: minutes ? "2-digit" : undefined, hour12: true, timeZone: "UTC" });
}

/** Minutes after midnight as a date for the 12-hour clock to format. */
function dateAt(minutes) {
  return new Date(Date.UTC(2024, 0, 1, 0, minutes));
}

/** A time of day as Home Assistant shows them: "06:30", or "6:30 AM". */
function clock(minutes) {
  return twelveHour ? twelve(true).format(dateAt(minutes)) : hhmm(minutes);
}

/** An hour of the axis: "06", or "6 AM". */
function axisHour(hour) {
  return twelveHour ? twelve(false).format(dateAt(hour * 60)) : String(hour).padStart(2, "0");
}

/** "AM" or "PM" as the cards' language writes them. */
function meridiem(pm) {
  const part = twelve(false).formatToParts(dateAt(pm ? DAY / 2 : 0)).find((p) => p.type === "dayPeriod");
  return part?.value ?? (pm ? "PM" : "AM");
}

/** A length of time as the hot water blocks label it: "2h", "1h30", "45m". */
function duration(minutes) {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (!h) return t("duration_minutes", { m });
  return m ? t("duration_hours_minutes", { h, mm: String(m).padStart(2, "0") }) : t("duration_hours", { h });
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
  const day = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(part("weekday"));
  return { day: day < 0 ? 0 : day, minute: Number(part("hour")) * 60 + Number(part("minute")) };
}

/** When a change `ahead` minutes from now, on `day`, at `minute`, happens: "at 22:30", "Wed 06:30". */
function when(now, day, minute, ahead) {
  if (day === now.day && ahead < DAY) return t("when_at", { time: clock(minute) });
  return t("when_day", { day: dayName(day), time: clock(minute) });
}

/** `value` within lo..hi, widened to take in `was`: what already stands is never pushed out. */
function bound(value, lo, hi, was) {
  return Math.min(Math.max(value, Math.min(lo, was)), Math.max(hi, was));
}

/** A drag's minutes on the grid. */
function snap(minutes) {
  return Math.round(minutes / SNAP) * SNAP;
}

/** A position along a day's bar. */
function along(minute) {
  return `${(minute / DAY) * 100}%`;
}

/** Fit the header, slot labels and time axis to the card's available width. */
function fit(root) {
  for (const head of root.querySelectorAll(".head")) {
    const titles = head.querySelector(".titles");
    head.classList.remove("compact");
    if ([...titles.children].some((line) => line.scrollWidth > titles.clientWidth)) head.classList.add("compact");
  }
  for (const el of root.querySelectorAll(".seg, .block")) {
    el.classList.remove("clipped");
    if (el.textContent && el.scrollWidth > el.clientWidth) el.classList.add("clipped");
  }
  for (const axis of root.querySelectorAll(".axis")) {
    for (const level of ["", "sparse", "ends", "first"]) {
      axis.classList.remove("sparse", "ends", "first");
      if (level) axis.classList.add(level);
      const shown = [...axis.children].filter((s) => s.offsetWidth).map((s) => s.getBoundingClientRect());
      if (shown.every((r, i) => !i || r.left >= shown[i - 1].right + 4)) break;
    }
  }
}

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

// Whether Home Assistant shows temperatures in Fahrenheit; the schedules
// are in Celsius whatever it shows.
let fahrenheit = false;

/** A temperature as Home Assistant shows them. */
function shown(temp) {
  return `${new Intl.NumberFormat(language, { maximumFractionDigits: 1 }).format(temp)}°`;
}

/** A schedule's Celsius temperature, in the unit Home Assistant shows. */
function degrees(celsius) {
  return shown(fahrenheit ? Math.round((celsius * 9) / 5 + 32) : celsius);
}

// ----- Heating: setpoints holding until the next -----

/** The least a range's high is above its low, °C, as the Nest keeps them. */
const RANGE_GAP = 1.5;

/** The week's setpoints in week order: {day, minute, temp, high}; a range
 * schedule's temp is its low, and high its high (null otherwise). */
function setpoints(week) {
  const out = [];
  DAYS.forEach((name, day) => {
    for (const p of week?.[name] ?? []) {
      const minute = toMinutes(p.time);
      const high = p.high === undefined ? null : Number(p.high);
      if (minute !== null) out.push({ day, minute, temp: Number(p.low ?? p.temp), high });
    }
  });
  return out.sort((a, b) => a.day * DAY + a.minute - (b.day * DAY + b.minute));
}

/** The setpoint carried into `day` at midnight: the last of the nearest earlier day with one. */
function carried(points, day) {
  for (let back = 1; back <= 7; back++) {
    const d = (day - back + 7) % 7;
    const last = points.filter((p) => p.day === d).at(-1);
    if (last) return last;
  }
  return null;
}

/** A setpoint's temperatures as shown: one, or a range's low and high. */
function temps(p) {
  return p.high === null || p.high === undefined ? degrees(p.temp) : `${degrees(p.temp)}–${degrees(p.high)}`;
}

/** Whether two setpoints set the same temperatures. */
function same(a, b) {
  return a.temp === b.temp && (a.high ?? null) === (b.high ?? null);
}

/** A day's segments, {start, end, temp, high}: from midnight, then each setpoint until the next. */
function heatingSegments(points, day) {
  const today = points.filter((p) => p.day === day);
  const segments = [];
  let start = 0;
  let from = carried(points, day) ?? { temp: null, high: null };
  for (const p of today) {
    if (p.minute > start) segments.push({ start, end: p.minute, temp: from.temp, high: from.high });
    start = p.minute;
    from = p;
  }
  segments.push({ start, end: DAY, temp: from.temp, high: from.high });
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
    if (!same(p, current)) {
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
    /* An opaque surface, as Home Assistant's dialogs have, for what floats
       over a card: a theme's cards may be see-through. */
    --u0-surface: var(--ha-dialog-surface-background, var(--mdc-theme-surface, var(--primary-background-color, #1c1c1e)));
    --u0-text: var(--primary-text-color, #e1e1e1);
    --u0-muted: var(--secondary-text-color, #9b9b9b);
    --u0-radius: 14px;
    display: block;
    /* What the card's own narrow rules, such as its padding, go by. */
    container-type: inline-size;
  }
  /* A cooling schedule in the cooling colour. */
  :host(.cooling) {
    --u0-warm: var(--state-climate-cool-color, #2196f3);
    --u0-cool: color-mix(in srgb, var(--u0-warm) 22%, var(--u0-card));
  }
  /* A range schedule (heat-cool) in Home Assistant's purple, between its
     heating and cooling colours. */
  :host(.ranging) {
    --u0-warm: var(--purple-color, #926bc7);
    --u0-cool: color-mix(in srgb, var(--u0-warm) 22%, var(--u0-card));
  }
  ha-card, .panel {
    background: var(--u0-card);
    color: var(--u0-text);
    border-radius: var(--ha-card-border-radius, 20px);
    container-type: inline-size;
  }
  ha-card { display: block; padding: 20px 20px 18px; }
  .head { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; column-gap: 8px; margin-bottom: 18px; }
  .titles { min-width: 0; }
  .title, .subtitle { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .title { font-size: 1.35rem; font-weight: 600; line-height: 1.2; }
  .subtitle { color: var(--u0-muted); margin-top: 4px; font-size: 0.95rem; }
  .pill {
    display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; border-radius: 999px; white-space: nowrap;
    background: var(--u0-raised); font-weight: 600; font-size: 1.05rem; border: 0; color: inherit; font-family: inherit;
  }
  .pill.on { background: var(--u0-on); color: #fff; }
  /* The thermostat's target while it heats or cools, in Home Assistant's colours. */
  .pill.heating { background: var(--state-climate-heat-color, #ff6f22); color: #fff; }
  .pill.cooling { background: var(--state-climate-cool-color, #2196f3); color: #fff; }
  button.pill { cursor: pointer; }
  .pill ha-icon { --mdc-icon-size: 20px; }
  .controls { grid-area: 1 / 2; display: flex; align-items: center; gap: 8px; }
  /* Keep the text block beside the controls, reducing button labels when space is tight. */
  .head.compact, .head.compact .controls { column-gap: 4px; }
  .head.compact .pill { padding-inline: 8px; gap: 4px; }
  .head.compact .edit-button { width: 32px; }
  .head.compact .boost { padding: 8px; gap: 0; }
  .head.compact .boost .text { display: none; }
  .head.compact [role="switch"] { font-size: 0.95rem; }
  .boost-wrap { position: relative; }
  .pill.boost { font-size: 0.95rem; padding: 8px 14px; }
  .pill.boost.active { background: var(--u0-boost); color: #fff; }
  .menu {
    --u0-raised: color-mix(in srgb, var(--primary-text-color, #e1e1e1) 6%, var(--u0-surface));
    position: absolute; right: 0; top: calc(100% + 6px); z-index: 5; min-width: 140px; padding: 6px;
    background: var(--u0-surface); border: 1px solid var(--u0-line); border-radius: 14px;
    box-shadow: 0 10px 30px rgba(0,0,0,.35); display: flex; flex-direction: column;
  }
  .menu-item {
    border: 0; background: transparent; color: var(--u0-text); font: inherit; font-weight: 600; text-align: left;
    padding: 10px 12px; border-radius: 10px; cursor: pointer;
  }
  .menu-item:hover { background: var(--u0-raised); }
  .heating-now { display: inline-flex; align-items: center; vertical-align: middle; gap: 4px;
    margin-inline-start: 8px; color: var(--u0-warm); font-weight: 600; font-size: 0.88rem; }
  .heating-now ha-icon { --mdc-icon-size: 16px; }
  .week { display: grid; row-gap: 10px; }
  .week-row { display: grid; grid-template-columns: 44px minmax(0, 1fr); align-items: center; background: none;
    border: 0; border-radius: 9px; color: inherit; padding: 0; font: inherit; text-align: start; min-width: 0; }
  .week-row:focus-visible { outline: 2px solid var(--primary-color, currentColor); outline-offset: 3px; }
  .day { font-weight: 600; color: var(--u0-muted); font-size: 0.95rem; }
  .day.today { color: var(--u0-text); }
  .bar {
    position: relative; height: 30px; border-radius: 9px; overflow: hidden; display: flex;
    background: var(--u0-track);
  }
  .bar.today { outline: 1px solid var(--u0-line); outline-offset: 2px; }
  .week [data-action] { cursor: pointer; }
  button.week-row:hover .bar { filter: brightness(1.12); }
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
  .axis span { white-space: nowrap; }
  .axis.sparse span:nth-child(even), .axis.ends span:not(:first-child, :last-child),
    .axis.first span:not(:first-child) { display: none; }
  .seg.clipped, .block.clipped { color: transparent; }
  .axis.indent { margin-left: 44px; }
  .empty { color: var(--u0-muted); font-size: 0.85rem; padding-left: 10px; align-self: center; }
  .note { color: var(--u0-muted); font-size: 0.9rem; margin-top: 16px; text-align: center; }
  .warning { color: var(--error-color, #db4437); }
  @container (max-width: 420px) {
    .seg.small, .block.small { color: transparent; }
    .title { font-size: 1.2rem; }
    .week-row { grid-template-columns: 38px minmax(0, 1fr); }
    .axis.indent { margin-left: 38px; }
    ha-card { padding: 16px 14px 14px; }
  }
  @container (max-width: 340px) {
    .title:has(.heating-now) { font-size: 1rem; line-height: 1.44rem; }
    .heating-now { gap: 2px; margin-inline-start: 4px; font-size: 0.75rem; line-height: 1.2; }
    .heating-now ha-icon { --mdc-icon-size: 14px; }
    .head.compact:has(.heating-now) .pill { padding-inline: 4px; }
    .head.compact:has(.heating-now) .controls { gap: 2px; }
  }

  dialog {
    padding: 0; border: 0; background: transparent; color: var(--u0-text); max-width: min(560px, calc(100vw - 16px));
    width: 100%; max-height: calc(100vh - 16px);
  }
  dialog::backdrop { background: rgba(0, 0, 0, 0.55); }
  .panel {
    /* The opaque surface; the colours mixed from the card follow it. */
    --u0-card: var(--u0-surface);
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
  /* A day that can be copied to, and one that is. */
  .tab.pick { box-shadow: inset 0 0 0 1.5px var(--u0-line); }
  .tab.copy { box-shadow: inset 0 0 0 2px var(--u0-warm); color: var(--u0-text); }
  .tab.changed .label::after { content: "•"; margin-left: 2px; color: var(--u0-warm); }
  .mini { position: relative; width: 70%; height: 5px; border-radius: 3px; overflow: hidden; background: var(--u0-track); }
  .mini .seg, .mini .block { top: 0; bottom: 0; border-radius: 0; }
  .day-head { display: flex; justify-content: space-between; align-items: baseline; margin: 18px 0 8px; gap: 8px; }
  .day-name { font-weight: 600; font-size: 1.05rem; }
  .carry { color: var(--u0-muted); font-size: 0.92rem; }
  .rows { display: flex; flex-direction: column; gap: 10px; margin-top: 16px; }
  /* What doesn't fit goes to a second line rather than out of sight, the remove button at the right. */
  .row {
    display: flex; flex-wrap: wrap; align-items: center; gap: 10px; background: var(--u0-raised); border-radius: var(--u0-radius);
    padding: 10px 12px;
  }
  .row > .icon-button { margin-left: auto; }
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
  .meridiem {
    border: 0; border-radius: 8px; padding: 5px 7px; background: var(--u0-raised); color: var(--u0-text);
    font: inherit; font-weight: 700; font-size: 0.85rem; cursor: pointer; white-space: nowrap;
  }
  .dash { color: var(--u0-muted); }
  .grow { flex: 1; }
  .stepper { display: inline-flex; align-items: center; gap: 6px; }
  .stepper .which { color: var(--u0-muted); font-size: 0.8rem; font-weight: 600; }
  /* A range: the time and remove on one line, the low and high under them. */
  .row.range { flex-wrap: wrap; row-gap: 8px; }
  .row.range .steppers { flex-basis: 100%; display: flex; justify-content: space-between; gap: 8px; }
  .seg.divided { box-shadow: inset 2px 0 0 var(--u0-surface); }
  .handles { position: absolute; inset: 0; z-index: 3; }
  .grab, .edge { position: absolute; top: 0; bottom: 0; touch-action: none; user-select: none; }
  .grab { cursor: grab; }
  .edge { transform: translateX(-50%); cursor: ew-resize; z-index: 1; }
  .edge::after {
    content: ""; position: absolute; left: 50%; margin-left: -1px; top: 5px; bottom: 5px; width: 2px; border-radius: 1px;
    background: #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.35); opacity: 0; transition: opacity .12s;
  }
  .bar:hover .edge::after { opacity: .55; }
  .edge:hover::after, .edge.active::after { opacity: 1; }
  .grab:hover, .grab.active { background: rgba(255,255,255,.12); }
  .dragging, .dragging * { cursor: grabbing; user-select: none; }
  .dragging .edge.active, .dragging:has(.edge.active) * { cursor: ew-resize; }
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
  .actions { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; margin-top: 18px; gap: 10px; }
  .ask { font-weight: 600; }
  .answers { display: flex; gap: 10px; margin-left: auto; }
  .action {
    border: 0; border-radius: 12px; padding: 12px 18px; background: var(--u0-raised); color: var(--u0-text);
    font: inherit; font-weight: 600; font-size: 1.02rem; cursor: pointer; display: inline-flex; gap: 8px; align-items: center;
  }
  .action.active { box-shadow: inset 0 0 0 2px var(--u0-warm); }
  .action.danger { background: var(--error-color, #db4437); color: #fff; }
  .action.primary { background: var(--u0-warm); color: #fff; }
  .action.primary.water { background: var(--u0-water); }
  .action:disabled, .action.primary:disabled, .action.primary.water:disabled {
    opacity: .45; cursor: default; background: var(--u0-raised); color: var(--u0-muted);
  }
  .errors { color: var(--error-color, #db4437); margin-top: 12px; font-size: 0.92rem; }
  @media (max-width: 420px) {
    .panel { padding: 14px 10px; }
  }
  /* The editor's rows by the room the panel has, a 12-hour clock's AM and PM
     taking the place of the time's icon. */
  @container (max-width: 500px) {
    .tab { padding: 8px 0 6px; font-size: 0.82rem; }
    .row { padding: 8px; gap: 6px; }
    .time .part, .colon { font-size: 1.05rem; }
    .time { gap: 4px; padding: 6px 6px; }
    .time.twelve { gap: 2px; padding: 6px 4px; }
    .time.twelve ha-icon { display: none; }
    .meridiem { padding: 5px 4px; font-size: 0.8rem; }
    .round { width: 34px; height: 34px; }
    .temp { font-size: 1.05rem; min-width: 2.8em; }
  }
  @container (max-width: 400px) {
    .row { gap: 4px; }
    .time ha-icon { display: none; }
    .time.twelve { gap: 1px; padding: 6px 3px; }
    .time.twelve .part { width: 1.4em; }
    .meridiem { padding: 4px 3px; font-size: 0.72rem; }
    .row > .icon-button { width: 32px; height: 32px; }
    .round { width: 30px; height: 30px; }
    .stepper { gap: 4px; }
    .temp { min-width: 2.6em; }
    /* A range's Low and High over their steppers. */
    .row.range .stepper { flex-wrap: wrap; justify-content: center; row-gap: 2px; }
    .row.range .which { flex-basis: 100%; text-align: center; }
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
    if (!config?.entity) throw new Error(t("choose_calendar"));
    this._config = config;
    this._rendered = null;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    // In the language the user has Home Assistant in; each card redrawn
    // once its words arrive, if it was drawn in another.
    useLanguage(hass.locale?.language ?? hass.language).then(() => {
      if (this._language !== language) {
        this._render();
        this._renderEditor();
      }
    });
    fahrenheit = hass.config?.unit_system?.temperature === "°F";
    useClock(hass.locale);
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
    // The boost menu closes on a press anywhere outside the card; one inside is _click's.
    this._outside = (e) => {
      if (this._menu && !e.composedPath().includes(this)) {
        this._menu = false;
        this._render();
      }
    };
    document.addEventListener("pointerdown", this._outside, true);
  }

  disconnectedCallback() {
    clearInterval(this._tick);
    document.removeEventListener("pointerdown", this._outside, true);
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
      this.shadowRoot.innerHTML = `<style>${STYLES}</style><ha-card></ha-card><dialog aria-labelledby="editor-title"></dialog>`;
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
      const dialog = this.shadowRoot.querySelector("dialog");
      // What fits follows the card's and the editor's widths.
      const resized = new ResizeObserver(() => {
        fit(this.shadowRoot.querySelector("ha-card"));
        fit(dialog);
      });
      resized.observe(this.shadowRoot.querySelector("ha-card"));
      resized.observe(dialog);
      dialog.addEventListener("close", () => {
        if (dialog.open) return;
        this._editor = null;
        this._drag = null;
        // A card refresh can replace the opener while the editor is open.
        if (this._opener && (document.activeElement === document.body || dialog.contains(this.shadowRoot.activeElement)))
          this.shadowRoot.querySelector(this._opener)?.focus({ preventScroll: true });
        this._opener = null;
      });
      // Escape asks before changes are discarded; pressed again, it closes.
      dialog.addEventListener("cancel", (e) => {
        const editor = this._editor;
        if (!editor || editor.saving || editor.discarding || !this._dirty()) return;
        e.preventDefault();
        editor.discarding = true;
        this._renderEditor();
      });
      dialog.addEventListener("pointerdown", (e) => this._dragStart(e));
      dialog.addEventListener("pointermove", (e) => this._dragMove(e));
      dialog.addEventListener("pointerup", (e) => this._dragEnd(e));
      dialog.addEventListener("pointercancel", (e) => this._dragEnd(e));
    }
    const card = this.shadowRoot.querySelector("ha-card");
    const state = this._state;
    if (!this._hass) return;
    if (!state) {
      card.innerHTML = `<div class="warning">${escapeHtml(t("entity_not_found", { entity: this._config.entity }))}</div>`;
      return;
    }
    this.classList.toggle("cooling", state.attributes.mode === "cool");
    this.classList.toggle("ranging", state.attributes.mode === "range");
    this.setAttribute("lang", language);
    this._language = language;
    const focusedDay = this.shadowRoot.activeElement?.closest(".week-row")?.dataset.day;
    card.innerHTML = this._cardHtml(state);
    if (focusedDay !== undefined) card.querySelector(`button[data-day="${focusedDay}"]`)?.focus({ preventScroll: true });
    fit(card);
  }

  _week(state) {
    return state.attributes.week ?? {};
  }

  _axis(indent) {
    return `<div class="axis${indent ? " indent" : ""}">${[0, 6, 12, 18, 24].map((h) => `<span>${axisHour(h)}</span>`).join("")}</div>`;
  }

  _editable(state) {
    return !unavailable(state) && state.attributes.writable !== false;
  }

  /** The header's button that opens the editor on today. */
  _editHtml(state) {
    if (!this._editable(state)) return "";
    return `<button class="icon-button edit-button" data-action="open" aria-label="${t("edit")}" title="${t("edit_hint")}"><ha-icon icon="mdi:pencil-outline"></ha-icon></button>`;
  }

  _dayRow(state, day, now, bar) {
    const today = day === now.day;
    const cls = today ? " today" : "";
    const marker = today ? `<span class="now" style="left:${along(now.minute)}"></span>` : "";
    const content = `<span class="day${cls}">${dayName(day)}</span><span class="bar${cls}">${bar}${marker}</span>`;
    if (!this._editable(state)) return `<div class="week-row">${content}</div>`;
    return `<button class="week-row" data-action="open" data-day="${day}" aria-label="${t("edit")} · ${dayName(day, "long")}">${content}</button>`;
  }

  /** A time field, "06 : 30", for row `i`'s `field`; on the 12-hour clock "6 : 30" and a button between AM and PM. */
  _timeHtml(field, value, i, icon, label) {
    const [h, m] = (value ?? "").split(":");
    const hint = (name) => (name === "h" ? t(twelveHour ? "hour_12" : "hour") : t("minute"));
    const part = (name, v, max) =>
      `<input class="part" inputmode="numeric" maxlength="2" value="${escapeHtml(v ?? "")}" data-field="${field}"
        data-part="${name}" data-max="${max}" data-row="${i}" aria-label="${label}: ${hint(name)}" title="${hint(name)}">`;
    const half = twelveHour && h
      ? `<button class="meridiem" data-action="meridiem" data-field="${field}" data-row="${i}">${meridiem(Number(h) >= 12)}</button>`
      : "";
    const hour = twelveHour && h ? String(Number(h) % 12 || 12) : h;
    return `<label class="time${half ? " twelve" : ""}"><ha-icon icon="${icon}"></ha-icon>${part("h", hour, 23)}<span class="colon">:</span>${part("m", m, 59)}${half}</label>`;
  }

  // --- The editor ---

  /** Opens the editor on `day`, or on today. */
  _open(day) {
    const state = this._state;
    this._opener = day === undefined ? ".edit-button" : `.week-row[data-day="${day}"]`;
    const week = this._week(state);
    const draft = {};
    for (const name of DAYS) draft[name] = this._rowsOf(week[name] ?? []);
    // A mode or handle change selects a different week from the one being edited.
    this._editor = { mode: state.attributes.mode, handle: state.attributes.schedule_handle, day: day ?? this._now.day, draft, original: JSON.parse(JSON.stringify(draft)), copy: new Set(), copying: false, discarding: false, error: null, saving: false };
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

  /** Whether closing the editor would lose changes. */
  _dirty() {
    return Object.keys(this._changes()).length > 0;
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
      const selected = i === e.day ? " selected" : e.copy.has(d) ? " copy" : "";
      // While copying, the other days' tabs take the shown day's rows or give them back.
      const pick = e.copying && i !== e.day;
      const title = pick ? t("copy_day_hint", { day: dayName(i, "long") }) : dayName(i, "long");
      return `<button class="tab${selected}${changed}${pick ? " pick" : ""}" data-action="day" data-day="${i}" aria-pressed="${i === e.day || e.copy.has(d)}" title="${title}">
        <span class="label">${dayName(i)}</span><span class="mini">${this._miniHtml(week, i)}</span></button>`;
    }).join("");
    const actions = e.discarding
      ? `<span class="ask">${t("discard_question")}</span>
          <span class="answers"><button class="action" data-action="keep">${t("keep_editing")}</button>
            <button class="action danger" data-action="discard">${t("discard")}</button></span>`
      : `<button class="action${e.copying ? " active" : ""}" data-action="copy" aria-pressed="${e.copying}" ${e.saving ? "disabled" : ""} title="${t("copy_hint")}"><ha-icon icon="mdi:content-copy"></ha-icon>${t("copy")}</button>
          <button class="action primary${this._water ? " water" : ""}" data-action="save" title="${t("save_hint")}"
            ${!Object.keys(changes).length || problems.size || e.saving ? "disabled" : ""}>${e.saving ? t("saving") : t("save")}</button>`;
    const errors = DAYS.flatMap((d, i) => (problems.get(d) ?? []).map((text) =>
      d === name ? text : `${dayName(i, "long")}: ${text}`));
    if (e.error) errors.push(e.error);
    // The time field being typed in, or the button pressed from the keyboard, to have it again once redrawn.
    const active = this.shadowRoot.activeElement;
    const focus = active && dialog.contains(active)
      ? active.localName + [...active.attributes].filter((a) => a.name.startsWith("data-")).map((a) => `[${a.name}="${CSS.escape(a.value)}"]`).join("")
      : null;
    dialog.innerHTML = `
      <div class="panel${this._drag ? " dragging" : ""}" role="document">
        <div class="panel-head">
          <span class="panel-title" id="editor-title">${escapeHtml(this._title())}</span>
          <button class="icon-button" data-action="close" aria-label="${t("close")}" title="${t("close_hint")}"><ha-icon icon="mdi:close"></ha-icon></button>
        </div>
        <div class="tabs">${tabs}</div>
        <div class="day-head"><span class="day-name">${dayName(e.day, "long")}</span><span class="carry">${this._carryText(week, e.day)}</span></div>
        <div class="bar">${this._dayBarHtml(week, e.day, false)}${this._handlesHtml(problems)}</div>
        ${this._axis(false)}
        <div class="rows">
          ${rows.map((r, i) => this._rowHtml(r, i, problems.get(`${name}:${i}`))).join("")}
          <button class="add" data-action="add" title="${rows.length >= max ? t("at_most_a_day", { max }) : this._addLabel}" ${rows.length >= max ? "disabled" : ""}>
            <ha-icon icon="mdi:plus"></ha-icon>${this._addLabel}</button>
        </div>
        ${errors.length ? `<div class="errors">${[...new Set(errors)].map(escapeHtml).join("<br>")}</div>` : ""}
        <div class="actions">${actions}</div>
      </div>`;
    fit(dialog);
    if (focus) dialog.querySelector(focus)?.focus();
  }

  _click(event) {
    const target = event.target.closest("[data-action]");
    const action = target?.dataset.action;
    // The boost menu closes on any click in the card outside it.
    if (this._menu && action !== "boost-menu" && action !== "boost") {
      this._menu = false;
      this._render();
    }
    if (!target) return;
    if (action === "open") return this._open(target.dataset.day === undefined ? undefined : Number(target.dataset.day));
    if (action === "toggle") return this._toggle();
    if (action === "boost-menu" || action === "boost") return this._boost(target);
    const e = this._editor;
    if (!e) return;
    const rows = e.draft[DAYS[e.day]];
    // The question about discarding stands until the next press.
    e.discarding = false;
    switch (action) {
      case "close":
        if (e.saving || !this._dirty()) return this._close();
        e.discarding = true;
        break;
      case "discard":
        return this._close();
      case "keep":
        break;
      case "day": {
        const day = Number(target.dataset.day);
        if (e.copying) {
          if (day !== e.day && !e.copy.delete(DAYS[day])) e.copy.add(DAYS[day]);
        } else {
          // The days copied to keep the rows they were given.
          for (const name of e.copy) e.draft[name] = rows.map((r) => ({ ...r }));
          e.copy.clear();
          e.day = day;
        }
        break;
      }
      case "add":
        rows.push(this._newRow(rows));
        break;
      case "remove":
        rows.splice(Number(target.dataset.row), 1);
        break;
      case "step":
        this._step(rows[Number(target.dataset.row)], Number(target.dataset.by), target.dataset.which);
        break;
      case "meridiem": {
        const row = rows[Number(target.dataset.row)];
        row[target.dataset.field] = hhmm(toMinutes(row[target.dataset.field]) + DAY / 2);
        break;
      }
      case "copy":
        e.copying = !e.copying;
        break;
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
    let n = Number.parseInt(input.value, 10);
    // On the 12-hour clock, 1 to 12 is an hour of the half of the day the time
    // is in; 0 and 13 to 23 are the 24-hour clock's.
    if (twelveHour && input.dataset.part === "h" && n >= 1 && n <= 12) n = (n % 12) + (Number(h) >= 12 ? 12 : 0);
    if (Number.isInteger(n) && n >= 0 && n <= Number(input.dataset.max)) {
      const value = String(n).padStart(2, "0");
      row[input.dataset.field] = input.dataset.part === "h" ? `${value}:${m}` : `${h}:${value}`;
    }
    e.error = null;
    if (this._pressing) this._redraw = true;
    else this._renderEditor();
  }

  // --- Dragging on the day's bar: an edge changes a time, the middle moves the slot ---

  _active(drag, row, owner) {
    return this._drag?.id === `${drag}:${owner}:${row}` ? " active" : "";
  }

  /** A slot's middle, which moves it whole. */
  _grab(row, left, width) {
    const owner = DAYS[this._editor.day];
    return `<div class="grab${this._active("move", row, owner)}" data-drag="move" data-row="${row}" data-day="${owner}"
      style="left:${along(left)};width:${along(width)}" title="${t("drag_move_hint")}"></div>`;
  }

  /** An edge, which changes one time: 14 px, at most half the shortest slot it borders that has a middle to grab. */
  _edge(drag, row, at, room, owner = DAYS[this._editor.day]) {
    const width = room ? `min(14px, ${along(room / 2)})` : "14px";
    return `<div class="edge${this._active(drag, row, owner)}" data-drag="${drag}" data-row="${row}" data-day="${owner}"
      style="left:${along(at)};width:${width}" title="${t("drag_edge_hint")}"></div>`;
  }

  /** A drag of the time `was` to the grid's points within lo..hi, each given to `set`; null when it has nowhere to go. */
  _mover(was, lo, hi, set) {
    const first = Math.ceil(lo / SNAP) * SNAP;
    const last = Math.floor(hi / SNAP) * SNAP;
    if (first > last || (first === last && first === was)) return null;
    return (delta) => {
      // Where it was until the pointer has moved half a step: a time off the grid stays as it is.
      const m = Math.abs(delta) < SNAP / 2 ? was : bound(snap(was + delta), first, last, was);
      set(m);
      return m;
    };
  }

  _dragStart(event) {
    const target = event.target.closest?.("[data-drag]");
    if (!target || event.button !== 0 || !this._editor || this._editor.saving || this._drag) return;
    const { drag, row, day } = target.dataset;
    // A time being typed counts before the drag: it is in the draft the drag starts from.
    const field = this.shadowRoot.activeElement;
    if (field?.matches?.("input[data-field]")) {
      this._change({ target: field });
      this.shadowRoot.activeElement?.blur();
    }
    const apply = this._dragger(drag, Number(row), day);
    if (!apply) return;
    event.preventDefault();
    this.shadowRoot.querySelector("dialog").setPointerCapture(event.pointerId);
    const width = this.shadowRoot.querySelector("dialog .bar").getBoundingClientRect().width;
    this._drag = { id: `${drag}:${day}:${row}`, pointer: event.pointerId, x: event.clientX, width, apply, last: null };
    this._renderEditor();
  }

  _dragMove(event) {
    const d = this._drag;
    if (!d || event.pointerId !== d.pointer || !this._editor) return;
    const key = d.apply(((event.clientX - d.x) / d.width) * DAY);
    if (key === d.last) return;
    d.last = key;
    this._editor.error = null;
    this._renderEditor();
  }

  _dragEnd(event) {
    if (!this._drag || event.pointerId !== this._drag.pointer) return;
    this._drag = null;
    this._renderEditor();
  }

  async _save() {
    const e = this._editor;
    const days = this._changes();
    if (!Object.keys(days).length) return;
    if (this._state?.attributes.mode !== e.mode || this._state?.attributes.schedule_handle !== e.handle) {
      e.error = t("mode_changed");
      return this._renderEditor();
    }
    e.saving = true;
    e.error = null;
    this._renderEditor();
    try {
      await this._hass.callService("unfr0zen", this._service, { days }, { entity_id: this._config.entity });
      this._close();
    } catch (err) {
      e.saving = false;
      e.error = await this._message(err);
      this._renderEditor();
    }
  }

  /** An error from the integration in the user's language, from its translation key. */
  async _message(err) {
    if (err?.translation_key && err?.translation_domain && this._hass.loadBackendTranslation) {
      try {
        const localize = await this._hass.loadBackendTranslation("exceptions", err.translation_domain);
        const key = `component.${err.translation_domain}.exceptions.${err.translation_key}.message`;
        const text = localize(key, err.translation_placeholders ?? {});
        if (text) return text;
      } catch {
        // The English message below.
      }
    }
    return err?.message ?? String(err);
  }
}

class Unfr0zenHeatingCard extends ScheduleCard {
  static getConfigForm() {
    return {
      computeLabel: (field) => t(`config_${field.name}`),
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
    return t("add_change");
  }

  /** The schedule the Nest runs: its cooling or heat-cool one as its mode is. */
  _title() {
    const mode = this._state?.attributes.mode;
    return this._config.name ?? t(mode === "cool" ? "cooling" : mode === "range" ? "heat_cool" : "heating");
  }

  get _range() {
    return this._state?.attributes.mode === "range";
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

  /** How strongly a setpoint is coloured, 0 to 100: the week's warmest the
   * most when heating, its coolest the most when cooling. */
  _warmth(points) {
    // A range by its middle.
    const level = (p) => (p.high === null || p.high === undefined ? p.temp : (p.temp + p.high) / 2);
    const levels = points.map(level);
    const lo = Math.min(...levels);
    const hi = Math.max(...levels);
    const cooling = this._state?.attributes.mode === "cool";
    return (p) => {
      // Ranges aren't warmer or cooler than each other: one shade.
      if (this._range || hi <= lo) return 70;
      const share = cooling ? (hi - level(p)) / (hi - lo) : (level(p) - lo) / (hi - lo);
      return Math.round(Math.pow(share, 1.6) * 100);
    };
  }

  _segmentsHtml(points, day, labels, warmth) {
    return heatingSegments(points, day)
      .filter((s) => s.temp !== null)
      .map((s) => {
        const left = (s.start / DAY) * 100;
        const width = ((s.end - s.start) / DAY) * 100;
        const w = warmth(s);
        const style = `left:${left}%;width:${width}%;background:color-mix(in srgb, var(--u0-warm) ${w}%, var(--u0-cool))`;
        // Ranges share a shade: a divider marks where one gives way to the next.
        const cls = `seg${w > 45 ? " warm" : ""}${width < 13 ? " small" : ""}${this._range && s.start > 0 ? " divided" : ""}`;
        const label = labels && width >= (s.high === null ? 6 : 11) ? temps(s) : "";
        const title = labels ? ` title="${clock(s.start)}–${clock(s.end)} · ${temps(s)}"` : "";
        return `<span class="${cls}" style="${style}"${title}>${label}</span>`;
      })
      .join("");
  }

  _cardHtml(state) {
    const week = this._week(state);
    const points = setpoints(week);
    const now = this._now;
    const warmth = this._warmth(points);
    const { current, next } = heatingNow(points, now);
    const away = unavailable(state);
    const climate = this._hass.states[this._relatedEntity()];
    const a = climate && !["unavailable", "unknown"].includes(climate.state) ? climate.attributes : {};
    const live = a.temperature != null ? shown(Number(a.temperature))
      : a.target_temp_low != null && a.target_temp_high != null ? `${shown(Number(a.target_temp_low))}–${shown(Number(a.target_temp_high))}` : "";
    const scheduled = current && !away ? temps(current) : "";
    const notes = [];
    if (!scheduled) notes.push(t(away ? "unavailable" : "no_schedule"));
    if (next && !away) notes.push(t("next_change", { temps: temps(next), when: when(now, next.day, next.minute, next.ahead) }));
    if (scheduled && live && live !== scheduled) notes.push(t("scheduled_temp", { temps: scheduled }));
    const subtitle = escapeHtml(notes.join(" · "));
    const rows = DAYS.map((_, day) => this._dayRow(state, day, now, points.length && !away
      ? this._segmentsHtml(points, day, true, warmth)
      : `<span class="empty">${t(away ? "unavailable" : "no_setpoints")}</span>`)).join("");
    return `
      <div class="head">
        <div class="titles"><div class="title">${escapeHtml(this._title())}</div>
          ${subtitle ? `<div class="subtitle" title="${subtitle}">${subtitle}</div>` : ""}</div>
        <div class="controls">${this._pillHtml(live, scheduled, a.hvac_action)}${this._editHtml(state)}</div>
      </div>
      <div class="week">${rows}</div>
      ${this._axis(true)}`;
  }

  _pillHtml(live, scheduled, hvacAction) {
    if (!live && !scheduled) return "";
    const text = live || t("scheduled_temp", { temps: scheduled });
    const action = hvacAction === "heating" || hvacAction === "cooling" ? hvacAction : "";
    const hint = live ? t(action ? `target_${action}` : "target") : text;
    // The schedule's mode, in Home Assistant's icons for it.
    const icon = { cool: "mdi:snowflake", range: "mdi:sun-snowflake-variant" }[this._state?.attributes.mode] ?? "mdi:fire";
    return `<span class="pill${action ? ` ${action}` : ""}" title="${hint}"><ha-icon icon="${icon}"></ha-icon><span class="text">${text}</span></span>`;
  }

  // --- Editor rows: a time and a temperature ---

  _rowsOf(list) {
    return list.map((p) =>
      p.high === undefined ? { time: p.time, temp: Number(p.temp) } : { time: p.time, temp: Number(p.low), high: Number(p.high) },
    );
  }

  _sorted(rows) {
    return [...rows].sort((a, b) => (toMinutes(a.time) ?? 0) - (toMinutes(b.time) ?? 0));
  }

  _payloadOf(row) {
    return this._range ? { time: row.time, low: row.temp, high: row.high } : { time: row.time, temp: row.temp };
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
    const from = carried(this._draftPoints(draft), day);
    return from === null ? "" : t("from_midnight", { temps: temps(from) });
  }

  _rowHtml(row, i, problem) {
    const { min, max, step } = this._limits();
    // One stepper, or a range's two: the low it heats to, the high it cools to.
    const stepper = (which, label) => {
      const value = row[which];
      return `<span class="stepper">
        ${label ? `<span class="which">${label}</span>` : ""}
        <button class="round" data-action="step" data-row="${i}" data-which="${which}" data-by="-1" aria-label="${t("cooler")}" title="${t("cooler_hint", { step })}" ${value <= min ? "disabled" : ""}><ha-icon icon="mdi:minus"></ha-icon></button>
        <span class="temp">${degrees(value)}</span>
        <button class="round" data-action="step" data-row="${i}" data-which="${which}" data-by="1" aria-label="${t("warmer")}" title="${t("warmer_hint", { step })}" ${value >= max ? "disabled" : ""}><ha-icon icon="mdi:plus"></ha-icon></button>
      </span>`;
    };
    // A range's two steppers share a line under the time.
    const steppers = this._range
      ? `<div class="steppers">${stepper("temp", t("low"))}${stepper("high", t("high"))}</div>`
      : stepper("temp", "");
    return `<div class="row${problem ? " invalid" : ""}${this._range ? " range" : ""}">
      ${this._timeHtml("time", row.time, i, "mdi:clock-outline", t("time"))}
      <span class="grow"></span>
      ${this._range ? "" : steppers}
      <button class="icon-button" data-action="remove" data-row="${i}" aria-label="${t("remove")}" title="${t("remove")}"><ha-icon icon="mdi:trash-can-outline"></ha-icon></button>
      ${this._range ? steppers : ""}
    </div>`;
  }

  /** The shown day's changes by time, [{i, m}]; null while a time is missing or doubled. */
  _timeline() {
    const e = this._editor;
    const list = e.draft[DAYS[e.day]].map((r, i) => ({ i, m: toMinutes(r.time) }));
    if (list.some((x) => x.m === null) || new Set(list.map((x) => x.m)).size !== list.length) return null;
    return list.sort((a, b) => a.m - b.m);
  }

  /** An edge at each change; the middle of each slot between two of the day's changes. */
  _handlesHtml() {
    const list = this._timeline();
    if (!list?.length) return "";
    const grabs = list
      .slice(0, -1)
      .map((a, k) => this._grab(a.i, a.m, list[k + 1].m - a.m))
      .join("");
    const edges = list
      .map((x, k) => {
        // The slots either side with a middle: the day's first and last run to midnight without one.
        const sides = [k > 0 ? x.m - list[k - 1].m : 0, k + 1 < list.length ? list[k + 1].m - x.m : 0].filter(Boolean);
        return this._edge("edge", x.i, x.m, sides.length ? Math.min(...sides) : 0);
      })
      .join("");
    return `<div class="handles">${grabs}${edges}</div>`;
  }

  /** The nearest changes on other days, in minutes from midnight, on the shown day and each it is copied to: the
   * latest last change before it, and the earliest first change after. */
  _around() {
    const e = this._editor;
    const points = setpoints(this._effective());
    const days = [e.day, ...[...e.copy].map((name) => DAYS.indexOf(name))];
    let before = -Infinity;
    let after = Infinity;
    for (const day of days) {
      const others = points.filter((p) => p.day !== day);
      const ahead = (p) => ((p.day - day + 7) % 7) * DAY + p.minute;
      before = Math.max(before, ...others.map((p) => ahead(p) - WEEK));
      after = Math.min(after, ...others.map(ahead));
    }
    return { before, after };
  }

  /** A change's time between its neighbours', SNAP apart; a slot moved whole, its neighbours taking up the difference. */
  _dragger(drag, row) {
    const rows = this._editor.draft[DAYS[this._editor.day]];
    const list = this._timeline();
    const k = list?.findIndex((x) => x.i === row) ?? -1;
    if (k < 0) return null;
    // Midnight's slot runs on from the day before's last change, and the day's last into the next day's first.
    const { before: carried, after: next } = this._around();
    const lo = k > 0 ? list[k - 1].m + SNAP : Math.max(0, carried + SNAP);
    const hiAfter = (j) => (j < list.length ? list[j].m - SNAP : Math.min(DAY - SNAP, next - SNAP));
    if (drag === "edge") {
      return this._mover(list[k].m, lo, hiAfter(k + 1), (m) => {
        rows[row].time = hhmm(m);
      });
    }
    const [a, b] = [list[k], list[k + 1]];
    if (!b) return null;
    const length = b.m - a.m;
    return this._mover(a.m, lo, hiAfter(k + 2) - length, (m) => {
      rows[a.i].time = hhmm(m);
      rows[b.i].time = hhmm(m + length);
    });
  }

  _step(row, by, which = "temp") {
    const { min, max, step } = this._limits();
    const next = Math.round((row[which] + by * step) / step) * step;
    row[which] = Math.min(max, Math.max(min, Number(next.toFixed(1))));
  }

  _newRow(rows) {
    const sorted = this._sorted(rows);
    const last = sorted.at(-1);
    const after = last ? Math.min((toMinutes(last.time) ?? 0) + 60, DAY - 1) : 12 * 60;
    const { min, max } = this._limits();
    if (this._range) {
      return last ? { time: hhmm(after), temp: last.temp, high: last.high } : { time: hhmm(after), temp: 19, high: 24 };
    }
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
      if (rows.length > limit) add(name, t("too_many_changes", { max: limit }));
      const seen = new Map();
      rows.forEach((r, i) => {
        const m = toMinutes(r.time);
        if (m === null) {
          add(`${name}:${i}`, "time");
          add(name, t("change_needs_time"));
        } else if (seen.has(m)) {
          add(`${name}:${i}`, "duplicate");
          add(name, t("two_changes", { time: clock(m) }));
        } else seen.set(m, i);
        const values = this._range ? [r.temp, r.high] : [r.temp];
        if (!values.every((v) => v >= min && v <= max)) add(name, t("temperatures_between", { min: degrees(min), max: degrees(max) }));
        if (this._range && !(r.high - r.temp >= RANGE_GAP)) {
          add(`${name}:${i}`, "gap");
          add(name, t("range_gap", { gap: RANGE_GAP }));
        }
      });
    }
    return problems;
  }
}

class Unfr0zenHotWaterCard extends ScheduleCard {
  static getConfigForm() {
    return {
      computeLabel: (field) => t(`config_${field.name}`),
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
    return t("add_period");
  }

  get _water() {
    return true;
  }

  _title() {
    return this._config.name ?? t("hot_water");
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
        const title = labels ? ` title="${clock(b.start)}–${clock(b.end)} · ${duration(b.length)}"` : "";
        return `<span class="block${width < 9 ? " small" : ""}" style="left:${left}%;width:${width}%"${title}>${label}</span>`;
      })
      .join("");
  }

  _cardHtml(state) {
    const spans = periods(this._week(state));
    const now = this._now;
    const status = hotWaterNow(spans, now);
    const boost = this._boostState();
    let subtitle = t("no_periods");
    if (spans.length) {
      const next = status.next ? t("next_period", { when: when(now, status.next.day, status.next.on, status.next.ahead) }) : "";
      subtitle = [status.on ? t("on_until", { time: clock(status.until) }) : "", next].filter(Boolean).join(" · ");
    }
    if (boost.active) subtitle = boost.minutes ? t("boosting_for", { duration: duration(boost.minutes) }) : t("boosting");
    const away = unavailable(state);
    if (away) subtitle = t("unavailable");
    const active = this._related().active;
    const heating = active && this._hass.states[active]?.state === "on"
      ? `<span class="heating-now"><ha-icon icon="mdi:fire"></ha-icon>${t("heating_now")}</span>`
      : "";
    const rows = DAYS.map((_, day) => this._dayRow(state, day, now,
      away ? `<span class="empty">${t("unavailable")}</span>` : this._blocksHtml(spans, day, true))).join("");
    return `
      <div class="head">
        <div class="titles"><div class="title">${escapeHtml(this._title())}${heating}</div>
          <div class="subtitle" title="${escapeHtml(subtitle)}">${escapeHtml(subtitle)}</div></div>
        <div class="controls">${this._boostHtml(boost)}${this._pillHtml()}${this._editHtml(state)}</div>
      </div>
      <div class="week">${rows}</div>
      ${this._axis(true)}`;
  }

  _pillHtml() {
    const { switch: sw, waterHeater } = this._related();
    const schedule = this._hass.states[sw];
    const entity = schedule || this._hass.states[waterHeater];
    if (!entity) return "";
    const available = !["unknown", "unavailable"].includes(entity.state);
    const on = available && (schedule ? entity.state === "on" : entity.state !== "off");
    const label = t(schedule ? "schedule" : "hot_water");
    const status = t(!available ? "unavailable" : on ? "on" : "off");
    return `<button class="pill${on ? " on" : ""}" data-action="toggle" role="switch" aria-label="${label}" aria-checked="${on}"
      title="${available ? t(on ? "turn_off" : "turn_on") : status}" ${available ? "" : "disabled"}>
      <ha-icon icon="mdi:calendar-clock"></ha-icon><span class="text">${status}</span></button>`;
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

  /** The boost: through its select (minutes, "off" for none); over Matter, the water heater's boost of a length, its
   * switch ending it; through its switch (a fixed boost); else the water heater's high demand mode. */
  _boostState() {
    const { boost, waterHeater } = this._related();
    const select = boost && this._hass.states[boost];
    const heater = waterHeater && this._hass.states[waterHeater];
    if (heater && this._hass.entities?.[waterHeater]?.platform === "matter" && !boost?.startsWith("select.")) {
      // Home Assistant's Matter water heater is in high demand while a boost runs.
      return { via: "matter", active: heater.state === "high_demand", minutes: 0, options: BOOST_MINUTES };
    }
    if (select && boost.startsWith("switch.")) return { via: "switch", active: select.state === "on", minutes: 0, options: [] };
    if (select) {
      // Its options are lengths, "30" or Matter's "30 min", and one that ends a boost, "off" or Matter's "Off".
      const all = select.attributes.options ?? [];
      const length = (o) => Number.parseInt(o, 10) || 0;
      const minutes = length(select.state);
      const off = all.find((o) => !length(o)) ?? "off";
      return { via: "select", active: minutes > 0, minutes, options: all.filter(length), off };
    }
    if (heater) return { via: "water_heater", active: heater.state === "high_demand", minutes: 0, options: [] };
    return { via: null, active: false };
  }

  _boostHtml(boost) {
    if (!boost.via) return "";
    const label = boost.active && boost.minutes ? t("boost_for", { duration: duration(boost.minutes) }) : t("boost");
    const items =
      boost.options.length
        ? boost.options.map((o) => {
            const minutes = Number.parseInt(o, 10);
            return { option: o, text: minutes >= 60 ? t("boost_hours", { n: minutes / 60 }) : t("boost_minutes", { n: minutes }) };
          })
        : [{ option: "on", text: t("boost_now") }];
    // Ending a boost is offered only while one runs.
    const off = boost.off ?? "off";
    if (boost.active) items.push({ option: off, text: t("off") });
    const menu = this._menu
      ? `<div class="menu" role="menu">${items
          .map((i) => {
            const hint =
              i.option === off ? t("boost_end") : i.option === "on" ? t("boost_heat") : t("boost_heat_for", { duration: i.text });
            return `<button class="menu-item" data-action="boost" data-option="${escapeHtml(i.option)}" title="${escapeHtml(hint)}">${escapeHtml(i.text)}</button>`;
          })
          .join("")}</div>`
      : "";
    return `<span class="boost-wrap"><button class="pill boost${boost.active ? " active" : ""}" data-action="boost-menu"
      aria-haspopup="menu" aria-expanded="${Boolean(this._menu)}" aria-label="${label}"
      title="${t(boost.active ? "boost_running" : "boost_start")}"><ha-icon icon="mdi:rocket-launch-outline"></ha-icon><span class="text">${label}</span></button>${menu}</span>`;
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
    } else if (via === "matter" && option !== "off") {
      await this._hass.callService("matter", "water_heater_boost", { duration: Number(option) * 60 }, { entity_id: waterHeater });
    } else if (via === "matter" && boost && this._hass.states[boost]) {
      await this._hass.callService("switch", "turn_off", {}, { entity_id: boost });
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
    return spill ? t("on_from_midnight", { time: clock(spill.end) }) : "";
  }

  _rowHtml(row, i, problem) {
    return `<div class="row${problem ? " invalid" : ""}">
      ${this._timeHtml("on", row.on, i, "mdi:water-boiler", t("on"))}
      <span class="dash">–</span>
      ${this._timeHtml("off", row.off, i, "mdi:water-boiler-off", t("off"))}
      <span class="grow"></span>
      <button class="icon-button" data-action="remove" data-row="${i}" aria-label="${t("remove")}" title="${t("remove")}"><ha-icon icon="mdi:trash-can-outline"></ha-icon></button>
    </div>`;
  }

  /** The week's periods as saved, as spans of the week: {name, i, day, start, end}. */
  _spans() {
    const week = this._effective();
    const out = [];
    DAYS.forEach((name, day) =>
      week[name].forEach((r, i) => {
        const on = toMinutes(r.on);
        const off = toMinutes(r.off);
        const length = on === null || off === null ? 0 : (off - on + DAY) % DAY;
        if (length) out.push({ name, i, day, start: day * DAY + on, end: day * DAY + on + length });
      }),
    );
    return out;
  }

  /** Each of the shown day's periods: an edge at its on and off, and its middle; the off of one from an earlier day
   * that ends in this one. None while the week has a problem. */
  _handlesHtml(problems) {
    if (problems.size) return "";
    const e = this._editor;
    const shown = DAYS[e.day];
    const from = e.day * DAY;
    // Each block in the bar, [on, off) from the shown day's midnight, with whose it is.
    const blocks = this._spans().flatMap((s) =>
      [-1, 0]
        .map((k) => ({ s, on: s.start + k * WEEK - from, off: s.end + k * WEEK - from }))
        .filter((b) => b.off > 0 && b.on < DAY),
    );
    const own = blocks.filter((b) => b.s.name === shown);
    const length = (b) => Math.min(b.off, DAY) - Math.max(b.on, 0);
    // An edge another of the day's periods shares: as narrow as the shorter of the two.
    const room = (b, at) => Math.min(length(b), ...own.filter((o) => o !== b && (o.on === at || o.off === at)).map(length));
    const html = blocks.map(({ s, on, off }, n) => {
      const b = blocks[n];
      if (s.name === shown) {
        return (
          this._grab(s.i, on, length(b)) +
          this._edge("on", s.i, on, room(b, on)) +
          (off <= DAY ? this._edge("off", s.i, off, room(b, off)) : "")
        );
      }
      // From an earlier day, unless that day is the shown one's copy.
      // Sized against the day's period starting where it ends, which has a middle to grab.
      const next = own.filter((o) => o.on === off).map(length);
      return on < 0 && !e.copy.has(s.name) ? this._edge("off", s.i, off, next.length ? Math.min(...next) : 0, s.name) : "";
    });
    return `<div class="handles">${html.join("")}</div>`;
  }

  /** An on or off time up to the neighbouring period, or with it when they touch; a period moved whole within the gap
   * around it. Each day the shown one is copied to has the same room. A period stays under a day, its on in its day. */
  _dragger(drag, row, owner) {
    const e = this._editor;
    const shown = DAYS[e.day];
    owner = owner ?? shown;
    if (owner !== shown && e.copy.has(owner)) return null;
    const spans = this._spans();
    const span = spans.find((s) => s.name === owner && s.i === row);
    if (!span) return null;
    // A row's periods: on its own day and, for the shown day's, each day it is copied to.
    const copies = (s) => spans.filter((o) => o.i === s.i && (o.name === s.name || (s.name === shown && e.copy.has(o.name))));
    // The nearest periods that stay, before and after, a week either side too.
    const nearest = (target, moving) => {
      const still = spans
        .filter((o) => !moving.includes(o))
        .flatMap((o) => [-1, 0, 1].map((k) => ({ ...o, k, os: o.start + k * WEEK, oe: o.end + k * WEEK })));
      return {
        prev: still.filter((o) => o.oe <= target.start).sort((a, b) => b.oe - a.oe)[0],
        next: still.filter((o) => o.os >= target.end).sort((a, b) => a.os - b.os)[0],
      };
    };
    const mine = copies(span);
    const { prev, next } = nearest(span, mine);
    // How far back and ahead every copy can go.
    const room = () => {
      let back = -Infinity;
      let ahead = Infinity;
      for (const c of mine) {
        const n = nearest(c, mine);
        back = Math.max(back, (n.prev?.oe ?? -Infinity) - c.start);
        ahead = Math.min(ahead, (n.next?.os ?? Infinity) - c.end);
      }
      return { back, ahead };
    };
    // A touching period follows the edge: one on the row's day, its copies beside the row's; or one elsewhere when
    // neither has copies.
    const follows = (o) =>
      o && (o.name === shown || !e.copy.has(o.name)) && (o.name === owner || (mine.length === 1 && copies(o).length === 1));
    const firstOn = (s, k = 0) => s.day * DAY + k * WEEK;
    const write = (s, start, end, k = 0) => {
      const r = e.draft[s.name][s.i];
      r.on = hhmm(start - firstOn(s, k));
      r.off = hhmm(((end % DAY) + DAY) % DAY);
    };
    const { start, end } = span;
    const day = firstOn(span);
    const longest = DAY - SNAP;
    if (drag === "on") {
      const shared = prev?.oe === start && follows(prev);
      const lo = Math.max(day, end - longest, shared ? prev.os + SNAP : start + room().back);
      const hi = Math.min(day + DAY - SNAP, end - SNAP, shared ? prev.os + longest : Infinity);
      return this._mover(start, lo, hi, (m) => {
        write(span, m, end);
        if (shared) write(prev, prev.os, m, prev.k);
      });
    }
    if (drag === "off") {
      const shared = next?.os === end && follows(next);
      const nextDay = shared ? firstOn(next, next.k) : 0;
      const lo = Math.max(start + SNAP, shared ? Math.max(next.oe - longest, nextDay) : -Infinity);
      const hi = Math.min(start + longest, shared ? Math.min(next.oe - SNAP, nextDay + DAY - SNAP) : end + room().ahead);
      return this._mover(end, lo, hi, (m) => {
        write(span, start, m);
        if (shared) write(next, m, next.oe, next.k);
      });
    }
    const { back, ahead } = room();
    return this._mover(start, Math.max(day, start + back), Math.min(day + DAY - SNAP, start + ahead), (m) =>
      write(span, m, m + end - start),
    );
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
      if (rows.length > limit) add(name, t("too_many_periods", { max: limit }));
      rows.forEach((r, i) => {
        const on = toMinutes(r.on);
        const off = toMinutes(r.off);
        if (on === null || off === null) {
          add(name, t("period_needs_times"));
          add(`${name}:${i}`, "time");
          return;
        }
        if (on === off) {
          add(name, t("period_to_itself", { time: clock(on) }));
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
        add(s.name, t("periods_overlap"));
        if (next.name !== s.name) add(next.name, t("periods_overlap"));
      }
    });
    return problems;
  }
}

customElements.get("unfr0zen-heating-card") || customElements.define("unfr0zen-heating-card", Unfr0zenHeatingCard);
customElements.get("unfr0zen-hot-water-card") || customElements.define("unfr0zen-hot-water-card", Unfr0zenHotWaterCard);

pickerEntries();
