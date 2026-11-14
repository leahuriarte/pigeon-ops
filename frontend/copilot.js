// LLM-written panels: a second read of the drone footage, a dispatcher brief, and an "Ask" box.
// The engine's numbers and ranking stay the source of truth; these panels only explain them.
// Every call is asynchronous with a timeout, and any failure hides the panel quietly, so the page
// works exactly as before without the LLM. LLM text is always set with textContent, never innerHTML.

const signalWords = { blocked: "road blocked", clear: "road clear", inconclusive: "inconclusive" };
let statusPromise = null;

function llmStatus() {
  statusPromise ??= fetch("/api/llm/status", { signal: AbortSignal.timeout(5000) })
    .then((response) => (response.ok ? response.json() : { enabled: false }))
    .catch(() => ({ enabled: false }));
  return statusPromise;
}

// Resolves to the JSON body, or to { available: false } on any error or timeout.
async function getJSON(url, timeoutMs, options = {}) {
  try {
    const response = await fetch(url, { ...options, signal: AbortSignal.timeout(timeoutMs) });
    const body = await response.json();
    return response.ok ? body : { available: false, reason: body.error || `HTTP ${response.status}` };
  } catch {
    return { available: false, reason: "no response" };
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const llmTag = (text) => el("span", "tag llm", text);
const madeBy = (model) => el("p", "fine-print", `Model: ${model}`);

// ---------------------------------------------------------------------------
// 1. Second read of the footage, under the CV suggestion in the inspection dialog.
// It never changes the preselected radio, which stays the CV suggestion.
// ---------------------------------------------------------------------------
let footageToken = 0;

export function clearFootageOpinion() {
  footageToken += 1;
  const card = document.querySelector("#inspection-llm");
  card.hidden = true;
  card.replaceChildren();
}

export async function renderFootageOpinion(caseItem, labels = {}, scenarioId = null) {
  clearFootageOpinion();
  const token = footageToken;
  const card = document.querySelector("#inspection-llm");
  if (!(await llmStatus()).enabled || token !== footageToken) return;
  delete card.dataset.agrees;
  card.replaceChildren(llmTag("AI review · unvalidated"), el("p", "note", "Reviewing…"));
  card.hidden = false;
  const query = new URLSearchParams({ case_id: caseItem.id });
  if (scenarioId) query.set("scenario", scenarioId);
  const result = await getJSON(`/api/llm/footage?${query}`, 45000);
  if (token !== footageToken) return; // The dispatcher picked another clip meanwhile.
  if (!result.available) {
    card.hidden = true;
    return;
  }
  const finding = labels[result.observation]?.toLowerCase() ?? signalWords[result.observation] ?? result.observation;
  const headline = el("h3", null, result.agrees_with_cv
    ? `Agrees: ${finding}`
    : `Disagrees: ${finding}`);
  headline.append(el("small", null, `${result.confidence} confidence`));
  const parts = [llmTag("AI review · unvalidated"), headline, el("p", null, result.rationale)];
  if (result.checks?.length) {
    const list = el("ul", "copilot-checks");
    result.checks.forEach((item) => list.append(el("li", null, item)));
    parts.push(el("strong", "copilot-label", "Check"), list);
  }
  parts.push(madeBy(result.model));
  card.dataset.agrees = String(Boolean(result.agrees_with_cv));
  card.replaceChildren(...parts);
}

// ---------------------------------------------------------------------------
// 2. Dispatcher brief. One renderer for the incident step and the respond step.
// ---------------------------------------------------------------------------
const briefTokens = new WeakMap();

export function clearBrief(container) {
  briefTokens.set(container, (briefTokens.get(container) ?? 0) + 1);
  container.hidden = true;
  container.replaceChildren();
}

export async function renderBrief(container, scenarioId, observation = null) {
  clearBrief(container);
  const token = briefTokens.get(container);
  const stale = () => briefTokens.get(container) !== token;
  if (!(await llmStatus()).enabled || stale()) return;
  const label = "AI explanation · unvalidated";
  container.replaceChildren(llmTag(label), el("p", "note", "Loading…"));
  container.hidden = false;
  const query = new URLSearchParams({ scenario: scenarioId });
  if (observation) query.set("observation", observation);
  const brief = await getJSON(`/api/llm/brief?${query}`, 45000);
  if (stale()) return; // The scenario or the observation changed meanwhile.
  if (!brief.available) {
    container.hidden = true;
    return;
  }
  const more = el("dl", "copilot-more");
  [["Could change if", brief.what_would_change_it], ["Affected", brief.who_is_affected]].forEach(([term, text]) => {
    const row = el("div");
    row.append(el("dt", null, term), el("dd", null, text));
    more.append(row);
  });
  container.replaceChildren(llmTag(label), el("h3", null, brief.headline), el("p", null, brief.why), more, madeBy(brief.model));
}

// ---------------------------------------------------------------------------
// 3. Ask about this decision. Hidden entirely when the LLM is off.
// ---------------------------------------------------------------------------
let askToken = 0;

export function resetAsk() {
  askToken += 1;
  const answer = document.querySelector("#copilot-answer");
  answer.hidden = true;
  answer.replaceChildren();
  document.querySelector("#copilot-ask-button").disabled = false;
}

function describeCall(call) {
  const args = call.arguments && typeof call.arguments === "object"
    ? Object.entries(call.arguments).map(([key, value]) => `${key}: ${value}`).join(", ")
    : String(call.arguments ?? "");
  return `${call.name}(${args})`;
}

export async function initAsk(getContext) {
  const box = document.querySelector("#copilot-ask");
  const input = document.querySelector("#copilot-question");
  const button = document.querySelector("#copilot-ask-button");
  const answer = document.querySelector("#copilot-answer");

  async function submit(question) {
    question = question.trim();
    if (!question) return;
    const token = ++askToken;
    const { scenario, observation } = getContext();
    button.disabled = true;
    answer.replaceChildren(el("p", "note", "Calculating…"));
    answer.hidden = false;
    const result = await getJSON("/api/llm/ask", 120000, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario, observation: observation || null, question }),
    });
    if (token !== askToken) return; // Scenario reset or a newer question.
    button.disabled = false;
    if (!result.available) {
      answer.replaceChildren(el("p", "note", "Couldn't get an answer. Try again."));
      return;
    }
    const parts = [llmTag("AI answer · unvalidated"), el("p", "copilot-answer-text", result.answer)];
    if (!result.grounded) {
      parts.push(el("p", "copilot-warn", "Some figures aren't in the calculation. Check before using them."));
    }
    const calculation = el("details", "more");
    calculation.append(el("summary", null, "Calculation details"), el("p", "fine-print", result.engine_calls?.length
      ? `Engine calls: ${result.engine_calls.map(describeCall).join("; ")}.`
      : "The AI answered without checking the calculation."));
    parts.push(calculation);
    parts.push(madeBy(result.model));
    answer.replaceChildren(...parts);
  }

  document.querySelector("#copilot-ask-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submit(input.value);
  });
  box.querySelectorAll("[data-question]").forEach((chip) => chip.addEventListener("click", () => {
    input.value = chip.dataset.question;
    submit(chip.dataset.question);
  }));
  box.hidden = !(await llmStatus()).enabled;
}
