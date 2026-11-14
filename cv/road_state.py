"""Turn vehicle boxes inside a road region into evidence for the decision engine.

A still image shows how tightly vehicles are packed on one road segment. It
does not show whether they are moving or why. Densely packed traffic is
evidence for a blockage further along the road, so the engine weighs it with
measured detector error and an explicit, assumed traffic model.

Drone video also shows movement. `assess_motion` uses vehicle speeds on the
road region: stopped traffic suggests a blockage, free-flowing traffic an open
road, and anything else stays inconclusive.
"""
import math

DEFAULT_RULE = {
    # Neighbours closer than this many vehicle lengths count as closely spaced.
    "gap_ratio": 1.5,
    "dense_min_close": 12,
    "light_max_close": 3,
    # Below this mean frame luminance (0-255), missing detections are not
    # trusted as evidence of a clear road; night frames fall well under it.
    "min_brightness_for_clear": 60,
}
VISUAL_STATES = ("dense_traffic", "light_traffic", "unclear")
SIGNAL_FOR_STATE = {"dense_traffic": "blocked", "light_traffic": "clear", "unclear": "inconclusive"}
SIGNALS = ("blocked", "clear", "inconclusive")
# Assumed, not measured: how often each visual state appears when the camera
# looks at traffic approaching a blocked or an open road.
ASSUMED_STATE_GIVEN_ROAD = {
    "blocked": {"dense_traffic": 0.75, "unclear": 0.20, "light_traffic": 0.05},
    "open": {"dense_traffic": 0.15, "unclear": 0.25, "light_traffic": 0.60},
}


def point_in_polygon(x, y, polygon):
    inside = False
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[index - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def to_pixels(polygon, width, height):
    return [(x * width, y * height) for x, y in polygon]


def on_road(box, road):
    """True when an [x1, y1, x2, y2] box's centre lies inside the road polygon."""
    return bool(road) and point_in_polygon((box[0] + box[2]) / 2, (box[1] + box[3]) / 2, road)


def closely_spaced_count(boxes, gap_ratio):
    """Count vehicles whose nearest neighbour is within gap_ratio vehicle sizes.

    Size is the square root of box area, so the measure stays comparable as
    vehicles shrink with distance from the camera.
    """
    centres = [((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) for box in boxes]
    sizes = [max(0.0, (box[2] - box[0]) * (box[3] - box[1])) ** 0.5 for box in boxes]
    return sum(
        any(other != index and math.dist(centres[index], centres[other]) < gap_ratio * (sizes[index] + sizes[other]) / 2
            for other in range(len(boxes)))
        for index in range(len(boxes))
    )


def assess_road(boxes, road, rule=DEFAULT_RULE, frame_brightness=None):
    """Classify one image's road region; boxes and road are in pixels.

    Pass frame_brightness for detector output. Reference annotations omit it
    because their completeness does not depend on lighting.
    """
    road_boxes = [box for box in boxes if on_road(box, road)]
    close = closely_spaced_count(road_boxes, rule["gap_ratio"])
    if close >= rule["dense_min_close"]:
        state, reason = "dense_traffic", f"{close} closely spaced vehicles on the road"
    elif close > rule["light_max_close"]:
        state, reason = "unclear", f"{close} closely spaced vehicles is between the light and dense thresholds"
    elif frame_brightness is not None and frame_brightness < rule["min_brightness_for_clear"]:
        state, reason = "unclear", "frame too dark to treat missing detections as an empty road"
    else:
        state, reason = "light_traffic", f"only {close} closely spaced vehicles on the road"
    return {
        "visual_state": state, "signal": SIGNAL_FOR_STATE[state], "reason": reason,
        "vehicles_on_road": len(road_boxes), "closely_spaced_vehicles": close, "rule": dict(rule),
    }


# Drone video adds what a still image lacks: whether vehicles on the road move.
# Speeds are approximate km/h after removing camera motion (cv/motion.py).
# Thresholds are set by hand, not fitted. They are deliberately low: a queue
# behind a full blockage stands still, while traffic crawling at 10 km/h is
# still getting through. Stock footage may also be slowed down.
DEFAULT_MOTION_RULE = {
    "stopped_below_kmh": 3,
    "moving_above_kmh": 10,
    # A heavy vehicle standing in a highway travel lane is itself evidence;
    # traffic crawling past it counts toward a queue, not toward an open road.
    "min_stopped_for_blocked": 1,
    "blocked_not_moving_share": 0.6,
    # An open road needs traffic seen passing through it.
    "min_moving_for_clear": 2,
    "clear_moving_share": 0.8,
}
MOTION_STATES = ("stopped_traffic", "free_flow", "mixed_flow", "no_traffic")
SIGNAL_FOR_MOTION = {"stopped_traffic": "blocked", "free_flow": "clear",
                     "mixed_flow": "inconclusive", "no_traffic": "inconclusive"}


def median(values):
    values = sorted(values)
    if not values:
        return 0
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def classify_speed(speed, rule=DEFAULT_MOTION_RULE):
    if speed < rule["stopped_below_kmh"]:
        return "stopped"
    return "moving" if speed > rule["moving_above_kmh"] else "slow"


def assess_motion(samples, rule=DEFAULT_MOTION_RULE):
    """Classify a road region from several video samples.

    `samples` holds one list per sampled frame with the speed of every vehicle
    found on the road region. Each count is the median over samples, so one
    frame's missed or duplicate detections do not flip the result.
    """
    kinds = [[classify_speed(speed, rule) for speed in speeds] for speeds in samples]
    on_road = median([len(row) for row in kinds])
    stopped = median([row.count("stopped") for row in kinds])
    moving = median([row.count("moving") for row in kinds])
    slow = median([row.count("slow") for row in kinds])
    stopped_share = median([row.count("stopped") / len(row) for row in kinds if row])
    not_moving_share = median([(len(row) - row.count("moving")) / len(row) for row in kinds if row])
    moving_share = median([row.count("moving") / len(row) for row in kinds if row])
    if not on_road:
        state = "no_traffic"
        reason = ("no vehicles found on the marked road; an empty road can also mean a closure upstream, "
                  "so it is not evidence that the road is open")
    elif stopped >= rule["min_stopped_for_blocked"] and not_moving_share >= rule["blocked_not_moving_share"]:
        state = "stopped_traffic"
        crawling = f" and {slow:g} crawling under {rule['moving_above_kmh']} km/h" if slow else ""
        reason = f"{stopped:g} vehicle(s) standing still{crawling}, of {on_road:g} on the marked road; nothing moves through"
    elif moving >= rule["min_moving_for_clear"] and moving_share >= rule["clear_moving_share"]:
        state = "free_flow"
        reason = f"{moving:g} of {on_road:g} vehicles on the marked road are moving through"
    else:
        state = "mixed_flow"
        reason = f"{stopped:g} standing, {moving:g} moving of {on_road:g} vehicles on the marked road; flow is unclear"
    return {
        "motion_state": state, "signal": SIGNAL_FOR_MOTION[state], "reason": reason,
        "vehicles_on_road": on_road, "stopped": stopped, "slow": slow, "moving": moving,
        "stopped_share": round(stopped_share, 3), "moving_share": round(moving_share, 3),
        "samples": len(samples), "rule": dict(rule),
    }


def likelihood_table(pairs, smoothing=1.0):
    """Estimate P(signal | reference visual state) from (reference, predicted) pairs.

    Additive smoothing keeps rare cells above zero, so a small evaluation set
    cannot make any signal look impossible.
    """
    table = {}
    for state in VISUAL_STATES:
        counts = {signal: smoothing for signal in SIGNALS}
        for reference, predicted in pairs:
            if reference == state:
                counts[SIGNAL_FOR_STATE[predicted]] += 1
        total = sum(counts.values())
        table[state] = {signal: round(count / total, 4) for signal, count in counts.items()}
        table[state]["n"] = sum(1 for reference, _ in pairs if reference == state)
    return table


def road_likelihoods(signal_given_state, state_given_road=ASSUMED_STATE_GIVEN_ROAD):
    """P(signal | road state) for the decision engine's `drone.cv_only.likelihoods`.

    Sums P(signal | visual state) * P(visual state | road) over visual states.
    """
    return {
        road: {signal: round(sum(signal_given_state[state][signal] * share for state, share in shares.items()), 4)
               for signal in SIGNALS}
        for road, shares in state_given_road.items()
    }
