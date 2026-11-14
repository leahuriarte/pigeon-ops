import { clearFootageOpinion, renderFootageOpinion } from "./copilot.js";
import { withWeb } from "./triage.js";

const dialog = document.querySelector("#inspection-dialog");
const clips = document.querySelector("#inspection-clips");
const video = document.querySelector("#inspection-video");
const photo = document.querySelector("#inspection-photo");
const toggle = document.querySelector("#inspection-image-toggle");
let libraryPromise;
let cases = [];
let selected;
let selectedId = null;
let scenarioId;
let showSource = false;
let submitting = false;

const labels = {
  visible_obstruction: "Wreck and debris across the travel lanes",
  no_visible_obstruction: "No visible obstruction in this view",
  dense_slow_traffic: "Dense, slow traffic · cause not visible",
  standing_queue: "Standing queue · cause not visible",
  flooded_road: "Water across the road · depth not visible",
  insufficient_detail: "Insufficient detail",
  image_unavailable: "Footage unavailable",
};
const escapeHtml = (value) => String(value).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
let signalLabels = {};

function showMedia() {
  video.hidden = showSource;
  photo.hidden = !showSource;
  if (showSource) video.pause();
  else video.play().catch(() => {});
  toggle.textContent = showSource ? "Show CV overlay" : "Show source frame";
}

function renderCase() {
  selected = cases.find((item) => item.id === selectedId);
  if (!selected) return;
  clips.querySelectorAll(".clip").forEach((button) => button.setAttribute("aria-selected", String(button.dataset.case === selectedId)));
  showSource = false;
  video.src = selected.annotated_video;
  video.poster = selected.annotated_image;
  photo.src = selected.source_image;
  photo.alt = `${selected.title}, unannotated source frame`;
  showMedia();
  const cv = selected.cv_assessment;
  document.querySelector("#inspection-case-title").textContent = selected.title;
  document.querySelector("#inspection-finding").textContent = labels[selected.visual_label] || "Visual review required";
  document.querySelector("#inspection-note").textContent = selected.review_note;
  document.querySelector("#inspection-count").textContent =
    `${cv.window_seconds} s of footage at ${cv.sample_fps} frames/s · ${selected.vehicle_detections} vehicles on the marked road, ${selected.excluded_detections} outside it (median per frame)`;
  document.querySelector("#inspection-cv").dataset.signal = cv.suggested_observation;
  document.querySelector("#inspection-cv-signal").textContent = signalLabels[cv.suggested_observation];
  document.querySelector("#inspection-cv-reason").textContent = `${cv.reason[0].toUpperCase()}${cv.reason.slice(1)}.`;
  document.querySelector("#inspection-cv-basis").textContent = cv.basis;
  const recorded = selected.source.date_description || selected.source.source_date || "";
  document.querySelector("#inspection-date").textContent = !selected.source.historical
    ? `Saved ${new Date(selected.source.retrieved_at).toLocaleString()}`
    : /^pre-recorded/i.test(recorded) ? recorded : `Pre-recorded · ${recorded}`;
  document.querySelector("#inspection-location").textContent = selected.source.location.locationName;
  const source = document.querySelector("#inspection-source");
  source.textContent = `${selected.source.credit} · ${selected.source.license}`;
  source.href = selected.source.source_url;
  document.querySelector("#inspection-review-note").value = "";
  document.querySelector("#inspection-error").textContent = "";
  document.querySelectorAll("[name=inspection-observation]").forEach((radio) => {
    radio.disabled = !selected.allowed_observations.includes(radio.value);
    radio.checked = radio.value === cv.suggested_observation && !radio.disabled;
  });
  document.querySelector("#inspection-apply").disabled = !document.querySelector("[name=inspection-observation]:checked");
  document.querySelector("#inspection-restriction").textContent = selected.allowed_observations.length === 1
    ? "This clip supports an inconclusive observation only."
    : "Confirm or change the suggestion. Traffic getting through this frame does not prove the road is safe beyond it.";
  renderFootageOpinion(selected, signalLabels, scenarioId); // LLM second read; never changes the preselected radio.
}

// Opens the dialog with the clips that fit this kind of check (`words` is the scenario's disruption.check).
// Returns false when the library has no clip showing that kind of hazard, so the page falls back to simulated results.
export async function openInspection(id, words) {
  if (!libraryPromise) {
    libraryPromise = fetch("/api/inspection-cases").then((response) => {
      if (!response.ok) throw new Error("Inspection library could not be loaded");
      return response.json();
    }).catch(() => ({ cases: [] }));
  }
  const library = await libraryPromise;
  cases = (library.cases || []).filter((item) => !item.check_types || item.check_types.includes(words.type));
  if (!cases.some((item) => item.visual_label !== "no_visible_obstruction")) return false;
  scenarioId = id;
  signalLabels = { blocked: `Road ${words.bad}`, clear: "Road clear: traffic gets through", inconclusive: "Inconclusive" };
  document.querySelector("#inspection-dialog-heading").textContent = `${words.label}: what does the footage show?`;
  document.querySelectorAll("[name=inspection-observation]").forEach((radio) => { radio.nextElementSibling.textContent = signalLabels[radio.value]; });
  clips.innerHTML = cases.map((item) => `<button class="clip" type="button" role="tab" data-case="${escapeHtml(item.id)}" aria-selected="false">
    <img src="${escapeHtml(item.annotated_image)}" alt="">
    <span>${escapeHtml(item.title)}</span>
  </button>`).join("");
  selectedId = cases[0].id;
  renderCase();
  dialog.showModal();
  window.dispatchEvent(new Event("inspection-dialog-open"));
  return true;
}

export function resetInspection() {
  if (dialog.open) dialog.close();
  scenarioId = null;
  clearFootageOpinion();
}

clips.addEventListener("click", (event) => {
  const button = event.target.closest(".clip");
  if (!button || submitting) return;
  selectedId = button.dataset.case;
  renderCase();
});
toggle.addEventListener("click", () => {
  showSource = !showSource;
  showMedia();
});
dialog.addEventListener("close", () => video.pause());
document.querySelector("#inspection-close").addEventListener("click", () => dialog.close());
document.querySelectorAll("[name=inspection-observation]").forEach((radio) => radio.addEventListener("change", () => {
  document.querySelector("#inspection-apply").disabled = submitting;
}));
document.querySelector("#inspection-apply").addEventListener("click", async () => {
  const observation = document.querySelector("[name=inspection-observation]:checked")?.value;
  if (!observation || !selected || submitting) return;
  const caseItem = selected;
  const reviewedScenario = scenarioId;
  const button = document.querySelector("#inspection-apply");
  submitting = true;
  button.disabled = true;
  button.textContent = "Applying…";
  clips.querySelectorAll(".clip").forEach((clip) => { clip.disabled = true; });
  try {
    const response = await fetch("/api/inspection-review", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(withWeb({ case_id: caseItem.id, scenario: reviewedScenario, observation,
        note: document.querySelector("#inspection-review-note").value, simulated_application: true })),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Review could not be applied");
    if (scenarioId !== reviewedScenario) return;
    window.dispatchEvent(new CustomEvent("inspection-reviewed", { detail: { ...result, caseItem } }));
    dialog.close();
  } catch (error) {
    document.querySelector("#inspection-error").textContent = error.message;
  } finally {
    submitting = false;
    button.disabled = false;
    button.textContent = "Apply to scenario";
    clips.querySelectorAll(".clip").forEach((clip) => { clip.disabled = false; });
  }
});
