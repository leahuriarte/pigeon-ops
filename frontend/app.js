import { openInspection, resetInspection } from "./inspection.js";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const money = (value) => `${value < 0 ? "−" : ""}$${Math.abs(Math.round(value)).toLocaleString("en-US")}`;
const pct = (value) => `${Math.round(value * 100)}%`;
const whole = (value) => Math.round(value).toLocaleString("en-US");
const esc = (value) => String(value).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const clockLabel = (minutes) => {
  const value = Math.floor(minutes + 1e-6);
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
};
const shortTruck = (id) => id.replace("SYN-TRK-", "T");
const svgNS = "http://www.w3.org/2000/svg";

const colors = {
  ink: "#1b1f23",
  muted: "#5b6268",
  stopped: "#7c858c",
  accent: "#24508f",
  selected: "#6a3fb5",
  risk: "#b42318",
  amber: "#c27c0e",
  route: "#8a939b",
};
const attentionStatuses = new Set(["at-risk", "watch", "unassigned"]);
const shipmentStatus = {
  "on-time": ["On time", "ok"],
  planned: ["Planned", "planned"],
  watch: ["Watch", "watch"],
  "at-risk": ["At risk", "risk"],
  unassigned: ["Unassigned", "risk"],
};
// Playback segment kinds: label, status tone, and whether the truck is moving.
const activity = {
  driving: ["Driving", "ok", true],
  empty: ["Driving empty", "ok", true],
  detour: ["On detour", "watch", true],
  waiting: ["Waiting", "planned", false],
  loading: ["Loading", "planned", false],
  unloading: ["Unloading", "planned", false],
  transfer: ["Swapping load", "watch", false],
  busy: ["Finishing job", "planned", false],
  blocked: ["Stuck", "risk", false],
  idle: ["Done", "planned", false],
};
const observationLabels = {
  blocked: "the feed reported the road blocked",
  clear: "the feed reported the road clear",
  inconclusive: "an inconclusive feed result",
};
const tag = ([label, tone]) => `<span class="status ${tone}">${label}</span>`;

let scenario;
let baseline = null; // Indicators for the normal scenario, the comparison point.
let activeScenario = "disruption";
let shipmentFilter = "attention";
let selectedTruck = null;

// ---------------------------------------------------------------------------
// Playback model
// ---------------------------------------------------------------------------
let playback = null;
let tracks = [];
let currentTime = 0;
let playing = false;
let speed = 5; // Simulated minutes per real second.
let lastFrame = 0;
let renderer = null;
let plan = { action: "hold-course", road: "open" };
let span = { start: 0, end: 1 }; // Timeline range, shared by every response's playback.
const liveKeys = new Map();

// Planar distance in metres, good enough for interpolation over a few kilometres.
function metres([lon1, lat1], [lon2, lat2]) {
  const k = Math.cos(((lat1 + lat2) / 2) * (Math.PI / 180));
  return Math.hypot((lon2 - lon1) * k, lat2 - lat1) * 111320;
}

function bearing([lon1, lat1], [lon2, lat2]) {
  const k = Math.cos(((lat1 + lat2) / 2) * (Math.PI / 180));
  return (Math.atan2((lon2 - lon1) * k, lat2 - lat1) * 180) / Math.PI;
}

function prepareTrack(track) {
  const full = [];
  const along = [];
  let total = 0;
  const segments = track.segments.map((segment) => {
    const moving = segment.path.length > 1 && segment.t1 > segment.t0;
    const prepared = { ...segment, moving, startDist: total, lens: [0], length: 0 };
    if (moving) {
      for (let i = 1; i < segment.path.length; i += 1) prepared.lens.push(prepared.lens[i - 1] + metres(segment.path[i - 1], segment.path[i]));
      prepared.length = prepared.lens.at(-1);
      segment.path.forEach((point, i) => {
        full.push(point);
        along.push(total + prepared.lens[i]);
      });
      total += prepared.length;
    } else if (!full.length) {
      full.push(segment.path[0]);
      along.push(0);
    }
    return prepared;
  });
  return { id: track.id, segments, full, along, total, heading: 0 };
}

function stateAt(track, t) {
  const index = Math.max(0, track.segments.findIndex((s) => t >= s.t0 && t < s.t1));
  const segment = t >= track.segments.at(-1).t1 ? track.segments.at(-1) : track.segments[index];
  const segmentIndex = track.segments.indexOf(segment);
  if (!segment.moving) {
    return { id: track.id, track, segment, segmentIndex, pos: segment.path[0], dist: segment.startDist, moving: false, heading: track.heading };
  }
  const d = clamp((t - segment.t0) / (segment.t1 - segment.t0), 0, 1) * segment.length;
  let i = 1;
  while (i < segment.lens.length - 1 && segment.lens[i] < d) i += 1;
  const a = segment.path[i - 1];
  const b = segment.path[i];
  const span = segment.lens[i] - segment.lens[i - 1] || 1;
  const f = clamp((d - segment.lens[i - 1]) / span, 0, 1);
  const pos = [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
  track.heading = bearing(a, b);
  return { id: track.id, track, segment, segmentIndex, pos, dist: segment.startDist + d, moving: true, heading: track.heading };
}

// Split a truck's whole route into the part already driven and the part ahead.
function splitRoute(state) {
  const { track, dist, pos } = state;
  if (track.full.length < 2) return [[], []];
  let i = 0;
  while (i < track.along.length && track.along[i] <= dist) i += 1;
  const behind = [...track.full.slice(0, i), pos];
  const ahead = [pos, ...track.full.slice(i)];
  return [behind.length > 1 ? behind : [], ahead.length > 1 ? ahead : []];
}

function truckTone(state) {
  if (state.segment.kind === "blocked") return colors.risk;
  if (state.segment.kind === "detour" || state.segment.kind === "transfer") return colors.amber;
  return state.moving ? colors.ink : colors.stopped;
}

const playbackCache = new Map();
async function fetchPlayback(action, road) {
  const params = new URLSearchParams({ scenario: activeScenario, action, road });
  const key = params.toString();
  if (!playbackCache.has(key)) {
    const request = fetch(`/api/playback?${params}`).then((response) => {
      if (!response.ok) throw new Error("The playback failed to load");
      return response.json();
    });
    playbackCache.set(key, request);
    request.catch(() => playbackCache.delete(key));
  }
  return playbackCache.get(key);
}

async function loadPlayback({ keepTime = false } = {}) {
  const actions = scenario.decision_model.actions.map((a) => a.id);
  const all = await Promise.all(actions.map((id) => fetchPlayback(id, plan.road)));
  playback = all[actions.indexOf(plan.action)];
  tracks = playback.tracks.map(prepareTrack);
  buildTallies(Object.fromEntries(actions.map((id, i) => [id, all[i]])));
  liveKeys.clear();
  // One timeline for every response, so switching responses keeps the clock and the race lines up.
  span = { start: playback.start, end: Math.max(...all.map((p) => p.end)) };
  const slider = $("#timeline");
  slider.min = span.start;
  slider.max = span.end;
  currentTime = keepTime ? clamp(currentTime, span.start, span.end) : span.start;
  $("#timeline-start").textContent = clockLabel(span.start);
  $("#timeline-end").textContent = clockLabel(span.end);
  playback.alternatives.forEach((alt) => document.documentElement.style.setProperty(`--route-${alt.id}`, alt.color));
  renderTicks();
  renderPlanNote();
  renderer?.setPlayback(playback, tracks);
  renderTallyFrame();
  updateFrame();
}

function renderTicks() {
  const width = span.end - span.start;
  const marks = playback.events.filter((event) => event.tone !== "info");
  $("#timeline-ticks").innerHTML = marks.map((event) => {
    const left = ((event.t - span.start) / width) * 100;
    const mine = selectedTruck && event.truck === selectedTruck;
    return `<i class="tick ${event.tone}${mine ? " mine" : ""}" style="left:${left.toFixed(2)}%"></i>`;
  }).join("");
}

function renderPlanNote() {
  if (scenario.scenario_type !== "disruption") return;
  const best = scenario.analysis.before[0];
  $$("#playback-action option").forEach((option) => {
    const action = scenario.decision_model.actions.find((a) => a.id === option.value);
    option.textContent = action.label + (option.value === best.id ? " (model’s pick)" : "");
  });
  $("#playback-action").value = plan.action;
  $(`#road-${plan.road}`).checked = true;
  const outcome = scenario.decision_model.actions.find((a) => a.id === plan.action).outcomes[plan.road];
  $("#plan-note").textContent = outcome.lateness_min > 0
    ? `${affectedShipment().id} arrives about ${Math.round(outcome.lateness_min)} min late in this case.`
    : `${affectedShipment().id} arrives on time in this case.`;
}

function updateFrame() {
  if (!playback) return;
  const slider = $("#timeline");
  slider.value = currentTime;
  slider.style.setProperty("--p", `${((currentTime - span.start) / (span.end - span.start)) * 100}%`);
  slider.setAttribute("aria-valuetext", clockLabel(currentTime));
  $("#map-clock").textContent = clockLabel(currentTime);
  const states = tracks.map((track) => stateAt(track, currentTime));
  renderer?.update(states);
  updateFleetLive(states);
  updateTruckCard(states);
  updateEventFeed();
  updateTally();
}

function frame(now) {
  if (!playing) return;
  const elapsed = lastFrame ? Math.min(0.1, (now - lastFrame) / 1000) : 0;
  lastFrame = now;
  currentTime = Math.min(span.end, currentTime + elapsed * speed);
  updateFrame();
  if (currentTime >= span.end) setPlaying(false);
  else requestAnimationFrame(frame);
}

function setPlaying(value) {
  if (!playback) return;
  if (value && currentTime >= span.end) currentTime = span.start;
  playing = value;
  lastFrame = 0;
  const button = $("#play");
  button.classList.toggle("playing", value);
  button.setAttribute("aria-label", value ? "Pause" : "Play");
  if (value) requestAnimationFrame(frame);
}

function updateFleetLive(states) {
  states.forEach((state) => {
    const key = `${state.segmentIndex}`;
    if (liveKeys.get(state.id) === key) return;
    liveKeys.set(state.id, key);
    const cell = document.querySelector(`[data-live="${state.id}"]`);
    if (!cell) return;
    const [label, tone] = activity[state.segment.kind] ?? [state.segment.kind, "planned"];
    cell.innerHTML = `${tag([label, tone])}<span class="detail">${esc(state.segment.text)}</span>`;
  });
}

function updateTruckCard(states) {
  const card = $("#truck-card");
  const state = states.find((item) => item.id === selectedTruck);
  if (!state) {
    card.hidden = true;
    return;
  }
  const truck = scenario.trucks.find((item) => item.id === state.id);
  const [label, tone] = activity[state.segment.kind] ?? [state.segment.kind, "planned"];
  const next = playback.events.find((event) => event.truck === state.id && event.t > currentTime + 0.01);
  const until = state.segment.kind === "idle" ? "" : ` until ${clockLabel(state.segment.t1)}`;
  card.hidden = false;
  card.innerHTML = `<div class="truck-card-head"><b>${esc(state.id)}</b>${tag([label, tone])}<button class="link" type="button" data-truck="${esc(state.id)}">Close</button></div>
    <p>${esc(state.segment.text)}${until}.</p>
    ${next ? `<p class="muted">Next, ${clockLabel(next.t)}: ${esc(next.text)}</p>` : ""}
    <p class="muted">${esc(truck.fuel)}, ${truck.duty_remaining_hr} h duty left at ${clockLabel(playback.start)}</p>`;
}

let feedKey = "";
function updateEventFeed() {
  const relevant = playback.events.filter((event) => !selectedTruck || event.truck === selectedTruck);
  const past = relevant.filter((event) => event.t <= currentTime + 0.01).slice(-3);
  const next = relevant.find((event) => event.t > currentTime + 0.01);
  const key = `${selectedTruck}|${past.map((e) => e.t + e.truck).join()}|${next?.t}`;
  if (key === feedKey) return;
  feedKey = key;
  const row = (event, upcoming) => `<li class="${upcoming ? "upcoming" : event.tone}">
    <time>${clockLabel(event.t)}</time>
    <button class="link quiet" type="button" data-truck="${esc(event.truck)}">${esc(shortTruck(event.truck))}</button>
    <span>${upcoming ? "Next: " : ""}${esc(event.text)}</span></li>`;
  const rows = [...past.map((event) => row(event, false)), next ? row(next, true) : ""];
  $("#event-feed").innerHTML = rows.join("") || '<li class="upcoming"><span>Press play to start the simulation.</span></li>';
}

// ---------------------------------------------------------------------------
// Running tally. For each response, the pool shipments' computed outcome rows
// (decision_model.actions[*].outcomes[road].detail.shipments) are spread over
// that response's playback, so the counters end exactly at the computed totals.
// Miles, CO2 and per-mile cost follow the truck's distance driven; driver time
// follows the clock; delay ticks once the truck can no longer arrive on time;
// a missed appointment counts when its window closes without the truck.
// ---------------------------------------------------------------------------
let tallies = {};
let planTotals = null; // Keep the current route with CA-47 open: the original plan.
const tallyKeys = new Map();
const tallyMetrics = [
  { id: "cost", label: "Cost so far", format: (v) => money(v) },
  { id: "co2", label: "CO₂ emitted", format: (v) => `${v.toFixed(1)} <small>kg</small>` },
  { id: "miles", label: "Miles driven", format: (v) => `${v.toFixed(1)} <small>mi</small>` },
  { id: "late", label: "Minutes late", format: (v) => `${whole(v)} <small>min</small>` },
  { id: "missed", label: "Missed appointments", format: (v) => whole(v) },
];

function outcomeRows(actionId, road) {
  const action = scenario.decision_model.actions.find((a) => a.id === actionId);
  return action?.outcomes?.[road]?.detail?.shipments ?? null;
}

function checkInTime(pb, row) {
  const event = pb.events.find((e) => e.truck === row.truck && e.text.startsWith("Checks in") && e.text.includes(row.shipment));
  return event ? event.t : clockMinutes(row.arrival);
}

function clockMinutes(text) {
  const [h, m] = text.split(":").map(Number);
  return h * 60 + m;
}

function buildTallies(playbacks) {
  tallies = {};
  tallyKeys.clear();
  for (const [actionId, pb] of Object.entries(playbacks)) {
    const rows = outcomeRows(actionId, plan.road);
    if (!rows) continue;
    tallies[actionId] = rows.map((row) => {
      const raw = pb.tracks.find((t) => t.id === row.truck);
      const track = raw ? prepareTrack(raw) : null;
      const t0 = pb.start;
      const t1 = Math.max(t0 + 1, checkInTime(pb, row));
      const d0 = track ? stateAt(track, t0).dist : 0;
      const d1 = track ? stateAt(track, t1).dist : 0;
      const windowEnd = clockMinutes(row.appointment.split("–")[1] ?? row.appointment);
      return { ...row, track, t0, t1, d0, d1, breachAt: Math.min(t1, windowEnd) };
    });
  }
  planTotals = totalsAt(outcomeRows("hold-course", "open")?.map((row) => ({ ...row, t0: 0, t1: 1, breachAt: 0 })) ?? [], Infinity);
}

function totalsAt(rows, t) {
  const m = scenario.decision_model;
  const out = { cost: 0, co2: 0, miles: 0, late: 0, missed: 0 };
  for (const row of rows) {
    const byTime = clamp((t - row.t0) / (row.t1 - row.t0), 0, 1);
    let byDistance = byTime;
    if (row.track && row.d1 > row.d0 && t < row.t1) byDistance = clamp((stateAt(row.track, Math.max(t, row.t0)).dist - row.d0) / (row.d1 - row.d0), 0, 1);
    const mileCost = row.miles * m.rates.cost_per_mile;
    const late = clamp(t - (row.t1 - row.lateness_min), 0, row.lateness_min);
    const missed = row.constraint_breaches > 0 && t >= row.breachAt ? row.constraint_breaches : 0;
    const co2 = row.emissions_kg * byDistance;
    out.miles += row.miles * byDistance;
    out.co2 += co2;
    out.late += late;
    out.missed += missed;
    out.cost += mileCost * byDistance + (row.trip_cost - mileCost) * byTime
      + late * m.lateness_cost_per_minute + co2 * m.emissions_cost_per_kg + missed * m.hard_constraint_penalty;
  }
  return out;
}

function setHTML(key, node, html) {
  if (tallyKeys.get(key) === html) return false;
  tallyKeys.set(key, html);
  node.innerHTML = html;
  return true;
}

function renderTallyFrame() {
  const block = $("#tally-block");
  const current = tallies[plan.action];
  block.hidden = !current;
  if (!current) return;
  const actions = scenario.decision_model.actions;
  const label = actions.find((a) => a.id === plan.action).label;
  const road = scenario.scenario_type === "disruption" ? `, CA-47 ${plan.road}` : "";
  $("#tally-scope").textContent = `${label}${road}`;
  $("#tally-race-road").textContent = scenario.scenario_type === "disruption" ? `(CA-47 ${plan.road})` : "";
  $("#tally").innerHTML = tallyMetrics.map((metric) => `<li class="tally-tile" data-metric="${metric.id}">
    <span class="kpi-label">${metric.label}</span>
    <span class="tally-value"></span>
    <span class="tally-bar" aria-hidden="true"><i></i></span>
    <span class="tally-foot"></span></li>`).join("");
  const best = scenario.analysis.before[0]?.id;
  $("#tally-race").innerHTML = actions.filter((a) => tallies[a.id]).map((a) => `<li>
    <button type="button" class="tally-row${a.id === plan.action ? " current" : ""}" data-show="${esc(a.id)}" aria-pressed="${a.id === plan.action}">
      <span class="tally-row-name">${esc(a.label)}${a.id === best ? ' <small>model’s pick</small>' : ""}</span>
      <span class="tally-row-bar" aria-hidden="true"><i class="ghost"></i><i class="fill"></i></span>
      <span class="tally-row-value"></span>
    </button></li>`).join("");
  const r = scenario.decision_model;
  const shipments = [...new Set(current.map((row) => row.shipment))].join(" and ");
  $("#tally-note").textContent = `Covers ${shipments}, the shipments the responses change, from ${clockLabel(span.start)} until each is delivered. Cost adds truck running cost ($${r.rates.cost_per_mile.toFixed(2)}/mi), driver time ($${r.rates.driver_hourly_rate}/h), delay (${money(r.lateness_cost_per_minute)}/min), CO₂ ($${r.emissions_cost_per_kg.toFixed(2)}/kg) and ${money(r.hard_constraint_penalty)} per missed appointment. All rates are assumed.`;
  tallyKeys.clear();
}

function updateTally() {
  const current = tallies[plan.action];
  if (!current) return;
  const now = totalsAt(current, currentTime);
  const final = totalsAt(current, Infinity);
  tallyMetrics.forEach((metric) => {
    const tile = document.querySelector(`.tally-tile[data-metric="${metric.id}"]`);
    if (!tile) return;
    const changed = setHTML(`v-${metric.id}`, tile.querySelector(".tally-value"), metric.format(now[metric.id]));
    const share = final[metric.id] > 0 ? now[metric.id] / final[metric.id] : 0;
    tile.querySelector(".tally-bar i").style.width = `${(share * 100).toFixed(1)}%`;
    const diff = final[metric.id] - planTotals[metric.id];
    const tone = Math.abs(diff) < 0.05 ? "" : diff > 0 ? "worse" : "better";
    const vsPlan = tone ? `${diff > 0 ? "+" : "−"}${metric.format(Math.abs(diff)).replace(/<[^>]+>/g, "")} vs plan` : "same as plan";
    setHTML(`f-${metric.id}`, tile.querySelector(".tally-foot"), `<span>Ends at ${metric.format(final[metric.id]).replace(/<[^>]+>/g, "")}</span><span class="${tone}">${vsPlan}</span>`);
    // A missed appointment or the first late minute is a step change worth drawing the eye to.
    if (changed && metric.id === "missed" && now.missed > 0) bump(tile);
    if (metric.id === "late") tile.classList.toggle("alert", now.late > 0);
    if (metric.id === "missed") tile.classList.toggle("alert", now.missed > 0);
  });
  const finals = Object.fromEntries(Object.entries(tallies).map(([id, rows]) => [id, totalsAt(rows, Infinity).cost]));
  const top = Math.max(...Object.values(finals), 1);
  $$("#tally-race .tally-row").forEach((row) => {
    const id = row.dataset.show;
    const cost = totalsAt(tallies[id], currentTime).cost;
    row.querySelector(".ghost").style.width = `${(finals[id] / top * 100).toFixed(1)}%`;
    row.querySelector(".fill").style.width = `${(cost / top * 100).toFixed(1)}%`;
    setHTML(`race-${id}`, row.querySelector(".tally-row-value"), `${money(cost)} <small>of ${money(finals[id])}</small>`);
  });
}

function bump(node) {
  node.classList.remove("bump");
  void node.offsetWidth;
  node.classList.add("bump");
}

async function watchResponse(actionId) {
  if (actionId === plan.action && playing) return;
  plan = { ...plan, action: actionId };
  setPlaying(false);
  await loadPlayback();
  $(".map-block").scrollIntoView({ behavior: "smooth", block: "start" });
  setPlaying(true);
}

function selectTruck(id) {
  selectedTruck = id;
  renderShipments();
  renderFleet();
  renderTicks();
  renderer?.setSelected(id);
  updateFrame();
}

// ---------------------------------------------------------------------------
// Map renderers. Both draw the same playback: ArcGIS on wide screens,
// an SVG schematic on narrow screens or when ArcGIS cannot load.
// ---------------------------------------------------------------------------
function visibleLocations() {
  return scenario.locations.filter((location) => location.kind !== "incident" || scenario.scenario_type === "disruption");
}

function alternativeEmphasis(id) {
  if (plan.action === "hold-course") return id === "current";
  if (plan.action === "reroute") return id === "detour";
  return false;
}

function createSchematicRenderer() {
  const routes = $("#routes");
  const markers = $("#markers");
  const truckMarks = $("#truck-marks");
  const items = new Map();
  const project = ([lon, lat]) => {
    const b = scenario.map_bounds;
    return [30 + ((lon - b.west) / (b.east - b.west)) * 700, 25 + ((b.north - lat) / (b.north - b.south)) * 320];
  };
  const points = (path) => path.map((p) => project(p).map((v) => v.toFixed(1)).join(",")).join(" ");
  const el = (name, attrs) => {
    const node = document.createElementNS(svgNS, name);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    return node;
  };
  return {
    setScenario() {
      markers.replaceChildren();
      visibleLocations().forEach((location) => {
        const [x, y] = project([location.lon, location.lat]);
        const group = el("g", { class: `place ${location.kind}` });
        group.innerHTML = location.kind === "terminal"
          ? `<rect x="${x - 5}" y="${y - 5}" width="10" height="10"/>`
          : `<circle cx="${x}" cy="${y}" r="${location.kind === "incident" ? 7 : 5}"/>`;
        group.innerHTML += `<text x="${x + 9}" y="${y + 4}">${esc(location.name)}</text>`;
        markers.appendChild(group);
      });
    },
    setPlayback(data, prepared) {
      routes.replaceChildren();
      truckMarks.replaceChildren();
      items.clear();
      if (scenario.scenario_type === "disruption") {
        data.alternatives.forEach((alt) => {
          routes.appendChild(el("polyline", { class: `alt ${alt.id}${alternativeEmphasis(alt.id) ? " active" : ""}`, points: points(alt.path) }));
        });
      }
      prepared.forEach((track) => {
        const behind = el("polyline", { class: "trail behind", points: "" });
        const ahead = el("polyline", { class: "trail ahead", points: "" });
        routes.append(behind, ahead);
        const group = el("g", { class: "truck", "data-truck": track.id });
        group.innerHTML = '<path class="arrow" d="M0 -8 L6 7 L0 3.5 L-6 7 Z"/><rect class="stop" x="-4.5" y="-4.5" width="9" height="9" transform="rotate(45)"/>'
          + `<text y="-11">${esc(shortTruck(track.id))}</text>`;
        truckMarks.appendChild(group);
        items.set(track.id, { behind, ahead, group, dist: -1 });
      });
      this.setSelected(selectedTruck);
    },
    setSelected(id) {
      items.forEach((item, truckId) => {
        item.group.classList.toggle("selected", truckId === id);
        item.ahead.classList.toggle("selected", truckId === id);
        item.behind.classList.toggle("selected", truckId === id);
        item.dist = -1;
      });
    },
    update(states) {
      states.forEach((state) => {
        const item = items.get(state.id);
        if (!item) return;
        if (Math.abs(item.dist - state.dist) > 1) {
          const [behind, ahead] = splitRoute(state);
          item.behind.setAttribute("points", points(behind));
          item.ahead.setAttribute("points", points(ahead));
          item.dist = state.dist;
        }
        const [x, y] = project(state.pos);
        item.group.setAttribute("transform", `translate(${x.toFixed(1)} ${y.toFixed(1)})`);
        item.group.querySelector(".arrow").setAttribute("transform", `rotate(${state.heading.toFixed(0)})`);
        item.group.classList.toggle("moving", state.moving);
        item.group.style.setProperty("--tone", truckTone(state));
      });
    },
  };
}

async function createArcGISRenderer() {
  const arcgis = globalThis.$arcgis;
  if (!arcgis?.import) throw new Error("ArcGIS SDK unavailable");
  const [GraphicsLayer, Graphic, Extent] = await arcgis.import([
    "@arcgis/core/layers/GraphicsLayer.js",
    "@arcgis/core/Graphic.js",
    "@arcgis/core/geometry/Extent.js",
  ]);
  const viewElement = $("#arcgis-map");
  await viewElement.viewOnReady();
  // Mute the OpenStreetMap tiles so routes and trucks stand out.
  viewElement.map.basemap?.baseLayers.forEach((layer) => {
    layer.effect = "grayscale(0.85) brightness(1.06) contrast(0.82)";
  });
  viewElement.view.constraints = { minZoom: 10, maxZoom: 18, snapToZoom: false };

  const layers = {
    alternatives: new GraphicsLayer({ title: "Route options" }),
    trails: new GraphicsLayer({ title: "Truck routes" }),
    places: new GraphicsLayer({ title: "Terminals and warehouses" }),
    trucks: new GraphicsLayer({ title: "Trucks" }),
  };
  viewElement.map.addMany(Object.values(layers));

  const rgba = (hex, alpha = 1) => [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16), alpha];
  const line = (path) => ({ type: "polyline", paths: path.length > 1 ? [path] : [], spatialReference: { wkid: 4326 } });
  const point = ([lon, lat]) => ({ type: "point", longitude: lon, latitude: lat });
  const arrow = "M12 1 L22 23 L12 17 L2 23 Z";
  const items = new Map();
  let fitted = false;

  const trailSymbols = (selected) => ({
    casing: { type: "simple-line", color: rgba("#ffffff", selected ? 0.95 : 0), width: 7, cap: "round", join: "round" },
    ahead: selected
      ? { type: "simple-line", color: rgba(colors.selected), width: 4, cap: "round", join: "round" }
      : { type: "simple-line", color: rgba(colors.route, 0.6), width: 2.25, cap: "round", join: "round" },
    behind: selected
      ? { type: "simple-line", color: rgba(colors.selected, 0.45), width: 2.5, style: "short-dot", cap: "round" }
      : { type: "simple-line", color: rgba(colors.route, 0.3), width: 1.5, style: "short-dot", cap: "round" },
  });

  const truckSymbol = (state, selected) => {
    const color = selected ? colors.selected : truckTone(state);
    return state.moving
      ? { type: "simple-marker", style: "path", path: arrow, size: selected ? 20 : 16, angle: state.heading, color, outline: { color: "#ffffff", width: 1.5 } }
      : { type: "simple-marker", style: "diamond", size: selected ? 15 : 12, color, outline: { color: "#ffffff", width: 2 } };
  };

  viewElement.addEventListener("arcgisViewClick", async (event) => {
    const hit = await viewElement.hitTest(event.detail, { include: [layers.trucks] });
    const id = hit.results.find((result) => result.graphic?.attributes?.truck)?.graphic.attributes.truck ?? null;
    selectTruck(id && id === selectedTruck ? null : id);
  });

  return {
    setScenario() {
      layers.places.removeAll();
      const style = {
        terminal: { type: "simple-marker", style: "square", size: 11, color: colors.ink, outline: { color: "#ffffff", width: 2 } },
        warehouse: { type: "simple-marker", style: "circle", size: 11, color: "#ffffff", outline: { color: colors.muted, width: 2.5 } },
        incident: { type: "simple-marker", style: "circle", size: 18, color: rgba(colors.risk, 0.9), outline: { color: "#ffffff", width: 2.5 } },
      };
      visibleLocations().forEach((location) => {
        const geometry = { type: "point", longitude: location.lon, latitude: location.lat };
        layers.places.add(new Graphic({ geometry, symbol: style[location.kind] }));
        if (location.kind === "incident") {
          layers.places.add(new Graphic({ geometry, symbol: { type: "text", text: "!", color: "#ffffff", yoffset: -4, font: { size: 12, weight: "bold" } } }));
        }
        layers.places.add(new Graphic({
          geometry,
          symbol: {
            type: "text", text: location.name, color: location.kind === "incident" ? colors.risk : "#2f353a",
            haloColor: "#ffffff", haloSize: 2, xoffset: 10, yoffset: -4, horizontalAlignment: "left",
            font: { size: 11, weight: location.kind === "warehouse" ? "normal" : "bold" },
          },
        }));
      });
    },
    setPlayback(data, prepared) {
      layers.alternatives.removeAll();
      layers.trails.removeAll();
      layers.trucks.removeAll();
      items.clear();
      if (scenario.scenario_type === "disruption") {
        data.alternatives.forEach((alt) => {
          const strong = alternativeEmphasis(alt.id);
          const color = alt.color;
          layers.alternatives.addMany([
            new Graphic({ geometry: line(alt.path), symbol: { type: "simple-line", color: rgba("#ffffff", strong ? 0.9 : 0.6), width: 9, cap: "round", join: "round" } }),
            new Graphic({ geometry: line(alt.path), symbol: { type: "simple-line", color: rgba(color, strong ? 0.95 : 0.4), width: 5, cap: "round", join: "round", style: alt.id === "detour" ? "short-dash" : "solid" } }),
          ]);
        });
      }
      prepared.forEach((track) => {
        const symbols = trailSymbols(track.id === selectedTruck);
        const casing = new Graphic({ geometry: line([]), symbol: symbols.casing });
        const behind = new Graphic({ geometry: line([]), symbol: symbols.behind });
        const ahead = new Graphic({ geometry: line(track.full), symbol: symbols.ahead });
        const marker = new Graphic({ geometry: point(track.full[0]), attributes: { truck: track.id } });
        const label = new Graphic({
          geometry: point(track.full[0]),
          attributes: { truck: track.id },
          symbol: { type: "text", text: shortTruck(track.id), color: colors.ink, haloColor: "#ffffff", haloSize: 2, yoffset: 12, font: { size: 10, weight: "bold" } },
        });
        layers.trails.addMany([casing, behind, ahead]);
        layers.trucks.addMany([marker, label]);
        items.set(track.id, { casing, behind, ahead, marker, label, dist: -1, key: "" });
      });
      this.setSelected(selectedTruck);
      if (!fitted) {
        fitted = true;
        // Fit every terminal and warehouse, with room for labels and the clock overlay.
        const lons = scenario.locations.map((l) => l.lon);
        const lats = scenario.locations.map((l) => l.lat);
        const [west, east, south, north] = [Math.min(...lons), Math.max(...lons), Math.min(...lats), Math.max(...lats)];
        const padX = (east - west) * 0.12;
        const padY = (north - south) * 0.1;
        viewElement.view.goTo(new Extent({
          xmin: west - padX, xmax: east + padX * 2.2, ymin: south - padY * 1.3, ymax: north + padY, spatialReference: { wkid: 4326 },
        }), { animate: false }).catch(() => {});
      }
    },
    setSelected(id) {
      items.forEach((item, truckId) => {
        const symbols = trailSymbols(truckId === id);
        item.casing.symbol = symbols.casing;
        item.ahead.symbol = symbols.ahead;
        item.behind.symbol = symbols.behind;
        item.dist = -1;
        item.key = "";
      });
      // Draw the selected truck on top of the others.
      const selected = items.get(id);
      if (selected) {
        const trails = layers.trails.graphics;
        const trucks = layers.trucks.graphics;
        trails.reorder(selected.casing, trails.length - 1);
        trails.reorder(selected.ahead, trails.length - 1);
        trucks.reorder(selected.marker, trucks.length - 1);
        trucks.reorder(selected.label, trucks.length - 1);
      }
    },
    update(states) {
      states.forEach((state) => {
        const item = items.get(state.id);
        if (!item) return;
        const selected = state.id === selectedTruck;
        if (Math.abs(item.dist - state.dist) > 2) {
          const [behind, ahead] = splitRoute(state);
          item.behind.geometry = line(behind);
          item.ahead.geometry = line(ahead);
          if (selected) item.casing.geometry = line(ahead);
          item.dist = state.dist;
        }
        item.marker.geometry = point(state.pos);
        item.label.geometry = point(state.pos);
        const key = `${state.moving}|${truckTone(state)}|${selected}|${state.moving ? Math.round(state.heading / 3) : ""}`;
        if (key !== item.key) {
          item.key = key;
          item.marker.symbol = truckSymbol(state, selected);
        }
      });
    },
  };
}

async function drawMap() {
  if (!renderer) {
    if (window.innerWidth < 920) {
      renderer = createSchematicRenderer();
      showSchematic("Schematic map (the street map loads at 920px and wider).");
    } else {
      try {
        renderer = await createArcGISRenderer();
        $("#map-fallback").classList.add("hidden");
        $("#arcgis-map").classList.remove("hidden");
        $("#map-provider").textContent = "Basemap and road paths from OpenStreetMap (roads routed with OSRM). Trucks and schedules are simulated.";
      } catch (error) {
        console.warn("Using the schematic map because ArcGIS failed to load:", error);
        renderer = createSchematicRenderer();
        showSchematic("The street map did not load, so this is a schematic.");
      }
    }
  }
  renderer.setScenario();
  if (playback) renderer.setPlayback(playback, tracks);
  updateFrame();
}

function showSchematic(message) {
  $(".map-shell").classList.add("schematic");
  $("#arcgis-map").classList.add("hidden");
  $("#map-fallback").classList.remove("hidden");
  $("#map-provider").textContent = `${message} Trucks and schedules are simulated.`;
}

function affectedShipment() {
  return scenario.shipments.find((item) => item.id === scenario.disruption.affected_shipment);
}

function renderDecision() {
  const d = scenario.disruption;
  const affected = affectedShipment();
  $("#scenario-time").textContent = scenario.generated_at.slice(11, 16);
  $("#situation-eyebrow").textContent = scenario.scenario_type === "disruption" ? "Changed condition" : "Normal operations";
  const when = scenario.scenario_type === "disruption" ? "reported" : "as of";
  $("#reported-at").textContent = d.reported_at ? ` · ${when} ${d.reported_at}` : "";
  $("#disruption-title").textContent = d.title;
  $("#disruption-description").textContent = d.description;
  $("#constraints").innerHTML = d.constraints.map((item) => `<li>${esc(item)}</li>`).join("");
  $("#risk-value").textContent = pct(d.prior_blocked_probability);
  $("#affected").innerHTML = `${esc(affected.id)}<small>${esc(affected.cargo)}, ${affected.slack_min} min slack</small>`;
  $("#deadline").textContent = d.decision_deadline;
}

// Indicator tiles. Normal flow is the baseline the changed condition is compared with.
const higherIsBetter = new Set(["appointments_feasible"]);
const basisLabel = { stub: "Not yet calculated", estimated: "Estimated", simulated: "Simulated", measured: "Measured" };
function indicatorNumber(item, value) {
  if (item.unit === "mi") return value.toFixed(1);
  return whole(value);
}
function renderIndicators(indicators) {
  const compare = scenario.scenario_type !== "normal" && baseline;
  const scope = "Each number covers every shipment in the plan, assuming you take the top-ranked response without inspecting first.";
  $("#indicators-note").textContent = compare
    ? `${scope} The arrows compare each one with normal flow.`
    : `${scope} The changed condition is compared with these values.`;
  $("#indicators").innerHTML = indicators.map((item) => {
    const label = esc(item.label.replace(/^Estimated /, "").replace("CO2", "CO₂"));
    const basis = `<span class="basis ${esc(item.basis)}">${esc(basisLabel[item.basis] ?? item.basis)}</span>`;
    if (item.value === null || item.value === undefined) {
      return `<li class="kpi pending"><span class="kpi-label">${label}</span><span class="kpi-value">—</span><span class="kpi-foot">${basis}</span></li>`;
    }
    const of = item.of !== undefined ? ` of ${whole(item.of)}` : "";
    // Count units repeat the label, so only physical units are printed.
    const unit = ["min", "mi", "kg"].includes(item.unit) ? ` <small>${esc(item.unit)}</small>` : "";
    let delta = '<span class="kpi-delta">Baseline</span>';
    const base = compare && baseline.find((b) => b.id === item.id);
    if (base && base.value !== null) {
      const diff = item.value - base.value;
      const shown = indicatorNumber(item, Math.abs(diff));
      if (Number(shown) === 0) {
        delta = '<span class="kpi-delta">Same as normal flow</span>';
      } else {
        const worse = (diff > 0) !== higherIsBetter.has(item.id);
        delta = `<span class="kpi-delta ${worse ? "worse" : "better"}"><span aria-hidden="true">${diff > 0 ? "▲" : "▼"}</span> ${diff > 0 ? "+" : "−"}${shown} vs normal flow</span>`;
      }
    }
    return `<li class="kpi"><span class="kpi-label">${label}</span><span class="kpi-value">${indicatorNumber(item, item.value)}${of}${unit}</span><span class="kpi-foot">${delta}${basis}</span></li>`;
  }).join("");
}

function renderRankings(items, observation = null) {
  $("#ranking-state").textContent = observation
    ? `Ranked by expected loss after ${observationLabels[observation]}`
    : "Ranked by expected loss before inspection";
  $("#rankings").innerHTML = items.map((item) => {
    const e = item.expected;
    return `<li class="option${item.rank === 1 ? " top" : ""}">
      <div class="option-head">
        <span class="rank">${item.rank}</span>
        <b class="option-name">${esc(item.label)}</b>
        <span class="option-loss"><b>${money(item.expected_loss)}</b><small>expected loss</small></span>
      </div>
      <p>${esc(item.summary)}</p>
      <p class="muted">${esc(item.stakeholder_effect)}</p>
      <button class="link watch" type="button" data-watch="${esc(item.id)}">Watch it play out on the map</button>
      <dl class="option-metrics">
        <div><dt>Delay</dt><dd>${whole(e.lateness_min)} min</dd></div>
        <div><dt>Direct cost</dt><dd>${money(e.incremental_cost)}</dd></div>
        <div><dt>CO₂</dt><dd>${whole(e.emissions_kg)} kg</dd></div>
        <div><dt>Breaches</dt><dd>${e.constraint_breaches.toFixed(1)}</dd></div>
      </dl>
    </li>`;
  }).join("");
}

function renderEvoi(analysis) {
  const e = analysis.evoi;
  const best = analysis.before[0];
  $("#recommendation").textContent = e.recommend_inspection
    ? `Inspect CA-47 before acting. Inspecting lowers the expected loss by ${money(e.gross_value_of_information)} and costs ${money(e.inspection_cost)}, so it comes out ${money(e.net_value_of_information)} ahead.`
    : `Act now. Inspecting costs ${money(e.inspection_cost)} and lowers the expected loss by ${money(e.gross_value_of_information)}.`;
  $("#recommendation-short").innerHTML = e.recommend_inspection
    ? `Inspect CA-47 first<small>If you act now: ${esc(best.label)}</small>`
    : `${esc(best.label)}<small>Inspecting costs more than it saves</small>`;
  $("#act-loss").textContent = money(e.best_act_now_loss);
  $("#evidence-loss").textContent = money(e.expected_loss_after_inspection);
  $("#inspection-cost").textContent = money(e.inspection_cost);
  $("#net-value").textContent = money(e.net_value_of_information);
  const breakEven = e.break_even_accuracy === null
    ? "Even perfect evidence would not pay for an inspection."
    : `An inspection pays off once its evidence is at least ${pct(e.break_even_accuracy)} accurate.`;
  const cv = e.cv_only;
  $("#cv-only").textContent = cv
    ? `Still-image CV alone (vehicle spacing, measured on 27 aerial frames): ${money(cv.net_value_of_information)} net. The clip suggestion uses vehicle motion and has not been measured yet. ${breakEven}`
    : breakEven;
  const a = e.assumptions;
  $("#assumptions").innerHTML = [
    ["Feed accuracy", `${pct(a.inspection_accuracy)} (${esc(a.accuracy_basis)})`],
    ["Time to launch and review", `${a.launch_and_review_minutes} min`],
    ["Chance the feed reports “blocked”", pct(a.signal_blocked_probability)],
  ].map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join("");
}

function renderShipments() {
  const attention = scenario.shipments.filter((item) => attentionStatuses.has(item.status));
  const visible = selectedTruck
    ? scenario.shipments.filter((item) => item.truck === selectedTruck)
    : shipmentFilter === "all" ? scenario.shipments : attention;
  $("#shipment-count").textContent = `${scenario.shipments.length} active, ${attention.length} need attention`;
  $("#truck-filter").hidden = !selectedTruck;
  $("#shipments .segmented").classList.toggle("inactive", Boolean(selectedTruck));
  $("#truck-filter-id").textContent = selectedTruck ?? "";
  const affectedId = scenario.disruption.affected_shipment;
  $("#shipment-list").innerHTML = visible.length ? visible.map((item) => {
    const unassigned = item.truck === "Unassigned";
    return `<tr class="${item.id === affectedId ? "affected" : ""}">
      <td>${esc(item.id)}</td>
      <td>${esc(item.cargo)}</td>
      <td>${esc(item.priority[0].toUpperCase() + item.priority.slice(1))}</td>
      <td>${unassigned ? '<span class="muted">None</span>' : `<button class="link" type="button" data-truck="${esc(item.truck)}">${esc(item.truck)}</button>`}</td>
      <td>${esc(item.appointment)}</td>
      <td>${esc(item.eta)}</td>
      <td class="num">${unassigned ? "—" : `${item.slack_min} min`}</td>
      <td>${tag(shipmentStatus[item.status] ?? [item.status, "planned"])}</td>
    </tr>`;
  }).join("") : '<tr><td colspan="8" class="muted">No shipments match this filter.</td></tr>';
}

function renderFleet() {
  const electric = scenario.trucks.filter((item) => item.fuel === "Battery electric").length;
  $("#fleet-count").textContent = `${scenario.trucks.length} trucks, ${electric} battery electric`;
  $("#fleet-list").innerHTML = scenario.trucks.map((truck) => `<tr class="${truck.id === selectedTruck ? "selected" : ""}">
    <td><button class="link" type="button" data-truck="${esc(truck.id)}" aria-pressed="${truck.id === selectedTruck}">${esc(truck.id)}</button><span class="detail">${esc(truck.assignment)}, ${esc(truck.fuel.toLowerCase())}</span></td>
    <td class="live" data-live="${esc(truck.id)}"></td>
    <td class="num">${truck.duty_remaining_hr} h</td>
  </tr>`).join("");
  liveKeys.clear();
}

function renderConditions() {
  const o = scenario.operations;
  $("#conditions").textContent = `Trucks are averaging ${o.network_speed_mph} mph across the network, and terminal turn time is ${o.terminal_turn_time_min} min. Weather: ${o.weather.replaceAll(" · ", ", ")}.`;
}

function renderProvenance() {
  const kinds = {
    "public baseline": "Public data",
    "publicly calibrated": "Simulated to match public data",
    synthetic: "Simulated",
    assumed: "Assumed",
  };
  $("#provenance").innerHTML = scenario.data_provenance.map((item) => `<tr><td>${esc(item.label)}</td><td>${esc(kinds[item.kind] ?? item.kind)}</td><td>${esc(item.source)}</td></tr>`).join("");
}

function renderDefinitions() {
  const m = scenario.decision_model;
  const definitions = [
    ["Needs attention", "A shipment with status at risk, watch, or unassigned."],
    ["Slack", "Minutes between a shipment’s ETA and the start of its appointment window."],
    ["Blockage probability", "The chance that CA-47 is blocked. It starts at the value set in the scenario and is recalculated with Bayes’ rule after an inspection result."],
    ["Breach", "A hard rule that an outcome breaks, such as missing an appointment window."],
    ["Expected loss", `Delay × ${money(m.lateness_cost_per_minute)} per minute, plus direct cost, plus CO₂ × $${m.emissions_cost_per_kg.toFixed(2)} per kg, plus breaches × ${money(m.hard_constraint_penalty)}. The blocked and open outcomes are averaged using the blockage probability.`],
    ["Net value of inspecting", "Expected loss if you act now, minus expected loss if you inspect first, minus the cost of inspecting. The page recommends inspecting when the net value is above zero."],
  ];
  $("#definitions").innerHTML = definitions.map(([term, text]) => `<dt>${term}</dt><dd>${esc(text)}</dd>`).join("");
}

function resetObservation() {
  resetInspection();
  $("#selected-evidence").hidden = true;
  $("#posterior").hidden = true;
  $("#observation-buttons").hidden = true;
  $$("[data-observation]").forEach((button) => button.setAttribute("aria-pressed", "false"));
  $("#inspect-button").disabled = false;
  $("#inspect-button").textContent = "Review drone footage";
}

async function observe(observation) {
  const response = await fetch(`/api/analysis?scenario=${encodeURIComponent(activeScenario)}&observation=${encodeURIComponent(observation)}`);
  if (!response.ok) throw new Error("The analysis request failed");
  await showObservation(await response.json());
}

// Shared by the simulated feed buttons and a reviewed inspection image.
async function showObservation(analysis) {
  const observation = analysis.observation;
  $$("[data-observation]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.observation === observation)));
  renderRankings(analysis.after, observation);
  const prior = pct(analysis.prior_blocked_probability);
  const posterior = pct(analysis.posterior_blocked_probability);
  const best = analysis.after[0].label;
  $("#posterior").textContent = {
    blocked: `The feed reported the road blocked, so the blockage probability rises from ${prior} to ${posterior}. “${best}” has the lowest expected loss.`,
    clear: `The feed reported the road clear, so the blockage probability drops from ${prior} to ${posterior}. “${best}” has the lowest expected loss.`,
    inconclusive: `The feed result was inconclusive, so the blockage probability stays at ${prior} and the ranking is the same as before.`,
  }[observation];
  $("#posterior").hidden = false;
  // Play the map out with the new best option and the now more likely road state.
  plan = { action: analysis.after[0].id, road: analysis.posterior_blocked_probability >= 0.5 ? "blocked" : "open" };
  await loadPlayback({ keepTime: true });
}

async function loadScenario(id) {
  activeScenario = id;
  selectedTruck = null;
  setPlaying(false);
  const response = await fetch(`/api/scenario?scenario=${encodeURIComponent(id)}`);
  if (!response.ok) throw new Error("The scenario failed to load");
  scenario = await response.json();
  if (!baseline) {
    const base = await fetch("/api/analysis?scenario=normal&observation=inconclusive");
    baseline = base.ok ? (await base.json()).indicators : null;
  }
  $(`#scenario-${id}`).checked = true;
  document.body.dataset.scenario = scenario.scenario_type;
  $("#drone-note").textContent = scenario.decision_model.drone.note;
  $("#playback-action").innerHTML = scenario.decision_model.actions.map((a) => `<option value="${esc(a.id)}">${esc(a.label)}</option>`).join("");
  plan = {
    action: scenario.analysis.before[0].id,
    road: scenario.disruption.prior_blocked_probability >= 0.5 ? "blocked" : "open",
  };
  resetObservation();
  renderDecision();
  renderIndicators(scenario.analysis.indicators);
  renderRankings(scenario.analysis.before);
  renderEvoi(scenario.analysis);
  renderShipments();
  renderFleet();
  renderConditions();
  renderProvenance();
  renderDefinitions();
  await loadPlayback();
  await drawMap();
}

function bindEvents() {
  $("#inspect-button").addEventListener("click", async () => {
    if (await openInspection(activeScenario)) return;
    $("#observation-buttons").hidden = false;
    $("#inspect-button").textContent = "Waiting for a result";
    $("#inspect-button").disabled = true;
  });
  $$("[data-observation]").forEach((button) => button.addEventListener("click", () => observe(button.dataset.observation).catch((error) => {
    $("#posterior").textContent = error.message;
    $("#posterior").hidden = false;
  })));
  $$('input[name="scenario"]').forEach((input) => input.addEventListener("change", () => loadScenario(input.value)));
  $$('input[name="shipment-filter"]').forEach((input) => input.addEventListener("change", () => {
    shipmentFilter = input.value;
    selectTruck(null);
  }));
  $("#clear-truck").addEventListener("click", () => selectTruck(null));
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-truck]");
    if (!button) return;
    selectTruck(selectedTruck === button.dataset.truck ? null : button.dataset.truck);
  });

  $("#play").addEventListener("click", () => setPlaying(!playing));
  document.addEventListener("click", (event) => {
    const watch = event.target.closest("[data-watch]");
    if (watch) watchResponse(watch.dataset.watch);
    const show = event.target.closest("[data-show]");
    if (show && show.dataset.show !== plan.action) {
      plan = { ...plan, action: show.dataset.show };
      loadPlayback({ keepTime: true });
    }
  });
  $("#timeline").addEventListener("input", (event) => {
    currentTime = Number(event.target.value);
    updateFrame();
  });
  $("#playback-speed").addEventListener("change", (event) => {
    speed = Number(event.target.value);
  });
  $("#playback-action").addEventListener("change", (event) => {
    plan = { ...plan, action: event.target.value };
    loadPlayback({ keepTime: true });
  });
  $$('input[name="road"]').forEach((input) => input.addEventListener("change", () => {
    plan = { ...plan, road: input.value };
    loadPlayback({ keepTime: true });
  }));
  document.addEventListener("keydown", (event) => {
    if (event.key !== " " || event.target.closest("input:not([type=range]), select, button, textarea")) return;
    if (!event.target.closest(".map-block")) return;
    event.preventDefault();
    setPlaying(!playing);
  });
}

window.addEventListener("inspection-reviewed", ({ detail }) => {
  if (detail.review.scenario !== activeScenario) return;
  showObservation(detail.analysis).catch((error) => {
    $("#posterior").textContent += ` Playback could not update: ${error.message}`;
  });
  const finding = { blocked: "road blocked", clear: "road clear", inconclusive: "inconclusive" };
  const cv = detail.review.cv_suggestion;
  const choice = detail.review.observation;
  const agreement = !cv ? "" : cv === choice ? ` (matches the CV suggestion)` : ` (CV suggested ${finding[cv]}; you overrode it)`;
  $("#posterior").textContent = `Drone footage reviewed: ${finding[choice]}${agreement}. For this fictional scenario, the blockage probability changes from ${pct(detail.analysis.prior_blocked_probability)} to ${pct(detail.analysis.posterior_blocked_probability)}. Lowest expected loss: ${detail.analysis.after[0].label}.`;
  $("#selected-evidence").hidden = false;
  $("#selected-evidence-image").src = detail.caseItem.annotated_image;
  $("#selected-evidence-title").textContent = detail.caseItem.title;
  $("#inspect-button").textContent = "Review another clip";
});

bindEvents();
loadScenario(activeScenario).catch((error) => {
  $("#disruption-title").textContent = "The demo could not load";
  $("#disruption-description").textContent = error.message;
});
