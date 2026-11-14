"""Produce annotated video and structured evidence for dispatcher review."""

import argparse
import html
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import imageio_ffmpeg

from cv.common import MODEL_CHOICES, ROOT, annotate, load_model, predict_frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--name", default="inspection")
    parser.add_argument("--model", choices=MODEL_CHOICES, default="yolo11s.pt")
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--sample-fps", type=float, default=2)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--tile-size", type=int, default=0)
    args = parser.parse_args()
    if args.sample_fps <= 0 or args.seconds <= 0 or args.imgsz <= 0 or args.tile_size < 0 or not 0 < args.confidence <= 1:
        parser.error("Use positive fps/duration/image size, nonnegative tile size, and confidence in (0, 1]")
    if Path(args.name).name != args.name or args.name in {".", ".."}:
        parser.error("name must be a single folder name")
    source = Path(args.source).resolve()
    if not source.is_file():
        parser.error(f"Video does not exist: {source}")
    manifest = {}
    for parent in source.parents:
        if (parent / "source.json").is_file():
            manifest = json.loads((parent / "source.json").read_text(encoding="utf-8"))
            break
    output = ROOT / "outputs" / args.name
    output.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise ValueError(f"Cannot open {source}")
    fps = capture.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        capture.release()
        raise ValueError("Video has no usable frame-rate metadata")
    stride = max(1, round(fps / args.sample_fps))
    model = load_model(args.model)
    frames = []
    writer = None
    started = time.perf_counter()
    frame_index = 0
    preview_names = []
    try:
        while frame_index / fps < args.seconds:
            ok, frame = capture.read()
            if not ok:
                break
            index = frame_index
            frame_index += 1
            if index % stride:
                continue
            predictions = predict_frame(model, frame, args.imgsz, args.confidence, args.tile_size)
            timestamp = round(index / fps, 3)
            frames.append({"source_frame": index, "timestamp_seconds": timestamp,
                           "vehicle_count": len(predictions), "detections": predictions})
            annotated = annotate(frame, predictions, f"{timestamp:.1f}s | {len(predictions)} vehicle detections | road state: needs review")
            height, width = annotated.shape[:2]
            if writer is None:
                writer = imageio_ffmpeg.write_frames(
                    str(output / "annotated.mp4"), (width, height), fps=fps / stride,
                    codec="libx264", pix_fmt_in="bgr24", pix_fmt_out="yuv420p",
                    macro_block_size=1, output_params=["-movflags", "+faststart"],
                )
                writer.send(None)
            writer.send(annotated.tobytes())
            if len(frames) <= 6:
                filename = f"frame-{len(frames):03d}.jpg"
                cv2.imwrite(str(output / filename), annotated)
                preview_names.append(filename)
            if len(frames) % 10 == 0:
                print(f"{args.name}: {len(frames)} sampled frames, {timestamp:.1f}s of footage", flush=True)
    finally:
        capture.release()
        if writer is not None:
            writer.close()
    if not frames:
        raise ValueError("Video contained no readable frames")
    evidence = {
        "label": "inconclusive", "review_status": "needs_human_review",
        "source": "prerecorded_external_dataset" if manifest else "user_supplied_video",
        "dataset": manifest.get("dataset", "Unspecified"), "dataset_url": manifest.get("source"),
        "creators": manifest.get("creators", []), "geography": manifest.get("geography"),
        "license": manifest.get("license"), "source_file": str(source),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model, "image_size": args.imgsz, "tile_size": args.tile_size,
        "confidence_threshold": args.confidence, "sampled_frames": len(frames),
        "sample_interval_seconds": round(stride / fps, 3),
        "source_duration_analyzed_seconds": round(frame_index / fps, 3),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "minimum_vehicles_per_sample": min(item["vehicle_count"] for item in frames),
        "maximum_vehicles_per_sample": max(item["vehicle_count"] for item in frames),
        "annotated_video": "annotated.mp4", "preview_frames": preview_names,
        "summary": "Vehicle detections are available for review. They do not establish road blockage or safety.",
        "limitations": ["This footage is separate from the fictional demo incident.",
                        "Vehicles may be parked, moving, occluded, or missed; counts are per sample, not unique tracks.",
                        "Detector confidence is not a blockage probability or the engine's inspection accuracy.",
                        "No automatic road-state classification or dispatch action is performed."],
        "frames": frames,
    }
    (output / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    summary = {key: value for key, value in evidence.items() if key != "frames"}
    report = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>PigeonOps inspection review</title>
<style>body{{font:16px system-ui;background:#102a35;color:#edf5f4;max-width:1100px;margin:30px auto;padding:20px}}video,img{{width:100%;border-radius:12px}}pre{{white-space:pre-wrap;background:#193c48;padding:20px}}a{{color:#64debd}}</style>
<h1>Inspection evidence for human review</h1><p>Road state: <strong>inconclusive — review required</strong></p>
<p>{html.escape(str(evidence['creators']))}, <a href="{html.escape(evidence['dataset_url'] or '#', quote=True)}">{html.escape(evidence['dataset'])}</a>, {html.escape(evidence['license'] or 'license unspecified')}. Footage is separate from the fictional demo incident.</p>
<video controls muted preload="metadata" src="annotated.mp4"></video><p>Sampled playback. Vehicle detections do not establish blockage or safety.</p>
<pre>{html.escape(json.dumps(summary, indent=2))}</pre></html>'''
    (output / "report.html").write_text(report, encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
