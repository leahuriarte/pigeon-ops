"""Serve the prepared drone inspection library without importing CV dependencies.

The library is built offline by `python -m cv.prepare_review` and committed, so
the demo works on a fresh clone. Reviews are appended to a local, ignored log.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from backend.decision_engine import analyze
from backend.scenario import load_scenario


CV = Path(__file__).resolve().parents[1] / "cv"
ASSETS = CV / "review_library"
LIBRARY = ASSETS / "library.json"
AUDIT = CV / "outputs" / "inspection-reviews.jsonl"
AUDIT_LOCK = threading.Lock()


def list_inspections(library_path=LIBRARY):
    if not library_path.is_file():
        return {"available": False, "cases": [], "message": "Build the drone inspection library with python -m cv.prepare_review."}
    return json.loads(library_path.read_text(encoding="utf-8"))


def review_inspection(payload, library_path=LIBRARY, audit_path=AUDIT):
    if not isinstance(payload, dict):
        raise ValueError("Review must be a JSON object")
    case = next((item for item in list_inspections(library_path)["cases"] if item["id"] == payload.get("case_id")), None)
    if case is None:
        raise ValueError("Unknown inspection clip")
    observation = payload.get("observation")
    if observation not in case["allowed_observations"]:
        raise ValueError("This clip does not support that observation; use inconclusive")
    if payload.get("simulated_application") is not True:
        raise ValueError("External images may only be applied to the synthetic demo scenario")
    note = payload.get("note", "")
    if not isinstance(note, str) or len(note) > 500:
        raise ValueError("Review note must be text of at most 500 characters")
    variant = payload.get("scenario", "disruption")
    if not isinstance(variant, str):
        raise ValueError("Scenario must be a name")
    analysis = analyze(load_scenario(variant=variant), observation)
    suggestion = case.get("cv_assessment", {}).get("suggested_observation")
    review = {
        "case_id": case["id"], "scenario": variant, "observation": observation,
        "note": note.strip(), "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "cv_suggestion": suggestion, "agrees_with_cv": None if suggestion is None else suggestion == observation,
        "review_method": "explicit_demo_review", "simulated_application": True,
        "source_url": case["source"]["source_url"], "source_sha256": case["source"]["sha256"],
        "interpretation": "Reviewed stock drone footage applied to a fictional scenario; not a current road-status report.",
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOCK, audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(review) + "\n")
    return {"analysis": analysis, "review": review}
