// The response dialog uses the same ranked outcomes shown in the compact cards.
const esc = (value) => String(value ?? "").replace(/[&<>\"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const number = (value, digits = 0) => Number(value ?? 0).toLocaleString("en-US", { maximumFractionDigits: digits });
const money = (value) => `$${number(Math.round(value))}`;

function actionRoute(scenario, option) {
  const action = scenario.decision_model.actions.find((item) => item.id === option.id);
  const affected = scenario.shipments.find((item) => item.id === scenario.disruption.affected_shipment);
  return action?.plan?.route ?? affected?.route;
}

function weightedRows(option, probability) {
  const open = option.outcomes?.open?.detail?.shipments ?? [];
  const blocked = option.outcomes?.blocked?.detail?.shipments ?? [];
  const byId = new Map(blocked.map((row) => [row.shipment, row]));
  return open.map((row) => ({
    ...row,
    driver_min: (1 - probability) * row.driver_min + probability * (byId.get(row.shipment)?.driver_min ?? row.driver_min),
  }));
}

function driverMinutes(option, probability) {
  return weightedRows(option, probability).reduce((sum, row) => sum + row.driver_min, 0);
}

function addedMiles(option, probability) {
  const open = option.outcomes?.open?.detail?.added_miles ?? 0;
  const blocked = option.outcomes?.blocked?.detail?.added_miles ?? open;
  return (1 - probability) * open + probability * blocked;
}

function fuelMiles(option, probability, trucks) {
  const totals = { Diesel: 0, Electric: 0, Other: 0 };
  for (const [state, weight] of [["open", 1 - probability], ["blocked", probability]]) {
    for (const row of option.outcomes?.[state]?.detail?.shipments ?? []) {
      const fuel = trucks.get(row.truck)?.fuel;
      const key = fuel === "Diesel" ? "Diesel" : fuel === "Battery electric" ? "Electric" : "Other";
      totals[key] += weight * (row.miles ?? 0);
    }
  }
  return totals;
}

function schoolProximity(list) {
  if (!list) return { close: null, nearest: null };
  return {
    close: list.filter((school) => school.distance_m <= 150).length,
    nearest: list.length ? Math.min(...list.map((school) => school.distance_m)) : null,
  };
}

function schoolCorridor(list) {
  if (!list) return "";
  const dots = list.map((school, index) => `<i class="compare-school-dot" style="left:${Math.min(98, school.distance_m / 300 * 100)}%;--dot-row:${index % 2}" title="${esc(school.name)} · ${number(school.distance_m)} m"></i>`).join("");
  return `<div class="compare-corridor" role="img" aria-label="${list.length} schools within 300 m; ${list.filter((school) => school.distance_m <= 150).length} within 150 m"><div class="compare-corridor-track"><div class="compare-corridor-close"></div>${dots}</div><div class="compare-corridor-axis"><span>Route · 0 m</span><span>150 m</span><span>300 m</span></div></div>`;
}

function fuelBar(miles) {
  const total = Object.values(miles).reduce((sum, value) => sum + value, 0);
  if (!total) return "";
  return `<div class="compare-fuel-bar" role="img" aria-label="Diesel ${number(miles.Diesel, 1)} miles, electric ${number(miles.Electric, 1)} miles, other ${number(miles.Other, 1)} miles"><i class="diesel" style="width:${miles.Diesel / total * 100}%"></i><i class="electric" style="width:${miles.Electric / total * 100}%"></i><i class="other" style="width:${miles.Other / total * 100}%"></i></div>`;
}

function schoolList(routeId, schools) {
  const list = schools?.routes?.[routeId];
  if (!list) return "<p>School names unavailable for this route.</p>";
  if (!list.length) return "<p>No OSM listed schools within 300 m.</p>";
  return `<ul class="compare-schools">${list.map((school) => {
    const url = /^((node|way|relation)\/\d+)$/.test(school.osm_id) ? `https://www.openstreetmap.org/${school.osm_id}` : null;
    return `<li>${url ? `<a href="${url}" target="_blank" rel="noopener noreferrer">${esc(school.name)}</a>` : esc(school.name)}<span>${number(school.distance_m)} m from route</span></li>`;
  }).join("")}</ul>`;
}

function impactRows(option, state, trucks) {
  const rows = option.outcomes?.[state]?.detail?.shipments ?? [];
  return rows.map((row) => {
    const truck = trucks.get(row.truck);
    const timing = row.constraint_breaches > 0 ? "Misses window" : row.lateness_min > 0 ? `${number(row.lateness_min)} min late` : `${number(row.slack_min)} min buffer`;
    return `<tr><th scope="row">${esc(row.shipment)}<small>${esc(row.route)}</small></th><td>${esc(row.truck)}<small>${esc(truck?.fuel ?? "Fuel unknown")}</small></td><td>${number(row.driver_min, 1)} min${row.reassigned ? "<small>Handover</small>" : ""}</td><td>${esc(row.arrival)}<small>${timing}</small></td></tr>`;
  }).join("");
}

export function renderResponseComparison(container, scenario, options, probability, selected, schools) {
  const trucks = new Map(scenario.trucks.map((truck) => [truck.id, truck]));
  const carbonRate = scenario.decision_model.rates.co2_kg_per_mile;
  const impacts = options.map((option) => ({ option, fuel: fuelMiles(option, probability, trucks) }));
  const tailpipe = (impact) => impact.fuel.Diesel * carbonRate;
  const maxCo2 = Math.max(1, ...impacts.map(tailpipe));
  const minCo2 = Math.min(...impacts.map(tailpipe));
  const currentRouteCo2 = tailpipe(impacts.find(({ option }) => option.id === "hold-course") ?? impacts[0]);
  const summaries = impacts.map(({ option, fuel }) => {
    const routeId = actionRoute(scenario, option);
    const route = scenario.routes.find((item) => item.id === routeId);
    const nearby = schools?.routes?.[routeId];
    const proximity = schoolProximity(nearby);
    const driver = driverMinutes(option, probability);
    const change = driver - driverMinutes(options[0], probability);
    const emissions = fuel.Diesel * carbonRate;
    const co2Difference = emissions - minCo2;
    const carbonChange = emissions - currentRouteCo2;
    const miles = addedMiles(option, probability);
    const fuelTotal = Object.values(fuel).reduce((sum, value) => sum + value, 0);
    const electricShare = fuelTotal ? Math.round(fuel.Electric / fuelTotal * 100) : 0;
    return `<article class="compare-option${option.id === selected ? " selected" : ""}${co2Difference < 0.05 ? " lowest-carbon" : ""}">
      <div class="compare-option-head"><div><span class="compare-rank">${option.rank === 1 ? "Recommended response" : `Option ${option.rank}`}</span><h3>${esc(option.label)}</h3><p>${esc(route?.name ?? routeId)}</p></div><strong>${money(option.expected_loss)}<small>expected loss</small></strong></div>
      <section class="compare-eco" aria-label="Environmental impact">
        <div class="compare-eco-heading"><span>Tailpipe CO₂ · two loads</span><strong>${number(emissions, 1)}<small>kg</small></strong></div>
        <div class="compare-bar" role="img" aria-label="${number(emissions, 1)} kilograms CO₂; ${number(co2Difference, 1)} more than the lowest CO₂ option"><i style="width:${Math.max(2, emissions / maxCo2 * 100)}%"></i></div>
        <div class="compare-bar-axis"><span>0</span><span>${number(maxCo2, 1)} kg</span></div>
        <div class="compare-carbon-facts"><span><b>${Math.abs(carbonChange) < 0.05 ? "0" : `${carbonChange > 0 ? "+" : "−"}${number(Math.abs(carbonChange), 1)}`} kg</b> vs current route</span><span><b>${number(option.expected.emissions_kg, 1)} kg</b> in decision model</span></div>
      </section>
      <section class="compare-environment" aria-label="Route environment">
        <div class="compare-environment-title"><h4>School corridor</h4><strong>${option.schools_within_300m ?? "—"}<small>within 300 m</small></strong></div>
        ${schoolCorridor(nearby)}
        <div class="compare-env-stats"><div><span>Within 150 m</span><b>${proximity.close ?? "—"}</b></div><div><span>Nearest school</span><b>${proximity.nearest === null ? "—" : `${number(proximity.nearest)} m`}</b></div><div><span>Added miles</span><b>${miles > 0 ? "+" : miles < 0 ? "−" : ""}${number(Math.abs(miles), 1)} mi</b></div></div>
        <details class="compare-schools-detail"><summary>School names</summary>${schoolList(routeId, schools)}</details>
      </section>
      <section class="compare-fuel-section" aria-label="Miles by truck fuel">
        <div class="compare-fuel-heading"><h4>Truck miles by fuel</h4><strong>${electricShare}% <small>electric miles</small></strong></div>${fuelBar(fuel)}
        <div class="compare-fuel-legend"><span><i class="diesel"></i>Diesel <b>${number(fuel.Diesel, 1)} mi</b></span><span><i class="electric"></i>Electric <b>${number(fuel.Electric, 1)} mi</b></span>${fuel.Other ? `<span><i class="other"></i>Other <b>${number(fuel.Other, 1)} mi</b></span>` : ""}</div>
      </section>
      <section class="compare-driver"><h4>Driver impact</h4><div><span><b>${number(driver, 1)} min</b> driver time <small>${option.rank === 1 ? "Recommended response" : `${change >= 0 ? "+" : "−"}${number(Math.abs(change), 1)} min vs recommended`}</small></span><span><b>${number(option.expected.lateness_min, 1)} min</b> delivery delay</span></div><p>${esc(option.stakeholder_effect)}</p></section>
      <details class="compare-detail"><summary>Road outcomes and arrivals</summary><div class="compare-road-states">${["open", "blocked"].map((state) => `<div><h5>Road ${esc(state === "blocked" ? scenario.disruption.check?.bad ?? "blocked" : scenario.disruption.check?.good ?? "open")}</h5><div class="table-wrap"><table><thead><tr><th>Load / route</th><th>Truck / fuel</th><th>Driver time</th><th>Arrival</th></tr></thead><tbody>${impactRows(option, state, trucks)}</tbody></table></div></div>`).join("")}</div></details>
      <button class="btn ${option.id === selected ? "primary" : ""}" type="button" data-compare-select="${esc(option.id)}">${option.id === selected ? "Selected response" : "Choose this response"}</button>
    </article>`;
  });
  container.innerHTML = `<div class="compare-intro"><div><span class="compare-intro-kicker">ENVIRONMENT / ROUTE / DRIVER</span><h3>See the tradeoffs at a glance</h3><p>Tailpipe CO₂, fuel miles, school proximity and driver impact for each response.</p></div><p>Tailpipe CO₂ counts diesel miles; electric trucks have no tailpipe exhaust. The recommendation uses its separate CO₂ model.</p></div><div class="compare-grid">${summaries.join("")}</div>`;
}
