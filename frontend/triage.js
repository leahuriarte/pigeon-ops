// Web check: an agent searches public feeds (CHP incident log, Caltrans message signs, NWS alerts)
// near the reported spot and says whether they back up the report. Nothing changes until the
// dispatcher clicks "Use as evidence"; the finding is then sent as `web=` with every later request,
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
  nothing_found: "No public reports of it",
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

const badWord = (result) => result.check?.bad ?? "blocked";

// Why the finding moves the estimate, in the direction it moves it.
const reading = {
  corroborates: "Matching public reports raise the model's estimate that the road is",
  contradicts: "Public reports point the other way, so they lower the model's estimate that the road is",
  nothing_found: "No public report mentions it. That is weak evidence, but it lowers the model's estimate that the road is",
};

const droneWorth = (inspection) => (inspection.recommend
  ? `expected to save ${money(inspection.net_value)}`
  : "not expected to pay off");

// The estimate the whole decision starts from, now and with this finding counted as evidence.
function effectBox(result, isApplied) {
  const e = result.effect;
  const bad = badWord(result);
  const box = el("div", `web-effect${isApplied ? " applied" : ""}`);
  box.append(el("p", "web-effect-label", `Model's estimate that the road is ${bad}`));

  const meter = el("div", "web-meter");
  meter.setAttribute("aria-hidden", "true");
  const before = el("i", "web-meter-before");
  before.style.left = pct(e.prior_before);
  const after = el("i", "web-meter-after");
  after.style.width = pct(e.prior_after);
  meter.append(after, before);

  const values = el("p", "web-effect-values");
  const now = el("span", null);
  now.append(el("small", null, isApplied ? "Without it" : "Now"), el("b", null, pct(e.prior_before)));
  const withIt = el("span", "with");
  withIt.append(el("small", null, isApplied ? "Using it" : "If you use it"), el("b", null, pct(e.prior_after)));
  values.append(now, el("span", "arrow", "→"), withIt);

  const changed = Math.round(e.prior_before * 100) !== Math.round(e.prior_after * 100);
  const why = changed ? `${reading[result.finding]} ${bad}.` : `This finding leaves the model's estimate that the road is ${bad} unchanged.`;
  const drone = e.inspection_before.recommend === e.inspection_after.recommend
    && Math.round(e.inspection_before.net_value) === Math.round(e.inspection_after.net_value)
    ? `A drone check stays ${droneWorth(e.inspection_after)}.`
    : isApplied
      ? `A drone check is now ${droneWorth(e.inspection_after)}, instead of ${droneWorth(e.inspection_before)} without it.`
      : `A drone check would then be ${droneWorth(e.inspection_after)}, instead of ${droneWorth(e.inspection_before)} now.`;
  box.append(meter, values, el("p", "web-effect-why", `${why} ${drone}`));
  return box;
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
  // Past decisions on similar reports, beside what the feeds say. A reference only (see history.js).
  if (result.history?.available && result.history.line) {
    const history = el("p", "web-history");
    history.append(el("span", "basis", "History"), ` ${result.history.line}`);
    parts.push(history);
  }
  const isApplied = applied === result.finding;
  parts.push(effectBox(result, isApplied));
  const actions = el("div", "web-actions");
  const use = el("button", `btn small${isApplied ? "" : " primary"}`, isApplied ? "Stop using as evidence" : "Use as evidence");
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
  const hint = el("p", "web-hint", isApplied
    ? "The model counts these reports: the drone check and the responses below start from the updated estimate."
    : "Your call: the model only counts these reports if you choose to. Nothing is sent or changed outside PigeonOps.");
  actions.append(use, hint, error);
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
