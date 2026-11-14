"""Build the dashboard's drone inspection library from top-down video clips.

For each clip in drone_review.json this samples a few seconds of footage,
detects vehicles, follows the hand-drawn road region as the camera drifts,
measures each vehicle's speed net of camera motion and applies the motion rule
in road_state.py. The result is a suggested observation for the dispatcher,
an annotated clip and keyframe, and a library.json the backend serves without
any CV dependencies.

    python -m cv.prepare_review
"""

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np

from cv.common import ROOT, load_model, predict_frame
from cv.motion import IDENTITY, camera_motion, compose, metres_per_pixel, vehicle_speeds, warp_points
from cv.road_state import DEFAULT_MOTION_RULE, assess_motion, classify_speed, on_road, to_pixels

MODEL = "drone-ensemble"
CONFIDENCE = 0.25
WINDOW_SECONDS = 4.0
SAMPLE_FPS = 8
SPEED_GAP_SECONDS = 0.25
OUTPUT_WIDTH = 960
# Visual labels for which a reviewer would expect a "blocked" suggestion.
BLOCKED_LABELS = {"visible_obstruction", "standing_queue", "flooded_road"}
# BGR colours: standing, slow, moving, outside the road or not re-found.
COLOURS = {"stopped": (40, 40, 230), "slow": (0, 170, 250), "moving": (90, 210, 60), "other": (190, 190, 190)}


def read_frame(capture, index):
    capture.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, frame = capture.read()
    return frame if ok else None


def gray(frame):
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def analyse_clip(path, start, model, rule=DEFAULT_MOTION_RULE, road=(), hover=False):
    """Detections, road region and vehicle speeds for each sampled frame.

    `hover` skips the camera-motion estimate and treats the camera as still.
    Use it only for a clip where vehicles cover so much of the frame that the
    estimate follows the traffic instead of the ground.
    """
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"Cannot open {path}")
    fps = capture.get(cv2.CAP_PROP_FPS)
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    stride = max(1, round(fps / SAMPLE_FPS))
    gap = max(1, round(fps * SPEED_GAP_SECONDS))
    first = round(start * fps)
    last = min(total - gap - 1, first + round(WINDOW_SECONDS * fps))
    samples, previous = [], None
    try:
        for index in range(first, last, stride):
            frame, later = read_frame(capture, index), read_frame(capture, index + gap)
            if frame is None or later is None:
                break
            boxes = [item["box"] for item in predict_frame(model, frame, confidence=CONFIDENCE)]
            # Follow the keyframe's road region as the camera drifts or turns.
            if previous is None or hover:
                to_here = IDENTITY.copy()
            else:
                step = camera_motion(gray(previous["frame"]), gray(frame), previous["boxes"])
                to_here = compose(step, previous["to_here"])
            samples.append({"index": index, "seconds": round((index - first) / fps, 2), "frame": frame,
                            "later": later, "boxes": boxes, "to_here": to_here,
                            "road": warp_points(to_here, road)})
            previous = samples[-1]
    finally:
        capture.release()
    if not samples:
        raise ValueError(f"No readable frames in {path}")
    # One ground scale per clip: altitude barely changes within a few seconds.
    scale = metres_per_pixel([box for sample in samples for box in sample["boxes"]])
    for sample in samples:
        speeds = vehicle_speeds(gray(sample["frame"]), gray(sample["later"]), sample["boxes"], gap / fps, scale,
                                camera=IDENTITY if hover else None) if scale else []
        sample["vehicles"] = []
        for box, speed in zip(sample["boxes"], speeds):
            inside = on_road(box, sample["road"])
            kind = classify_speed(speed, rule) if inside and speed is not None else "other"
            sample["vehicles"].append({"box": box, "speed_kmh": speed, "on_road": inside, "kind": kind})
    assessment = assess_motion([[v["speed_kmh"] for v in sample["vehicles"] if v["kind"] != "other"]
                                for sample in samples], rule)
    return samples, assessment, {"fps": fps, "metres_per_pixel": round(scale, 4) if scale else None,
                                 "sample_fps": round(fps / stride, 2), "speed_gap_seconds": round(gap / fps, 3)}


def render(sample, caption):
    image = sample["frame"].copy()
    polygon = np.array(sample["road"], np.int32)
    if len(polygon):
        tint = image.copy()
        cv2.fillPoly(tint, [polygon], (200, 160, 0))
        image = cv2.addWeighted(tint, 0.14, image, 0.86, 0)
        cv2.polylines(image, [polygon], True, (230, 200, 20), 3)
    width = image.shape[1]
    thickness = max(2, width // 480)
    for vehicle in sample["vehicles"]:
        x1, y1, x2, y2 = [round(value) for value in vehicle["box"]]
        cv2.rectangle(image, (x1, y1), (x2, y2), COLOURS[vehicle["kind"]], thickness)
        if vehicle["kind"] != "other":
            label = f"{vehicle['speed_kmh']:.0f} km/h"
            cv2.putText(image, label, (x1, max(22, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, width / 1900, (0, 0, 0), thickness + 3)
            cv2.putText(image, label, (x1, max(22, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, width / 1900, (255, 255, 255), thickness)
    image = cv2.resize(image, (OUTPUT_WIDTH, round(image.shape[0] * OUTPUT_WIDTH / width)), interpolation=cv2.INTER_AREA)
    bar = np.full((34, OUTPUT_WIDTH, 3), (53, 42, 16), np.uint8)
    cv2.putText(bar, caption, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return np.vstack([bar, image])


def counts(sample):
    kinds = [vehicle["kind"] for vehicle in sample["vehicles"]]
    return {kind: kinds.count(kind) for kind in ("stopped", "slow", "moving")}


def caption_for(sample):
    seen = counts(sample)
    return (f"t={sample['seconds']:.1f}s  road: {seen['stopped']} standing, {seen['slow']} slow, "
            f"{seen['moving']} moving  |  off road or not re-found: {sum(v['kind'] == 'other' for v in sample['vehicles'])}")


def write_video(path, frames, fps):
    height, width = frames[0].shape[:2]
    writer = imageio_ffmpeg.write_frames(str(path), (width, height), fps=fps, codec="libx264",
                                         pix_fmt_in="bgr24", pix_fmt_out="yuv420p", macro_block_size=2,
                                         output_params=["-crf", "30", "-preset", "slow", "-movflags", "+faststart"])
    writer.send(None)
    for frame in frames:
        writer.send(np.ascontiguousarray(frame).tobytes())
    writer.close()


def prepare(catalog_path, clips_dir, output, model_name=MODEL):
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    # Raw clips are gitignored: check them all before touching the committed library.
    missing = [str(clips_dir / item["file"]) for item in catalog["clips"] if not (clips_dir / item["file"]).is_file()]
    if missing:
        raise ValueError(f"Missing clips {', '.join(missing)}; see cv/SOURCES.md for where to download them. "
                         "The existing library was left unchanged.")
    model = load_model(model_name)
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    cases = []
    for item in catalog["clips"]:
        path = clips_dir / item["file"]
        probe = cv2.VideoCapture(str(path))
        keyframe = read_frame(probe, round(item["start_seconds"] * probe.get(cv2.CAP_PROP_FPS)))
        probe.release()
        if keyframe is None:
            raise ValueError(f"Cannot read a frame at {item['start_seconds']} s in {path}")
        road = to_pixels(item["road"], keyframe.shape[1], keyframe.shape[0])
        hover = item.get("camera") == "hover"
        samples, assessment, timing = analyse_clip(path, item["start_seconds"], model, road=road, hover=hover)
        # Show the sample that best matches the clip-level counts as the still.
        target = (assessment["stopped"], assessment["moving"])
        still = min(samples, key=lambda s: abs(counts(s)["stopped"] - target[0]) + abs(counts(s)["moving"] - target[1]))
        destination = output / item["id"]
        destination.mkdir()
        source_still = cv2.resize(still["frame"], (OUTPUT_WIDTH, round(still["frame"].shape[0] * OUTPUT_WIDTH / still["frame"].shape[1])),
                                  interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(destination / "source.jpg"), source_still, [cv2.IMWRITE_JPEG_QUALITY, 82])
        cv2.imwrite(str(destination / "annotated.jpg"), render(still, caption_for(still)), [cv2.IMWRITE_JPEG_QUALITY, 82])
        write_video(destination / "annotated.mp4", [render(sample, caption_for(sample)) for sample in samples], timing["sample_fps"])
        on_road_counts = sorted(sum(v["on_road"] for v in sample["vehicles"]) for sample in samples)
        all_counts = sorted(len(sample["vehicles"]) for sample in samples)
        median_on_road = on_road_counts[len(on_road_counts) // 2]
        cases.append({
            "id": item["id"], "title": item["title"], "visual_label": item["visual_label"],
            # Which drone checks this clip can illustrate: crash, congestion, environment.
            "check_types": item["check_types"],
            "review_note": item["note"], "annotation_method": catalog["annotation_method"],
            "image_quality": "reviewable",
            "road_region": item["road"],
            "source_image": f"/inspection-assets/{item['id']}/source.jpg",
            "annotated_image": f"/inspection-assets/{item['id']}/annotated.jpg",
            "annotated_video": f"/inspection-assets/{item['id']}/annotated.mp4",
            "vehicle_detections": median_on_road,
            "excluded_detections": all_counts[len(all_counts) // 2] - median_on_road,
            "allowed_observations": ["blocked", "clear", "inconclusive"],
            "cv_assessment": {
                **assessment, "suggested_observation": assessment["signal"],
                "model": f"{model_name}, confidence {CONFIDENCE}",
                "window_seconds": round(samples[-1]["seconds"], 2), **timing,
                "camera_motion": "assumed none (hovering drone, set by hand)" if hover else "estimated from the background",
                "basis": "Unvalidated rule: hand-set speed thresholds, approximate speeds from an assumed 2 m vehicle "
                         "width, no labelled blockage footage to score it against. A dispatcher confirms or overrides it.",
            },
            "source": {
                "publisher": "Pexels", "credit": item["credit"], "source_url": item["source_url"],
                "license": catalog["license"], "terms_url": catalog["license_url"],
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "clip_start_seconds": item["start_seconds"],
                "location": {"locationName": "Not stated by the source; shown on the simulated CA-47 connector"},
                "historical": True,
                "date_description": "Pre-recorded stock footage; capture date not published",
            },
            "review_status": "needs_dispatcher_review", "simulated_application": True,
        })
        print(f"{item['id']}: {assessment['signal']} ({assessment['reason']}); expected {item['visual_label']}", flush=True)
    agreement = sum((case["visual_label"] in BLOCKED_LABELS) == (case["cv_assessment"]["signal"] == "blocked")
                    for case in cases)
    summary = {
        "cases": len(cases), "model": model_name,
        "suggestions": {signal: sum(case["cv_assessment"]["signal"] == signal for case in cases)
                        for signal in ("blocked", "clear", "inconclusive")},
        "blocked_suggestion_matches_visual_review": f"{agreement} of {len(cases)}",
        "interpretation": "A demonstration on hand-picked clips, not an accuracy measurement.",
    }
    library = {"available": True, "scope": catalog["scope"], "annotation_method": catalog["annotation_method"],
               "motion_rule": dict(DEFAULT_MOTION_RULE), "created_at": datetime.now(timezone.utc).isoformat(),
               "summary": summary, "cases": cases}
    (output / "library.json").write_text(json.dumps(library, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return library


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clips", type=Path, default=ROOT / "clips" / "raw")
    parser.add_argument("--output", type=Path, default=ROOT / "review_library")
    args = parser.parse_args()
    prepare(ROOT / "drone_review.json", args.clips, args.output)


if __name__ == "__main__":
    main()
