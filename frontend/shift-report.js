// Shift report: a full-window dialog opened at the end of the morning. It reads the records app.js
// keeps per report (how the estimate moved, the response chosen and its simulated result), the
// decision log and the saved decision-history records, and lays them out for reading and printing.
// Everything in it is simulation output: fictional loads, assumed rates, estimated results.

const $ = (selector, root = document) => root.querySelector(selector);
const esc = (value) => String(value).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const pct = (value) => `${Math.round(value * 100)}%`;
const money = (value) => `${value < 0 ? "−" : ""}$${Math.abs(Math.round(value)).toLocaleString("en-US")}`;
const whole = (value) => Math.round(value).toLocaleString("en-US");
const tenth = (value) => value.toFixed(1);
const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

// Simulated trip totals per report (app.js totalsAt), lower is better for all of them.
const results = [
  { id: "late", label: "Minutes late", unit: "min", format: whole },
  { id: "missed", label: "Missed appointments", unit: "", format: whole },
  { id: "co2", label: "CO₂ emitted", unit: "kg", format: tenth },
  { id: "miles", label: "Miles driven", unit: "mi", format: tenth },
  { id: "cost", label: "Total cost", unit: "", format: money },
];

const webWords = {
  corroborates: "Feeds supported the report",
  contradicts: "Feeds contradicted the report",
  nothing_found: "No public reports of it",
};

let data = null;
let logFilter = "all";

function delta(value, planned, metric) {
  const diff = value - planned;
  if (Math.abs(diff) < 0.05) return '<span class="sr-delta">same as plan</span>';
  const tone = diff > 0 ? "worse" : "better";
  return `<span class="sr-delta ${tone}">${diff > 0 ? "+" : "−"}${metric.format(Math.abs(diff))}${metric.unit ? ` ${metric.unit}` : ""} vs plan</span>`;
}

function sumResults(incidents, key) {
  const out = Object.fromEntries(results.map((m) => [m.id, 0]));
  incidents.forEach((r) => results.forEach((m) => { out[m.id] += r[key]?.[m.id] ?? 0; }));
  return out;
}

function summary() {
  const incidents = data.incidents;
  const n = incidents.length;
  const followed = incidents.filter((r) => r.action === r.recommended).length;
  const flown = incidents.filter((r) => r.drone).length;
  const web = incidents.filter((r) => r.web).length;
  const played = incidents.filter((r) => r.result);
  const total = sumResults(played, "result");
  const planned = sumResults(played, "planned");
  const decisions = [
    ["Reports handled", n, ""],
    ["Recommendation followed", `${followed}<small> of ${n}</small>`, followed === n ? "" : `${plural(n - followed, "override")}`],
    ["Drone checks flown", flown, `${incidents.filter((r) => r.droneAdvice).length} advised`],
    ["Public reports used", web, "as evidence"],
  ];
  const tiles = results.filter((m) => m.id !== "miles").map((m) => `<li class="sr-tile${total[m.id] > planned[m.id] + 0.05 && (m.id === "late" || m.id === "missed") ? " alert" : ""}">
      <span class="sr-tile-label">${m.label}</span>
      <b>${m.format(total[m.id])}${m.unit ? `<small> ${m.unit}</small>` : ""}</b>
      ${delta(total[m.id], planned[m.id], m)}
    </li>`).join("");
  return `<section id="sr-summary" class="sr-section" aria-labelledby="sr-summary-title">
    <h3 id="sr-summary-title" class="sr-heading">At a glance</h3>
    <ul class="sr-decisions">${decisions.map(([label, value, note]) => `<li><b>${value}</b><span>${label}</span>${note ? `<small>${esc(note)}</small>` : ""}</li>`).join("")}</ul>
    ${played.length ? `<h4 class="sr-subheading">Simulated result for the loads each report affected</h4>
    <ul class="sr-tiles">${tiles}</ul>
    <p class="sr-note">Totals over ${plural(played.length, "report")}, from the start of the shift to delivery. "Plan" is the same loads on their planned routes with the road open.</p>` : ""}
  </section>`;
}

// One row per piece of evidence, each with the chance of the bad road state after it.
function ladder(r) {
  const bad = r.check.bad;
  const rows = [
    { step: "Report came in", detail: "Starting estimate", value: r.start, state: "done" },
    r.web
      ? { step: "Public reports", detail: `${webWords[r.web.finding] ?? r.web.finding} · used as evidence`, value: r.web.after, state: "done" }
      : { step: "Public reports", detail: "Not used as evidence", value: null, state: "skipped" },
    r.drone
      ? { step: r.check.label ?? "Drone check", detail: `Observed: ${r.drone.finding}`, value: r.drone.after, state: "done" }
      : { step: r.check.label ?? "Drone check", detail: r.droneAdvice ? "Skipped (the model advised flying)" : "Skipped, as advised", value: null, state: "skipped" },
  ];
  const final = [...rows].reverse().find((row) => row.value != null)?.value ?? r.start;
  const truth = r.road === "blocked";
  const called = final >= 0.5 === truth;
  return `<div class="sr-ladder">
    <h4 class="sr-subheading">How the chance the road was ${esc(bad)} moved</h4>
    <ol>${rows.map((row) => `<li class="${row.state}">
        <span class="sr-step"><b>${esc(row.step)}</b><small>${esc(row.detail)}</small></span>
        <span class="sr-meter" aria-hidden="true">${row.value == null ? "" : `<i style="width:${pct(row.value)}"></i>`}</span>
        <span class="sr-value">${row.value == null ? "—" : pct(row.value)}</span>
      </li>`).join("")}
      <li class="truth ${called ? "called" : "missed"}">
        <span class="sr-step"><b>Road turned out</b><small>${called ? "In line with the final estimate" : "Against the final estimate"}</small></span>
        <span class="sr-truth">${esc(r.roadWord)}</span>
      </li>
    </ol>
  </div>`;
}

function decision(r) {
  const override = r.action !== r.recommended;
  return `<div class="sr-decision${override ? " override" : ""}">
    <h4 class="sr-subheading">Response</h4>
    <p class="sr-choice"><b>${esc(r.actionLabel)}</b> <span class="tag ${override ? "" : "pick"}">${override ? "Override" : "Recommended"}</span></p>
    ${override ? `<p class="sr-note">Model recommended ${esc(r.recommendedLabel)}.${r.reason ? ` Your reason: “${esc(r.reason)}”` : " No reason given."}</p>` : ""}
    <p class="sr-note">Confirmed at ${data.clock(r.decidedAt)}, ${Math.max(0, Math.round(r.decidedAt - r.at))} min after the report.</p>
  </div>`;
}

function costs(r) {
  if (!r.costs?.length) return "";
  const top = Math.max(...r.costs.map((c) => c.cost), 1);
  const low = Math.min(...r.costs.map((c) => c.cost));
  return `<div class="sr-costs">
    <h4 class="sr-subheading">Trip cost of each response, road ${esc(r.roadWord)}</h4>
    <ol>${r.costs.map((c) => `<li class="${c.id === r.action ? "chosen" : ""}">
        <span class="sr-cost-name">${esc(c.label)}${c.id === r.action ? " <small>your choice</small>" : ""}${c.id === r.recommended && c.id !== r.action ? " <small>recommended</small>" : ""}</span>
        <span class="sr-meter" aria-hidden="true"><i style="width:${(c.cost / top * 100).toFixed(1)}%"></i></span>
        <span class="sr-value">${money(c.cost)}${Math.abs(c.cost - low) < 0.5 ? " <small>lowest</small>" : ""}</span>
      </li>`).join("")}</ol>
    <p class="sr-note">Simulated with what the road turned out to be, which nobody knew when deciding.</p>
  </div>`;
}

// The played-back trip totals for the loads the report affected, against the same loads on plan.
function resultTable(r) {
  if (!r.result) return "";
  const show = (value, m) => `${m.format(value)}${m.unit ? ` ${m.unit}` : ""}`;
  return `<div class="sr-results" data-report="${r.index}">
    <h4 class="sr-subheading">Trip totals, as played back</h4>
    <div class="table-wrap"><table class="data">
      <thead><tr><th>Indicator</th><th class="num">${esc(r.actionLabel)}</th><th class="num">Plan</th></tr></thead>
      <tbody>${results.map((m) => `<tr>
          <td>${m.label}</td>
          <td class="num"><b>${show(r.result[m.id], m)}</b>${r.planned ? delta(r.result[m.id], r.planned[m.id], m) : ""}</td>
          <td class="num">${r.planned ? show(r.planned[m.id], m) : "—"}</td>
        </tr>`).join("")}</tbody>
    </table></div>
    <p class="sr-note">${esc(r.shipments ?? "The affected loads")}, from the start of the shift to delivery, with the road ${esc(r.roadWord)}. Plan: the same loads on their planned routes with the road open. Total cost counts driving, driver time, delay, CO₂ and missed appointments.</p>
    <p class="sr-note sr-saved">Saving to decision history…</p>
  </div>`;
}

function incident(r, i) {
  const entries = data.log.filter((entry) => entry.report === r.index);
  return `<article id="sr-incident-${i}" class="sr-incident" aria-labelledby="sr-incident-${i}-title">
    <header class="sr-incident-head">
      <time>${data.clock(r.at)}</time>
      <div>
        <p class="sr-incident-label">${esc(r.label)} · ${esc(r.place)}</p>
        <h3 id="sr-incident-${i}-title">${esc(r.title)}</h3>
      </div>
      <span class="sr-road ${r.road}">Road ${esc(r.roadWord)}</span>
    </header>
    <div class="sr-incident-grid">
      ${ladder(r)}
      ${decision(r)}
    </div>
    ${costs(r)}
    ${resultTable(r)}
    <details class="more sr-incident-log"><summary>Log for this report (${entries.length})</summary>
      <ol class="sr-log">${entries.map(logRow).join("")}</ol>
    </details>
  </article>`;
}

function logRow(entry) {
  const label = entry.report >= 0 ? data.reports[entry.report] : "Shift";
  return `<li data-report="${entry.report}"><time>${data.clock(entry.t)}</time><span class="sr-log-tag">${esc(label)}</span><span>${esc(entry.text)}</span></li>`;
}

function logSection() {
  const groups = [["all", "All"], ["-1", "Shift"], ...data.incidents.map((r) => [String(r.index), r.label])];
  return `<section id="sr-log" class="sr-section" aria-labelledby="sr-log-title">
    <h3 id="sr-log-title" class="sr-heading">Decision log</h3>
    <div class="sr-chips" role="group" aria-label="Filter the log">${groups.map(([id, label]) => `<button type="button" class="sr-chip" data-filter="${id}" aria-pressed="${id === logFilter}">${esc(label)}</button>`).join("")}</div>
    <ol class="sr-log sr-log-full">${data.log.map(logRow).join("")}</ol>
  </section>`;
}

function planSection() {
  if (!data.indicators.rows) return "";
  return `<section id="sr-plan" class="sr-section" aria-labelledby="sr-plan-title">
    <h3 id="sr-plan-title" class="sr-heading">Whole plan at each report</h3>
    <div class="table-wrap"><table class="data sr-plan-table">
      <thead><tr>${data.indicators.head}</tr></thead>
      <tbody>${data.indicators.rows}</tbody>
    </table></div>
    <p class="sr-note">Whole-plan estimates before inspection, calculated separately for each report, so columns do not add up. Small figures show the change from a shift without incidents.</p>
  </section>`;
}

function applyFilter(dialog) {
  dialog.querySelectorAll(".sr-chip").forEach((chip) => chip.setAttribute("aria-pressed", String(chip.dataset.filter === logFilter)));
  dialog.querySelectorAll(".sr-log-full li").forEach((row) => {
    row.hidden = logFilter !== "all" && row.dataset.report !== logFilter;
  });
}

// Says, per report, whether its decision reached the decision history.
async function fillRecords(dialog, saved) {
  let records;
  try {
    records = await saved;
  } catch {
    records = new Map();
  }
  if (data?.saved !== saved) return;
  data.incidents.forEach((r) => {
    const box = dialog.querySelector(`.sr-results[data-report="${r.index}"]`);
    if (!box) return;
    const record = records.get(r.index);
    box.querySelector(".sr-saved").textContent = record
      ? `Saved to the decision history as ${record.id}.`
      : "Could not save to the decision history.";
  });
}

export function openShiftReport(input) {
  data = input;
  logFilter = "all";
  const dialog = $("#shift-report");
  const n = data.incidents.length;
  $("#shift-report-title").textContent = `Morning shift, ${data.day}`;
  $("#sr-sub").textContent = `${data.clock(data.start)} to ${data.clock(data.end)} · ${plural(n, "incident report")} · fictional loads, assumed rates, simulated results`;
  const nav = [
    ["sr-summary", "At a glance"],
    ...data.incidents.map((r, i) => [`sr-incident-${i}`, `${data.clock(r.at)} ${r.label}`]),
    data.indicators.rows ? ["sr-plan", "Whole plan"] : null,
    ["sr-log", "Decision log"],
  ].filter(Boolean);
  $("#sr-nav").innerHTML = `<ol>${nav.map(([id, label]) => `<li><button type="button" data-jump="${id}">${esc(label)}</button></li>`).join("")}</ol>`;
  $("#sr-body").innerHTML = [
    summary(),
    n ? `<section class="sr-section" aria-labelledby="sr-incidents-title"><h3 id="sr-incidents-title" class="sr-heading">Reports</h3>${data.incidents.map(incident).join("")}</section>` : "",
    planSection(),
    logSection(),
  ].join("");
  applyFilter(dialog);
  if (!dialog.open) dialog.showModal();
  $("#sr-body").scrollTop = 0;
  fillRecords(dialog, data.saved);
}

function bind() {
  const dialog = $("#shift-report");
  if (!dialog) return;
  $("#sr-close").addEventListener("click", () => dialog.close());
  $("#sr-print").addEventListener("click", () => {
    dialog.querySelectorAll(".sr-incident-log").forEach((details) => { details.open = true; });
    window.print();
  });
  $("#sr-nav").addEventListener("click", (event) => {
    const button = event.target.closest("[data-jump]");
    if (!button) return;
    document.getElementById(button.dataset.jump)?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  $("#sr-body").addEventListener("click", (event) => {
    const chip = event.target.closest("[data-filter]");
    if (!chip) return;
    logFilter = chip.dataset.filter;
    applyFilter(dialog);
  });
}

bind();
