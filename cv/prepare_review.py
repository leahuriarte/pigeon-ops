"""Prepare a portable California evidence library with visually reviewed road areas."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import cv2
import numpy as np

from cv.common import ROOT, load_model, predict_frame
from cv.road_state import point_in_polygon


def prepare_cases(snapshot, output, catalog_path=ROOT / "california_review.json"):
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    snapshot_manifest = json.loads((snapshot / "source.json").read_text(encoding="utf-8"))
    cameras = {item["image"]: item for item in json.loads((snapshot / "snapshots.json").read_text(encoding="utf-8"))["images"]}
    cached_path = ROOT / "outputs" / "california-highways" / "evidence.json"
    cached_run = json.loads(cached_path.read_text(encoding="utf-8")) if cached_path.exists() else {}
    cached = {Path(item["source_file"]).name: item for item in cached_run.get("observations", [])}
    model = None
    cases = []
    for item in catalog["camera_cases"] + catalog["historical_cases"]:
        camera = cameras.get(item["file"]) if "download_url" not in item else None
        if "download_url" not in item and camera is None:
            raise ValueError(f"Snapshot has no metadata for {item['file']}")
        if camera:
            source = snapshot / "images" / item["file"]
            case_id = "caltrans-" + source.stem
            provenance = {
                "publisher": "Caltrans", "credit": "Caltrans District 7",
                "source_url": camera["source_url"], "context_url": snapshot_manifest["source"],
                "retrieved_at": camera["retrieved_at_utc"], "source_date": None,
                "date_description": "Saved snapshot; capture time is shown in the original image overlay.",
                "location": camera["location"], "historical": False,
                "license": "Caltrans Conditions of Use", "terms_url": "https://dot.ca.gov/conditions-of-use",
            }
            title = camera["location"]["locationName"]
        else:
            source = ROOT / "data" / "california-hazards" / item["file"]
            source.parent.mkdir(parents=True, exist_ok=True)
            if not source.exists():
                request = Request(item["download_url"], headers={"User-Agent": "PigeonOps/0.1"})
                with urlopen(request, timeout=60) as response:
                    source.write_bytes(response.read())
            case_id, title = item["id"], item["title"]
            provenance = {
                "publisher": "USGS" if "usgs.gov" in item["source_url"] else "Caltrans",
                "credit": item["credit"], "source_url": item["source_url"],
                "image_url": item["download_url"], "source_date": item["source_date"],
                "date_description": item["date_kind"], "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "location": {"locationName": "California State Route 1, Big Sur coast", "route": "SR-1"},
                "historical": True, "license": item["license"],
            }
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        provenance["sha256"] = digest
        frame = cv2.imread(str(source))
        if frame is None:
            raise ValueError(f"Unreadable source image: {source}")
        height, width = frame.shape[:2]
        unavailable = item["visual_label"] == "image_unavailable"
        previous = cached.get(item["file"], {})
        cache_matches = camera and (previous.get("camera_context") or {}).get("sha256") == digest and cached_run.get("model") == "yolo11s.pt" and cached_run.get("image_size") == 1280 and cached_run.get("confidence_threshold") == 0.25 and not cached_run.get("tile_size")
        if unavailable:
            predictions = []
        elif cache_matches:
            predictions = previous["detections"]
        else:
            if model is None:
                model = load_model("yolo11s.pt")
            predictions = predict_frame(model, frame, 1280, 0.25)
        road = [(x * width, y * height) for x, y in item["road"]]
        retained = [prediction for prediction in predictions if road and point_in_polygon(
            (prediction["box"][0] + prediction["box"][2]) / 2,
            (prediction["box"][1] + prediction["box"][3]) / 2, road)]
        rendered = frame.copy()
        if road:
            polygon = np.array(road, np.int32)
            tint = rendered.copy()
            cv2.fillPoly(tint, [polygon], (150, 190, 0))
            rendered = cv2.addWeighted(tint, 0.12, rendered, 0.88, 0)
            cv2.polylines(rendered, [polygon], True, (210, 230, 20), 2)
        for detection in retained:
            x1, y1, x2, y2 = [round(value) for value in detection["box"]]
            cv2.rectangle(rendered, (x1, y1), (x2, y2), (80, 220, 100), 2)
        if item.get("hazard"):
            hazard = np.array([(x * width, y * height) for x, y in item["hazard"]], np.int32)
            cv2.polylines(rendered, [hazard], True, (0, 130, 255), max(2, width // 500))
        destination = output / case_id
        destination.mkdir(parents=True, exist_ok=True)
        for filename, image in [("source.jpg", frame), ("annotated.jpg", rendered)]:
            scale = min(1.0, 1400 / max(image.shape[:2]))
            displayed = cv2.resize(image, (round(width * scale), round(height * scale))) if scale < 1 else image
            cv2.imwrite(str(destination / filename), displayed)
        restricted = item["visual_label"] in {"image_unavailable", "insufficient_detail", "construction_visible"}
        cases.append({
            "id": case_id, "title": title, "visual_label": item["visual_label"],
            "review_note": item["note"], "annotation_method": catalog["annotation_method"],
            "image_quality": "unavailable" if unavailable else "limited" if restricted else "reviewable",
            "road_region": item["road"], "hazard_region": item.get("hazard", []),
            "source": provenance, "source_image": f"/inspection-assets/{case_id}/source.jpg",
            "annotated_image": f"/inspection-assets/{case_id}/annotated.jpg",
            "vehicle_detections": len(retained), "detections_before_region_filter": len(predictions),
            "excluded_detections": len(predictions) - len(retained),
            "vehicle_model": "YOLO11s, 1280, confidence 0.25; cached source-matched detections where available",
            "detector_observation": "inconclusive",
            "allowed_observations": ["inconclusive"] if restricted else ["blocked", "clear", "inconclusive"],
            "review_status": "needs_dispatcher_review", "simulated_application": True,
        })
        print(f"{case_id}: {len(predictions)} detections -> {len(retained)} in road region; {item['visual_label']}", flush=True)
    summary = {
        "cases": len(cases), "camera_cases": sum(not case["source"]["historical"] for case in cases),
        "historical_cases": sum(case["source"]["historical"] for case in cases),
        "vehicle_detections_before": sum(case["detections_before_region_filter"] for case in cases),
        "vehicle_detections_in_road_regions": sum(case["vehicle_detections"] for case in cases),
        "excluded_detections": sum(case["excluded_detections"] for case in cases),
        "interpretation": "Region filtering removes off-road detections; this is not a measured accuracy improvement or a validated road-blockage classifier.",
    }
    library = {"available": True, "scope": catalog["scope"], "annotation_method": catalog["annotation_method"],
               "created_at": datetime.now(timezone.utc).isoformat(), "summary": summary, "cases": cases}
    (output / "library.json").write_text(json.dumps(library, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data" / "california" / "20261114T191704Z")
    args = parser.parse_args()
    prepare_cases(args.snapshot, ROOT / "outputs" / "review-cases")


if __name__ == "__main__":
    main()
