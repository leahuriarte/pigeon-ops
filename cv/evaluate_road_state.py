"""Measure how often detector-based road states agree with reference annotations."""

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np

from cv.benchmark import reference_boxes
from cv.common import MODEL_CHOICES, ROOT, dataset_root, load_model, predict_frame
from cv.road_state import DEFAULT_RULE, assess_road, likelihood_table, to_pixels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=MODEL_CHOICES, default="yolo11s.pt")
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--tile-size", type=int, default=0)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--rois", default=str(ROOT / "road_rois.json"))
    args = parser.parse_args()
    root = dataset_root()
    from ultralytics.utils import YAML
    names = YAML.load(root / "data.yaml")["names"]
    annotations = json.loads(Path(args.rois).read_text(encoding="utf-8"))
    tile_suffix = f"-tile{args.tile_size}" if args.tile_size else ""
    output = ROOT / "outputs" / f"road-state-{args.model.removesuffix('.pt')}-{args.imgsz}{tile_suffix}"
    output.mkdir(parents=True, exist_ok=True)
    model = load_model(args.model)
    records, pairs, panels = [], [], []
    for item in annotations["images"]:
        image = root / item["split"] / "images" / item["image"]
        frame = cv2.imread(str(image))
        if frame is None:
            raise ValueError(f"Cannot decode {image}")
        height, width = frame.shape[:2]
        road = to_pixels(item["road"], width, height)
        label = root / item["split"] / "labels" / f"{Path(item['image']).stem}.txt"
        reference = assess_road(reference_boxes(label, width, height, names), road)
        predictions = predict_frame(model, frame, args.imgsz, args.confidence, args.tile_size)
        brightness = float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean())
        detected = assess_road([detection["box"] for detection in predictions], road, frame_brightness=brightness)
        pairs.append((reference["visual_state"], detected["visual_state"]))
        records.append({
            "image": item["image"], "reference_state": reference["visual_state"],
            "detected_state": detected["visual_state"],
            "reference_on_road": reference["vehicles_on_road"], "detected_on_road": detected["vehicles_on_road"],
            "reference_close": reference["closely_spaced_vehicles"], "detected_close": detected["closely_spaced_vehicles"],
        })
        overlay = frame.copy()
        cv2.fillPoly(overlay, [np.array(road, np.int32)], (0, 200, 0))
        panel = cv2.addWeighted(overlay, 0.25, frame, 0.75, 0)
        for detection in predictions:
            x1, y1, x2, y2 = [round(value) for value in detection["box"]]
            cv2.rectangle(panel, (x1, y1), (x2, y2), (80, 220, 255), 2)
        panel = cv2.resize(panel, (640, round(640 * height / width)))
        caption = f"ref {reference['visual_state']} / cv {detected['visual_state']}"
        cv2.rectangle(panel, (0, 0), (640, 34), (16, 42, 53), -1)
        cv2.putText(panel, caption, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
        panels.append(cv2.resize(panel, (640, 360)))
        print(f"{len(records)}/{len(annotations['images'])}: {caption}", flush=True)
    while len(panels) % 3:
        panels.append(np.zeros_like(panels[0]))
    cv2.imwrite(str(output / "review.jpg"), np.vstack([np.hstack(panels[index:index + 3]) for index in range(0, len(panels), 3)]))
    confusion = {}
    for reference, detected in pairs:
        confusion.setdefault(reference, {}).setdefault(detected, 0)
        confusion[reference][detected] += 1
    summary = {
        "dataset": annotations["dataset"], "images": len(records), "model": args.model,
        "image_size": args.imgsz, "tile_size": args.tile_size, "confidence_threshold": args.confidence,
        "rule": DEFAULT_RULE, "confusion_reference_to_detected": confusion,
        "agreement": round(sum(reference == detected for reference, detected in pairs) / len(pairs), 4),
        "signal_given_reference_state": likelihood_table(pairs),
        "evaluation": "Reference states apply the same rule to annotated boxes, so this measures detector-induced error only. "
                      "It does not measure whether dense traffic means a blocked road.",
        "scope": "China aerial imagery with hand-drawn road regions; frames from one video are correlated.",
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with (output / "per-image.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
