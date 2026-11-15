// Web check: an agent searches public feeds (CHP incident log, Caltrans message signs, NWS alerts)
// near the reported spot and says whether they back up the report. Nothing changes until the
// dispatcher clicks "Use this finding"; the finding is then sent as `web=` with every later request,
// and the server starts the decision from the updated blockage chance.
// Feed text and agent text come from outside, so they are always set with textContent.

let applied = null; // The finding the dispatcher applied, or null.
let token = 0;
const results = new Map(); // One run per incident while the page is open; cleared by clearTriage.

export const appliedWeb = () => applied;
export const setAppliedWeb = (finding) => {
  applied = finding;
};

// Adds the applied finding to a URLSearchParams or a request body object.
export function withWeb(target) {
  if (!applied) return target;
  if (target instanceof URLSearchParams) target.set("web", applied);
  else target.web = applied;
  return target;
}

const pct = (value) => `${Math.round(value * 100)}%`;
const money = (value) => `${value < 0 ? "−" : ""}$${Math.round(Math.abs(value)).toLocaleString("en-US")}`;
const headline = {
  corroborates: "Feeds support the report",
  contradicts: "Feeds contradict the report",
  nothing_found: "No matching reports",
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function link(href, text) {
  const node = el("a", null, text);
  if (/^https:\/\//.test(href || "")) {
    node.href = href;
    node.target = "_blank";
    node.rel = "noopener noreferrer";
  }
  return node;
}

export function clearTriage(container) {
  token += 1;
  results.clear();
  container.hidden = true;
  container.replaceChildren();
}

function header() {
  const top = el("div", "web-head");
  top.append(el("h2", null, "Public reports"));
  return top;
}

function evidenceList(result) {
  const box = el("details", "more web-evidence");
  box.append(el("summary", null, `Sources (${result.evidence.length})`));
  if (!result.evidence.length) box.append(el("p", "note", "No records near the reported spot."));
  const list = el("ul");
  result.evidence.forEach((item) => {
    const row = el("li", item.cited ? "cited" : "");
    const meta = el("p", "web-meta");
    const where = item.distance_km === null || item.distance_km === undefined ? "covers the area"
      : item.distance_km < 0.1 ? "under 0.1 km away" : `${item.distance_km} km away`;
    meta.append(` ${item.source} · ${item.time || "time not given"} · ${where}${item.cited ? " · cited" : ""} · `, link(item.source_url, "source"));
    row.append(meta, el("p", "web-title", item.title), el("p", "web-detail", item.detail));
    list.append(row);
  });
  box.append(list);
  const searched = result.searches.map((s) => `${s.tool.replaceAll("_", " ")}${s.radius_km ? ` (${s.radius_km} km)` : ""}: ${s.found}`).join("; ");
  const feeds = result.sources.map((s) => `${s.source}: ${s.status}`).join(" · ");
  box.append(el("p", "fine-print", `Searches: ${searched || "none"}. Feeds: ${feeds}.`));
  return box;
}

function effectLine(result) {
  const e = result.effect;
  const risk = e.prior_after === e.prior_before
    ? `${pct(e.prior_before)} unchanged`
    : `${pct(e.prior_before)} → ${pct(e.prior_after)}`;
  return `If applied: road blocked ${risk}. Inspection value: ${money(e.inspection_after.net_value)}.`;
}

// Runs the web check for an incident and renders the card. `onApply(finding | null, result)` is called
// when the dispatcher uses or removes the finding.
export async function renderTriage(container, scenarioId, options = {}) {
  const mine = ++token;
  const stale = () => mine !== token;
  container.hidden = false;
  container.replaceChildren(header(), el("p", "note", "Searching nearby CHP, Caltrans and NWS reports…"));
  let result = results.get(scenarioId);
  if (!result) {
    try {
      const response = await fetch(`/api/triage?${new URLSearchParams({ scenario: scenarioId })}`, { signal: AbortSignal.timeout(120000) });
      result = await response.json();
      if (!response.ok) result = { available: false, reason: result.error };
    } catch {
      result = { available: false, reason: "no response" };
    }
    if (result.available) results.set(scenarioId, result);
  }
  if (stale()) return;
  if (!result.available) {
    container.replaceChildren(header(), el("p", "note", "Web check unavailable."));
    return;
  }

  container.dataset.finding = result.finding;
  const by = result.agent === "llm"
    ? el("span", "tag llm", `AI agent · ${result.confidence} confidence`)
    : el("span", "tag llm", "Keyword rule · no AI");
  const parts = [header(), el("h3", null, headline[result.finding])];
  if (result.finding === "nothing_found") parts.push(el("p", "note", "Missing reports are weak evidence."));
  // Past decisions on similar reports, beside what the feeds say. A reference only (see history.js).
  if (result.history?.available && result.history.line) {
    const history = el("p", "web-history");
    history.append(el("span", "basis", "History"), ` ${result.history.line}`);
    parts.push(history);
  }
  const isApplied = applied === result.finding;
  if (isApplied) {
    parts.push(el("p", "web-effect applied", `Applied: blockage risk ${pct(result.effect.prior_after)} (was ${pct(result.effect.prior_before)}).`));
  } else {
    parts.push(el("p", "web-effect", effectLine(result)));
  }
  const actions = el("div", "web-actions");
  const use = el("button", `btn small${isApplied ? "" : " primary"}`, isApplied ? "Remove finding" : "Apply finding");
  use.type = "button";
  const error = el("p", "note");
  error.hidden = true;
  use.addEventListener("click", async () => {
    use.disabled = true;
    error.hidden = true;
    const previous = applied;
    applied = isApplied ? null : result.finding;
    try {
      await options.onApply?.(applied, result);
    } catch (failure) {
      console.error(failure);
      applied = previous;
      use.disabled = false;
      error.textContent = isApplied ? "Could not stop using the finding. Try again." : "Could not apply the finding. Try again.";
      error.hidden = false;
      return;
    }
    if (!stale()) renderTriage(container, scenarioId, options);
  });
  actions.append(use, error);
  const evidence = evidenceList(result);
  evidence.append(el("p", "web-summary", result.summary), by);
  if (result.agent_note) evidence.append(el("p", "fine-print", result.agent_note));
  parts.push(actions, evidence);
  const notes = result.fallback_reason ? ["Live feeds unavailable."] : [];
  const fine = el("p", "fine-print", notes.join(" "));
  if (result.agent_note) fine.title = result.agent_note;
  if (notes.length) parts.push(fine);
  container.replaceChildren(...parts);
}
