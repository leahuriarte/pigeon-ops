// Web check: an agent searches public feeds (CHP incident log, Caltrans message signs, NWS alerts)
// near the reported spot and says whether they back up the report. Nothing changes until the
// dispatcher clicks "Use this finding"; the finding is then sent as `web=` with every later request,
// and the server starts the decision from the updated blockage chance.
// Feed text and agent text come from outside, so they are always set with textContent.

let applied = null; // The finding the dispatcher applied, or null.
let appliedMode = null; // The feed ("live" or "demo") the applied finding came from.
let token = 0;
let mode = "live";
const results = new Map(); // One run per incident and feed while the page is open; cleared by clearTriage.

export const appliedWeb = () => applied;
export const setAppliedWeb = (finding) => {
  applied = finding;
  if (!finding) appliedMode = null;
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
  corroborates: "Public feeds back up the report",
  contradicts: "Public feeds point the other way",
  nothing_found: "Nothing in the public feeds yet",
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

function modeSwitch(container, scenarioId, options) {
  const box = el("div", "web-mode");
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", "Feed to search");
  [["live", "Live feeds"], ["demo", "Demo feed"]].forEach(([value, label]) => {
    const button = el("button", "btn small", label);
    button.type = "button";
    button.setAttribute("aria-pressed", String(value === mode));
    button.addEventListener("click", () => {
      if (value === mode) return;
      mode = value;
      renderTriage(container, scenarioId, options);
    });
    box.append(button);
  });
  return box;
}

function header(container, scenarioId, options) {
  const top = el("div", "web-head");
  top.append(el("h2", null, "Web check"), modeSwitch(container, scenarioId, options));
  return top;
}

function evidenceList(result) {
  const box = el("details", "more web-evidence");
  box.append(el("summary", null, `What the agent found (${result.evidence.length} record${result.evidence.length === 1 ? "" : "s"})`));
  if (!result.evidence.length) box.append(el("p", "note", "No records near the reported spot."));
  const list = el("ul");
  result.evidence.forEach((item) => {
    const row = el("li", item.cited ? "cited" : "");
    const meta = el("p", "web-meta");
    meta.append(el("span", `basis ${item.basis === "live" ? "live" : "synthetic"}`, item.basis === "live" ? "Live" : "Synthetic"));
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
  const bad = result.check.bad;
  const drone = e.inspection_after.recommend
    ? `a drone check would still be worth it (${money(e.inspection_after.net_value)} estimated net)`
    : `a drone check would not be worth it (${money(e.inspection_after.net_value)} estimated net)`;
  if (e.prior_after === e.prior_before) return `Using it leaves the assumed chance CA-47 is ${bad} at ${pct(e.prior_before)}.`;
  return `Using it moves the chance CA-47 is ${bad} from ${pct(e.prior_before)} (assumed) to ${pct(e.prior_after)} (estimated), and ${drone}.`;
}

// Runs the web check for an incident and renders the card. `onApply(finding | null, result)` is called
// when the dispatcher uses or removes the finding.
export async function renderTriage(container, scenarioId, options = {}) {
  const mine = ++token;
  const stale = () => mine !== token;
  const feed = mode;
  container.hidden = false;
  container.replaceChildren(header(container, scenarioId, options),
    el("p", "note", `Searching the CHP incident log, Caltrans message signs and NWS alerts near the reported spot${mode === "demo" ? " (demo feed)" : ""}…`));
  const key = `${scenarioId}|${mode}`;
  let result = results.get(key);
  if (!result) {
    try {
      const response = await fetch(`/api/triage?${new URLSearchParams({ scenario: scenarioId, mode })}`, { signal: AbortSignal.timeout(120000) });
      result = await response.json();
      if (!response.ok) result = { available: false, reason: result.error };
    } catch {
      result = { available: false, reason: "no response" };
    }
    if (result.available) results.set(key, result);
  }
  if (stale()) return;
  if (!result.available) {
    container.replaceChildren(header(container, scenarioId, options), el("p", "note", "The web check could not run. Decide with the drone check alone."));
    return;
  }

  container.dataset.finding = result.finding;
  const by = result.agent === "llm"
    ? el("span", "tag llm", `AI agent · ${result.confidence} confidence · unvalidated`)
    : el("span", "tag llm", "Keyword rule · no AI");
  const source = result.mode === "live"
    ? "Live public feeds describe real roads now. The incident on this page is fictional, so a real match is unlikely."
    : `Demo feed: synthetic records written for this incident, not real reports.${result.fallback_reason ? " Shown because every live feed failed." : ""}`;

  const parts = [header(container, scenarioId, options), by, el("h3", null, headline[result.finding]), el("p", "web-summary", result.summary)];
  const isApplied = applied === result.finding && appliedMode === feed;
  if (isApplied) {
    parts.push(el("p", "web-effect applied", `Applied. The estimated chance CA-47 is ${result.check.bad} is now ${pct(result.effect.prior_after)} (was ${pct(result.effect.prior_before)}, assumed).`));
  } else {
    parts.push(el("p", "web-effect", effectLine(result)));
  }
  const actions = el("div", "web-actions");
  const use = el("button", `btn small${isApplied ? "" : " primary"}`, isApplied ? "Stop using it" : "Use this finding");
  use.type = "button";
  const error = el("p", "note");
  error.hidden = true;
  use.addEventListener("click", async () => {
    use.disabled = true;
    error.hidden = true;
    const previous = [applied, appliedMode];
    applied = isApplied ? null : result.finding;
    appliedMode = applied ? feed : null;
    try {
      await options.onApply?.(applied, result);
    } catch (failure) {
      console.error(failure);
      [applied, appliedMode] = previous;
      use.disabled = false;
      error.textContent = isApplied ? "Could not stop using the finding. Try again." : "Could not apply the finding. Try again.";
      error.hidden = false;
      return;
    }
    if (!stale()) renderTriage(container, scenarioId, options);
  });
  actions.append(use, error);
  parts.push(actions, evidenceList(result));
  const notes = [source, `The update uses an assumed reliability for web reports (${result.effect.likelihoods_basis}).`];
  if (result.agent_note) notes.push(result.agent_note.startsWith("LLM unavailable")
    ? "The AI agent is unavailable, so a keyword rule decided."
    : "The AI agent's report failed a check, so a keyword rule decided.");
  const fine = el("p", "fine-print", notes.join(" "));
  if (result.agent_note) fine.title = result.agent_note;
  parts.push(fine);
  container.replaceChildren(...parts);
}
