import unittest

from backend.decision_engine import analyze, likelihoods_from_accuracy, posterior_blocked, rank_actions
from backend.outcomes import INSPECTED, apply_route_outcomes
from backend.scenario import available_scenarios, load_scenario


class DecisionEngineTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario(variant="disruption")

    def test_bayesian_update_moves_probability(self):
        self.assertGreater(posterior_blocked(0.55, "blocked", 0.9), 0.55)
        self.assertLess(posterior_blocked(0.55, "clear", 0.9), 0.55)
        self.assertEqual(0.55, posterior_blocked(0.55, "inconclusive", 0.9))

    def test_accuracy_and_its_table_give_the_same_posterior(self):
        for observation in ("blocked", "clear", "inconclusive"):
            self.assertAlmostEqual(posterior_blocked(0.55, observation, 0.9),
                                   posterior_blocked(0.55, observation, likelihoods_from_accuracy(0.9)))

    def test_inconclusive_is_informative_when_its_rate_depends_on_the_road(self):
        table = {"blocked": {"blocked": 0.5, "clear": 0.1, "inconclusive": 0.4},
                 "open": {"blocked": 0.1, "clear": 0.7, "inconclusive": 0.2}}
        self.assertGreater(posterior_blocked(0.5, "inconclusive", table), 0.5)

    def test_rank_is_sorted_and_complete(self):
        ranked = rank_actions(self.scenario["decision_model"], 0.55)
        self.assertEqual([1, 2, 3], [item["rank"] for item in ranked])
        self.assertEqual(sorted(x["expected_loss"] for x in ranked), [x["expected_loss"] for x in ranked])

    def test_disruption_a_recommends_inspection(self):
        result = analyze(self.scenario)
        self.assertTrue(result["evoi"]["recommend_inspection"])
        self.assertGreater(result["evoi"]["net_value_of_information"], 0)

    def test_disruption_b_does_not_recommend_inspection(self):
        result = analyze(load_scenario(variant="disruption_b"))
        self.assertFalse(result["evoi"]["recommend_inspection"])
        self.assertLess(result["evoi"]["net_value_of_information"], 0)

    def test_observation_produces_after_ranking(self):
        result = analyze(self.scenario, "clear")
        self.assertEqual(3, len(result["after"]))
        self.assertLess(result["posterior_blocked_probability"], result["prior_blocked_probability"])

    def test_normal_scenario_recommends_acting_now(self):
        result = analyze(load_scenario(variant="normal"))
        self.assertFalse(result["evoi"]["recommend_inspection"])

    def test_evidence_that_cannot_change_the_action_only_costs_the_wait(self):
        self.scenario["decision_model"]["drone"]["accuracy"] = 0.6
        evoi = analyze(self.scenario)["evoi"]
        model = self.scenario["decision_model"]
        # Rerouting stays best, six minutes later: six more late minutes and six paid waiting minutes.
        wait = 6 * model["lateness_cost_per_minute"] + 6 * model["rates"]["driver_hourly_rate"] / 60
        self.assertAlmostEqual(-wait, evoi["gross_value_of_information"], places=1)
        self.assertFalse(evoi["recommend_inspection"])

    def with_delay(self, minutes):
        scenario = load_scenario(variant="disruption")
        scenario["decision_model"]["drone"]["launch_and_review_minutes"] = minutes
        return apply_route_outcomes(scenario)

    def held(self, scenario, action_id, shipment_id, state="open"):
        action = next(a for a in scenario["decision_model"]["actions"] if a["id"] == action_id)
        return next(r for r in action[INSPECTED][state]["detail"]["shipments"] if r["shipment"] == shipment_id)

    def test_inspection_delay_moves_the_free_truck_and_is_not_a_flat_cost(self):
        row = self.held(self.scenario, "hold-course", "SYN-SHP-1042")
        self.assertEqual(("10:09", 6.0, 0.0), (row["arrival"], row["slack_min"], row["lateness_min"]))
        self.assertIn("hold for inspection", row["path"])
        # SYN-TRK-12 is not free until 09:25, after the inspection ends at 09:18, so it does not wait.
        self.assertEqual("10:42", self.held(self.scenario, "hold-course", "SYN-SHP-1048")["arrival"])
        self.assertEqual("10:39", self.held(self.scenario, "swap-truck", "SYN-SHP-1042")["arrival"])
        evoi = analyze(self.scenario)["evoi"]
        self.assertEqual(self.scenario["decision_model"]["drone"]["direct_cost"], evoi["inspection_cost"])

    def test_delay_eating_into_slack_lowers_the_net_value_of_inspecting(self):
        nets = [analyze(self.with_delay(m))["evoi"]["net_value_of_information"] for m in (0, 6, 12, 18)]
        self.assertEqual(sorted(nets, reverse=True), nets)
        self.assertEqual(4, len(set(nets)))
        # Twelve minutes use up SYN-SHP-1042's slack exactly; eighteen make it six minutes late on a clear road.
        self.assertEqual(0.0, self.held(self.with_delay(12), "hold-course", "SYN-SHP-1042")["lateness_min"])
        self.assertEqual(6.0, self.held(self.with_delay(18), "hold-course", "SYN-SHP-1042")["lateness_min"])
        self.assertGreater(nets[2] - nets[3], nets[1] - nets[2])

    def test_ranking_after_an_observation_starts_after_the_delay(self):
        result = analyze(self.scenario, "clear")
        arrival = {key: next(a for a in result[key] if a["id"] == "hold-course")["outcomes"]["open"]["detail"]["arrival"]
                   for key in ("before", "after")}
        self.assertEqual({"before": "10:03", "after": "10:09"}, arrival)
        for option in result["assignments"]["after"]:
            self.assertNotIn(INSPECTED, option)

    def test_break_even_accuracy_separates_worth_it_from_not(self):
        threshold = analyze(self.scenario)["evoi"]["break_even_accuracy"]
        for accuracy, worth_it in ((threshold - 0.01, False), (threshold + 0.01, True)):
            self.scenario["decision_model"]["drone"]["accuracy"] = accuracy
            self.assertEqual(worth_it, analyze(self.scenario)["evoi"]["recommend_inspection"])

    def test_measured_cv_alone_is_reported_alongside_reviewed_evidence(self):
        evoi = analyze(self.scenario)["evoi"]
        self.assertTrue(evoi["recommend_inspection"])
        # Current measured CV error is too high to justify a flight on its own.
        self.assertFalse(evoi["cv_only"]["recommend_inspection"])

    def test_scenario_catalog(self):
        self.assertEqual(["normal", "disruption", "disruption_b"], [item["id"] for item in available_scenarios()])

    def test_sensitivity_spans_zero_to_one(self):
        points = analyze(self.scenario)["sensitivity"]
        self.assertEqual(21, len(points))
        self.assertEqual((0, 1), (points[0]["prior"], points[-1]["prior"]))
        # With no uncertainty an inspection can only cost money.
        self.assertLess(points[0]["net_value"], 0)
        self.assertLess(points[-1]["net_value"], 0)


if __name__ == "__main__":
    unittest.main()
