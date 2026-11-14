"""Evaluate pretrained vehicle detection on annotated aerial and highway images."""

import argparse
import csv
import html
import json
import random
import time
from pathlib import Path

import cv2

from cv.common import MODEL_CHOICES, ROOT, annotate, dataset_root, load_model, predict_frame, write_report
from cv.metrics import match_detections, summarize_counts


def reference_boxes(path, width, height, names):
    boxes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        values = line.split()
        if not values:
            continue
        name = names[int(values[0])]
        # Collapse the annotated motor-vehicle classes. Bikes are outside scope.
        if name not in {"car", "taxi", "bus", "truck", "other_vehicle"}:
            continue
        coordinates = [float(value) for value in values[1:]]
        if len(coordinates) != 8:
            raise ValueError(f"Expected four OBB corners in {path}")
        xs, ys = coordinates[0::2], coordinates[1::2]
        boxes.append([min(xs) * width, min(ys) * height, max(xs) * width, max(ys) * height])
    return boxes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["uav-obb", "transset"], default="uav-obb")
    parser.add_argument("--model", choices=MODEL_CHOICES, default="yolo11n.pt")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--split", choices=["valid", "test"], default="valid")
    parser.add_argument("--limit", type=int, default=0, help="0 evaluates the complete split")
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--tile-size", type=int, default=0, help="0 disables overlapping tile inference")
    args = parser.parse_args()
    if args.imgsz <= 0 or args.tile_size < 0 or args.limit < 0 or not 0 < args.confidence <= 1:
        parser.error("Use positive image size, nonnegative limit/tile size, and confidence in (0, 1]")
    highway_references = {}
    if args.dataset == "uav-obb":
        root = dataset_root()
        # Use the mapping actually published in the archive.
        from ultralytics.utils import YAML
        names = YAML.load(root / "data.yaml")["names"]
        images = sorted((root / args.split / "images").glob("*.jpg"))
        dataset_name = "UAV-OBB v4"
        source_url = "https://data.mendeley.com/datasets/6snrjwcpkh/4"
        creators = "Ahmad et al."
        evaluation = "Class-agnostic motor vehicles; IoU 0.5 against axis-aligned envelopes of oriented annotations. Not official OBB mAP or blockage accuracy."
        scope = "China aerial imagery; not observations of the synthetic Port of LA incident. Frames may be correlated."
    else:
        root = ROOT / "data" / "TRANSSET" / "TRANSSET_v2"
        annotation = json.loads((root / "label_coco" / "coco.json").read_text(encoding="utf-8"))
        stems = {item["id"]: Path(item["file_name"]).stem for item in annotation["images"]}
        for item in annotation["annotations"]:
            if item.get("iscrowd", 0):
                continue
            x, y, width, height = item["bbox"]
            highway_references.setdefault(stems[item["image_id"]], []).append([x, y, x + width, y + height])
        images = sorted((root / "images").glob("*.jpg"))
        # The archive has no predefined splits. Reserve deterministic disjoint image lists.
        random.Random(42).shuffle(images)
        boundary = len(images) * 4 // 5
        images = images[:boundary] if args.split == "valid" else images[boundary:]
        dataset_name = "TRANSSET v5"
        source_url = "https://doi.org/10.6084/m9.figshare.26082217.v5"
        creators = "Gebre, Blackston and Hashemi Beni"
        evaluation = "Class-agnostic vehicle matching against published COCO boxes at IoU 0.5. Not mAP or blockage accuracy."
        scope = "North Carolina highways, not Port of LA observations. Seed-42 shuffled 80/20 image partition; adjacent frames can cross partitions, so results do not establish scene-independent generalization."
    if args.limit:
        images = images[:args.limit]
    if not images:
        raise ValueError(f"No images found in {root / args.split}")
    tile_suffix = f"-tile{args.tile_size}" if args.tile_size else ""
    dataset_prefix = "transset-" if args.dataset == "transset" else ""
    # Partial runs get their own folder so they never overwrite a full-split result.
    limit_suffix = f"-first{args.limit}" if args.limit else ""
    output = ROOT / "outputs" / f"{dataset_prefix}{args.model.removesuffix('.pt')}-{args.imgsz}{tile_suffix}-{args.split}{limit_suffix}"
    output.mkdir(parents=True, exist_ok=True)
    model = load_model(args.model)
    # Warm up so the first image does not inflate timing.
    predict_frame(model, cv2.imread(str(images[0])), args.imgsz, args.confidence, args.tile_size)
    started = time.perf_counter()
    records = []
    previews = []
    for index, image in enumerate(images):
        frame = cv2.imread(str(image))
        if frame is None:
            raise ValueError(f"Cannot decode {image}")
        predictions = predict_frame(model, frame, args.imgsz, args.confidence, args.tile_size)
        height, width = frame.shape[:2]
        if args.dataset == "uav-obb":
            label = root / args.split / "labels" / f"{image.stem}.txt"
            references = reference_boxes(label, width, height, names)
        else:
            references = highway_references.get(image.stem, [])
        counts = match_detections(predictions, references)
        records.append({"image": image.name, "reference_count": len(references),
                        "detected_count": len(predictions), **counts})
        if index % max(1, len(images) // 12) == 0:
            preview = annotate(frame, predictions, f"Detected: {len(predictions)} | reference: {len(references)}")
            filename = f"preview-{index:04d}.jpg"
            cv2.imwrite(str(output / filename), preview)
            previews.append((filename, image.name, len(predictions), len(references)))
        if (index + 1) % 25 == 0 or index + 1 == len(images):
            print(f"{output.name}: {index + 1}/{len(images)}", flush=True)
    elapsed = time.perf_counter() - started
    totals = {key: sum(row[key] for row in records) for key in ("tp", "fp", "fn")}
    summary = {
        "dataset": dataset_name, "split": args.split, "images": len(images),
        "model": args.model, "image_size": args.imgsz, "confidence_threshold": args.confidence,
        "tile_size": args.tile_size,
        "iou_threshold": 0.5, **summarize_counts(**totals),
        "reference_vehicles": sum(row["reference_count"] for row in records),
        "detected_vehicles": sum(row["detected_count"] for row in records),
        "mean_absolute_count_error": round(sum(abs(row["detected_count"] - row["reference_count"]) for row in records) / len(records), 3),
        "elapsed_seconds": round(elapsed, 2), "seconds_per_image": round(elapsed / len(images), 3),
        "evaluation": evaluation, "scope": scope,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with (output / "per-image.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)
    cards = "".join(f'<figure><img src="{filename}"><figcaption>{html.escape(name)}: {found} detections / {actual} references</figcaption></figure>' for filename, name, found, actual in previews)
    write_report(output, "PigeonOps vehicle detection evaluation", f'''<p>Data: {dataset_name}, {creators}, <a href="{source_url}">CC BY 4.0</a>. External imagery, not Port of LA footage.</p>
<p>Boxes indicate model detections. Road state remains inconclusive pending human review. Detector scores are not road-blockage probabilities.</p>
''', summary, cards)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
