"""Route-based outcomes and plan indicators."""
import unittest
from copy import deepcopy

from backend.decision_engine import analyze
from backend.outcomes import (METERS_PER_MILE, apply_override, apply_route_outcomes, clock_minutes,
                              fraction_along)
from backend.scenario import available_scenarios, load_scenario

INDICATOR_IDS = ["shipments_at_risk", "appointments_feasible", "expected_delay_min", "added_miles", "co2_kg",
                 "community_exposure"]


def action(scenario, action_id):
    return next(a for a in scenario["decision_model"]["actions"] if a["id"] == action_id)


class GeometryTests(unittest.TestCase):
    LINE = {"type": "LineString", "coordinates": [[-118.0, 33.0], [-118.0, 33.1], [-118.0, 33.2]]}

    def test_fraction_along_projects_onto_line(self):
        self.assertAlmostEqual(0.0, fraction_along(self.LINE, 33.0, -118.0), places=3)
        self.assertAlmostEqual(0.5, fraction_along(self.LINE, 33.1, -117.99), places=3)
        self.assertAlmostEqual(1.0, fraction_along(self.LINE, 33.3, -118.0), places=3)

    def test_clock_minutes_reads_windows(self):
        self.assertEqual([615, 645], clock_minutes("10:15–10:45"))
        self.assertEqual([], clock_minutes("—"))


class RouteOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario(variant="disruption")
        self.model = self.scenario["decision_model"]
        self.rates = self.model["rates"]

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
        # CO2 = miles x 1.6 kg
        self.assertAlmostEqual(detail["miles"] * self.rates["co2_kg_per_mile"], reroute["emissions_kg"], delta=0.1)
        # cost = miles x cost_per_mile + driver minutes x hourly_rate / 60
        cost = detail["miles"] * self.rates["cost_per_mile"] + detail["driver_min"] * self.rates["driver_hourly_rate"] / 60
        self.assertAlmostEqual(cost, detail["trip_cost"], delta=0.1)
        self.assertAlmostEqual(detail["trip_cost"] - planned["trip_cost"], reroute["incremental_cost"], delta=0.1)

    def test_detour_is_longer_than_the_remaining_planned_trip(self):
        reroute = action(self.scenario, "reroute")["outcomes"]
        self.assertGreater(reroute["open"]["detail"]["added_miles"], 0)
        self.assertEqual(reroute["open"], reroute["blocked"])

    def test_blocked_road_makes_holding_course_worse(self):
        hold = action(self.scenario, "hold-course")["outcomes"]
        self.assertGreater(hold["blocked"]["lateness_min"], hold["open"]["lateness_min"])
        self.assertGreater(hold["blocked"]["emissions_kg"], hold["open"]["emissions_kg"])
        self.assertEqual(1, hold["blocked"]["constraint_breaches"])

    def test_distances_come_from_route_data(self):
        routes = {r["id"]: r for r in self.scenario["routes"]}
        planned = self.model["planned_trip"]
        share = 1 - planned["truck_position_share"]
        self.assertAlmostEqual(share * routes["primary"]["distance_m"] / METERS_PER_MILE, planned["miles"], delta=0.05)

    def test_computed_outcomes_are_labelled_estimates(self):
        for action_id in ("hold-course", "reroute"):
            for outcome in action(self.scenario, action_id)["outcomes"].values():
                self.assertEqual("estimated", outcome["basis"])
        for outcome in action(self.scenario, "swap-truck")["outcomes"].values():
            self.assertEqual("override", outcome["basis"])
            self.assertEqual("assumed", outcome["override_basis"])

    def test_override_wins_over_computed_values(self):
        scenario = load_scenario(variant="disruption")
        reroute = action(scenario, "reroute")
        reroute["override"] = {"basis": "assumed", "blocked": {"lateness_min": 99}}
        apply_route_outcomes(scenario)
        outcomes = action(scenario, "reroute")["outcomes"]
        self.assertEqual(99, outcomes["blocked"]["lateness_min"])
        self.assertEqual("override", outcomes["blocked"]["basis"])
        self.assertEqual("estimated", outcomes["open"]["basis"])

    def test_action_without_plan_needs_a_complete_override(self):
        with self.assertRaises(ValueError):
            apply_override({}, {"open": {"lateness_min": 1}})

    def test_changing_rates_changes_cost(self):
        scenario = deepcopy(self.scenario)
        scenario["decision_model"]["rates"]["cost_per_mile"] *= 2
        apply_route_outcomes(scenario)
        before = action(self.scenario, "reroute")["outcomes"]["open"]["incremental_cost"]
        after = action(scenario, "reroute")["outcomes"]["open"]["incremental_cost"]
        self.assertGreater(after, before)


class IndicatorTests(unittest.TestCase):
    def test_every_scenario_returns_all_indicators(self):
        for item in available_scenarios():
            with self.subTest(variant=item["id"]):
                indicators = analyze(load_scenario(variant=item["id"]))["indicators"]
                self.assertEqual(INDICATOR_IDS, [i["id"] for i in indicators])
                for indicator in indicators:
                    self.assertLessEqual({"id", "label", "value", "unit", "basis"}, set(indicator))
                    if indicator["id"] != "community_exposure":
                        self.assertIsNotNone(indicator["value"], indicator["id"])
                        self.assertEqual("estimated", indicator["basis"])
                feasible = next(i for i in indicators if i["id"] == "appointments_feasible")
                self.assertEqual(12, feasible["of"])

    def test_disruption_indicators_follow_the_top_option(self):
        result = analyze(load_scenario(variant="disruption"))
        self.assertEqual("reroute", result["before"][0]["id"])
        values = {i["id"]: i["value"] for i in result["indicators"]}
        reroute = result["before"][0]["outcomes"]["open"]
        self.assertEqual(1, values["shipments_at_risk"])
        self.assertEqual(reroute["lateness_min"], values["expected_delay_min"])
        self.assertAlmostEqual(reroute["detail"]["added_miles"], values["added_miles"], delta=0.05)

    def test_normal_has_nothing_at_risk(self):
        values = {i["id"]: i["value"] for i in analyze(load_scenario(variant="normal"))["indicators"]}
        self.assertEqual(0, values["shipments_at_risk"])
        self.assertEqual(11, values["appointments_feasible"])


if __name__ == "__main__":
    unittest.main()
