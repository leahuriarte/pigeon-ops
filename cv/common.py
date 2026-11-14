"""Paths and model output conversion shared by the offline CV commands."""

from pathlib import Path

from cv.metrics import intersection_over_union


ROOT = Path(__file__).resolve().parent
# COCO names for the general models; DOTA names for the aerial OBB models.
VEHICLE_NAMES = {"car", "bus", "truck", "small vehicle", "large vehicle"}
# The COCO and DOTA models miss different vehicles, so their union recovers
# far more of them. Each member runs at the size that scored best on UAV-OBB.
ENSEMBLES = {"aerial-ensemble": [("yolo11s.pt", 1280), ("yolo11m-obb.pt", 1024)]}
MODEL_CHOICES = ["yolo11n.pt", "yolo11s.pt", "yolo11s-obb.pt", "yolo11m-obb.pt", *ENSEMBLES]


def dataset_root():
    candidates = sorted((ROOT / "data" / "UAV-OBB").rglob("data.yaml"))
    if len(candidates) != 1:
        raise ValueError("Run python -m cv.download_data first; expected one data.yaml")
    return candidates[0].parent


def load_model(name):
    if name in ENSEMBLES:
        return [(load_model(member), size) for member, size in ENSEMBLES[name]]
    from ultralytics import YOLO

    models = ROOT / "models"
    models.mkdir(parents=True, exist_ok=True)
    return YOLO(str(models / name))


def vehicle_ids(model):
    return [index for index, name in model.names.items() if name in VEHICLE_NAMES]


def detections(result):
    # OBB models return rotated boxes; their axis-aligned envelopes match the
    # reference boxes used in evaluation.
    boxes = result.boxes if result.boxes is not None else result.obb
    return [
        {"class": result.names[int(class_id)], "confidence": round(float(confidence), 4),
         "box": [round(float(value), 2) for value in box]}
        for box, confidence, class_id in zip(
            boxes.xyxy.cpu().tolist(),
            boxes.conf.cpu().tolist(),
            boxes.cls.cpu().tolist(),
        )
    ]


def tile_origins(length, size):
    if length <= size:
        return [0]
    return sorted(set([*range(0, length - size + 1, max(1, int(size * 0.8))), length - size]))


def suppress_duplicates(predictions):
    """Keep the most confident box among overlaps, collapsing vehicle classes."""
    selected = []
    for detection in sorted(predictions, key=lambda item: item["confidence"], reverse=True):
        if all(intersection_over_union(detection["box"], existing["box"]) < 0.5 for existing in selected):
            selected.append(detection)
    return selected


def predict_frame(model, frame, image_size=1280, confidence=0.25, tile_size=0):
    """Optionally combine full-frame and overlapping tile detections.

    An ensemble from load_model ignores image_size; each member uses its own.
    """
    if isinstance(model, list):
        return suppress_duplicates([detection for member, size in model
                                    for detection in predict_frame(member, frame, size, confidence, tile_size)])
    options = {"classes": vehicle_ids(model), "device": "cpu", "verbose": False, "conf": confidence}
    predictions = detections(model.predict(frame, imgsz=image_size, **options)[0])
    if not tile_size:
        return predictions
    height, width = frame.shape[:2]
    for top in tile_origins(height, tile_size):
        for left in tile_origins(width, tile_size):
            crop = frame[top:top + tile_size, left:left + tile_size]
            for detection in detections(model.predict(crop, imgsz=tile_size, **options)[0]):
                x1, y1, x2, y2 = detection["box"]
                detection["box"] = [x1 + left, y1 + top, x2 + left, y2 + top]
                predictions.append(detection)
    return suppress_duplicates(predictions)


def annotate(frame, predictions, caption=""):
    import cv2

    image = frame.copy()
    for item in predictions:
        x1, y1, x2, y2 = [round(value) for value in item["box"]]
        cv2.rectangle(image, (x1, y1), (x2, y2), (80, 220, 100), 2)
        cv2.putText(image, f"{item['class']} {item['confidence']:.2f}", (x1, max(18, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 220, 100), 1)
    # Add a header rather than covering camera timestamp overlays.
    image = cv2.copyMakeBorder(image, 48, 0, 0, 0, cv2.BORDER_CONSTANT, value=(16, 42, 53))
    text_width = cv2.getTextSize(caption, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0][0]
    scale = min(0.7, 0.7 * (image.shape[1] - 30) / max(1, text_width))
    cv2.putText(image, caption, (15, 32), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2)
    return image
