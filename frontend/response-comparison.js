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
  const maxCo2 = Math.max(1, ...options.map((option) => option.expected.emissions_kg));
  const minCo2 = Math.min(...options.map((option) => option.expected.emissions_kg));
  const summaries = options.map((option) => {
    const routeId = actionRoute(scenario, option);
    const route = scenario.routes.find((item) => item.id === routeId);
    const driver = driverMinutes(option, probability);
    const change = driver - driverMinutes(options[0], probability);
    const emissions = option.expected.emissions_kg;
    const co2Difference = emissions - minCo2;
    const miles = addedMiles(option, probability);
    const fuelMix = [...new Map((option.outcomes?.open?.detail?.shipments ?? []).map((row) => [row.truck, trucks.get(row.truck)?.fuel ?? "Unknown fuel"])).entries()]
      .map(([truck, fuel]) => `${truck.replace("SYN-TRK-", "T")} ${fuel}`).join(" · ");
    return `<article class="compare-option${option.id === selected ? " selected" : ""}">
      <div class="compare-option-head"><div><span class="compare-rank">${option.rank === 1 ? "Recommended" : `Option ${option.rank}`}</span><h3>${esc(option.label)}</h3><p>${esc(route?.name ?? routeId)}</p></div><strong>${money(option.expected_loss)}<small>expected loss</small></strong></div>
      <div class="compare-metrics">
        <div class="compare-co2"><span>CO₂ · two loads</span><strong>${number(emissions, 1)} kg</strong><small>${co2Difference < 0.05 ? "Lowest CO₂ option" : `+${number(co2Difference, 1)} kg vs lowest CO₂ option`}</small><div class="compare-bar"><i style="width:${Math.max(2, emissions / maxCo2 * 100)}%"></i></div><small class="compare-fuel">${esc(fuelMix)}</small></div>
        <div><span>Schools near route</span><strong>${option.schools_within_300m ?? "—"}</strong></div>
        <div><span>Added miles</span><strong>${miles > 0 ? "+" : miles < 0 ? "−" : ""}${number(Math.abs(miles), 1)} mi</strong></div>
        <div><span>Driver time · two loads</span><strong>${number(driver, 1)} min</strong><small>${option.rank === 1 ? "Recommended response" : `${change >= 0 ? "+" : "−"}${number(Math.abs(change), 1)} min vs recommended`}</small></div>
        <div><span>Delivery delay</span><strong>${number(option.expected.lateness_min, 1)} min</strong></div>
      </div>
      <div class="compare-detail"><section><h4>Nearby schools</h4>${schoolList(routeId, schools)}</section><section><h4>Driver and delivery impact</h4><p>${esc(option.stakeholder_effect)}</p><div class="compare-road-states">${["open", "blocked"].map((state) => `<div><h5>Road ${esc(state === "blocked" ? scenario.disruption.check?.bad ?? "blocked" : scenario.disruption.check?.good ?? "open")}</h5><div class="table-wrap"><table><thead><tr><th>Load / route</th><th>Truck / fuel</th><th>Driver time</th><th>Arrival</th></tr></thead><tbody>${impactRows(option, state, trucks)}</tbody></table></div></div>`).join("")}</div></section></div>
      <button class="btn ${option.id === selected ? "primary" : ""}" type="button" data-compare-select="${esc(option.id)}">${option.id === selected ? "Selected response" : "Choose this response"}</button>
    </article>`;
  });
  container.innerHTML = `<p class="compare-method">CO₂ uses the same diesel rate for every truck. Truck fuel types appear below. School names come from OpenStreetMap listings within 300 m of each response route.</p><div class="compare-grid">${summaries.join("")}</div>`;
}
