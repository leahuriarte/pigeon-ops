const $ = (selector) => document.querySelector(selector);
const money = (value) => `$${Math.round(value).toLocaleString()}`;
const pct = (value) => `${Math.round(value * 100)}%`;
const svgNS = "http://www.w3.org/2000/svg";
let scenario;

function project(lat, lon) {
  const b = scenario.map_bounds;
  return [30 + ((lon - b.west) / (b.east - b.west)) * 700, 25 + ((b.north - lat) / (b.north - b.south)) * 320];
}

function drawSchematicMap() {
  const routes = $("#routes");
  scenario.routes.forEach((route, index) => {
    const line = document.createElementNS(svgNS, "polyline");
    line.setAttribute("points", route.points.map(([lat, lon]) => project(lat, lon).join(",")).join(" "));
    line.setAttribute("stroke", route.color);
    line.setAttribute("class", `route ${index ? "detour" : ""}`);
    routes.appendChild(line);
  });
  const markers = $("#markers");
  scenario.locations.forEach((location) => {
    const [x, y] = project(location.lat, location.lon);
    const group = document.createElementNS(svgNS, "g");
    group.setAttribute("class", "marker");
    const color = { terminal: "#087f75", warehouse: "#2e5868", incident: "#c94c4c" }[location.kind];
    group.innerHTML = `<circle cx="${x}" cy="${y}" r="8" fill="${color}"/>${location.kind === "incident" ? `<circle class="incident-ring" cx="${x}" cy="${y}" r="9"/>` : ""}<text x="${x + 12}" y="${y - 10}">${location.name}</text>`;
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

  scenario.routes.forEach((route, index) => {
    routeLayer.add(new Graphic({
      geometry: {
        type: "polyline",
        paths: [route.points.map(([lat, lon]) => [lon, lat])],
        spatialReference: { wkid: 4326 },
      },
      symbol: {
        type: "simple-line",
        color: route.color,
        width: index ? 4 : 5,
        style: index ? "short-dash" : "solid",
      },
      attributes: { name: route.name },
      popupTemplate: { title: "{name}" },
    }));
  });

  const markerColors = { terminal: "#087f75", warehouse: "#2e5868", incident: "#c94c4c" };
  scenario.locations.forEach((location) => {
    markerLayer.add(new Graphic({
      geometry: {
        type: "point",
        longitude: location.lon,
        latitude: location.lat,
      },
      symbol: {
        type: "simple-marker",
        color: markerColors[location.kind],
        size: location.kind === "incident" ? 14 : 11,
        outline: { color: "white", width: 2 },
      },
      attributes: { name: location.name, kind: location.kind },
      popupTemplate: { title: "{name}", content: "{kind}" },
    }));
  });

  const viewElement = $("#arcgis-map");
  viewElement.center = [
    (scenario.map_bounds.west + scenario.map_bounds.east) / 2,
    (scenario.map_bounds.south + scenario.map_bounds.north) / 2,
  ];
  viewElement.zoom = 12;
  await viewElement.viewOnReady();
  viewElement.map.addMany([routeLayer, markerLayer]);
  $("#map-fallback").classList.add("hidden");
  $("#map-provider").textContent = "ArcGIS Maps SDK • OpenStreetMap";
}

async function drawMap() {
  drawSchematicMap();
  try {
    await drawArcGISMap();
  } catch (error) {
    console.warn("Using offline map fallback:", error);
    $("#arcgis-map").classList.add("hidden");
    $("#map-fallback").classList.remove("hidden");
    $("#map-provider").textContent = "Offline schematic • ArcGIS unavailable";
  }
}

function renderShipments() {
  $("#shipment-strip").innerHTML = scenario.shipments.map((s) => `<div class="shipment ${s.status}"><b>${s.id} · ${s.container}</b>${s.cargo}<br>${s.truck} → ${s.appointment} appointment</div>`).join("");
}

function renderRankings(items, after = false) {
  $("#ranking-title").textContent = after ? "Ranked after observation" : "Ranked before inspection";
  $("#rankings").innerHTML = items.map((item) => `<div class="option-row ${item.rank === 1 ? "top" : ""}">
    <div class="option-label"><span class="rank">${item.rank}</span><div><b>${item.label}</b><small>${item.summary}</small></div></div>
    <span>${item.expected.lateness_min} min</span><span>${money(item.expected.incremental_cost)}</span><span>${item.expected.emissions_kg} kg</span><span>${item.expected.constraint_breaches}</span><span>${money(item.expected_loss)}</span>
  </div>`).join("");
}

function renderEvoi(analysis) {
  const e = analysis.evoi;
  const recommendation = $("#recommendation");
  recommendation.textContent = e.recommend_inspection ? "Inspect first — evidence is expected to improve this decision." : "Act now — inspection is not worth its cost in this scenario.";
  recommendation.classList.toggle("no", !e.recommend_inspection);
  $("#act-loss").textContent = money(e.best_act_now_loss);
  $("#evidence-loss").textContent = money(e.expected_loss_after_inspection);
  $("#inspection-cost").textContent = money(e.inspection_cost);
  $("#net-value").textContent = money(e.net_value_of_information);
  $("#assumptions").innerHTML = `${e.formula}<br>Observation accuracy: ${pct(e.assumptions.inspection_accuracy)}<br>Review time: ${e.assumptions.launch_and_review_minutes} min<br>Probability observation says blocked: ${pct(e.assumptions.signal_blocked_probability)}`;
}

async function observe(observation) {
  const response = await fetch(`/api/analysis?observation=${observation}`);
  const analysis = await response.json();
  renderRankings(analysis.after, true);
  const labels = { blocked: "Obstruction detected", clear: "Route appears clear", inconclusive: "Image inconclusive" };
  $("#feed-label").textContent = labels[observation];
  $("#posterior").classList.remove("hidden");
  $("#posterior").innerHTML = `<b>Updated blockage likelihood: ${pct(analysis.posterior_blocked_probability)}</b><br>Top option: ${analysis.after[0].label}. Dispatcher still makes the final call.`;
}

async function init() {
  const response = await fetch("/api/scenario");
  if (!response.ok) throw new Error("Scenario failed to load");
  scenario = await response.json();
  $("#disruption-id").textContent = scenario.disruption.id;
  $("#disruption-title").textContent = scenario.disruption.title;
  $("#disruption-description").textContent = scenario.disruption.description;
  $("#risk-value").textContent = pct(scenario.disruption.prior_blocked_probability);
  $("#drone-note").textContent = scenario.decision_model.drone.note;
  $("#constraints").innerHTML = scenario.disruption.constraints.map((item) => `<li>${item}</li>`).join("");
  drawMap(); renderShipments(); renderRankings(scenario.analysis.before); renderEvoi(scenario.analysis);
  $("#inspect-button").addEventListener("click", () => {
    $("#observation-buttons").classList.remove("hidden");
    $("#inspect-button").textContent = "Simulation ready — choose evidence";
    $("#inspect-button").disabled = true;
  });
  document.querySelectorAll("[data-observation]").forEach((button) => button.addEventListener("click", () => observe(button.dataset.observation)));
}

$("#clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
init().catch((error) => { $("#disruption-title").textContent = "Could not load demo"; $("#disruption-description").textContent = error.message; });
