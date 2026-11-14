"""Small, class-agnostic vehicle evaluation; not the official OBB benchmark."""


def intersection_over_union(first, second):
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right, bottom = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    first_area = max(0, first[2] - first[0]) * max(0, first[3] - first[1])
    second_area = max(0, second[2] - second[0]) * max(0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union else 0.0


def match_detections(predictions, references, threshold=0.5):
    """Match in descending confidence order, using each reference at most once."""
    available = set(range(len(references)))
    matches = 0
    for prediction in sorted(predictions, key=lambda item: item["confidence"], reverse=True):
        if not available:
            break
        best = max(available, key=lambda index: intersection_over_union(prediction["box"], references[index]))
        if intersection_over_union(prediction["box"], references[best]) >= threshold:
            available.remove(best)
            matches += 1
    return {"tp": matches, "fp": len(predictions) - matches, "fn": len(references) - matches}


def summarize_counts(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 4), "recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
    }
