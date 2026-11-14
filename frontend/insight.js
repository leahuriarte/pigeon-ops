// Plain-language explanations built from analysis fields the dashboard computed but did not show:
// analysis.sensitivity (when an inspection pays off) and each option's open and blocked outcome rows.
// Every function is pure except renderSensitivity, which draws into the container it is given.

const money = (value) => `${value < 0 ? "−" : ""}$${Math.abs(Math.round(value)).toLocaleString("en-US")}`;
const signed = (value) => `${value < 0 ? "−" : "+"}$${Math.abs(Math.round(value)).toLocaleString("en-US")}`;
const pct = (value) => `${Math.round(value * 100)}%`;
const esc = (value) => String(value).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// Why the page recommends inspecting or not, in one or two sentences.
export function inspectionSentence(evoi) {
  const wait = evoi.assumptions.launch_and_review_minutes;
  const gross = evoi.gross_value_of_information;
  const cost = evoi.inspection_cost;
  if (evoi.recommend_inspection) {
    return `Estimated net saving: ${money(evoi.net_value_of_information)}, including the ${money(cost)} check and ${wait}-minute wait.`;
  }
  if (gross > 0) return `Estimated saving: ${money(gross)}. Inspection cost: ${money(cost)}.`;
  return `Checking first adds an estimated ${money(-evoi.net_value_of_information)} in cost and delay.`;
}

// How accurate the inspection has to be, what the page assumes, and how automated CV alone compares.
export function accuracyNote(evoi) {
  const a = evoi.assumptions;
  const breakEven = evoi.break_even_accuracy;
  const need = breakEven === null || breakEven === undefined
    ? "Even 100% accuracy would not cover the inspection cost."
    : breakEven >= 0.995
      ? "Break-even accuracy: over 99%."
      : `Break-even accuracy: ${pct(breakEven)}.`;
  const assumed = `Assumed accuracy after human review: ${pct(a.inspection_accuracy)}. Unmeasured.`;
  const cv = evoi.cv_only;
  if (!cv) return `${need} ${assumed}`;
  const alone = cv.recommend_inspection
    ? `${money(cv.net_value_of_information)} estimated saving`
    : `${money(-cv.net_value_of_information)} estimated added loss`;
  return `${need} ${assumed} Automated image analysis alone: ${alone}, using accuracy measured on aerial stills.`;
}

// Ranges of blockage chance where the net value of inspecting is above zero, as [low, high] pairs.
export function payoffWindows(points) {
  const windows = [];
  let start = null;
  const crossing = (a, b) => a.prior + ((0 - a.net_value) / (b.net_value - a.net_value)) * (b.prior - a.prior);
  points.forEach((point, i) => {
    const previous = points[i - 1];
    if (point.net_value > 0 && start === null) start = previous ? crossing(previous, point) : point.prior;
    if (point.net_value <= 0 && start !== null) {
      windows.push([start, crossing(previous, point)]);
      start = null;
    }
  });
  if (start !== null) windows.push([start, points.at(-1).prior]);
  return windows;
}

function valueAt(points, prior) {
  const i = Math.min(points.length - 2, Math.max(0, points.findIndex((p) => p.prior >= prior) - 1));
  const [a, b] = [points[i], points[i + 1]];
  const f = Math.min(1, Math.max(0, (prior - a.prior) / (b.prior - a.prior)));
  return a.net_value + (b.net_value - a.net_value) * f;
}

// Line chart of analysis.sensitivity: net value of inspecting against the chance the road is blocked.
// The shaded range is where inspecting pays. Markers show the chance now and, after an inspection,
// the chance before it.
export function renderSensitivity(container, analysis, words = { bad: "blocked" }) {
  const points = analysis.sensitivity;
  if (!points?.length) {
    container.hidden = true;
    return;
  }
  const before = analysis.prior_blocked_probability;
  const after = analysis.posterior_blocked_probability;
  const inspected = after !== null && after !== undefined && Boolean(analysis.observation);
  const now = inspected ? after : before;
  const windows = payoffWindows(points);

  const [W, H, left, right, top, bottom] = [340, 168, 10, 10, 24, 30];
  const values = points.map((p) => p.net_value);
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  const pad = (high - low || 1) * 0.08;
  const x = (prior) => left + prior * (W - left - right);
  const y = (value) => top + ((high + pad - value) / (high - low + pad * 2)) * (H - top - bottom);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(p.prior).toFixed(1)} ${y(p.net_value).toFixed(1)}`).join(" ");
  const bands = windows.map(([a, b]) => {
    const width = x(b) - x(a);
    const label = width >= 52 ? `<text class="sens-band-label" x="${(x(a) + width / 2).toFixed(1)}" y="${H - bottom - 6}" text-anchor="middle">Pays off</text>` : "";
    return `<rect class="sens-band" x="${x(a).toFixed(1)}" y="${top}" width="${width.toFixed(1)}" height="${H - top - bottom}"/>${label}`;
  }).join("");
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => `<text x="${x(t)}" y="${H - bottom + 13}" text-anchor="${t === 0 ? "start" : t === 1 ? "end" : "middle"}">${pct(t)}</text>`).join("");
  const anchor = (px) => (px < 44 ? "start" : px > W - 44 ? "end" : "middle");
  const marker = (prior, label, solid) => {
    const px = x(prior);
    return `<line class="sens-mark" x1="${px.toFixed(1)}" x2="${px.toFixed(1)}" y1="${top - 4}" y2="${H - bottom}"/>
      <circle class="sens-dot${solid ? "" : " hollow"}" cx="${px.toFixed(1)}" cy="${y(valueAt(points, prior)).toFixed(1)}" r="4.5"/>
      <text class="sens-mark-label" x="${px.toFixed(1)}" y="${top - 9}" text-anchor="${anchor(px)}">${label} ${pct(prior)}</text>`;
  };
  // Leave the "before" marker out when its label would sit on top of the "now" label.
  const showBefore = inspected && Math.abs(x(before) - x(now)) > 70;
  const peak = points.reduce((best, p) => (p.net_value > best.net_value ? p : best));
  const crowded = [now, showBefore ? before : null].some((p) => p !== null && Math.abs(x(p) - x(peak.prior)) < 26);
  const peakLabel = peak.net_value > 0 && !crowded
    ? `<text x="${x(peak.prior).toFixed(1)}" y="${(y(peak.net_value) - 7).toFixed(1)}" text-anchor="${anchor(x(peak.prior))}">${signed(peak.net_value)}</text>`
    : "";

  const range = windows.map(([a, b]) => `${pct(a)} to ${pct(b)}`).join(" or ");
  const first = windows.length
    ? `Inspection pays at a ${range} chance the road is ${words.bad}.`
    : "Inspection costs more than it saves across this range.";
  const inWindow = windows.some(([a, b]) => now > a && now < b);
  const second = inspected
    ? `Risk after review: ${pct(now)} (was ${pct(before)}). ${inWindow ? "A second check could pay." : "A second check would not pay."}`
    : `Current estimate: ${pct(now)}.`;
  const summary = `${first} ${second}`;

  container.hidden = false;
  container.innerHTML = `<h3>When a drone check pays off</h3>
    <p class="sens-sub">Estimated net saving ($)</p>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(summary)}">
      ${bands}
      <line class="sens-zero" x1="${left}" x2="${W - right}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}"/>
      <text x="${W - right}" y="${(y(0) - 4).toFixed(1)}" text-anchor="end">$0, break even</text>
      <path class="sens-line" d="${line}"/>
      ${peakLabel}
      ${showBefore ? marker(before, "Before", false) : ""}
      ${marker(now, "Now", true)}
      <g class="sens-hover" hidden><line y1="${top}" y2="${H - bottom}"/><circle r="4"/></g>
      ${ticks}
      <text x="${W / 2}" y="${H - 3}" text-anchor="middle">Chance the road is ${esc(words.bad)}</text>
    </svg>
    <p class="sens-readout">${signed(valueAt(points, now))} at ${pct(now)} risk.</p>
    <figcaption>${esc(summary)}</figcaption>`;

  const svg = container.querySelector("svg");
  const hover = container.querySelector(".sens-hover");
  const readout = container.querySelector(".sens-readout");
  svg.addEventListener("pointermove", (event) => {
    const box = svg.getBoundingClientRect();
    const prior = ((event.clientX - box.left) / box.width * W - left) / (W - left - right);
    const point = points.reduce((best, p) => (Math.abs(p.prior - prior) < Math.abs(best.prior - prior) ? p : best));
    hover.removeAttribute("hidden");
    hover.querySelector("line").setAttribute("x1", x(point.prior));
    hover.querySelector("line").setAttribute("x2", x(point.prior));
    hover.querySelector("circle").setAttribute("cx", x(point.prior));
    hover.querySelector("circle").setAttribute("cy", y(point.net_value));
    readout.textContent = `${signed(point.net_value)} at ${pct(point.prior)} risk.`;
  });
  svg.addEventListener("pointerleave", () => {
    hover.setAttribute("hidden", "");
    readout.textContent = `${signed(valueAt(points, now))} at ${pct(now)} risk.`;
  });
}

function shipmentPhrase(row) {
  const late = Math.round(row.lateness_min);
  const head = `${esc(row.shipment)}: ${esc(row.arrival)}`;
  if (row.constraint_breaches > 0) return `${head}, ${late} min late; appointment missed`;
  if (late > 0) return `${head}, ${late} min late; within appointment window`;
  return `${head}, on time`;
}

function caseFor(outcome, affectedId) {
  if (!outcome) return null;
  const rows = outcome.detail?.shipments;
  if (!rows?.length) {
    const late = Math.round(outcome.lateness_min);
    const text = outcome.constraint_breaches > 0 ? `${late} min late; appointment missed` : late > 0 ? `${late} min late` : "On time";
    return { text, tone: outcome.constraint_breaches > 0 ? "bad" : late > 0 ? "warn" : "ok" };
  }
  const shown = rows.filter((row) => row.shipment === affectedId || row.reassigned || row.lateness_min > 0 || row.constraint_breaches > 0);
  const tone = shown.some((row) => row.constraint_breaches > 0) ? "bad" : shown.some((row) => row.lateness_min > 0) ? "warn" : "ok";
  return { text: (shown.length ? shown : rows.slice(0, 1)).map(shipmentPhrase).join(". "), tone };
}

// What a response does to the affected shipments in each road state, as list markup for an option card.
// `option` is one entry of analysis.before, analysis.after or analysis.assignments.before.
export function outcomeCases(option, blockedProbability, affectedId, words = { bad: "blocked", good: "open" }) {
  const open = caseFor(option.outcomes?.open, affectedId);
  const blocked = caseFor(option.outcomes?.blocked, affectedId);
  if (!open || !blocked) return "";
  const row = (label, share, item) => `<li class="${item.tone}"><span class="case">${label}${share === null ? "" : ` <small>${pct(share)} estimated chance</small>`}</span><span>${item.text}.</span></li>`;
  if (open.text === blocked.text) return `<ul class="option-cases">${row(`Road ${words.good} or ${words.bad}`, null, open)}</ul>`;
  return `<ul class="option-cases">${row(`Road ${words.good}`, 1 - blockedProbability, open)}${row(`Road ${words.bad}`, blockedProbability, blocked)}</ul>`;
}
