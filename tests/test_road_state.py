import unittest

from cv.road_state import assess_motion, assess_road, likelihood_table, road_likelihoods

ROAD = [(0, 0), (1000, 0), (1000, 100), (0, 100)]


def row_of_cars(count, gap, top=40):
    """Cars 20 px long spaced gap px apart along the road."""
    return [[index * (20 + gap), top, index * (20 + gap) + 20, top + 20] for index in range(count)]


class RoadStateTests(unittest.TestCase):
    def test_vehicle_spacing_on_the_road_sets_the_signal(self):
        dense = assess_road(row_of_cars(15, gap=5), ROAD)
        self.assertEqual(("dense_traffic", "blocked"), (dense["visual_state"], dense["signal"]))
        light = assess_road(row_of_cars(5, gap=150), ROAD)
        self.assertEqual(("light_traffic", "clear"), (light["visual_state"], light["signal"]))
        parking_lot = row_of_cars(15, gap=5, top=300)
        self.assertEqual(0, assess_road(parking_lot, ROAD)["vehicles_on_road"])

    def test_dark_frame_without_detections_is_not_clear(self):
        self.assertEqual("inconclusive", assess_road([], ROAD, frame_brightness=30)["signal"])

    def test_vehicle_motion_sets_the_suggested_observation(self):
        cases = (([[1, 2, 0], [2, 0, 60]], "stopped_traffic", "blocked"),
                 ([[60, 84, 42], [70, 80]], "free_flow", "clear"),
                 ([[], [60], []], "no_traffic", "inconclusive"),
                 ([[0, 2, 60, 80]], "mixed_flow", "inconclusive"))
        for samples, state, signal in cases:
            with self.subTest(state=state):
                result = assess_motion(samples)
                self.assertEqual((state, signal), (result["motion_state"], result["signal"]))

    def test_likelihood_table_is_smoothed_and_normalized(self):
        table = likelihood_table([("dense_traffic", "dense_traffic"), ("light_traffic", "light_traffic")])
        for row in table.values():
            probabilities = [row[signal] for signal in ("blocked", "clear", "inconclusive")]
            self.assertAlmostEqual(1.0, sum(probabilities), places=3)
            self.assertTrue(all(value > 0 for value in probabilities))

    def test_road_likelihoods_mix_visual_states(self):
        perfect = {"dense_traffic": {"blocked": 1, "clear": 0, "inconclusive": 0},
                   "light_traffic": {"blocked": 0, "clear": 1, "inconclusive": 0}}
        table = road_likelihoods(perfect, {"blocked": {"dense_traffic": 1.0}, "open": {"dense_traffic": 0.5, "light_traffic": 0.5}})
        self.assertEqual({"blocked": 1, "clear": 0, "inconclusive": 0}, table["blocked"])
        self.assertEqual(0.5, table["open"]["clear"])


if __name__ == "__main__":
    unittest.main()
