// Past dispatcher decisions for the incident on screen, and the save of this run's decisions at the
// end of the morning. History is a reference: nothing here changes the probability or the ranking.
// Past cases are synthetic and saved ones simulated. Server text is always set with textContent.

import { appliedWeb, withWeb } from "./triage.js";

const money = (value) => `${value < 0 ? "−" : ""}$${Math.abs(Math.round(value)).toLocaleString("en-US")}`;
const pct = (value) => `${Math.round(value * 100)}%`;
const whole = (value) => Math.round(value).toLocaleString("en-US");
const tenth = (value) => value.toFixed(1);
const amount = (value) => (Number.isInteger(value) ? String(value) : value.toFixed(1));
const times = (n) => (n === 1 ? "once" : n === 2 ? "twice" : `${n} times`);
const sentence = (text) => (/[.?!]$/.test(text) ? text : `${text}.`);

// Figures recorded per decision: estimated at the time and what then happened.
const metrics = [
  { key: "lateness_min", label: "Delay", short: "Delay", unit: "min", format: whole },
  { key: "cost_usd", label: "Cost", short: "Cost", unit: "", format: money },
  { key: "co2_kg", label: "CO₂", short: "CO₂", unit: "kg", format: tenth },
  { key: "added_miles", label: "Added miles", short: "Miles", unit: "mi", format: tenth },
  { key: "missed_appointments", label: "Missed appointments", short: "Missed", unit: "", format: amount },
];
const webWords = { corroborates: "feeds supported it", contradicts: "feeds contradicted it", nothing_found: "nothing found" };

let token = 0;
let summary = null; // Summary of the history on screen, for the lines under the response cards.
let opened = false; // Whether the dispatcher opened a list in the card for this report.
const openLists = new Set(); // Lists kept open when the card reloads after a web finding.

const newRunId = () => crypto.randomUUID?.() ?? `run-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
let runId = newRunId();
let decisions = new Map(); // Report index -> { body, meta, request }, the confirmed response for each report.

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const words = (check) => ({ label: "Drone check", ...check, bad: "blocked", good: "open" });
const roadWord = (state, w) => (state === "blocked" ? w.bad : w.good);
const observationText = (observation, w) => ({ blocked: `road ${w.bad}`, clear: `road ${w.good}`, inconclusive: "inconclusive" })[observation] ?? observation;
const baseRate = (s, w) => `Road ${w.bad} in ${s.bad_count} of ${s.count} similar reports`;

export function clearHistory(container) {
  token += 1;
  summary = null;
  opened = false;
  openLists.clear();
  container.hidden = true;
  container.replaceChildren();
  annotateRankings();
}

function hide(container) {
  summary = null;
  container.hidden = true;
  container.replaceChildren();
  annotateRankings();
}

// Loads the history for an incident and renders the card. Hidden when the server has none.
// `onOpen(text)` is called the first time the dispatcher opens one of its lists for this report.
export async function renderHistory(container, scenarioId, { onOpen } = {}) {
  const mine = ++token;
  let data = null;
  try {
    const response = await fetch(`/api/history?${withWeb(new URLSearchParams({ scenario: scenarioId }))}`);
    if (response.ok) data = await response.json();
  } catch {
    data = null;
  }
  if (mine !== token) return;
  if (!data?.available || !data.summary?.count) {
    hide(container);
    return;
  }
  try {
    const parts = card(data, onOpen);
    summary = data.summary;
    container.replaceChildren(...parts);
    container.hidden = false;
    annotateRankings();
  } catch (error) {
    console.error(error);
    hide(container);
  }
}

function card(data, onOpen) {
  const s = data.summary;
  const w = words(data.check);
  const head = el("div", "web-head");
  const title = el("h2", null, "Past decisions");
  title.append(el("span", "count", String(s.count)));
  head.append(title);

  const rate = el("p", "history-rate");
  rate.append(el("b", null, baseRate(s, w)));
  if (s.current_estimate != null) rate.append(` · now ${pct(s.current_estimate)}`);

  const parts = [head, rate];
  if (s.by_action?.length) parts.push(actionTable(s, w));
  const i = s.inspection;
  if (i) {
    parts.push(el("p", "note", i.flown
      ? `${w.label} flown in ${i.flown} of ${s.count}; ${i.changed_response ? `it changed the response ${times(i.changed_response)}` : "it never changed the response"}.`
      : `No ${w.label.toLowerCase()} flown.`));
  }
  const reached = () => {
    if (opened) return;
    opened = true;
    onOpen?.(`${baseRate(s, w)}.`);
  };
  if (data.cases?.length) parts.push(caseList(data, w, reached));
  const fine = el("p", "fine-print", "History does not affect the risk estimate.");
  if (data.note) fine.title = data.note;
  parts.push(fine);
  return parts;
}

// Average result per response, with the estimate at the time beneath it when the two differ.
function actionTable(s, w) {
  const wrap = el("div", "table-wrap");
  const table = el("table", "data history-table");
  table.append(el("caption", null, "Average results; estimates in small type."));
  const head = el("tr");
  head.append(el("th", null, "Response"), el("th", "num", "Chosen"));
  metrics.forEach((m) => {
    const th = el("th", "num", m.short);
    const unit = m.key === "missed_appointments" ? "total" : m.unit;
    if (unit) th.append(el("span", "detail", unit));
    head.append(th);
  });
  const thead = el("thead");
  thead.append(head);
  const tbody = el("tbody");
  s.by_action.forEach((a) => {
    const row = el("tr", a.recommended ? "pick" : "");
    const name = el("td", null, a.label);
    if (a.recommended) name.append(el("span", "detail", "Recommended now"));
    const chosen = el("td", "num", String(a.count));
    if (a.count && a.bad_count) chosen.append(el("span", "detail", `${a.bad_count} ${w.bad}`));
    row.append(name, chosen);
    metrics.forEach((m) => {
      if (m.key === "missed_appointments") {
        row.append(el("td", "num", a.count ? amount(a.missed_appointments ?? 0) : "—"));
        return;
      }
      const actual = a.actual?.[m.key];
      const estimate = a.estimate?.[m.key];
      const td = el("td", "num", actual == null ? "—" : m.format(actual));
      if (estimate != null && (actual == null || m.format(estimate) !== m.format(actual))) td.append(el("span", "detail", m.format(estimate)));
      row.append(td);
    });
    tbody.append(row);
  });
  table.append(thead, tbody);
  wrap.append(table);
  return wrap;
}

function list(title, key, reached) {
  const box = el("details", "more history-list");
  box.append(el("summary", null, title));
  box.open = openLists.has(key);
  box.addEventListener("toggle", () => {
    if (!box.open) {
      openLists.delete(key);
      return;
    }
    openLists.add(key);
    reached();
  });
  return box;
}

function caseList(data, w, reached) {
  const labels = Object.fromEntries((data.summary.by_action ?? []).map((a) => [a.action, a.label]));
  const box = list(`Cases (${data.cases.length})`, "cases", reached);
  box.append(el("p", "note", "Plan → result."));
  const items = el("ol", "history-cases");
  data.cases.forEach((c) => items.append(caseItem(c, w, labels)));
  box.append(items);
  return box;
}

function caseItem(c, w, labels) {
  const item = el("li", c.source === "saved" ? "saved" : "");
  const meta = el("p", "history-meta", [c.date, c.reported_at, c.place].filter(Boolean).join(" · "));
  if (c.source === "saved") meta.prepend(el("span", "basis", "Your earlier run"), " ");
  item.append(meta);
  if (c.title) item.append(el("p", "history-title", c.title));

  const before = [];
  if (c.reports) before.push(sentence(c.reports));
  if (c.estimated_bad_probability != null) before.push(`${pct(c.estimated_bad_probability)} chance the road is ${w.bad}.`);
  if (c.web_finding) before.push(`Web check: ${webWords[c.web_finding] ?? c.web_finding}.`);
  if (c.inspection?.flown) {
    const wait = c.inspection.wait_min != null ? `, ${whole(c.inspection.wait_min)} min` : "";
    before.push(`${w.label}: ${observationText(c.inspection.observation, w)}${wait}.`);
  } else if (c.inspection) {
    before.push(`No ${w.label.toLowerCase()}.`);
  }
  if (before.length) item.append(el("p", null, before.join(" ")));

  let choice = `Chose ${c.action_label ?? labels[c.action] ?? c.action}`;
  if (c.recommended_action && c.recommended_action !== c.action) choice += ` over ${labels[c.recommended_action] ?? c.recommended_action}`;
  choice += ".";
  if (c.override_reason) choice += ` Reason: ${sentence(c.override_reason)}`;
  if (c.actual_road_state) choice += ` Road was ${roadWord(c.actual_road_state, w)}.`;
  item.append(el("p", null, choice));

  const figures = metrics.filter((m) => m.key !== "added_miles").map((m) => {
    const estimate = c.estimate?.[m.key];
    const actual = c.actual?.[m.key];
    if (estimate == null || actual == null) return "";
    if (m.key === "missed_appointments" && !estimate && !actual) return "";
    return `${m.label} ${m.format(estimate)} → ${m.format(actual)}${m.unit ? ` ${m.unit}` : ""}`;
  }).filter(Boolean);
  if (figures.length) item.append(el("p", "history-figures", figures.join(" · ")));
  if (c.actual_note) item.append(el("p", "detail", c.actual_note));
  if (c.lesson) item.append(el("p", "history-lesson", c.lesson));
  return item;
}

function rankingLine(a) {
  const late = a.actual.lateness_min;
  const delay = late == null ? "" : late >= 0.5 ? `, ${whole(late)} min late` : ", on time";
  const co2 = a.actual.co2_kg == null ? "" : `, ${tenth(a.actual.co2_kg)} kg CO₂`;
  return `History: chosen ${times(a.count)}${delay}${co2} on average.`;
}

// Adds one history line under each response card, or removes them without history. app.js calls this
// after it renders the cards; the card calls it again when the history arrives.
export function annotateRankings(cards = document.querySelector("#rankings")) {
  if (!cards) return;
  cards.querySelectorAll(".option-history").forEach((node) => node.remove());
  if (!summary?.by_action) return;
  cards.querySelectorAll('input[name="response"]').forEach((input) => {
    const a = summary.by_action.find((row) => row.action === input.value);
    if (!a?.count || !a.actual) return;
    const label = cards.querySelector(`label[for="${CSS.escape(input.id)}"]`);
    if (!label) return;
    const line = el("span", "option-history", rankingLine(a));
    const after = label.querySelector(".option-summary");
    if (after) after.after(line);
    else label.append(line);
  });
}

// ---------------------------------------------------------------------------
// Saving. Each morning has a run id. The confirmed response for each report is remembered (a later
// confirmation for the same report replaces it) and posted once when the shift report is shown.
// ---------------------------------------------------------------------------
export function newHistoryRun() {
  runId = newRunId();
  decisions = new Map();
}

// `decision` is the POST body without run_id; `meta` ({ label, check }) is for display only.
export function rememberDecision(key, decision, meta) {
  if (decisions.get(key)?.request) return;
  decisions.set(key, { body: { run_id: runId, ...decision }, meta });
}

// The dispatcher changed the simulated road state after confirming.
export function setRememberedRoad(key, road) {
  const entry = decisions.get(key);
  if (entry && !entry.request) entry.body.road = road;
}

async function save(entry) {
  try {
    const response = await fetch("/api/history", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(entry.body),
    });
    if (!response.ok) return null;
    return (await response.json()).record ?? null;
  } catch {
    return null;
  }
}

// Saves this run's decisions and fills `section` (its `.history-results-body`) with estimate against
// result per incident. The section stays hidden when no response was confirmed.
export async function renderHistoryResults(section) {
  const body = section.querySelector(".history-results-body");
  const run = runId;
  const entries = [...decisions.entries()].sort(([a], [b]) => a - b).map(([, entry]) => entry);
  section.hidden = !entries.length;
  if (!entries.length) return;
  body.replaceChildren(el("p", "note", "Saving to decision history…"));
  entries.forEach((entry) => { entry.request ??= save(entry); });
  const records = await Promise.all(entries.map((entry) => entry.request));
  if (run !== runId) return;
  const parts = [];
  records.forEach((record, i) => {
    if (!record) return;
    try {
      parts.push(...resultTable(record, entries[i].meta));
    } catch (error) {
      console.error(error);
    }
  });
  parts.push(el("p", "fine-print", records.every(Boolean) ? "Saved to decision history." : "Could not save to history."));
  body.replaceChildren(...parts);
}

function resultTable(record, meta) {
  const w = words(meta?.check);
  const wrap = el("div", "table-wrap");
  const table = el("table", "data history-result");
  const road = record.actual_road_state ? ` · road ${roadWord(record.actual_road_state, w)}` : "";
  table.append(el("caption", null, `${meta?.label ?? record.title} · ${record.action_label ?? record.action}${road}`));
  const head = el("tr");
  const happened = el("th", "num", "Simulated result");
  head.append(el("th", null, "Indicator"), el("th", "num", "Estimate"), happened);
  const thead = el("thead");
  thead.append(head);
  const tbody = el("tbody");
  const show = (value, m) => (value == null ? "—" : `${m.format(value)}${m.unit ? ` ${m.unit}` : ""}`);
  metrics.forEach((m) => {
    const row = el("tr");
    row.append(el("td", null, m.label), el("td", "num", show(record.estimate?.[m.key], m)), el("td", "num", show(record.actual?.[m.key], m)));
    tbody.append(row);
  });
  table.append(thead, tbody);
  wrap.append(table);
  const parts = [wrap];
  if (record.actual_note) parts.push(el("p", "note", record.actual_note));
  if (record.lesson) parts.push(el("p", "history-lesson", record.lesson));
  return parts;
}
