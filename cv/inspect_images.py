"""Analyze a local image or image folder and produce a visual evidence report."""

import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path

import cv2
from PIL import Image, ImageDraw

from cv.common import MODEL_CHOICES, ROOT, annotate, load_model, predict_frame, write_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="One image or a folder of images")
    parser.add_argument("--name", default="image-inspection")
    parser.add_argument("--model", choices=MODEL_CHOICES, default="yolo11s.pt")
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--tile-size", type=int, default=0)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--limit", type=int, default=12, help="Evenly sample a folder; 0 analyzes every image")
    args = parser.parse_args()
    if args.imgsz <= 0 or args.tile_size < 0 or not 0 < args.confidence <= 1:
        parser.error("Use positive image size, nonnegative tile size, and confidence in (0, 1]")
    if Path(args.name).name != args.name or args.name in {".", ".."}:
        parser.error("name must be one folder name")
    source = Path(args.source).resolve()
    images = sorted(path for path in source.rglob("*") if path.suffix.lower() in {".jpg", ".jpeg", ".png"}) if source.is_dir() else [source]
    if not images or not all(path.is_file() for path in images):
        parser.error("Source must contain readable images")
    if args.limit < 0:
        parser.error("limit cannot be negative")
    if args.limit and len(images) > args.limit:
        images = [images[index * len(images) // args.limit] for index in range(args.limit)]
    output = ROOT / "outputs" / args.name
    output.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for parent in [source.parent, *source.parents]:
        if (parent / "source.json").is_file():
            manifest = json.loads((parent / "source.json").read_text(encoding="utf-8"))
            break
    model = load_model(args.model)
    observations = []
    sheet = Image.new("RGB", (1200, ((len(images) + 2) // 3) * 280), "#102a35")
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(images):
        frame = cv2.imread(str(path))
        if frame is None:
            raise ValueError(f"Cannot decode image: {path}")
        predictions = predict_frame(model, frame, args.imgsz, args.confidence, args.tile_size)
        rendered = annotate(frame, predictions, f"{len(predictions)} vehicle detections | review required")
        filename = f"image-{index + 1:03d}.jpg"
        cv2.imwrite(str(output / filename), rendered)
        thumbnail = Image.fromarray(cv2.cvtColor(rendered, cv2.COLOR_BGR2RGB))
        thumbnail.thumbnail((390, 235))
        x, y = (index % 3) * 400 + 5, (index // 3) * 280 + 5
        sheet.paste(thumbnail, (x, y))
        draw.text((x, y + 240), f"{index + 1:02d}: {len(predictions)} detections | needs review", fill="white")
        observations.append({"source_file": str(path), "annotated_image": filename,
                             "label": "inconclusive", "review_status": "needs_human_review",
                             "vehicle_count": len(predictions), "detections": predictions})
        print(f"{index + 1}/{len(images)}: {path.name}: {len(predictions)} vehicles", flush=True)
    sheet.save(output / "contact-sheet.jpg")
    evidence = {
        "label": "inconclusive", "review_status": "needs_human_review",
        "source": "external_dataset" if manifest else "user_supplied_images",
        "dataset": manifest.get("dataset", "Unspecified; provide provenance before presentation"),
        "dataset_url": manifest.get("source"), "license": manifest.get("license"),
        "creators": manifest.get("creators", []), "geography": manifest.get("geography"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model, "image_size": args.imgsz, "tile_size": args.tile_size,
        "confidence_threshold": args.confidence, "images_analyzed": len(images),
        "summary": "Vehicle detections support visual review; a single image does not establish vehicle motion, congestion, blockage, or road safety.",
        "observations": observations,
    }
    (output / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    cards = "".join(f'<figure><img src="{item["annotated_image"]}"><figcaption>{html.escape(Path(item["source_file"]).name)} — {item["vehicle_count"]} detections; human review required.</figcaption></figure>' for item in observations)
    summary = {key: value for key, value in evidence.items() if key != "observations"}
    write_report(output, "Road image inspection", f'''<p>Model detections are visual evidence for review. Road state remains <strong>inconclusive</strong> until reviewed. These images are separate from the fictional demo incident.</p>
<p>Attribution and source: {html.escape(str(evidence['creators']))}, <a href="{html.escape(evidence['dataset_url'] or '#', quote=True)}">{html.escape(evidence['dataset'])}</a>, {html.escape(evidence['license'] or 'license unspecified')}.</p>
<img src="contact-sheet.jpg" alt="Overview of annotated road images">''', summary, cards)
    print(f"Report ready: {output / 'report.html'}", flush=True)


if __name__ == "__main__":
    main()
