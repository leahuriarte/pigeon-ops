"""Turn vehicle boxes inside a road region into evidence for the decision engine.

A still image shows how tightly vehicles are packed on one road segment. It
does not show whether they are moving or why. Densely packed traffic is
evidence for a blockage further along the road, so the engine weighs it with
measured detector error and an explicit, assumed traffic model.
"""

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


def boxes_on_road(boxes, road):
    """Keep axis-aligned boxes whose centre lies inside the road polygon."""
    return [box for box in boxes if point_in_polygon((box[0] + box[2]) / 2, (box[1] + box[3]) / 2, road)]


def closely_spaced_count(boxes, gap_ratio):
    """Count vehicles whose nearest neighbour is within gap_ratio vehicle sizes.

    Size is the square root of box area, so the measure stays comparable as
    vehicles shrink with distance from the camera.
    """
    centres = [((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) for box in boxes]
    sizes = [max(0.0, (box[2] - box[0]) * (box[3] - box[1])) ** 0.5 for box in boxes]
    close = 0
    for index, (x, y) in enumerate(centres):
        for other, (ox, oy) in enumerate(centres):
            scale = (sizes[index] + sizes[other]) / 2
            if other != index and scale and ((x - ox) ** 2 + (y - oy) ** 2) ** 0.5 / scale < gap_ratio:
                close += 1
                break
    return close


def assess_road(boxes, road, rule=DEFAULT_RULE, frame_brightness=None):
    """Classify one image's road region; boxes and road are in pixels.

    Pass frame_brightness for detector output. Reference annotations omit it
    because their completeness does not depend on lighting.
    """
    on_road = boxes_on_road(boxes, road)
    close = closely_spaced_count(on_road, rule["gap_ratio"])
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
        "vehicles_on_road": len(on_road), "closely_spaced_vehicles": close, "rule": dict(rule),
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
