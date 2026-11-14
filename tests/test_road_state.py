import unittest

from cv.road_state import DEFAULT_RULE, assess_road, likelihood_table, point_in_polygon, road_likelihoods

ROAD = [(0, 0), (1000, 0), (1000, 100), (0, 100)]


def row_of_cars(count, gap, top=40):
    """Cars 20 px long spaced gap px apart along the road."""
    return [[index * (20 + gap), top, index * (20 + gap) + 20, top + 20] for index in range(count)]


class RoadStateTests(unittest.TestCase):
    def test_point_in_polygon(self):
        self.assertTrue(point_in_polygon(500, 50, ROAD))
        self.assertFalse(point_in_polygon(500, 150, ROAD))

    def test_tight_queue_is_dense(self):
        result = assess_road(row_of_cars(15, gap=5), ROAD)
        self.assertEqual(("dense_traffic", "blocked"), (result["visual_state"], result["signal"]))

    def test_spread_out_cars_are_light(self):
        result = assess_road(row_of_cars(5, gap=150), ROAD)
        self.assertEqual(("light_traffic", "clear"), (result["visual_state"], result["signal"]))

    def test_parked_cars_off_the_road_are_ignored(self):
        parking_lot = row_of_cars(15, gap=5, top=300)
        self.assertEqual(0, assess_road(parking_lot, ROAD)["vehicles_on_road"])

    def test_dark_frame_without_detections_is_not_clear(self):
        result = assess_road([], ROAD, frame_brightness=30)
        self.assertEqual("inconclusive", result["signal"])

    def test_dark_frame_still_reports_visible_queue(self):
        result = assess_road(row_of_cars(15, gap=5), ROAD, frame_brightness=30)
        self.assertEqual("blocked", result["signal"])

    def test_threshold_band_is_unclear(self):
        close = DEFAULT_RULE["light_max_close"] + 2
        self.assertEqual("inconclusive", assess_road(row_of_cars(close, gap=5), ROAD)["signal"])

    def test_likelihood_table_is_smoothed_and_normalized(self):
        table = likelihood_table([("dense_traffic", "dense_traffic"), ("light_traffic", "light_traffic")])
        for row in table.values():
            probabilities = [row[signal] for signal in ("blocked", "clear", "inconclusive")]
            self.assertAlmostEqual(1.0, sum(probabilities), places=3)
            self.assertTrue(all(value > 0 for value in probabilities))
        self.assertEqual(1, table["dense_traffic"]["n"])
        self.assertEqual(0, table["unclear"]["n"])

    def test_road_likelihoods_mix_visual_states(self):
        perfect = {"dense_traffic": {"blocked": 1, "clear": 0, "inconclusive": 0},
                   "light_traffic": {"blocked": 0, "clear": 1, "inconclusive": 0}}
        table = road_likelihoods(perfect, {"blocked": {"dense_traffic": 1.0}, "open": {"dense_traffic": 0.5, "light_traffic": 0.5}})
        self.assertEqual({"blocked": 1, "clear": 0, "inconclusive": 0}, table["blocked"])
        self.assertEqual(0.5, table["open"]["clear"])


if __name__ == "__main__":
    unittest.main()
