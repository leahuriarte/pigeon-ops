import unittest

from cv.metrics import intersection_over_union, match_detections, summarize_counts
from cv.common import suppress_duplicates, tile_origins


class VehicleMetricTests(unittest.TestCase):
    def test_one_vehicle_seen_as_two_classes_counts_once(self):
        predictions = [{"class": "car", "box": [0, 0, 10, 10], "confidence": 0.6},
                       {"class": "truck", "box": [0, 0, 10, 11], "confidence": 0.8},
                       {"class": "car", "box": [50, 0, 60, 10], "confidence": 0.5}]
        kept = suppress_duplicates(predictions)
        self.assertEqual(["truck", "car"], [item["class"] for item in kept])

    def test_tiles_cover_far_edge_and_do_not_repeat_it(self):
        self.assertEqual([0, 512, 1024, 1280], tile_origins(1920, 640))
        self.assertEqual([0], tile_origins(320, 640))

    def test_duplicate_detections_do_not_reuse_reference(self):
        predictions = [{"box": [0, 0, 10, 10], "confidence": score} for score in (0.9, 0.8)]
        self.assertEqual({"tp": 1, "fp": 1, "fn": 0},
                         match_detections(predictions, [[0, 0, 10, 10]]))

    def test_misses_and_false_alarms_are_both_counted(self):
        predictions = [{"box": [20, 20, 30, 30], "confidence": 0.9}]
        self.assertEqual({"tp": 0, "fp": 1, "fn": 1},
                         match_detections(predictions, [[0, 0, 10, 10]]))

    def test_empty_scene_has_defined_metrics(self):
        self.assertEqual(0.0, summarize_counts(0, 0, 0)["f1"])
        self.assertEqual(0.0, intersection_over_union([0, 0, 0, 0], [0, 0, 0, 0]))

    def test_confidence_order_controls_matching(self):
        predictions = [{"box": [0, 0, 10, 10], "confidence": 0.1},
                       {"box": [0, 0, 15, 10], "confidence": 0.9}]
        self.assertEqual({"tp": 1, "fp": 1, "fn": 1},
                         match_detections(predictions, [[0, 0, 10, 10], [5, 0, 15, 10]]))


if __name__ == "__main__":
    unittest.main()
