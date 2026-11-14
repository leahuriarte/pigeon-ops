"""Route candidate generation and synthetic traffic scoring."""
import unittest

from backend.route_optimizer import load_inputs, optimize_routes, traffic_at
from backend.scenario import load_scenario


class RouteOptimizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = load_scenario(variant="normal")
        cls.catalog, cls.traffic = load_inputs()

    def test_saved_catalog_covers_all_shipments_and_uses_current_truck_position(self):
        self.assertEqual({item["id"] for item in self.scenario["shipments"]},
                         set(self.catalog["shipments"]))
        truck = next(item for item in self.scenario["trucks"] if item["id"] == "SYN-TRK-07")
        first = self.catalog["shipments"]["SYN-SHP-1042"]
        self.assertEqual("synthetic truck position", first["start_basis"])
        self.assertAlmostEqual(truck["lon"], first["start"][0], places=4)
        self.assertAlmostEqual(truck["lat"], first["start"][1], places=4)
        for record in self.catalog["shipments"].values():
            self.assertTrue(record["candidates"])
            for route in record["candidates"]:
                self.assertTrue(route["id"].startswith("SYN-"))
                self.assertEqual("estimated", route["basis"])
                self.assertGreater(route["distance_m"], 0)
                self.assertGreater(route["duration_s"], 0)
                self.assertEqual("LineString", route["geometry"]["type"])

    def test_traffic_changes_with_time_and_is_labeled(self):
        morning = traffic_at(self.traffic, 9 * 60 + 30)
        peak = traffic_at(self.traffic, 10 * 60 + 30)
        self.assertEqual("synthetic", morning["basis"])
        self.assertGreater(peak["duration_multiplier"], morning["duration_multiplier"])
        self.assertGreater(peak["extra_delay_max_min"], morning["extra_delay_max_min"])

    def test_search_covers_all_assigned_shipments_and_preserves_dispatch_control(self):
        plan = optimize_routes(self.scenario, catalog=self.catalog, traffic=self.traffic)
        rows = [row for truck in plan["trucks"] for row in truck["shipments"]]
        self.assertEqual(12, len(rows))
        self.assertEqual(9, len(plan["trucks"]))
        self.assertTrue(plan["advisory"])
        self.assertFalse(plan["truck_validated"])
        self.assertEqual({"estimated"}, {row["basis"] for row in rows})
        self.assertEqual({"synthetic"}, {row["traffic_basis"] for row in rows})
        self.assertTrue(all(0 <= row["risk_after_start"] <= 1 for row in rows))
        self.assertTrue(all(0 <= row["missed_appointment_probability"] <= 1 for row in rows))
        self.assertTrue(all(row["expected_lateness_min"] >= row["lateness_min"] for row in rows))
        self.assertTrue(any(row["expected_lateness_min"] > row["lateness_min"] for row in rows))

    def test_later_traffic_time_is_a_shifted_start_not_a_live_position(self):
        early = optimize_routes(self.scenario, at="09:12", catalog=self.catalog, traffic=self.traffic)
        later = optimize_routes(self.scenario, at="10:30", catalog=self.catalog, traffic=self.traffic)
        first_early = early["trucks"][0]["shipments"][0]["arrival"]
        first_later = later["trucks"][0]["shipments"][0]["arrival"]
        self.assertLess(first_early, first_later)
        self.assertGreaterEqual(first_later, "10:30")

    def test_earlier_trip_delay_propagates_to_later_load_on_same_truck(self):
        scenario = {
            "generated_at": "2026-11-14T09:00:00-08:00",
            "shipments": [
                {"id": "SYN-SHP-A", "truck": "SYN-TRK-A", "origin": "a", "destination": "b", "route": "one", "eta": "10:00", "appointment": "10:15–10:45"},
                {"id": "SYN-SHP-B", "truck": "SYN-TRK-A", "origin": "b", "destination": "c", "route": "two", "eta": "11:00", "appointment": "11:15–11:45"},
            ],
            "decision_model": {"rates": {"co2_kg_per_mile": 1.6, "cost_per_mile": 1.3, "driver_hourly_rate": 35},
                               "lateness_cost_per_minute": 85, "emissions_cost_per_kg": 0.25, "hard_constraint_penalty": 2500},
        }
        route = lambda id_: {"id": id_, "distance_m": 1000, "duration_s": 600, "basis": "estimated"}
        catalog = {"shipments": {"SYN-SHP-A": {"candidates": [route("SYN-ROUTE-A")]},
                                 "SYN-SHP-B": {"candidates": [route("SYN-ROUTE-B")]}}}
        traffic = {"periods": [{"id": "SYN-TRAFFIC", "start": "09:00", "end": "12:00",
                                "duration_multiplier": 3, "extra_delay_max_min": 10, "basis": "synthetic"}],
                   "route_adjustments": []}
        rows = optimize_routes(scenario, catalog=catalog, traffic=traffic)["trucks"][0]["shipments"]
        self.assertEqual("10:20", rows[0]["arrival"])
        self.assertEqual("11:40", rows[1]["arrival"])


if __name__ == "__main__":
    unittest.main()
