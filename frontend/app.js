import { openInspection, resetInspection } from "./inspection.js";
import { clearBrief, initAsk, renderBrief, resetAsk } from "./copilot.js";
import { clearTriage, renderTriage, setAppliedWeb, withWeb } from "./triage.js";
import { inspectionSentence, inspectionChoices, responseExplanation, accuracyNote, renderSensitivity, outcomeCases } from "./insight.js";
import { renderResponseComparison } from "./response-comparison.js";

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
  ink: "#142231",
  muted: "#4a5866",
  stopped: "#7c858c",
  accent: "#24508f",
  selected: "#a3176e",
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
const tag = ([label, tone]) => `<span class="status ${esc(tone)}">${esc(label)}</span>`;
const activityFor = (state) => state.segment.kind === "blocked" && check().type === "congestion"
  ? ["In queue", "watch", false] : activity[state.segment.kind] ?? [state.segment.kind, "planned", false];

let scenario;
// Wording for the kind of drone check the incident calls for (disruption.check): a crash check asks
// whether the road is blocked, a traffic check whether it is jammed, a road damage check whether it is impassable.
const check = () => ({ type: "crash", label: "Drone check", bad: "blocked", good: "open", ...scenario?.disruption?.check });
const roadWord = (state) => (state === "blocked" ? check().bad : check().good);
const findingText = (observation) => ({ blocked: `road ${check().bad}`, clear: `road ${check().good}`, inconclusive: "inconclusive" })[observation];
const incidentPlace = () => scenario.disruption.location_label ?? "CA-47 connector";
const riskText = () => `Chance the road is ${check().bad}`;
let baseline = null; // Indicators for the normal scenario, the comparison point.
let activeScenario = "normal";
let modelPick = null; // Top-ranked response, updated after an inspection.
let shipmentFilter = "attention";
let selectedTruck = null;

// ---------------------------------------------------------------------------
// Playback model
// ---------------------------------------------------------------------------
let playback = null;
let rawPlayback = null; // The chosen response as the backend planned it, before stitching.
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
async function fetchPlayback(action, road, scenarioId = activeScenario, inspected = Boolean(observed)) {
  // After a drone check the trucks start late, so the playback has to include the wait.
  const params = new URLSearchParams({ scenario: scenarioId, action, road, ...(inspected ? { inspected: "1" } : {}) });
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

// ---------------------------------------------------------------------------
// Incident runs. The backend plans every response from the start of the
// morning, but the report only arrives at incidentAt(). So until then the map
// plays what it was already showing (the usual plan, or the previous incident's
// response), and the new response takes over at the report. A truck that is
// not where its response puts it by then drives back along the road it came
// on until it meets its response's path, and the rest of its response runs
// that much later: a wait for a fixed time (an appointment, a release, a
// hold) absorbs the delay, otherwise it arrives later.
// ---------------------------------------------------------------------------
const reportTime = (index) => span.start + reports[index].after;
const incidentAt = () => reportTime(Math.max(reportIndex, 0));

function cutPath(path, from, to) {
  if (path.length < 2 || (from <= 0 && to >= 1)) return path;
  const lens = [0];
  for (let i = 1; i < path.length; i += 1) lens.push(lens[i - 1] + metres(path[i - 1], path[i]));
  const total = lens.at(-1) || 1;
  const at = (share) => {
    const d = share * total;
    let i = 1;
    while (i < lens.length - 1 && lens[i] < d) i += 1;
    const f = clamp((d - lens[i - 1]) / ((lens[i] - lens[i - 1]) || 1), 0, 1);
    return [path[i - 1][0] + (path[i][0] - path[i - 1][0]) * f, path[i - 1][1] + (path[i][1] - path[i - 1][1]) * f];
  };
  const inside = path.filter((_, i) => lens[i] > from * total && lens[i] < to * total);
  return [at(from), ...inside, at(to)];
}

// The parts of a track's segments between two times, cutting a drive where it crosses one.
function clipSegments(segments, low, high) {
  return segments.flatMap((segment) => {
    if (segment.t1 <= low || segment.t0 >= high) return [];
    if (segment.t0 >= low && segment.t1 <= high) return [segment];
    const t0 = Math.max(segment.t0, low);
    const t1 = Math.min(segment.t1, high);
    const length = segment.t1 - segment.t0 || 1;
    const [from, to] = [(t0 - segment.t0) / length, (t1 - segment.t0) / length];
    const clipped = { ...segment, t0, t1, path: cutPath(segment.path, from, to) };
    if (segment.distance_m) clipped.distance_m = Math.round(segment.distance_m * (to - from));
    return [clipped];
  });
}

// Points about every 15 m along a track between two times, with the time the truck is there and
// the minutes it has spent driving so far (stops add none).
function trackPoints(segments, low, high) {
  const points = [];
  let driven = 0;
  for (const segment of clipSegments(segments, low, high)) {
    const minutes = segment.t1 - segment.t0;
    if (segment.path.length < 2) {
      points.push({ pos: segment.path[0], t: segment.t0, until: segment.t1, driven });
      continue;
    }
    const lens = [0];
    for (let i = 1; i < segment.path.length; i += 1) lens.push(lens[i - 1] + metres(segment.path[i - 1], segment.path[i]));
    const total = lens.at(-1) || 1;
    for (let i = 1; i < segment.path.length; i += 1) {
      const [a, b] = [segment.path[i - 1], segment.path[i]];
      const steps = Math.max(1, Math.ceil((lens[i] - lens[i - 1]) / 15));
      for (let k = i === 1 ? 0 : 1; k <= steps; k += 1) {
        const share = (lens[i - 1] + ((lens[i] - lens[i - 1]) * k) / steps) / total;
        points.push({ pos: [a[0] + ((b[0] - a[0]) * k) / steps, a[1] + ((b[1] - a[1]) * k) / steps], t: segment.t0 + share * minutes, driven: driven + share * minutes });
      }
    }
    driven += minutes;
  }
  return points;
}

// Where a truck that drove `head` meets its new plan `planned`: a point it passed and a point on the plan
// within 25 m of each other, chosen for the least time off the plan's schedule. A minute of driving back
// counts three times as much as a minute of waiting or delay, turning round adds a minute, and so does
// each 10 m between the two points.
function rejoin(head, planned, at) {
  const now = head.at(-1);
  let best = null;
  for (let i = head.length - 1; i >= 0; i -= 1) {
    const h = head[i];
    const back = now.driven - h.driven;
    if (best && 3 * back >= best.cost) break;
    for (const p of planned) {
      const gap = Math.abs(p.pos[1] - h.pos[1]) > 0.0003 ? Infinity : metres(h.pos, p.pos);
      if (gap > 25) continue;
      const t = clamp(at + back, p.t, p.until ?? p.t);
      const cost = 3 * back + (back > 0 ? 1 : 0) + gap / 10 + Math.abs(at + back - t);
      if (!best || cost < best.cost) best = { index: i, point: { ...p, t }, back, cost };
    }
  }
  return best;
}

// Moves segments `shift` minutes later. A stop that waits for a fixed time absorbs what it can.
function delaySegments(segments, shift) {
  const marks = [];
  const moved = segments.flatMap((segment) => {
    marks.push([segment.t0, shift]);
    const t0 = segment.t0 + shift;
    const waits = segment.path.length < 2 && ["waiting", "idle"].includes(segment.kind);
    const t1 = waits ? Math.max(segment.t1, t0) : segment.t1 + shift;
    shift = t1 - segment.t1;
    return t1 - t0 > 0.001 ? [{ ...segment, t0: Math.round(t0 * 100) / 100, t1: Math.round(t1 * 100) / 100 }] : [];
  });
  const timeOf = (t) => t + (marks.find(([t0]) => t0 >= t - 0.01)?.[1] ?? shift);
  return { segments: moved, timeOf };
}

// A check-in event's wording for the time it now happens, as backend/playback.py words it.
function checkInEvent(event, t) {
  const shipment = scenario.shipments.find((item) => event.text.includes(item.id));
  if (!event.text.startsWith("Checks in") || !shipment) return { ...event, t };
  const [start, end] = shipment.appointment.split("–").map(clockMinutes);
  const head = event.text.slice(0, event.text.indexOf(shipment.id) + shipment.id.length);
  const [note, tone] = t > end ? ["after its appointment window closed", "risk"]
    : t - start > 0.5 ? [`${Math.round(t - start)} min late for its ${clockLabel(start)} appointment`, "watch"]
    : [`on time for its ${clockLabel(start)} appointment`, "ok"];
  return { ...event, t, text: `${head}, ${note}`, tone };
}

const turnBackText = "Turning back to rejoin its new route";

// `previous` is the unstitched response `usual` was built from, if any: a truck it plans
// exactly like `response` keeps the track already on the map.
function stitchPlayback(usual, response, at, previous = null) {
  const usualTracks = new Map(usual.tracks.map((track) => [track.id, track]));
  const previousTracks = new Map((previous?.tracks ?? []).map((track) => [track.id, JSON.stringify(track.segments)]));
  const metresPerMin = scenario.operations.network_speed_mph * 26.8224;
  const kept = new Set();
  const moves = new Map();
  const tracks = response.tracks.map((track) => {
    const before = usualTracks.get(track.id);
    if (!before) return track;
    if (previousTracks.get(track.id) === JSON.stringify(track.segments)) {
      kept.add(track.id);
      return before;
    }
    const head = trackPoints(before.segments, -Infinity, at);
    // The truck may rejoin its plan ahead of schedule, but not past a stop where it loads, unloads or swaps.
    const task = track.segments.find((segment) => segment.t0 >= at && segment.path.length < 2 && !["waiting", "busy", "idle"].includes(segment.kind));
    const planned = trackPoints(track.segments, response.start, Math.min(task?.t0 ?? Infinity, at + 60));
    const join = rejoin(head, planned, at);
    if (!join) return { ...track, segments: [...clipSegments(before.segments, -Infinity, at), ...clipSegments(track.segments, at, Infinity)] };
    const { point, back, index } = join;
    const path = [...head.slice(index).reverse().map((h) => h.pos), point.pos]
      .filter((pos, i, all) => i === 0 || metres(all[i - 1], pos) > 0.5);
    const length = path.slice(1).reduce((sum, pos, i) => sum + metres(path[i], pos), 0);
    const rejoined = at + Math.max(back, length / metresPerMin);
    const tail = clipSegments(track.segments, point.t, Infinity);
    const bridge = [];
    if (path.length > 1) {
      bridge.push({ t0: at, t1: rejoined, kind: back > 0.1 ? "detour" : tail[0]?.kind ?? "driving",
        text: back > 0.1 ? turnBackText : tail[0]?.text ?? "", shipment: tail[0]?.shipment ?? null, path, distance_m: Math.round(length) });
    }
    const end = path.length > 1 ? rejoined : at;
    if (end < point.t) {
      const held = track.segments.find((segment) => segment.kind === "waiting" && segment.path.length < 2 && segment.t0 < point.t && segment.t1 > end);
      bridge.push({ t0: end, t1: point.t, kind: "waiting", text: held?.text ?? "Holding until its new plan resumes", shipment: tail[0]?.shipment ?? null, path: [point.pos] });
    }
    const delayed = delaySegments(tail, Math.max(0, end - point.t));
    moves.set(track.id, { from: point.t, timeOf: delayed.timeOf, turned: back > 0.1 });
    return { ...track, segments: [...clipSegments(before.segments, -Infinity, at), ...bridge, ...delayed.segments] };
  });
  // The two trucks of a load swap start the transfer together: the one there first waits for the other.
  const swaps = tracks.flatMap((track, n) => {
    const i = track.segments.findIndex((segment) => segment.kind === "transfer" && segment.t0 >= at);
    return i > 0 ? [{ n, i, t0: track.segments[i].t0 }] : [];
  });
  const meet = Math.max(...swaps.map((swap) => swap.t0));
  const syncs = new Map();
  if (swaps.length === 2) swaps.filter(({ t0 }) => meet - t0 > 0.01).forEach(({ n, i, t0 }) => {
    const { segments, id } = tracks[n];
    const delayed = delaySegments(segments.slice(i), meet - t0);
    const wait = { ...segments[i], t0, t1: meet, kind: "waiting", text: "Waiting for the other truck to arrive for the swap" };
    tracks[n] = { ...tracks[n], segments: [...segments.slice(0, i), wait, ...delayed.segments] };
    syncs.set(id, { from: t0, timeOf: delayed.timeOf });
  });
  const retime = (event, t) => {
    const sync = syncs.get(event.truck);
    const final = Math.round((sync && t >= sync.from - 0.01 ? sync.timeOf(t) : t) * 100) / 100;
    return Math.abs(final - event.t) > 0.01 ? checkInEvent(event, final) : event;
  };
  const events = [
    ...usual.events.filter((event) => event.t < at || kept.has(event.truck)).map((event) => (event.t < at ? event : retime(event, event.t))),
    ...[...moves].filter(([, move]) => move.turned).map(([truck]) => ({ t: at, truck, text: "Turns back to rejoin its new route", tone: "watch" })),
    ...response.events.flatMap((event) => {
      if (kept.has(event.truck)) return [];
      const move = moves.get(event.truck);
      if (!move) return event.t >= at ? [retime(event, event.t)] : [];
      if (event.t < move.from - 0.01) return [];
      return [retime(event, Math.max(at, move.timeOf(event.t)))];
    }),
  ].sort((a, b) => a.t - b.t);
  const last = Math.max(...tracks.map((track) => track.segments.at(-1).t1));
  return { ...response, end: Math.max(response.end, Math.ceil(last)), tracks, events };
}

async function loadPlayback({ keepTime = false } = {}) {
  const actions = scenario.decision_model.actions.map((a) => a.id);
  let all = await Promise.all(actions.map((id) => fetchPlayback(id, plan.road)));
  const raw = all[actions.indexOf(plan.action)];
  if (scenario.scenario_type === "disruption") {
    const usual = lead?.shown ?? await fetchPlayback("hold-course", "open", activeScenario, false);
    all = all.map((response) => stitchPlayback(usual, response, incidentAt(), lead?.raw));
  }
  rawPlayback = raw;
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
  if (mode === "report") renderReport();
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
  if (scenario.scenario_type !== "disruption") {
    $("#plan-note").textContent = "";
    return;
  }
  $(`#road-${plan.road}`).checked = true;
  $$('input[name="road"]').forEach((input) => { $(`label[for="${input.id}"]`).textContent = roadWord(input.value); });
  const outcome = scenario.decision_model.actions.find((a) => a.id === plan.action)[outcomesKey()][plan.road];
  const row = tallies[plan.action]?.find((item) => item.shipment === affectedShipment().id);
  const late = row ? row.lateness_min : outcome.lateness_min;
  const later = row && row.moved > 0.5
    ? ` It checks in ${Math.round(row.moved)} min later than the estimate, which assumes the response began at ${clockLabel(span.start)}: from the ${clockLabel(incidentAt())} report the truck first drives back or waits to rejoin it (simulated).`
    : "";
  $("#plan-note").textContent = `${affectedShipment().id} arrives ${late > 0 ? `about ${Math.round(late)} min late` : "on time"} in this case.${later}`;
}

function updateFrame() {
  if (!playback) return;
  const slider = $("#timeline");
  slider.value = currentTime;
  slider.style.setProperty("--p", `${((currentTime - span.start) / (span.end - span.start)) * 100}%`);
  slider.setAttribute("aria-valuetext", clockLabel(currentTime));
  $("#clock").textContent = clockLabel(currentTime);
  // The route options, the obstruction and their legend entries appear only once the report is in.
  const live = scenario.scenario_type === "disruption" && currentTime >= incidentAt() - 0.01;
  document.body.dataset.incidentShown = String(live);
  renderer?.setIncidentVisible(live);
  const states = tracks.map((track) => stateAt(track, currentTime));
  renderer?.update(states);
  updateFleetLive(states);
  updateFleetWatch(states);
  updateTruckCard(states);
  updateEventFeed();
  updateTally();
  updateLiveComparison();
  if (step === "monitor") updateMonitorLive(states);
}

function frame(now) {
  if (!playing) return;
  const elapsed = lastFrame ? Math.min(0.1, (now - lastFrame) / 1000) : 0;
  lastFrame = now;
  currentTime = Math.min(span.end, currentTime + elapsed * speed);
  if (awaitingReport() && currentTime >= reportTime(reportIndex + 1)) {
    nextReport();
    return;
  }
  updateFrame();
  if (currentTime < span.end) {
    requestAnimationFrame(frame);
    return;
  }
  setPlaying(false);
  if (mode === "incident" && step === "outcome") showReport();
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
    const [label, tone] = activityFor(state);
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
  const [label, tone] = activityFor(state);
  const next = playback.events.find((event) => event.truck === state.id && event.t > currentTime + 0.01);
  const until = state.segment.kind === "idle" ? "" : ` until ${clockLabel(state.segment.t1)}`;
  const watchText = (selector) => [...(document.querySelector(selector)?.childNodes ?? [])].map((part) => part.textContent.trim()).filter(Boolean).join(" · ") || "—";
  const checkin = watchText(`[data-checkin="${state.id}"]`);
  const risk = watchText(`[data-risk="${state.id}"]`);
  const route = watchText(`[data-route-watch="${state.id}"]`);
  card.hidden = false;
  card.innerHTML = `<div class="truck-card-head"><b>${esc(state.id)}</b>${tag([label, tone])}<button class="link" type="button" data-truck="${esc(state.id)}">Close</button></div>
    <p>${esc(state.segment.text)}${until}.</p>
    ${next ? `<p class="muted">Next, ${clockLabel(next.t)}: ${esc(next.text)}</p>` : ""}
    <p><b>Next check-in:</b> ${esc(checkin)}. <b>Schedule risk:</b> ${esc(risk)}.</p>
    <p class="muted">Route watch: ${esc(route)}.</p>
    <p class="muted">${esc(truck.fuel)}, ${truck.duty_remaining_hr} h duty left at ${clockLabel(playback.start)}</p>`;
}

let feedKey = "";
function updateEventFeed() {
  const relevant = playback.events.filter((event) => !selectedTruck || event.truck === selectedTruck);
  const past = relevant.filter((event) => event.t <= currentTime + 0.01).at(-1);
  const next = relevant.find((event) => event.t > currentTime + 0.01);
  const key = `${selectedTruck}|${past ? past.t + past.truck : ""}|${next?.t}`;
  if (key === feedKey) return;
  feedKey = key;
  const row = (event, upcoming) => `<li class="${upcoming ? "upcoming" : event.tone}"><time>${clockLabel(event.t)}</time><button class="link quiet" type="button" data-truck="${esc(event.truck)}">${esc(shortTruck(event.truck))}</button><span>${upcoming ? "Next: " : ""}${esc(event.text)}</span></li>`;
  // Nothing to show until something has happened, unless a truck is being followed.
  $("#event-feed").innerHTML = !past && !selectedTruck ? "" : [past ? row(past, false) : "", next ? row(next, true) : ""].join("");
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

// After a drone check every response starts late, so its delayed outcomes apply.
const outcomesKey = () => (observed ? "outcomes_after_inspection" : "outcomes");
function outcomeRows(actionId, road, key = outcomesKey()) {
  const action = scenario.decision_model.actions.find((a) => a.id === actionId);
  return action?.[key]?.[road]?.detail?.shipments ?? null;
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
      const [windowStart, windowEnd] = row.appointment.split("–").map(clockMinutes);
      // A truck that had to rejoin its response after a report checks in later than estimated: count it as it plays.
      const moved = t1 - clockMinutes(row.arrival);
      const timing = Math.abs(moved) > 0.5 ? { lateness_min: Math.max(0, t1 - windowStart), constraint_breaches: t1 > (windowEnd ?? windowStart) ? 1 : 0 } : {};
      return { ...row, ...timing, moved, track, t0, t1, d0, d1, breachAt: Math.min(t1, windowEnd ?? windowStart) };
    });
    // Driving back to rejoin a response adds miles, CO₂ and driver time to the truck's next delivery.
    const r = scenario.decision_model.rates;
    pb.tracks.forEach((track) => track.segments.filter((segment) => segment.text === turnBackText).forEach((segment) => {
      const row = tallies[actionId].filter((item) => item.truck === track.id && item.t1 > segment.t0).sort((a, b) => a.t1 - b.t1)[0];
      if (!row) return;
      const miles = segment.distance_m / 1609.344;
      row.miles += miles;
      row.emissions_kg += miles * r.co2_kg_per_mile;
      row.trip_cost += miles * r.cost_per_mile + ((segment.t1 - segment.t0) * r.driver_hourly_rate) / 60;
    }));
  }
  planTotals = totalsAt(outcomeRows("hold-course", "open", "outcomes")?.map((row) => ({ ...row, t0: 0, t1: 1, breachAt: 0 })) ?? [], Infinity);
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
  const normal = scenario.scenario_type === "normal";
  $("#tally-title").textContent = normal ? "Planned trips" : "Trip totals";
  $("#tally-alternatives").hidden = normal;
  const label = actions.find((a) => a.id === plan.action).label;
  const road = scenario.scenario_type === "disruption" ? `, ${incidentPlace()}: ${roadWord(plan.road)}` : "";
  $("#tally-scope").textContent = normal ? `${current.length} modeled shipments on their planned routes · estimated` : `${label}${road}`;
  $("#tally-race-road").textContent = scenario.scenario_type === "disruption" ? `(${incidentPlace()}: ${roadWord(plan.road)})` : "";
  $("#tally").innerHTML = tallyMetrics.map((metric) => `<li class="tally-tile" data-metric="${metric.id}">
    <span class="kpi-label">${metric.label}</span>
    <span class="tally-value"></span>
    <span class="tally-bar" aria-hidden="true"><i></i></span>
    <span class="tally-foot"></span></li>`).join("");
  const best = modelPick?.id;
  $("#tally-race").innerHTML = actions.filter((a) => tallies[a.id]).map((a) => {
    const notes = [a.id === plan.action && "selected", a.id === best && "recommended"].filter(Boolean).join(", ");
    return `<li class="race-row${a.id === plan.action ? " current" : ""}" data-action="${esc(a.id)}">
      <span class="race-name">${esc(a.label)}${notes ? ` <small>${notes}</small>` : ""}</span>
      <span class="race-bar" aria-hidden="true"><i class="ghost"></i><i class="fill"></i></span>
      <span class="race-value"></span>
    </li>`;
  }).join("");
  const r = scenario.decision_model;
  const shipments = [...new Set(current.map((row) => row.shipment))].join(" and ");
  $("#tally-note").textContent = `${shipments}, from ${clockLabel(span.start)} to delivery. Assumed rates: driving $${r.rates.cost_per_mile.toFixed(2)}/mi; driver time $${r.rates.driver_hourly_rate}/h; delay ${money(r.lateness_cost_per_minute)}/min; CO₂ $${r.emissions_cost_per_kg.toFixed(2)}/kg; missed appointment ${money(r.hard_constraint_penalty)}.`;
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
    const normal = scenario.scenario_type === "normal";
    const diff = final[metric.id] - planTotals[metric.id];
    const tone = Math.abs(diff) < 0.05 ? "" : diff > 0 ? "worse" : "better";
    const vsPlan = tone ? `${diff > 0 ? "+" : "−"}${metric.format(Math.abs(diff)).replace(/<[^>]+>/g, "")} vs plan` : "same as plan";
    setHTML(`f-${metric.id}`, tile.querySelector(".tally-foot"), normal
      ? `<span>Estimated at delivery: ${metric.format(final[metric.id]).replace(/<[^>]+>/g, "")}</span>`
      : `<span>Ends at ${metric.format(final[metric.id]).replace(/<[^>]+>/g, "")}</span><span class="${tone}">${vsPlan}</span>`);
    // A missed appointment or the first late minute is a step change worth drawing the eye to.
    if (changed && metric.id === "missed" && now.missed > 0) bump(tile);
    if (metric.id === "late") tile.classList.toggle("alert", now.late > 0);
    if (metric.id === "missed") tile.classList.toggle("alert", now.missed > 0);
  });
  if (scenario.scenario_type === "normal") return;
  const finals = Object.fromEntries(Object.entries(tallies).map(([id, rows]) => [id, totalsAt(rows, Infinity).cost]));
  const top = Math.max(...Object.values(finals), 1);
  $$("#tally-race .race-row").forEach((row) => {
    const id = row.dataset.action;
    const cost = totalsAt(tallies[id], currentTime).cost;
    row.querySelector(".ghost").style.width = `${(finals[id] / top * 100).toFixed(1)}%`;
    row.querySelector(".fill").style.width = `${(cost / top * 100).toFixed(1)}%`;
    setHTML(`race-${id}`, row.querySelector(".race-value"), `${money(cost)} <small>of ${money(finals[id])}</small>`);
  });
}

function bump(node) {
  node.classList.remove("bump");
  void node.offsetWidth;
  node.classList.add("bump");
}

function selectTruck(id) {
  selectedTruck = id;
  if (id) {
    setView("map");
    showMap();
  }
  renderFleet();
  renderTicks();
  renderer?.setSelected(id);
  updateFrame();
}

// ---------------------------------------------------------------------------
// Map renderers. Both draw the same playback: ArcGIS at every width,
// and an SVG schematic when ArcGIS cannot load.
// ---------------------------------------------------------------------------
function visibleLocations() {
  return scenario.locations
    .filter((location) => location.kind !== "incident" || scenario.scenario_type === "disruption")
    .map((location) => location.kind === "incident" ? { ...location, name: incidentPlace() } : location);
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
    setIncidentVisible() {}, // CSS hides .alt and .place.incident until body[data-incident-shown="true"].
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
  let timer;
  await Promise.race([
    viewElement.viewOnReady(),
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("ArcGIS view did not load in 10 s")), 10000); }),
  ]).finally(() => clearTimeout(timer));
  // Mute the OpenStreetMap tiles so routes and trucks stand out.
  viewElement.map.basemap?.baseLayers.forEach((layer) => {
    layer.effect = "grayscale(0.85) brightness(1.06) contrast(0.82)";
  });
  viewElement.view.constraints = { minZoom: 10, maxZoom: 18, snapToZoom: false };

  const layers = {
    alternatives: new GraphicsLayer({ title: "Route options" }),
    trails: new GraphicsLayer({ title: "Truck routes" }),
    places: new GraphicsLayer({ title: "Terminals and warehouses" }),
    incident: new GraphicsLayer({ title: "Reported incident", visible: false }),
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
    setIncidentVisible(visible) {
      if (layers.alternatives.visible !== visible) layers.alternatives.visible = visible;
      if (layers.incident.visible !== visible) layers.incident.visible = visible;
    },
    setScenario() {
      layers.places.removeAll();
      layers.incident.removeAll();
      const style = {
        terminal: { type: "simple-marker", style: "square", size: 11, color: colors.ink, outline: { color: "#ffffff", width: 2 } },
        warehouse: { type: "simple-marker", style: "circle", size: 11, color: "#ffffff", outline: { color: colors.muted, width: 2.5 } },
        incident: { type: "simple-marker", style: "circle", size: 18, color: rgba(colors.risk, 0.9), outline: { color: "#ffffff", width: 2.5 } },
      };
      visibleLocations().forEach((location) => {
        const geometry = { type: "point", longitude: location.lon, latitude: location.lat };
        const layer = location.kind === "incident" ? layers.incident : layers.places;
        layer.add(new Graphic({ geometry, symbol: style[location.kind] }));
        if (location.kind === "incident") {
          layer.add(new Graphic({ geometry, symbol: { type: "text", text: "!", color: "#ffffff", yoffset: -4, font: { size: 12, weight: "bold" } } }));
        }
        layer.add(new Graphic({
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
    try {
      renderer = await createArcGISRenderer();
      $("#map-fallback").classList.add("hidden");
      $("#arcgis-map").classList.remove("hidden");
      $("#map-provider").textContent = "OpenStreetMap · OSRM routes · Simulated trucks";
    } catch (error) {
      console.warn("Using the schematic map because ArcGIS failed to load:", error);
      renderer = createSchematicRenderer();
      showSchematic("Map unavailable. Showing a schematic.");
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
  $("#map-provider").textContent = `${message} Simulated trucks.`;
}

function affectedShipment() {
  return scenario.shipments.find((item) => item.id === scenario.disruption.affected_shipment);
}

function renderDecision() {
  const d = scenario.disruption;
  const affected = affectedShipment();
  $("#situation-eyebrow").textContent = scenario.scenario_type === "disruption" ? `Incident · ${check().label.toLowerCase()}` : "All clear";
  $("#risk-label").textContent = riskText();
  $$("[data-observation]").forEach((button) => { button.textContent = { blocked: `Road ${check().bad}`, clear: `Road ${check().good}`, inconclusive: "Inconclusive" }[button.dataset.observation]; });
  $("#affected-label").textContent = scenario.scenario_type === "disruption" ? "Affected shipment" : "Tightest delivery";
  const when = scenario.scenario_type === "disruption" ? "Reported" : "As of";
  $("#reported-at").textContent = d.reported_at ? ` · ${when} ${reportClock(d.reported_at)}` : "";
  $("#disruption-title").textContent = d.title;
  $("#disruption-description").textContent = d.description;
  $("#incident-place").textContent = `${d.evidence_summary ?? "No incident reports"} · Synthetic`;
  $("#inspection-limits").textContent = d.inspection_limits ?? "";
  $("#inspection-limits").hidden = !$("#inspection-limits").textContent;
  $("#response-limits").textContent = check().type === "environment" ? d.inspection_limits ?? "" : "";
  $("#response-limits").hidden = !$("#response-limits").textContent;
  $("#incident-stop-label").textContent = check().type === "congestion" ? "In queue" : check().type === "environment" ? "At water hazard" : "At closure";
  $("#road-case-label").textContent = `Simulate ${incidentPlace()} as`;
  $("#constraints").innerHTML = d.constraints.map((item) => `<li>${esc(item)}</li>`).join("");
  $("#risk-value").innerHTML = d.web_evidence
    ? `${pct(d.prior_blocked_probability)}<small>estimated · was ${pct(d.web_evidence.prior_before)} (assumed) before the web check</small>`
    : `${pct(d.prior_blocked_probability)}<small>assumed</small>`;
  $("#affected").innerHTML = `${esc(affected.id)}<small>${esc(affected.cargo)}, ${affected.slack_min} min slack</small>`;
  $("#deadline").innerHTML = reportShift()
    ? `${reportClock(d.decision_deadline)}<small>synthetic · moved with the simulated report time</small>`
    : esc(d.decision_deadline);
}

// The config gives each incident report a time; in the scripted morning a report can arrive later,
// so its report time and deadline move to the simulated clock.
const minutesOf = (text) => {
  const [hours, minutes] = text.split(":").map(Number);
  return hours * 60 + minutes;
};
function reportShift() {
  const d = scenario.disruption;
  if (scenario.scenario_type !== "disruption" || !d.reported_at) return 0;
  return Math.round(incidentAt()) - minutesOf(d.reported_at);
}
const reportClock = (text) => clockLabel(minutesOf(text) + reportShift());

// Indicator tiles. Normal flow is the baseline the changed condition is compared with.
const higherIsBetter = new Set(["appointments_feasible"]);
const basisLabel = { stub: "Not yet calculated", estimated: "Estimated", simulated: "Simulated", measured: "Measured" };
function indicatorNumber(item, value) {
  if (item.unit === "mi") return value.toFixed(1);
  return whole(value);
}
function indicatorLabel(item) {
  return esc(item.label.replace(/^Estimated /, "").replace("CO2", "CO₂"));
}
function indicatorText(item, value) {
  const of = item.of !== undefined ? ` of ${whole(item.of)}` : "";
  const unit = ["min", "mi", "kg"].includes(item.unit) ? ` ${item.unit}` : "";
  return `${indicatorNumber(item, value)}${of}${unit}`;
}
// Change against the usual morning: { amount, tone, arrow }, or null when the shown values match.
function indicatorChange(item, base) {
  const diff = item.value - base.value;
  const shown = indicatorNumber(item, Math.abs(diff));
  if (Number(shown) === 0) return null;
  const worse = (diff > 0) !== higherIsBetter.has(item.id);
  return { amount: `${diff > 0 ? "+" : "−"}${shown}`, tone: worse ? "worse" : "better", arrow: diff > 0 ? "▲" : "▼" };
}
const shortLabels = { appointments_feasible: "Appointments within reach", community_exposure: "Schools near routes" };
const comparisonIndicators = new Set(["expected_delay_min", "co2_kg"]);
function wholePlanLiveMetrics(t) {
  const loaded = playback.tracks.flatMap((track) => track.segments)
    .filter((segment) => segment.shipment && ["driving", "detour"].includes(segment.kind) && segment.distance_m > 0);
  const totalDistance = loaded.reduce((sum, segment) => sum + segment.distance_m, 0);
  const drivenDistance = loaded.reduce((sum, segment) => sum + segment.distance_m * clamp((t - segment.t0) / (segment.t1 - segment.t0), 0, 1), 0);
  const progress = totalDistance ? clamp(drivenDistance / totalDistance, 0, 1) : 0;
  const planCo2 = scenario.analysis.indicators.find((item) => item.id === "co2_kg")?.value;
  const checkins = playback.events.filter((event) => event.text.startsWith("Checks in") && event.t <= t + 0.01);
  const delay = checkins.reduce((sum, event) => {
    const shipment = scenario.shipments.find((item) => event.text.includes(item.id));
    return shipment ? sum + Math.max(0, event.t - clockMinutes(shipment.appointment.split("–")[0])) : sum;
  }, 0);
  return { delay, completed: checkins.length, co2: planCo2 == null ? null : planCo2 * progress, progress };
}

function updateLiveComparison() {
  if (scenario.scenario_type !== "normal" || !playback) return;
  const live = wholePlanLiveMetrics(currentTime);
  $("#live-delay").textContent = `${live.delay.toFixed(1)} min`;
  $("#live-co2").textContent = live.co2 == null ? "—" : `${live.co2.toFixed(1)} kg`;
  $("#live-delay-progress").textContent = `${live.completed} of ${scenario.shipments.length} check-ins`;
  $("#live-co2-progress").textContent = `${Math.round(live.progress * 100)}% of estimated trip distance`;
}

function renderIndicators(indicators) {
  const compare = scenario.scenario_type !== "normal" && baseline;
  const missing = indicators.filter((item) => item.value === null || item.value === undefined);
  const pending = missing.map((item) => ` ${shortLabels[item.id] ?? indicatorLabel(item)}: not yet calculated.`).join("");
  $("#indicators-note").textContent = scenario.scenario_type === "normal"
    ? `End values are estimates. Live delay comes from completed simulated check-ins; live CO₂ allocates the whole-plan estimate by loaded trip progress.${pending}`
    : `Estimates before inspection.${activeScenario === "congestion" ? " Cargo released earlier than baseline." : ""}${pending}`;
  $("#comparison-caption").textContent = scenario.scenario_type === "normal"
    ? "Normal morning · live progress" : `Changed scenario · ${scenario.name}`;
  $("#comparison-base-head").textContent = scenario.scenario_type === "normal" ? "Estimated at end" : "Normal";
  $("#comparison-current-head").textContent = scenario.scenario_type === "normal" ? "Simulated so far" : "Current";
  $("#comparison-change-head").textContent = scenario.scenario_type === "normal" ? "Progress" : "Change";
  $("#scenario-comparison").innerHTML = indicators.filter((item) => comparisonIndicators.has(item.id)).map((item) => {
    if (scenario.scenario_type === "normal") {
      const delay = item.id === "expected_delay_min";
      const expected = item.value == null ? "—" : `${item.value.toFixed(1)} ${item.unit}`;
      return `<tr><th scope="row">${delay ? "Delivery delay" : "CO₂"}</th><td class="num">${expected}<small>estimated</small></td><td class="num"><b id="live-${delay ? "delay" : "co2"}">—</b><small>${delay ? "simulated" : "estimated · simulated progress"}</small></td><td id="live-${delay ? "delay" : "co2"}-progress" class="num">—</td></tr>`;
    }
    const base = baseline?.find((entry) => entry.id === item.id);
    const available = item.value != null && base?.value != null;
    const change = available ? indicatorChange(item, base) : null;
    return `<tr><th scope="row">${indicatorLabel(item)}</th><td class="num">${base?.value == null ? "—" : indicatorText(item, base.value)}</td><td class="num"><b>${item.value == null ? "—" : indicatorText(item, item.value)}</b></td><td class="num ${change?.tone ?? ""}">${available ? change ? `${change.amount} ${esc(item.unit)}` : "Same" : "—"}</td></tr>`;
  }).join("");
  $("#indicators").innerHTML = indicators.filter((item) => !missing.includes(item) && !comparisonIndicators.has(item.id)).map((item) => {
    const label = shortLabels[item.id] ?? indicatorLabel(item);
    const basis = basisLabel[item.basis] ?? item.basis;
    const title = `title="${indicatorLabel(item)} · ${esc(basis)}"`;
    const of = item.of !== undefined ? ` of ${whole(item.of)}` : "";
    // Count units repeat the label, so only physical units are printed.
    const unit = ["min", "mi", "kg"].includes(item.unit) ? ` <small>${esc(item.unit)}</small>` : "";
    let delta = '<span class="stat-delta">Usual morning</span>';
    const base = compare && baseline.find((b) => b.id === item.id);
    if (item.basis !== "estimated") {
      delta = `<span class="stat-delta">${esc(basis)}</span>`;
    } else if (base && base.value !== null) {
      const change = indicatorChange(item, base);
      delta = change
        ? `<span class="stat-delta ${change.tone}"><span aria-hidden="true">${change.arrow}</span> ${change.amount} vs usual</span>`
        : '<span class="stat-delta">Same as usual</span>';
    }
    return `<li class="stat" ${title}><span class="stat-label">${label}</span><b class="stat-value">${indicatorNumber(item, item.value)}${of}${unit}</b>${delta}</li>`;
  }).join("");
}

let comparisonOptions = [];
let comparisonProbability = 0;
let communitySchools = null;

function updateResponseComparison() {
  renderResponseComparison($("#response-comparison-body"), scenario, comparisonOptions, comparisonProbability, plan.action, communitySchools);
  $("#comparison-context").textContent = `${observed ? "After drone check" : "Before drone check"} · ${scenario.name} · Estimated`;
}

async function openResponseComparison() {
  if (!communitySchools) {
    const response = await fetch("/community-schools.json");
    if (response.ok) communitySchools = await response.json();
  }
  updateResponseComparison();
  $("#response-comparison").showModal();
}

function renderRankings(items, observation = null, blocked = scenario.disruption.prior_blocked_probability) {
  comparisonOptions = items;
  comparisonProbability = blocked;
  $("#ranking-state").textContent = `${observation ? "After inspection" : "Before inspection"} · Estimated`;
  $("#response-why").textContent = responseExplanation(items);
  $("#rankings").innerHTML = items.map((item) => {
    const e = item.expected;
    const id = `response-${esc(item.id)}`;
    return `<li class="option${item.rank === 1 ? " top" : ""}">
      <input type="radio" name="response" id="${id}" value="${esc(item.id)}"${item.id === plan.action ? " checked" : ""}>
      <label for="${id}">
        <span class="option-head">
          <span class="rank">${item.rank}</span>
          <b class="option-name">${esc(item.label)}</b>
          ${item.rank === 1 ? '<span class="tag pick">Recommended</span>' : ""}
          <span class="option-loss"><b>${money(item.expected_loss)}</b><small>expected loss</small></span>
        </span>
        <span class="option-summary">${esc(item.summary)}</span>
        ${outcomeCases(item, blocked, scenario.disruption.affected_shipment, check())}
        <span class="option-more">${esc(item.stakeholder_effect)}${schoolsNote(item)}</span>
        <span class="option-metrics"><span><small>Delay</small>${whole(e.lateness_min)} min</span><span><small>Direct cost</small>${money(e.incremental_cost)}</span><span><small>CO₂</small>${whole(e.emissions_kg)} kg</span><span><small>Missed appts</small>${e.constraint_breaches.toFixed(1)}</span></span>
      </label>
    </li>`;
  }).join("");
  renderConfirm();
  if ($("#response-comparison").open) updateResponseComparison();
}

// Fairness measure for a response: schools within 300 m of the route it sends the affected load on.
function schoolsNote(item) {
  const n = item.schools_within_300m;
  if (n === null || n === undefined) return "";
  return ` ${n} ${n === 1 ? "school" : "schools"} within 300 m (OSM estimate).`;
}

function actionLabel(id) {
  return scenario.decision_model.actions.find((a) => a.id === id)?.label ?? id;
}

// Choosing a response the model did not rank first is an override, and needs a reason for the decision log.
function renderConfirm() {
  const override = Boolean(modelPick) && plan.action !== modelPick.id;
  $("#override-reason-wrap").hidden = !override;
  $("#confirm-button").textContent = override ? "Confirm other response" : "Confirm response";
  $("#confirm-button").disabled = override && !$("#override-reason").value.trim();
}

function renderEvoi(analysis) {
  const e = analysis.evoi;
  const best = analysis.before[0];
  $("#recommendation").textContent = inspectionSentence(e);
  $("#inspection-choices").textContent = inspectionChoices(e, check());
  $("#recommendation-short").innerHTML = e.recommend_inspection
    ? "Check first"
    : `Skip the check<small>Recommended: ${esc(best.label)}</small>`;
  $("#act-loss").textContent = money(e.best_act_now_loss);
  $("#evidence-loss").textContent = money(e.expected_loss_after_inspection);
  $("#inspection-cost").textContent = money(e.inspection_cost);
  $("#net-value").textContent = money(e.net_value_of_information);
  $("#inspect-button").classList.toggle("primary", e.recommend_inspection);
  $("#skip-inspection").classList.toggle("primary", !e.recommend_inspection);
  $("#inspect-button").textContent = "Run drone check";
  $("#cv-only").textContent = accuracyNote(e);
  renderSensitivity($("#sensitivity"), analysis, check());
  const a = e.assumptions;
  $("#assumptions").innerHTML = [
    ["Assumed accuracy", `${pct(a.inspection_accuracy)} (${esc(a.accuracy_basis)})`],
    ["Assumed check time", `${a.launch_and_review_minutes} min`],
    [`Estimated chance of a “${esc(check().bad)}” result`, pct(a.signal_blocked_probability)],
  ].map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join("");
  // LLM-written brief from the engine's numbers; the normal morning has no incident step.
  if (activeScenario === "normal") clearBrief($("#brief-incident"));
  else renderBrief($("#brief-incident"), activeScenario);
}

function renderShipments() {
  const attention = scenario.shipments.filter((item) => attentionStatuses.has(item.status));
  const visible = shipmentFilter === "all" ? scenario.shipments : attention;
  $("#shipment-count").textContent = scenario.shipments.length;
  $("#shipment-summary").textContent = `${scenario.shipments.length} active, ${attention.length} need attention`;
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
  $("#fleet-count").textContent = scenario.trucks.length;
  $("#fleet-summary").textContent = `${scenario.trucks.length} trucks, ${electric} battery electric`;
  $("#fleet-list").innerHTML = scenario.trucks.map((truck) => `<tr class="${truck.id === selectedTruck ? "selected" : ""}">
    <td><button class="link" type="button" data-truck="${esc(truck.id)}" aria-pressed="${truck.id === selectedTruck}">${esc(truck.id)}</button><span class="detail">${esc(truck.assignment)}, ${esc(truck.fuel.toLowerCase())}</span></td>
    <td class="live" data-live="${esc(truck.id)}"></td>
    <td data-checkin="${esc(truck.id)}"></td>
    <td data-risk="${esc(truck.id)}"></td>
    <td data-route-watch="${esc(truck.id)}"></td>
    <td class="num">${truck.duty_remaining_hr} h</td>
  </tr>`).join("");
  liveKeys.clear();
  fleetWatchKey = "";
}

let fleetWatchKey = "";
function updateFleetWatch(states) {
  if (!playback || !scenario) return;
  const key = `${activeScenario}|${plan.action}|${plan.road}|${Math.floor(currentTime)}`;
  if (key === fleetWatchKey) return;
  fleetWatchKey = key;
  const routes = Object.fromEntries(scenario.routes.map((route) => [route.id, route]));
  const fallback = scenario.decision_model.blockage?.fallback_route;
  const watched = [];
  for (const state of states) {
    const next = playback.events.filter((event) => event.truck === state.id && event.t > currentTime + 0.01 && event.text.startsWith("Checks in"))
      .map((event) => ({ event, shipment: scenario.shipments.find((shipment) => event.text.includes(shipment.id)) }))
      .find(({ shipment }) => shipment);
    const checkin = document.querySelector(`[data-checkin="${state.id}"]`);
    const riskCell = document.querySelector(`[data-risk="${state.id}"]`);
    const routeCell = document.querySelector(`[data-route-watch="${state.id}"]`);
    if (!checkin || !riskCell || !routeCell) continue;
    if (!next) {
      checkin.textContent = "Deliveries complete";
      riskCell.textContent = "—";
      routeCell.textContent = "—";
      continue;
    }
    const { event, shipment } = next;
    const appointmentStart = clockMinutes(shipment.appointment.split("–")[0]);
    const buffer = appointmentStart - event.t;
    const remaining = Math.max(0, event.t - currentTime);
    const delayRange = Math.min(30, remaining * 0.25);
    const chance = buffer < 0 ? 100 : delayRange > 0 ? Math.round(clamp((delayRange - buffer) / delayRange, 0, 1) * 100) : 0;
    const routeId = plan.action === "reroute" && shipment.id === scenario.disruption.affected_shipment ? fallback : shipment.route;
    const route = routes[routeId];
    const activeDetour = routeId === fallback && shipment.route !== fallback;
    const canDetour = routeId === scenario.decision_model.blockage?.route && routes[fallback];
    checkin.innerHTML = `${clockLabel(event.t)}<span class="detail">${esc(shipment.id)} · ${Math.max(0, Math.round(remaining))} min away</span>`;
    riskCell.innerHTML = `<span class="status ${chance >= 50 ? "risk" : chance >= 20 ? "watch" : "ok"}">${chance}% assumed</span><span class="detail">${buffer >= 0 ? `${Math.round(buffer)} min before` : `${Math.round(-buffer)} min after`} appointment start</span>`;
    const routeNote = activeDetour ? "Detour in simulated plan" : canDetour ? `${esc(routes[fallback].name)} · contingency only` : "";
    routeCell.innerHTML = `${esc(route?.name ?? routeId)}${routeNote ? `<span class="detail">${routeNote}</span>` : ""}`;
    if (chance >= 20) watched.push({ truck: state.id, shipment: shipment.id, chance, buffer });
  }
  if (step === "monitor") {
    watched.sort((a, b) => b.chance - a.chance);
    $("#monitor-alerts").innerHTML = watched.length
      ? `<h2>Schedule watch <small>illustrative · assumed</small></h2><ul>${watched.slice(0, 3).map((item) => `<li><button class="link" type="button" data-truck="${esc(item.truck)}">${esc(shortTruck(item.truck))}</button> · ${esc(item.shipment)}: ${item.chance}% chance of arriving after the appointment starts (${item.buffer >= 0 ? `${Math.round(item.buffer)} min buffer` : `${Math.round(-item.buffer)} min past start`}).</li>`).join("")}</ul>`
      : '<h2>Schedule watch</h2><p>No modeled trip is near its appointment start.</p>';
  }
}

function renderConditions() {
  const o = scenario.operations;
  $("#conditions").textContent = `Simulated conditions: ${o.network_speed_mph} mph average; ${o.terminal_turn_time_min} min terminal turn time. ${o.weather.replaceAll(" · ", ", ")}.`;
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
    ["Needs attention", "A shipment at risk, under watch, or without a truck."],
    ["Slack", "Time between arrival and the start of the appointment window."],
    ["Road risk", `Assumed chance the road at ${incidentPlace()} is ${check().bad}. Updated with Bayes’ rule after inspection. A slowdown adds time; a closure requires turning back and detouring.`],
    ["Missed appointment", "Arrival after the appointment window closes. A constraint breach can also mean another hard rule was broken."],
    ["Expected loss", `Average cost across the possible road conditions. Assumed rates: delay ${money(m.lateness_cost_per_minute)}/min, CO₂ $${m.emissions_cost_per_kg.toFixed(2)}/kg, missed appointment ${money(m.hard_constraint_penalty)}, plus driving costs.`],
    ["Inspection value", "Expected loss without inspection minus expected loss with inspection, including its cost and wait. A positive value favors inspection."],
  ];
  $("#definitions").innerHTML = definitions.map(([term, text]) => `<dt>${term}</dt><dd>${esc(text)}</dd>`).join("");
}

// ---------------------------------------------------------------------------
// Drone check. The simulated buttons and a reviewed clip share showObservation.
// ---------------------------------------------------------------------------
let observed = null; // The applied observation, or null without a drone check.
let dialogOpen = false;

function resetObservation() {
  resetInspection();
  observed = null;
  $("#selected-evidence").hidden = true;
  $("#observation-buttons").hidden = true;
  $$("[data-observation]").forEach((button) => button.setAttribute("aria-pressed", "false"));
  $("#inspect-button").disabled = false;
  $("#reinspect-button").textContent = "Review footage";
  $("#override-reason").value = "";
  clearBrief($("#brief-respond"));
  resetAsk();
  $("#posterior").textContent = `No inspection · ${incidentPlace()}. Assumed ${riskText().toLowerCase()}: ${pct(scenario.disruption.prior_blocked_probability)}.`;
}

// Opens the footage dialog, or falls back to simulated results when there is no library.
async function startInspection() {
  if (await openInspection(activeScenario, check())) return;
  setStep("incident");
  $("#observation-buttons").hidden = false;
  $("#inspect-button").textContent = "Choose a result";
  $("#inspect-button").disabled = true;
}

async function observe(observation) {
  const response = await fetch(`/api/analysis?${withWeb(new URLSearchParams({ scenario: activeScenario, observation }))}`);
  if (!response.ok) throw new Error("The analysis request failed");
  await showObservation(await response.json());
}

async function showObservation(analysis, summary = null) {
  const observation = analysis.observation;
  observed = observation;
  $$("[data-observation]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.observation === observation)));
  const prior = pct(analysis.prior_blocked_probability);
  const posterior = pct(analysis.posterior_blocked_probability);
  const best = analysis.after[0].label;
  modelPick = analysis.after[0];
  const finding = findingText(observation);
  const bad = check().bad;
  // The check took time: move the clock to when its result came in.
  currentTime = Math.max(currentTime, span.start + scenario.decision_model.drone.launch_and_review_minutes);
  logDecision(`${check().label} at ${incidentPlace()}: ${finding}. ${riskText()} ${prior} → ${posterior}. Best response: ${best}.`);
  $("#posterior").textContent = summary ?? {
    blocked: `Finding: road ${bad}. Estimated ${riskText().toLowerCase()}: ${posterior} (was ${prior}).`,
    clear: `Finding: road ${check().good}. Estimated ${riskText().toLowerCase()}: ${posterior} (was ${prior}).`,
    inconclusive: `Inconclusive. Assumed ${riskText().toLowerCase()}: ${prior}, unchanged.`,
  }[observation];
  $("#risk-value").innerHTML = `${posterior}<small>estimated · ${posterior === prior ? "unchanged" : `was ${prior}`}</small>`;
  $("#recommendation-short").innerHTML = `${esc(finding)}<small>Recommended: ${esc(best)}</small>`;
  renderSensitivity($("#sensitivity"), analysis, check());
  renderBrief($("#brief-respond"), activeScenario, observation);
  // Play the map out with the new best option and the now more likely road state.
  plan = { action: analysis.after[0].id, road: analysis.posterior_blocked_probability >= 0.5 ? "blocked" : "open" };
  renderRankings(analysis.after, observation, analysis.posterior_blocked_probability);
  setStep("respond");
  await loadPlayback({ keepTime: true });
}

// Overlapping loads (a fast scenario switch, or a web finding applied mid-switch) must not mix
// variants, so each load gets a number and only the latest one renders. Returns false when superseded.
let scenarioRequest = 0;

async function loadScenario(id, { keepTime = false } = {}) {
  const mine = ++scenarioRequest;
  const stale = () => mine !== scenarioRequest;
  activeScenario = id;
  selectedTruck = null;
  setPlaying(false);
  const response = await fetch(`/api/scenario?${withWeb(new URLSearchParams({ scenario: id }))}`);
  if (stale()) return false;
  if (!response.ok) throw new Error("The scenario failed to load");
  const data = await response.json();
  if (stale()) return false;
  scenario = data;
  if (!baseline) {
    const base = await fetch("/api/analysis?scenario=normal&observation=inconclusive");
    const indicators = base.ok ? (await base.json()).indicators : null;
    baseline ??= indicators;
    if (stale()) return false;
  }
  document.body.dataset.scenario = scenario.scenario_type;
  $("#drone-note").textContent = "Stock footage. Inspection accuracy and review time are assumed.";
  plan = {
    action: scenario.analysis.before[0].id,
    road: scenario.disruption.prior_blocked_probability >= 0.5 ? "blocked" : "open",
  };
  modelPick = scenario.analysis.before[0];
  if (scenario.scenario_type === "disruption" && reports[reportIndex]?.id === id) {
    incidentPlans[reportIndex] = { label: reports[reportIndex].label, title: scenario.disruption.title, at: incidentAt(), indicators: scenario.analysis.indicators };
  }
  furthest = 0;
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
  await loadPlayback({ keepTime });
  if (stale()) return false;
  await drawMap();
  if (stale()) return false;
  renderMode();
  return true;
}

// ---------------------------------------------------------------------------
// Flow. One scripted morning: normal operations, then each report in
// `reports` arrives at its simulated time. For each one the dispatcher
// decides on a drone check, chooses a response and watches it play out
// until the next report. The shift report closes the morning.
// ---------------------------------------------------------------------------
const reports = [
  { id: "disruption", after: 2, label: "Crash" }, // Minutes after the start of the morning (simulated).
  { id: "flood", after: 8, label: "Water" },
];
const steps = ["start", "incident", "respond", "outcome"]; // "monitor" is outside the stepper.
let mode = "monitoring";
let step = "start";
let furthest = 0; // Index in steps of the furthest step reached for this report.
let reportIndex = -1; // Index in reports of the incident on screen; -1 before the first report.
let lead = null; // From the latest report: { shown, raw }, the playback on the map then, stitched and as planned.
let incidentPlans = []; // Per report reached: its whole-plan indicators before inspection, for the shift report.
let decisionLog = [];

function logDecision(text) {
  decisionLog.push({ t: currentTime, text });
}

function renderMode() {
  document.body.dataset.mode = mode;
  const atRisk = scenario.analysis.indicators.find((item) => item.id === "shipments_at_risk")?.value ?? 0;
  const unassigned = scenario.shipments.filter((item) => item.truck === "Unassigned").length;
  const deliveries = (n) => `${n} ${n === 1 ? "delivery" : "deliveries"}`;
  const [text, tone] = mode === "report" ? ["Shift complete", "neutral"]
    : mode === "incident" ? [atRisk ? `Incident · ${deliveries(atRisk)} at risk` : "Incident · arrival buffer available", atRisk ? "risk" : "watch"]
    : atRisk ? [`${deliveries(atRisk)} at risk`, "watch"]
    : unassigned ? [`Deliveries on track · ${unassigned} load${unassigned === 1 ? "" : "s"} without a truck`, "watch"] : ["All deliveries on track", "ok"];
  $("#ops-status").textContent = text;
  $("#ops-status").className = `ops-status ${tone}`;
  renderStepper();
  renderOutcome();
}

// Below 1000px the page scrolls, with the map on top and the guide under it.
const stacked = () => window.matchMedia("(max-width: 999.98px)").matches;
function showMap() {
  if (stacked()) window.scrollTo({ top: 0, behavior: "smooth" });
}
function showGuide() {
  if (stacked()) $(".panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

function setStep(name) {
  step = name;
  furthest = Math.max(furthest, steps.indexOf(name));
  document.body.dataset.step = name;
  $$(".step[data-step]").forEach((section) => { section.hidden = section.dataset.step !== name; });
  $(".panel-body").scrollTop = 0;
  if (name === "start") renderStart();
  if (name === "monitor") renderMonitor();
  if (name === "respond") renderConfirm();
  if (name === "incident" || name === "respond") showGuide();
  renderMode();
}

function setView(name) {
  document.body.dataset.view = name;
  $$("button[data-view]").forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.view === name)));
  $$(".view").forEach((panel) => { panel.hidden = panel.id !== `view-${name}`; });
}

function setSpeed(value) {
  speed = value;
  $("#playback-speed").value = String(value);
}

function renderStepper() {
  const at = steps.indexOf(step);
  const states = {
    incident: at === 1 && !dialogOpen ? "current" : at >= 1 ? "done" : "pending",
    inspect: dialogOpen ? "current" : observed ? "done" : at >= 2 ? "skipped" : "pending",
    respond: at === 2 ? "current" : at > 2 ? "done" : "pending",
    outcome: at === 3 ? (mode === "report" ? "done" : "current") : "pending",
  };
  const reached = { incident: furthest >= 1, inspect: furthest >= 1, respond: furthest >= 2, outcome: furthest >= 3 };
  $$("#stepper [data-goto]").forEach((button) => {
    const key = button.dataset.goto;
    button.closest("li").dataset.state = states[key];
    button.disabled = step === "start" || !reached[key];
    if (states[key] === "current") button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  });
}

function renderStart() {
  $("#start-button").disabled = false;
  $("#start-note").textContent = "";
}

// Before the first report: nothing to decide, so the guide shows what PigeonOps is watching.
function renderMonitor() {
  const a = scenario.analysis;
  const feasible = a.indicators.find((item) => item.id === "appointments_feasible");
  $("#monitor-feasible").innerHTML = feasible ? `${whole(feasible.value)} of ${whole(feasible.of)}<small>estimated</small>` : "—";
  const totalCo2 = a.indicators.find((item) => item.id === "co2_kg");
  const schools = a.indicators.find((item) => item.id === "community_exposure");
  $("#monitor-environment").textContent = `End estimate: ${totalCo2?.value == null ? "CO₂ unavailable" : `${totalCo2.value.toFixed(1)} kg CO₂`}; ${schools?.value == null ? "school proximity unavailable" : `${whole(schools.value)} schools within 300 m of planned routes (OSM estimate)`}. Live CO₂ follows the simulated progress of all assigned loads.`;
  const unassigned = scenario.shipments.filter((item) => item.truck === "Unassigned").length;
  $("#monitor-note").textContent = unassigned ? `${unassigned} load${unassigned === 1 ? " needs" : "s need"} a truck.` : "";
  fleetWatchKey = "";
  if (tracks.length) updateFleetWatch(tracks.map((track) => stateAt(track, currentTime)));
  if (tracks.length) updateMonitorLive(tracks.map((track) => stateAt(track, currentTime)));
}

function updateMonitorLive(states) {
  const moving = states.filter((state) => state.moving).length;
  $("#monitor-moving").innerHTML = `${moving} of ${states.length}<small>simulated positions</small>`;
  const co2 = wholePlanLiveMetrics(currentTime).co2;
  $("#monitor-co2").innerHTML = co2 === null ? "—" : `${co2.toFixed(1)} kg<small>estimated · simulated progress</small>`;
}

function renderOutcome() {
  const report = mode === "report";
  $("#report").hidden = !report;
  $("#outcome-title").textContent = report ? "Shift report" : actionLabel(plan.action);
  if (!report) $("#outcome-sub").textContent = "";
  $("#skip-end").hidden = report;
  $("#skip-end").textContent = reports[reportIndex + 1] ? "Skip to next report" : "View shift report";
  $("#change-response").hidden = report;
  $("#report-restart").hidden = !report;
}

// True while the map plays toward the next report: before the first one, or after a confirmed response.
const awaitingReport = () => Boolean(reports[reportIndex + 1]) && (step === "monitor" || (mode === "incident" && step === "outcome"));

function nextReport() {
  setPlaying(false);
  currentTime = reportTime(reportIndex + 1);
  updateFrame();
  triggerIncident().catch((error) => console.error(error));
}

async function triggerIncident() {
  reportIndex += 1;
  // Each report starts from what the map shows now, so the trucks stay in place up to the report.
  lead = { shown: playback, raw: rawPlayback };
  mode = "incident";
  setAppliedWeb(null);
  clearTriage($("#web-triage"));
  if (!(await loadScenario(reports[reportIndex].id, { keepTime: true }))) return;
  const d = scenario.disruption;
  logDecision(`${d.title}. Reported ${clockLabel(incidentAt())} (simulated). ${d.evidence_summary}. Synthetic scenario.`);
  logDecision(`${inspectionSentence(scenario.analysis.evoi)} ${inspectionChoices(scenario.analysis.evoi, check())}`);
  setStep("incident");
  renderTriage($("#web-triage"), activeScenario, { onApply: applyWebFinding });
}

// The dispatcher used (or stopped using) the web check's finding: reload the incident from the new chance.
async function applyWebFinding(finding, result) {
  setAppliedWeb(finding);
  if (!(await loadScenario(activeScenario, { keepTime: true }))) return;
  const cited = result.cited_ids.length ? ` (${result.cited_ids.join(", ")})` : "";
  logDecision(finding
    ? `Web check used: ${result.finding.replace("_", " ")}${cited}. ${riskText()} at ${incidentPlace()} now ${pct(scenario.disruption.prior_blocked_probability)}.`
    : `Web check no longer used. ${riskText()} at ${incidentPlace()} back to ${pct(scenario.disruption.prior_blocked_probability)}.`);
}

// Play the morning from its start; the first report arrives after a short normal lead-in.
function startMorning() {
  logDecision("Started on the planned routes.");
  setStep("monitor");
  setView("map");
  showMap();
  setSpeed(1);
  setPlaying(true);
}

async function restartMorning() {
  mode = "monitoring";
  reportIndex = -1;
  lead = null;
  incidentPlans = [];
  decisionLog = [];
  setSpeed(5);
  setAppliedWeb(null);
  clearTriage($("#web-triage"));
  if (!(await loadScenario("normal"))) return;
  setStep("start");
  setView("map");
}

function confirmResponse() {
  const label = actionLabel(plan.action);
  logDecision($("#response-why").textContent);
  logDecision(plan.action === modelPick?.id
    ? `Confirmed: ${label} (recommended).`
    : `Chose ${label} over ${modelPick.label}. Reason: ${$("#override-reason").value.trim()}`);
  mode = "incident";
  setStep("outcome");
  setView("map");
  showMap();
  // Slow enough to watch the response start before the next report arrives.
  setSpeed(reports[reportIndex + 1] ? 1 : 15);
  setPlaying(true);
}

function showReport() {
  setPlaying(false);
  mode = "report";
  renderReport();
  renderMode();
}

function renderReport() {
  const day = new Date(scenario.generated_at).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  const count = reportIndex + 1;
  $("#outcome-sub").textContent = `${day} · ${clockLabel(span.start)} to ${clockLabel(currentTime)} · ${count} incident report${count === 1 ? "" : "s"}. Simulated results.`;

  const plans = incidentPlans.filter(Boolean);
  const names = plans.map((p) => `${p.label} (${p.title}, ${clockLabel(p.at)})`).join(" and ");
  $("#report-head").innerHTML = `<th>Indicator</th><th class="num">Usual</th>${plans.map((p) => `<th class="num">${esc(p.label)}<span class="detail">${clockLabel(p.at)}</span></th>`).join("")}`;
  $("#report-indicators-note").textContent = `Usual is the planned morning with no incident. ${names ? `The ${names} column${plans.length === 1 ? " estimates" : "s each estimate"}` : "Each report column estimates"} the whole plan under that report's recommended response, before inspection. Each is worked out on its own from ${clockLabel(span.start)}, as if that report were the only incident${plans.length > 1 ? `, so the ${plans.at(-1).label} column does not include the effects of the ${plans[0].label.toLowerCase()} report and the columns do not add up` : ""}. The small figure under each value is the change from usual.`;
  $("#report-indicators").innerHTML = (baseline ?? plans[0]?.indicators ?? []).map((base) => {
    const items = plans.map((p) => p.indicators.find((item) => item.id === base.id));
    const bases = [...new Set([base, ...items].filter(Boolean).map((item) => basisLabel[item.basis] ?? item.basis))].join(" / ");
    const cells = items.map((item) => {
      if (item?.value == null) return '<td class="num">—</td>';
      const change = base.value == null ? null : indicatorChange(item, base);
      const shown = base.value == null ? "" : change
        ? `<span class="detail kpi-delta ${change.tone}"><span aria-hidden="true">${change.arrow}</span> ${change.amount}</span>`
        : '<span class="detail kpi-delta">Same</span>';
      return `<td class="num">${indicatorText(item, item.value)}${shown}</td>`;
    }).join("");
    return `<tr><td>${shortLabels[base.id] ?? indicatorLabel(base)}<span class="detail">${esc(bases)}</span></td><td class="num">${base.value == null ? "—" : indicatorText(base, base.value)}</td>${cells}</tr>`;
  }).join("");

  const action = scenario.decision_model.actions.find((a) => a.id === plan.action);
  const closing = plan.action === modelPick?.id
    ? `Completed: ${action.label} (recommended).`
    : `Completed: ${action.label}. Recommended: ${modelPick.label}.`;
  $("#report-log").innerHTML = [...decisionLog, { t: currentTime, text: closing }]
    .map((entry) => `<li><time>${clockLabel(entry.t)}</time><span>${esc(entry.text)}</span></li>`).join("");
}

// Skips to the next report while one is still due, otherwise to the end of the morning.
function skipToReport() {
  if (reports[reportIndex + 1]) {
    nextReport();
    return;
  }
  setPlaying(false);
  currentTime = span.end;
  updateFrame();
  showReport();
}

function bindEvents() {
  const fail = (error) => console.error(error);
  $("#start-button").addEventListener("click", startMorning);
  $("#monitor-skip").addEventListener("click", skipToReport);
  $("#inspect-button").addEventListener("click", () => startInspection().catch(fail));
  $("#reinspect-button").addEventListener("click", () => startInspection().catch(fail));
  $("#skip-inspection").addEventListener("click", () => {
    logDecision("Skipped inspection.");
    setStep("respond");
  });
  $$("[data-observation]").forEach((button) => button.addEventListener("click", () => observe(button.dataset.observation).catch((error) => {
    $("#posterior").textContent = error.message;
    setStep("respond");
  })));
  $("#rankings").addEventListener("change", (event) => {
    if (event.target.name !== "response") return;
    plan = { ...plan, action: event.target.value };
    renderConfirm();
    if ($("#response-comparison").open) updateResponseComparison();
    if (!$("#override-reason-wrap").hidden) $("#override-reason").focus();
    loadPlayback({ keepTime: true }).catch(fail);
  });
  $("#compare-responses").addEventListener("click", () => openResponseComparison().catch(fail));
  $("#close-response-comparison").addEventListener("click", () => $("#response-comparison").close());
  $("#response-comparison-body").addEventListener("click", (event) => {
    const button = event.target.closest("[data-compare-select]");
    if (!button) return;
    const input = document.getElementById(`response-${button.dataset.compareSelect}`);
    if (!input) return;
    input.checked = true;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    $("#response-comparison").close();
    input.focus();
  });
  $("#confirm-button").addEventListener("click", confirmResponse);
  $("#override-reason").addEventListener("input", renderConfirm);
  $("#skip-end").addEventListener("click", skipToReport);
  $("#change-response").addEventListener("click", () => {
    setPlaying(false);
    setStep("respond");
  });
  $("#restart").addEventListener("click", () => restartMorning().catch(fail));
  $("#report-restart").addEventListener("click", () => restartMorning().catch(fail));
  $("#stepper").addEventListener("click", (event) => {
    const button = event.target.closest("[data-goto]");
    if (!button || button.disabled) return;
    setPlaying(false);
    if (button.dataset.goto === "inspect") startInspection().catch(fail);
    else setStep(button.dataset.goto);
  });
  $$("button[data-view]").forEach((tab) => tab.addEventListener("click", () => setView(tab.dataset.view)));
  window.addEventListener("inspection-dialog-open", () => {
    dialogOpen = true;
    renderStepper();
  });
  $("#inspection-dialog").addEventListener("close", () => {
    dialogOpen = false;
    renderStepper();
  });

  $$('input[name="shipment-filter"]').forEach((input) => input.addEventListener("change", () => {
    shipmentFilter = input.value;
    renderShipments();
  }));
  document.addEventListener("click", (event) => {
    const truck = event.target.closest("[data-truck]");
    if (truck) selectTruck(selectedTruck === truck.dataset.truck ? null : truck.dataset.truck);
  });

  $("#play").addEventListener("click", () => setPlaying(!playing));
  $("#timeline").addEventListener("input", (event) => {
    currentTime = Number(event.target.value);
    updateFrame();
  });
  $("#playback-speed").addEventListener("change", (event) => {
    speed = Number(event.target.value);
  });
  $$('input[name="road"]').forEach((input) => input.addEventListener("change", () => {
    plan = { ...plan, road: input.value };
    loadPlayback({ keepTime: true }).catch(fail);
  }));
  // The ask box's text input is an input, so the space-bar handler below ignores it.
  initAsk(() => ({ scenario: activeScenario, observation: observed })).catch(fail);
  document.addEventListener("keydown", (event) => {
    if (event.key !== " " || dialogOpen || document.body.dataset.view !== "map") return;
    if (event.target.closest("input:not([type=range]), select, button, textarea")) return;
    event.preventDefault();
    setPlaying(!playing);
  });
}

window.addEventListener("inspection-reviewed", ({ detail }) => {
  if (detail.review.scenario !== activeScenario) return;
  const cv = detail.review.cv_suggestion;
  const choice = detail.review.observation;
  const agreement = !cv ? "" : cv === choice ? " Matches video analysis." : ` Video analysis suggested ${findingText(cv)}.`;
  const before = pct(detail.analysis.prior_blocked_probability);
  const after = pct(detail.analysis.posterior_blocked_probability);
  const change = before === after ? "unchanged" : `was ${before}`;
  const summary = `Reviewed: ${findingText(choice)}. ${incidentPlace()}. Estimated ${riskText().toLowerCase()}: ${after} (${change}).${agreement}`;
  $("#selected-evidence").hidden = false;
  $("#selected-evidence-image").src = detail.caseItem.annotated_image;
  $("#selected-evidence-title").textContent = detail.caseItem.title;
  $("#reinspect-button").textContent = "Review drone footage";
  showObservation(detail.analysis, summary).catch((error) => {
    $("#posterior").textContent += ` Playback could not update: ${error.message}`;
  });
});

bindEvents();
setView("map");
loadScenario("normal")
  .then(() => setStep("start"))
  .catch((error) => {
    $("#start-note").textContent = `PigeonOps could not load: ${error.message}`;
  });
