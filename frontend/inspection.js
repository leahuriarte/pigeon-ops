const dialog = document.querySelector("#inspection-dialog");
const picker = document.querySelector("#inspection-case");
const photo = document.querySelector("#inspection-photo");
let libraryPromise;
let cases = [];
let selected;
let scenarioId;
let showSource = false;
let submitting = false;

const labels = {
  no_visible_obstruction: "No visible obstruction in this view",
  traffic_present: "Traffic visible · movement unknown",
  insufficient_detail: "Insufficient detail",
  construction_visible: "Construction visible · lane status uncertain",
  image_unavailable: "Camera image unavailable",
  visible_obstruction: "Debris visible on roadway",
};

function renderCase() {
  selected = cases.find((item) => item.id === picker.value);
  if (!selected) return;
  showSource = false;
  photo.src = selected.annotated_image;
  photo.alt = `${selected.title}. ${selected.review_note}`;
  document.querySelector("#inspection-case-title").textContent = selected.title;
  document.querySelector("#inspection-finding").textContent = labels[selected.visual_label] || "Visual review required";
  document.querySelector("#inspection-note").textContent = selected.review_note;
  document.querySelector("#inspection-count").textContent = selected.image_quality === "unavailable"
    ? "No image analysis available"
    : `${selected.vehicle_detections} detections in marked road area · ${selected.excluded_detections} outside it`;
  document.querySelector("#inspection-image-toggle").textContent = "Show source image";
  document.querySelector("#inspection-date").textContent = selected.source.historical
    ? `Historical image · ${selected.source.source_date} · ${selected.source.date_description}`
    : `Saved ${new Date(selected.source.retrieved_at).toLocaleString()} · capture time: see image overlay`;
  const location = selected.source.location;
  document.querySelector("#inspection-location").textContent = location.latitude
    ? `${location.route} · ${Number(location.latitude).toFixed(4)}, ${Number(location.longitude).toFixed(4)}`
    : location.locationName;
  const source = document.querySelector("#inspection-source");
  source.textContent = `${selected.source.credit} · ${selected.source.license}`;
  source.href = selected.source.source_url;
  document.querySelector("#inspection-review-note").value = "";
  document.querySelector("#inspection-error").textContent = "";
  document.querySelectorAll("[name=inspection-observation]").forEach((radio) => {
    radio.checked = false;
    radio.disabled = !selected.allowed_observations.includes(radio.value);
  });
  document.querySelector("#inspection-apply").disabled = true;
  document.querySelector("#inspection-restriction").textContent = selected.allowed_observations.length === 1
    ? "This image supports an inconclusive observation only."
    : "Choose what this view supports. No visible obstruction does not establish road safety.";
}

export async function openInspection(id) {
  if (!libraryPromise) {
    libraryPromise = fetch("/api/inspection-cases").then((response) => {
      if (!response.ok) throw new Error("Inspection library could not be loaded");
      return response.json();
    }).catch(() => ({ cases: [] }));
  }
  const library = await libraryPromise;
  cases = library.cases || [];
  if (!cases.length) return false;
  scenarioId = id;
  picker.replaceChildren(...cases.map((item) => new Option(
    `${item.source.historical ? "Historical · " : ""}${item.title}`, item.id)));
  picker.value = cases.find((item) => item.id === "caltrans-i405335wilmingtonave")?.id || cases[0].id;
  renderCase();
  dialog.showModal();
  return true;
}

export function resetInspection() {
  if (dialog.open) dialog.close();
  scenarioId = null;
}

picker.addEventListener("change", renderCase);
document.querySelector("#inspection-image-toggle").addEventListener("click", () => {
  showSource = !showSource;
  photo.src = showSource ? selected.source_image : selected.annotated_image;
  document.querySelector("#inspection-image-toggle").textContent = showSource ? "Show marked road area" : "Show source image";
});
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
  picker.disabled = true;
  try {
    const response = await fetch("/api/inspection-review", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case_id: caseItem.id, scenario: reviewedScenario, observation,
        note: document.querySelector("#inspection-review-note").value, simulated_application: true }),
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
    button.textContent = "Use for demo";
    picker.disabled = false;
  }
});
