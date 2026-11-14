"""Estimate how fast detected vehicles move in drone video, net of camera motion.

A hovering drone still drifts and turns. The camera's own motion is fitted as a
similarity transform (shift, rotation, scale) from background features, with
vehicle boxes masked out so a road full of traffic cannot pass for the
background. The later frame is warped back by that transform, and each vehicle
is found again by template matching; its remaining shift is its own motion.

Speeds are approximate km/h. Ground scale comes from the median short side of
the detected boxes, taken as a 2 m vehicle width; cars and trucks are both
about that wide seen from above, while their lengths differ several-fold. The
scale is an assumption, so treat speeds as bands, not measurements.
"""
import cv2
import numpy as np

IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
VEHICLE_WIDTH_M = 2.0
MAX_SPEED_KMH = 140
MIN_MATCH = 0.6


def metres_per_pixel(boxes):
    """Ground scale from the median short side of the boxes, or None without boxes."""
    sides = sorted(min(x2 - x1, y2 - y1) for x1, y1, x2, y2 in boxes)
    return VEHICLE_WIDTH_M / max(1.0, sides[len(sides) // 2]) if sides else None


def camera_motion(previous_gray, gray, boxes=(), scale=0.5):
    """2x3 transform mapping previous-frame pixels to current-frame pixels.

    ECC image alignment fits an affine transform to every background pixel,
    with vehicle boxes masked out. It starts once from "no motion" and once from
    a phase-correlation shift of the frame with vehicles blanked, and keeps the
    better-aligned result. Sparse feature matching locked onto lane markings,
    and whole-frame phase correlation onto a road full of moving cars.
    """
    small = cv2.resize(previous_gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA).astype(np.float32)
    later = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA).astype(np.float32)
    mask = np.full(small.shape, 255, np.uint8)
    for x1, y1, x2, y2 in boxes:
        mask[max(0, int(y1 * scale) - 2):int(y2 * scale) + 3, max(0, int(x1 * scale) - 2):int(x2 * scale) + 3] = 0
    fill = float(small[mask > 0].mean()) if mask.any() else 0.0
    (dx, dy), _ = cv2.phaseCorrelate(np.where(mask > 0, small, fill).astype(np.float32),
                                     np.where(mask > 0, later, fill).astype(np.float32))
    criteria = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 100, 1e-5)
    best, best_score = np.float32([[1, 0, dx], [0, 1, dy]]), -1.0
    for start in (np.float32([[1, 0, 0], [0, 1, 0]]), np.float32([[1, 0, dx], [0, 1, dy]])):
        try:
            score, warp = cv2.findTransformECC(small, later, start, cv2.MOTION_AFFINE, criteria, mask, 5)
        except cv2.error:
            continue  # did not converge from this start
        if score > best_score:
            best, best_score = warp, score
    best = best.astype(np.float64)
    best[:, 2] /= scale
    return best


def compose(second, first):
    """Transform that applies `first`, then `second`."""
    return (np.vstack([second, [0, 0, 1]]) @ np.vstack([first, [0, 0, 1]]))[:2]


def warp_points(transform, points):
    return [tuple(float(v) for v in transform @ np.array([x, y, 1.0])) for x, y in points]


def vehicle_speeds(gray, later_gray, boxes, seconds, metres_per_px, camera=None):
    """Approximate km/h of each [x1, y1, x2, y2] box between two frames `seconds` apart.

    Returns None for a vehicle that could not be found again (occluded, left
    the frame, or matched poorly); callers should leave it out of the count.
    """
    if not boxes:
        return []
    camera = camera_motion(gray, later_gray, boxes) if camera is None else camera
    height, width = gray.shape
    # Bring the later frame into the first frame's coordinates.
    aligned = cv2.warpAffine(later_gray, cv2.invertAffineTransform(camera), (width, height),
                             borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    reach = int(MAX_SPEED_KMH / 3.6 * seconds / metres_per_px) + 4
    speeds = []
    for x1, y1, x2, y2 in boxes:
        x1, y1, x2, y2 = int(max(0, x1)), int(max(0, y1)), int(min(width, x2)), int(min(height, y2))
        template = gray[y1:y2, x1:x2]
        left, top = max(0, x1 - reach), max(0, y1 - reach)
        window = aligned[top:min(height, y2 + reach), left:min(width, x2 + reach)]
        if template.size < 64 or window.shape[0] < template.shape[0] or window.shape[1] < template.shape[1]:
            speeds.append(None)
            continue
        scores = cv2.matchTemplate(window, template, cv2.TM_CCOEFF_NORMED)
        _, best, _, (bx, by) = cv2.minMaxLoc(scores)
        if best < MIN_MATCH:
            speeds.append(None)
            continue
        shift = np.hypot(left + bx - x1, top + by - y1)
        speeds.append(round(float(shift) * metres_per_px / seconds * 3.6, 1))
    return speeds
