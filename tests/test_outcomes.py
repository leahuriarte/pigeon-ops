"""Route-based outcomes."""
import unittest

from backend.outcomes import clock_minutes, fraction_along
from backend.scenario import load_scenario


def action(scenario, action_id):
    return next(a for a in scenario["decision_model"]["actions"] if a["id"] == action_id)


class RouteOutcomeTests(unittest.TestCase):
    LINE = {"type": "LineString", "coordinates": [[-118.0, 33.0], [-118.0, 33.1], [-118.0, 33.2]]}

    def setUp(self):
        self.scenario = load_scenario(variant="disruption")
        self.model = self.scenario["decision_model"]
        self.rates = self.model["rates"]

    def test_geometry_and_clock_helpers(self):
        self.assertAlmostEqual(0.0, fraction_along(self.LINE, 33.0, -118.0), places=3)
        self.assertAlmostEqual(0.5, fraction_along(self.LINE, 33.1, -117.99), places=3)
        self.assertAlmostEqual(1.0, fraction_along(self.LINE, 33.3, -118.0), places=3)
        self.assertEqual([615, 645], clock_minutes("10:15–10:45"))
        self.assertEqual([], clock_minutes("—"))

    def test_planned_trip_arrives_at_planned_eta(self):
        hold = action(self.scenario, "hold-course")["outcomes"]["open"]
        self.assertEqual(self.scenario["shipments"][0]["eta"], hold["detail"]["arrival"])
        self.assertEqual(0, hold["incremental_cost"])
        self.assertEqual(0, hold["lateness_min"])

    def test_formulas_match_methods(self):
        planned = self.model["planned_trip"]
        reroute = action(self.scenario, "reroute")["outcomes"]["open"]
        detail = reroute["detail"]
        # lateness = max(0, arrival - appointment start)
        appointment = clock_minutes(self.scenario["shipments"][0]["appointment"])[0]
        self.assertAlmostEqual(max(0, clock_minutes(detail["arrival"])[0] - appointment), reroute["lateness_min"],
                               delta=1)
        # CO2 = miles x kg per mile
        self.assertAlmostEqual(detail["miles"] * self.rates["co2_kg_per_mile"], reroute["emissions_kg"], delta=0.1)
        # cost = miles x cost_per_mile + driver minutes x hourly_rate / 60
        cost = detail["miles"] * self.rates["cost_per_mile"] + detail["driver_min"] * self.rates["driver_hourly_rate"] / 60
        self.assertAlmostEqual(cost, detail["trip_cost"], delta=0.1)
        self.assertAlmostEqual(detail["trip_cost"] - planned["trip_cost"], reroute["incremental_cost"], delta=0.1)

    def test_blocked_road_makes_holding_course_worse(self):
        hold = action(self.scenario, "hold-course")["outcomes"]
        self.assertGreater(hold["blocked"]["lateness_min"], hold["open"]["lateness_min"])
        self.assertGreater(hold["blocked"]["emissions_kg"], hold["open"]["emissions_kg"])

    def test_heavy_traffic_delays_the_truck_without_turning_it_back(self):
        scenario = load_scenario(variant="congestion")
        hold = action(scenario, "hold-course")["outcomes"]
        slowdown = scenario["decision_model"]["blockage"]["slowdown_min"]
        late = clock_minutes(hold["blocked"]["detail"]["arrival"])[0] - clock_minutes(hold["open"]["detail"]["arrival"])[0]
        self.assertEqual(slowdown, late)
        self.assertEqual(hold["open"]["detail"]["miles"], hold["blocked"]["detail"]["miles"])
        self.assertNotIn("detour", hold["blocked"]["detail"]["path"])

    def test_terminal_access_queue_is_ahead_of_the_load_and_fits_its_buffer(self):
        from backend.outcomes import RouteModel
        scenario = load_scenario(variant="congestion")
        model = RouteModel(scenario)
        self.assertLess(model.truck_at, model.incident_at)
        self.assertLess(model.incident_at, 0.1)  # at the terminal end of this route
        self.assertEqual(35, model.shipment["slack_min"])
        outcomes = action(scenario, "hold-course")["outcomes"]
        self.assertEqual("10:10", outcomes["blocked"]["detail"]["arrival"])
        self.assertEqual(0, outcomes["blocked"]["lateness_min"])
        self.assertEqual(0, outcomes["blocked"]["constraint_breaches"])
        # Variant overlays must not move the truck or queue in the crash scenario.
        crash = RouteModel(load_scenario(variant="disruption"))
        self.assertGreater(crash.incident_at, 0.5)
        self.assertGreater(crash.truck_at, model.truck_at)


if __name__ == "__main__":
    unittest.main()
