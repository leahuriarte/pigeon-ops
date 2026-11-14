const $ = (selector) => document.querySelector(selector);
const money = (value) => `${value < 0 ? "−" : ""}$${Math.abs(Math.round(value)).toLocaleString()}`;
const pct = (value) => `${Math.round(value * 100)}%`;
const svgNS = "http://www.w3.org/2000/svg";
const attentionStatuses = new Set(["at-risk", "watch", "unassigned"]);
let scenario;
let activeScenario = "disruption";
let shipmentFilter = "attention";
let mapLayers = [];

function project(lat, lon) {
  const b = scenario.map_bounds;
  return [30 + ((lon - b.west) / (b.east - b.west)) * 700, 25 + ((b.north - lat) / (b.north - b.south)) * 320];
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

  scenario.locations
    .filter((location) => location.kind !== "incident" || scenario.scenario_type === "disruption")
    .forEach((location) => {
      const [x, y] = project(location.lat, location.lon);
      const group = document.createElementNS(svgNS, "g");
      group.setAttribute("class", "marker");
      const color = { terminal: "#087f75", warehouse: "#2e5868", incident: "#c94c4c" }[location.kind];
      group.innerHTML = `<circle cx="${x}" cy="${y}" r="7" fill="${color}"/>${location.kind === "incident" ? `<circle class="incident-ring" cx="${x}" cy="${y}" r="9"/>` : ""}<text x="${x + 10}" y="${y - 9}">${location.name}</text>`;
      markers.appendChild(group);
    });

  scenario.trucks.forEach((truck) => {
    const [x, y] = project(truck.lat, truck.lon);
    const group = document.createElementNS(svgNS, "g");
    group.setAttribute("class", "truck-marker");
    group.innerHTML = `<rect x="${x - 6}" y="${y - 6}" width="12" height="12" rx="3" transform="rotate(45 ${x} ${y})"/><text x="${x + 9}" y="${y + 4}">${truck.id.replace("SYN-TRK-", "T")}</text>`;
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
  const routeLayer = new GraphicsLayer({ title: "Dispatch routes" });
  const markerLayer = new GraphicsLayer({ title: "Operations locations" });
  const truckLayer = new GraphicsLayer({ title: "Synthetic truck positions" });

  scenario.routes.forEach((route) => {
    routeLayer.add(new Graphic({
      geometry: { type: "polyline", paths: [route.points.map(([lat, lon]) => [lon, lat])], spatialReference: { wkid: 4326 } },
      symbol: { type: "simple-line", color: route.color, width: route.role === "fleet" ? 2 : 4.5, style: route.role === "detour" ? "short-dash" : "solid" },
      attributes: { name: route.name, role: route.role },
      popupTemplate: { title: "{name}", content: "Route role: {role}" },
    }));
  });

  const markerColors = { terminal: "#087f75", warehouse: "#2e5868", incident: "#c94c4c" };
  scenario.locations
    .filter((location) => location.kind !== "incident" || scenario.scenario_type === "disruption")
    .forEach((location) => markerLayer.add(new Graphic({
      geometry: { type: "point", longitude: location.lon, latitude: location.lat },
      symbol: { type: "simple-marker", color: markerColors[location.kind], size: location.kind === "incident" ? 15 : 10, outline: { color: "white", width: 2 } },
      attributes: { name: location.name, kind: location.kind },
      popupTemplate: { title: "{name}", content: "Synthetic operations location · {kind}" },
    })));

  scenario.trucks.forEach((truck) => truckLayer.add(new Graphic({
    geometry: { type: "point", longitude: truck.lon, latitude: truck.lat },
    symbol: { type: "simple-marker", style: "diamond", color: truck.status === "waiting" ? "#e89116" : "#132f3a", size: 11, outline: { color: "#ffffff", width: 1.5 } },
    attributes: { id: truck.id, status: truck.status, assignment: truck.assignment, fuel: truck.fuel, duty: truck.duty_remaining_hr },
    popupTemplate: { title: "{id}", content: "<b>{status}</b><br>Assignment: {assignment}<br>Powertrain: {fuel}<br>Duty remaining: {duty} hr<br><small>Synthetic record</small>" },
  })));

  const viewElement = $("#arcgis-map");
  await viewElement.viewOnReady();
  if (mapLayers.length) viewElement.map.removeMany(mapLayers);
  mapLayers = [routeLayer, markerLayer, truckLayer];
  viewElement.map.addMany(mapLayers);
  const visibleGraphics = [
    ...routeLayer.graphics.toArray(),
    ...markerLayer.graphics.toArray(),
    ...truckLayer.graphics.toArray(),
  ];
  await viewElement.view.goTo(visibleGraphics, { animate: false, padding: 36 });
  $("#arcgis-map").classList.remove("hidden");
  $("#map-fallback").classList.add("hidden");
  $("#map-provider").textContent = "ArcGIS Maps SDK • OpenStreetMap • synthetic fleet positions";
}

async function drawMap() {
  drawSchematicMap();
  if (window.innerWidth < 920) {
    $("#arcgis-map").classList.add("hidden");
    $("#map-fallback").classList.remove("hidden");
    $("#map-provider").textContent = "Responsive operations schematic • synthetic fleet positions";
    return;
  }
  try {
    await drawArcGISMap();
  } catch (error) {
    console.warn("Using offline map fallback:", error);
    $("#arcgis-map").classList.add("hidden");
    $("#map-fallback").classList.remove("hidden");
    $("#map-provider").textContent = "Offline schematic • ArcGIS unavailable";
  }
}

function renderSummary() {
  const attention = scenario.shipments.filter((item) => attentionStatuses.has(item.status)).length;
  const assigned = scenario.shipments.filter((item) => item.truck !== "Unassigned").length;
  const electric = scenario.trucks.filter((item) => item.fuel === "Battery electric").length;
  const summary = [
    [scenario.shipments.length, "active shipments", `${assigned} assigned to trucks`],
    [attention, "need attention", attention ? "risk, watch, or unassigned" : "all within tolerance"],
    [scenario.trucks.length, "trucks visible", `${electric} battery electric`],
    [`${scenario.operations.network_speed_mph} mph`, "network speed", scenario.operations.weather],
    [`${scenario.operations.terminal_turn_time_min} min`, "terminal turn time", "publicly calibrated estimate"],
  ];
  $("#ops-summary").innerHTML = summary.map(([value, label, note]) => `<div class="metric"><strong>${value}</strong><span>${label}</span><small>${note}</small></div>`).join("");
  $("#map-truck-count").textContent = scenario.trucks.length;
  $("#map-shipment-count").textContent = scenario.shipments.length;
  $("#map-risk-count").textContent = attention;
}

function renderShipments() {
  const visible = shipmentFilter === "all" ? scenario.shipments : scenario.shipments.filter((item) => attentionStatuses.has(item.status));
  $("#shipment-strip").innerHTML = visible.map((item) => `<article class="shipment ${item.status}" tabindex="0">
    <div class="shipment-top"><b>${item.id}</b><span>${item.status.replace("-", " ")}</span></div>
    <strong>${item.cargo}</strong>
    <small>${item.truck} · ETA ${item.eta}</small>
    <div class="shipment-meta"><span>${item.appointment}</span><span>${item.slack_min}m slack</span><span>${item.priority}</span></div>
  </article>`).join("");
}

function renderFleet() {
  $("#fleet-count").textContent = scenario.trucks.length;
  $("#fleet-list").innerHTML = scenario.trucks.map((truck) => `<div class="fleet-row"><span class="fleet-dot ${truck.status}"></span><div><b>${truck.id}</b><small>${truck.assignment} · ${truck.fuel}</small></div><span>${truck.duty_remaining_hr}h</span></div>`).join("");
}

function renderProvenance() {
  $("#provenance").innerHTML = scenario.data_provenance.map((item) => `<div><span class="provenance-kind ${item.kind.replaceAll(" ", "-")}">${item.kind}</span><p><b>${item.label}</b><small>${item.source}</small></p></div>`).join("");
}

function renderRankings(items, after = false) {
  $("#ranking-title").textContent = after ? "Ranked after observation" : "Ranked before inspection";
  $("#rankings").innerHTML = items.map((item) => `<div class="option-row ${item.rank === 1 ? "top" : ""}">
    <div class="option-label"><span class="rank">${item.rank}</span><div><b>${item.label}</b><small>${item.summary}</small><em>${item.stakeholder_effect}</em></div></div>
    <span>${item.expected.lateness_min} min</span><span>${money(item.expected.incremental_cost)}</span><span>${item.expected.emissions_kg} kg</span><span>${item.expected.constraint_breaches}</span><span>${money(item.expected_loss)}</span>
  </div>`).join("");
}

function renderEvoi(analysis) {
  const e = analysis.evoi;
  const recommendation = $("#recommendation");
  recommendation.textContent = e.recommend_inspection ? "Inspect first — evidence is expected to improve this decision." : "Act now — inspection is unlikely to change the best action enough to justify its cost.";
  recommendation.classList.toggle("no", !e.recommend_inspection);
  $("#act-loss").textContent = money(e.best_act_now_loss);
  $("#evidence-loss").textContent = money(e.expected_loss_after_inspection);
  $("#inspection-cost").textContent = money(e.inspection_cost);
  $("#net-value").textContent = money(e.net_value_of_information);
  const cv = e.cv_only;
  const breakEven = e.break_even_accuracy === null ? "even perfect evidence would not pay for a flight" : `a flight pays off at ≥${pct(e.break_even_accuracy)} accuracy`;
  $("#cv-only").innerHTML = cv
    ? `<b>Automated CV alone (measured):</b> ${money(cv.net_value_of_information)} net — ${cv.recommend_inspection ? "worth a flight" : "not worth a flight yet"}. Misses ${pct(cv.missed_blockage_probability)} of blockages; ${breakEven}.`
    : `Break-even: ${breakEven}.`;
  const a = e.assumptions;
  $("#assumptions").innerHTML = `${e.formula}<br>Evidence model: ${a.observation_source}<br>Flags a real blockage: ${pct(a.detects_blockage_probability)} · misses it (says clear): ${pct(a.missed_blockage_probability)} · false alarm on an open road: ${pct(a.false_alarm_probability)}<br>Chance the inspection comes back inconclusive: ${pct(a.signal_probability.inconclusive)}<br>Launch and review: ${a.launch_and_review_minutes} min`;
}

function resetObservation() {
  $("#feed-label").textContent = "Awaiting simulated evidence";
  $("#posterior").classList.add("hidden");
  $("#observation-buttons").classList.add("hidden");
  $("#inspect-button").disabled = false;
  $("#inspect-button").textContent = "Run simulated inspection";
}

async function observe(observation) {
  const response = await fetch(`/api/analysis?scenario=${encodeURIComponent(activeScenario)}&observation=${encodeURIComponent(observation)}`);
  const analysis = await response.json();
  renderRankings(analysis.after, true);
  const labels = { blocked: "Obstruction detected", clear: "Route appears clear", inconclusive: "Image inconclusive" };
  $("#feed-label").textContent = labels[observation];
  $("#posterior").classList.remove("hidden");
  $("#posterior").innerHTML = `<b>Updated blockage likelihood: ${pct(analysis.posterior_blocked_probability)}</b><br>Top option: ${analysis.after[0].label}. Dispatcher still makes the final call.`;
}

async function loadScenario(id) {
  activeScenario = id;
  const response = await fetch(`/api/scenario?scenario=${encodeURIComponent(id)}`);
  if (!response.ok) throw new Error("Scenario failed to load");
  scenario = await response.json();
  $("#scenario-eyebrow").textContent = scenario.disruption.eyebrow;
  $("#disruption-id").textContent = scenario.disruption.id;
  $("#disruption-title").textContent = scenario.disruption.title;
  $("#disruption-description").textContent = scenario.disruption.description;
  $("#risk-value").textContent = pct(scenario.disruption.prior_blocked_probability);
  $("#drone-note").textContent = scenario.decision_model.drone.note;
  $("#constraints").innerHTML = scenario.disruption.constraints.map((item) => `<li>${item}</li>`).join("");
  document.querySelectorAll("[data-scenario]").forEach((button) => button.classList.toggle("active", button.dataset.scenario === id));
  document.body.dataset.scenario = scenario.scenario_type;
  resetObservation();
  renderSummary();
  renderShipments();
  renderFleet();
  renderProvenance();
  renderRankings(scenario.analysis.before);
  renderEvoi(scenario.analysis);
  await drawMap();
}

function bindEvents() {
  $("#inspect-button").addEventListener("click", () => {
    $("#observation-buttons").classList.remove("hidden");
    $("#inspect-button").textContent = "Simulation ready — choose evidence";
    $("#inspect-button").disabled = true;
  });
  document.querySelectorAll("[data-observation]").forEach((button) => button.addEventListener("click", () => observe(button.dataset.observation)));
  document.querySelectorAll("[data-scenario]").forEach((button) => button.addEventListener("click", () => loadScenario(button.dataset.scenario)));
  document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => {
    shipmentFilter = button.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach((candidate) => candidate.classList.toggle("active", candidate === button));
    renderShipments();
  }));
}

$("#clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
bindEvents();
loadScenario(activeScenario).catch((error) => { $("#disruption-title").textContent = "Could not load demo"; $("#disruption-description").textContent = error.message; });
