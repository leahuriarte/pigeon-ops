"""Route-based outcomes."""
import unittest

from backend.outcomes import apply_route_outcomes, clock_minutes, fraction_along
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

    def test_override_wins_over_computed_values(self):
        action(self.scenario, "reroute")["override"] = {"basis": "assumed", "blocked": {"lateness_min": 99}}
        apply_route_outcomes(self.scenario)
        outcomes = action(self.scenario, "reroute")["outcomes"]
        self.assertEqual(99, outcomes["blocked"]["lateness_min"])
        self.assertEqual("override", outcomes["blocked"]["basis"])
        self.assertEqual("estimated", outcomes["open"]["basis"])


if __name__ == "__main__":
    unittest.main()
