"""Serve prepared visual evidence without importing optional CV dependencies."""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from backend.decision_engine import analyze
from backend.scenario import load_scenario


ASSETS = Path(__file__).resolve().parents[1] / "cv" / "outputs" / "review-cases"
LIBRARY = ASSETS / "library.json"
AUDIT = ASSETS.parent / "inspection-reviews.jsonl"
AUDIT_LOCK = threading.Lock()


def list_inspections(library_path=LIBRARY):
    if not library_path.is_file():
        return {"available": False, "cases": [], "message": "Prepare the optional image library with python -m cv.prepare_review."}
    return json.loads(library_path.read_text(encoding="utf-8"))


def review_inspection(payload, library_path=LIBRARY, audit_path=AUDIT):
    if not isinstance(payload, dict):
        raise ValueError("Review must be a JSON object")
    case = next((item for item in list_inspections(library_path)["cases"] if item["id"] == payload.get("case_id")), None)
    if case is None:
        raise ValueError("Unknown inspection image")
    observation = payload.get("observation")
    if observation not in case["allowed_observations"]:
        raise ValueError("This image does not support that observation; use inconclusive")
    if payload.get("simulated_application") is not True:
        raise ValueError("External images may only be applied to the synthetic demo scenario")
    note = payload.get("note", "")
    if not isinstance(note, str) or len(note) > 500:
        raise ValueError("Review note must be text of at most 500 characters")
    variant = payload.get("scenario", "disruption")
    if not isinstance(variant, str):
        raise ValueError("Scenario must be a name")
    analysis = analyze(load_scenario(variant=variant), observation)
    review = {
        "case_id": case["id"], "scenario": variant, "observation": observation,
        "note": note.strip(), "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "review_method": "explicit_demo_review", "simulated_application": True,
        "source_url": case["source"]["source_url"], "source_sha256": case["source"]["sha256"],
        "interpretation": "Reviewed external illustration applied to a fictional scenario; not a current road-status report.",
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOCK, audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(review) + "\n")
    return {"analysis": analysis, "review": review}
