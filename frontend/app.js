const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const money = (value) => `${value < 0 ? "−" : ""}$${Math.abs(Math.round(value)).toLocaleString("en-US")}`;
const pct = (value) => `${Math.round(value * 100)}%`;
const whole = (value) => Math.round(value).toLocaleString("en-US");
const esc = (value) => String(value).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const svgNS = "http://www.w3.org/2000/svg";

const colors = { ink: "#1b1f23", warehouse: "#5b6268", risk: "#b42318", amber: "#c27c0e" };
const attentionStatuses = new Set(["at-risk", "watch", "unassigned"]);
const shipmentStatus = {
  "on-time": ["On time", "ok"],
  planned: ["Planned", "planned"],
  watch: ["Watch", "watch"],
  "at-risk": ["At risk", "risk"],
  unassigned: ["Unassigned", "risk"],
};
const truckStatus = {
  "en-route": ["En route", "ok"],
  "available-soon": ["Available soon", "watch"],
  "at-terminal": ["At terminal", "planned"],
  waiting: ["Waiting", "watch"],
};
const observationLabels = {
  blocked: "the feed reported the road blocked",
  clear: "the feed reported the road clear",
  inconclusive: "an inconclusive feed result",
};
const truckIsWaiting = (truck) => truck.status === "waiting" || truck.status === "available-soon";
const tag = ([label, tone]) => `<span class="status ${tone}">${label}</span>`;

let scenario;
let activeScenario = "disruption";
let shipmentFilter = "attention";
let selectedTruck = null;
let mapLayers = [];

function project(lat, lon) {
  const b = scenario.map_bounds;
  return [30 + ((lon - b.west) / (b.east - b.west)) * 700, 25 + ((b.north - lat) / (b.north - b.south)) * 320];
}

function visibleLocations() {
  return scenario.locations.filter((location) => location.kind !== "incident" || scenario.scenario_type === "disruption");
}

function drawSchematicMap() {
  const routes = $("#routes");
  const markers = $("#markers");
  routes.replaceChildren();
  markers.replaceChildren();

  scenario.routes.forEach((route) => {
    const line = document.createElementNS(svgNS, "polyline");
    line.setAttribute("points", route.points.map(([lat, lon]) => project(lat, lon).join(",")).join(" "));
    line.setAttribute("stroke", route.color);
    line.setAttribute("class", `route ${route.role}`);
    routes.appendChild(line);
  });

  visibleLocations().forEach((location) => {
    const [x, y] = project(location.lat, location.lon);
    const group = document.createElementNS(svgNS, "g");
    group.setAttribute("class", "marker");
    const color = { terminal: colors.ink, warehouse: colors.warehouse, incident: colors.risk }[location.kind];
    group.innerHTML = `<circle cx="${x}" cy="${y}" r="6" fill="${color}"/><text x="${x + 9}" y="${y - 8}">${esc(location.name)}</text>`;
    markers.appendChild(group);
  });

  scenario.trucks.forEach((truck) => {
    const [x, y] = project(truck.lat, truck.lon);
    const group = document.createElementNS(svgNS, "g");
    group.setAttribute("class", `truck-marker${truckIsWaiting(truck) ? " waiting" : ""}`);
    group.innerHTML = `<rect x="${x - 5}" y="${y - 5}" width="10" height="10" transform="rotate(45 ${x} ${y})"/><text x="${x + 9}" y="${y + 4}">${esc(truck.id.replace("SYN-TRK-", "T"))}</text>`;
    markers.appendChild(group);
  });
}

async function drawArcGISMap() {
  const arcgis = globalThis.$arcgis;
  if (!arcgis?.import) throw new Error("ArcGIS SDK unavailable");
  const [GraphicsLayer, Graphic] = await arcgis.import([
    "@arcgis/core/layers/GraphicsLayer.js",
    "@arcgis/core/Graphic.js",
  ]);
  const routeLayer = new GraphicsLayer({ title: "Routes" });
  const markerLayer = new GraphicsLayer({ title: "Terminals and warehouses" });
  const truckLayer = new GraphicsLayer({ title: "Trucks" });

  scenario.routes.forEach((route) => {
    routeLayer.add(new Graphic({
      geometry: { type: "polyline", paths: [route.points.map(([lat, lon]) => [lon, lat])], spatialReference: { wkid: 4326 } },
      symbol: { type: "simple-line", color: route.color, width: route.role === "fleet" ? 2 : 4, style: route.role === "detour" ? "short-dash" : "solid" },
      attributes: { name: route.name, role: route.role },
      popupTemplate: { title: "{name}", content: "Route type: {role}" },
    }));
  });

  const markerColors = { terminal: colors.ink, warehouse: colors.warehouse, incident: colors.risk };
  visibleLocations().forEach((location) => markerLayer.add(new Graphic({
    geometry: { type: "point", longitude: location.lon, latitude: location.lat },
    symbol: { type: "simple-marker", color: markerColors[location.kind], size: location.kind === "incident" ? 13 : 9, outline: { color: "white", width: 1.5 } },
    attributes: { name: location.name, kind: location.kind },
    popupTemplate: { title: "{name}", content: "Location type: {kind}" },
  })));

  scenario.trucks.forEach((truck) => truckLayer.add(new Graphic({
    geometry: { type: "point", longitude: truck.lon, latitude: truck.lat },
    symbol: { type: "simple-marker", style: "diamond", color: truckIsWaiting(truck) ? colors.amber : colors.ink, size: 10, outline: { color: "#ffffff", width: 1.5 } },
    attributes: { id: truck.id, status: truckStatus[truck.status]?.[0] ?? truck.status, assignment: truck.assignment, fuel: truck.fuel, duty: truck.duty_remaining_hr },
    popupTemplate: { title: "{id}", content: "Status: {status}<br>Load: {assignment}<br>Fuel: {fuel}<br>Duty time left: {duty} h<br>Simulated record" },
  })));

  const viewElement = $("#arcgis-map");
  await viewElement.viewOnReady();
  if (mapLayers.length) viewElement.map.removeMany(mapLayers);
  mapLayers = [routeLayer, markerLayer, truckLayer];
  viewElement.map.addMany(mapLayers);
  const visibleGraphics = [...routeLayer.graphics.toArray(), ...markerLayer.graphics.toArray(), ...truckLayer.graphics.toArray()];
  await viewElement.view.goTo(visibleGraphics, { animate: false, padding: 32 });
  $("#arcgis-map").classList.remove("hidden");
  $("#map-fallback").classList.add("hidden");
  $("#map-provider").textContent = "OpenStreetMap basemap. Truck positions are simulated.";
}

function showSchematic(message) {
  $("#arcgis-map").classList.add("hidden");
  $("#map-fallback").classList.remove("hidden");
  $("#map-provider").textContent = message;
}

async function drawMap() {
  drawSchematicMap();
  if (window.innerWidth < 920) {
    showSchematic("Schematic map (ArcGIS loads at 920px and wider). Truck positions are simulated.");
    return;
  }
  try {
    await drawArcGISMap();
  } catch (error) {
    console.warn("Using the schematic map because ArcGIS failed to load:", error);
    showSchematic("ArcGIS did not load, so this is a schematic. Truck positions are simulated.");
  }
}

function affectedShipment() {
  return scenario.shipments.find((item) => item.id === scenario.disruption.affected_shipment);
}

function renderDecision() {
  const d = scenario.disruption;
  const affected = affectedShipment();
  $("#scenario-time").textContent = scenario.generated_at.slice(11, 16);
  $("#disruption-title").textContent = d.title;
  $("#disruption-description").textContent = d.description;
  $("#constraints").innerHTML = d.constraints.map((item) => `<li>${esc(item)}</li>`).join("");
  $("#risk-value").textContent = pct(d.prior_blocked_probability);
  $("#affected").innerHTML = `${esc(affected.id)}<small>${esc(affected.cargo)}, ${affected.slack_min} min of slack</small>`;
  $("#deadline").innerHTML = `${esc(d.decision_deadline)}<small>Reported at ${esc(d.reported_at)}</small>`;
}

function renderRankings(items, observation = null) {
  $("#ranking-state").textContent = observation
    ? `Ranked by expected loss after ${observationLabels[observation]}`
    : "Ranked by expected loss before inspection";
  $("#rankings").innerHTML = items.map((item) => {
    const e = item.expected;
    return `<tr class="${item.rank === 1 ? "top" : ""}">
      <td class="num">${item.rank}</td>
      <td><b>${esc(item.label)}</b><span class="detail">${esc(item.summary)}</span><span class="detail">${esc(item.stakeholder_effect)}</span></td>
      <td class="num">${whole(e.lateness_min)}</td>
      <td class="num">${money(e.incremental_cost)}</td>
      <td class="num">${whole(e.emissions_kg)}</td>
      <td class="num">${e.constraint_breaches.toFixed(1)}</td>
      <td class="num"><b>${money(item.expected_loss)}</b></td>
    </tr>`;
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
  const a = e.assumptions;
  $("#assumptions").innerHTML = [
    ["Feed accuracy (assumed)", pct(a.inspection_accuracy)],
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
    <td>${tag(truckStatus[truck.status] ?? [truck.status, "planned"])}</td>
    <td class="num">${truck.duty_remaining_hr} h</td>
  </tr>`).join("");
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
  $("#posterior").hidden = true;
  $("#observation-buttons").hidden = true;
  $$("[data-observation]").forEach((button) => button.setAttribute("aria-pressed", "false"));
  $("#inspect-button").disabled = false;
  $("#inspect-button").textContent = "Run simulated inspection";
}

async function observe(observation) {
  const response = await fetch(`/api/analysis?scenario=${encodeURIComponent(activeScenario)}&observation=${encodeURIComponent(observation)}`);
  if (!response.ok) throw new Error("The analysis request failed");
  const analysis = await response.json();
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
}

async function loadScenario(id) {
  activeScenario = id;
  selectedTruck = null;
  const response = await fetch(`/api/scenario?scenario=${encodeURIComponent(id)}`);
  if (!response.ok) throw new Error("The scenario failed to load");
  scenario = await response.json();
  $(`#scenario-${id}`).checked = true;
  document.body.dataset.scenario = scenario.scenario_type;
  $("#drone-note").textContent = scenario.decision_model.drone.note;
  resetObservation();
  renderDecision();
  renderRankings(scenario.analysis.before);
  renderEvoi(scenario.analysis);
  renderShipments();
  renderFleet();
  renderConditions();
  renderProvenance();
  renderDefinitions();
  await drawMap();
}

function bindEvents() {
  $("#inspect-button").addEventListener("click", () => {
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
    selectedTruck = null;
    renderShipments();
    renderFleet();
  }));
  $("#clear-truck").addEventListener("click", () => {
    selectedTruck = null;
    renderShipments();
    renderFleet();
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-truck]");
    if (!button) return;
    const fromFleet = Boolean(button.closest("#fleet-list"));
    selectedTruck = selectedTruck === button.dataset.truck ? null : button.dataset.truck;
    renderShipments();
    renderFleet();
    if (fromFleet && selectedTruck) $("#shipments").scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

bindEvents();
loadScenario(activeScenario).catch((error) => {
  $("#disruption-title").textContent = "The demo could not load";
  $("#disruption-description").textContent = error.message;
});
