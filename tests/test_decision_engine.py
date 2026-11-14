import unittest

from backend.decision_engine import (
    analyze, observation_likelihoods, posterior_blocked, rank_actions, symmetric_likelihoods,
)
from backend.scenario import available_scenarios, load_scenario


class DecisionEngineTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario(variant="disruption")

    def test_bayesian_update_moves_probability(self):
        self.assertGreater(posterior_blocked(0.55, "blocked", 0.9), 0.55)
        self.assertLess(posterior_blocked(0.55, "clear", 0.9), 0.55)

    def test_rank_is_sorted_and_complete(self):
        ranked = rank_actions(self.scenario["decision_model"], 0.55)
        self.assertEqual([1, 2, 3], [item["rank"] for item in ranked])
        self.assertEqual(sorted(x["expected_loss"] for x in ranked), [x["expected_loss"] for x in ranked])

    def test_demo_recommends_inspection(self):
        result = analyze(self.scenario)
        self.assertTrue(result["evoi"]["recommend_inspection"])
        self.assertGreater(result["evoi"]["net_value_of_information"], 0)

    def test_observation_produces_after_ranking(self):
        result = analyze(self.scenario, "clear")
        self.assertEqual(3, len(result["after"]))
        self.assertLess(result["posterior_blocked_probability"], result["prior_blocked_probability"])

    def test_normal_scenario_recommends_acting_now(self):
        result = analyze(load_scenario(variant="normal"))
        self.assertFalse(result["evoi"]["recommend_inspection"])

    def test_accuracy_and_symmetric_table_agree(self):
        table = symmetric_likelihoods(0.9)
        for observation in ("blocked", "clear", "inconclusive"):
            self.assertAlmostEqual(posterior_blocked(0.55, observation, 0.9),
                                   posterior_blocked(0.55, observation, table))
        self.assertEqual(0.55, posterior_blocked(0.55, "inconclusive", 0.9))

    def test_observation_model_composes_measured_and_assumed_parts(self):
        drone = {"observation_model": {
            "signal_given_visual_state": {
                "dense_traffic": {"blocked": 0.8, "clear": 0.0, "inconclusive": 0.2},
                "light_traffic": {"blocked": 0.0, "clear": 0.9, "inconclusive": 0.1},
            },
            "visual_state_given_road": {
                "blocked": {"dense_traffic": 1.0, "light_traffic": 0.0},
                "open": {"dense_traffic": 0.5, "light_traffic": 0.5},
            },
        }}
        table = observation_likelihoods(drone)
        self.assertEqual({"blocked": 0.8, "clear": 0.0, "inconclusive": 0.2}, table["blocked"])
        self.assertAlmostEqual(0.45, table["open"]["clear"])
        # Inconclusive is informative once its rate differs by road state.
        self.assertGreater(posterior_blocked(0.5, "inconclusive", table), 0.5)

    def test_evidence_that_cannot_change_the_action_has_no_value(self):
        scenario = load_scenario(variant="disruption")
        scenario["decision_model"]["drone"]["accuracy"] = 0.6
        evoi = analyze(scenario)["evoi"]
        self.assertAlmostEqual(0, evoi["gross_value_of_information"], places=1)
        self.assertFalse(evoi["recommend_inspection"])

    def test_break_even_accuracy_separates_worth_it_from_not(self):
        scenario = load_scenario(variant="disruption")
        threshold = analyze(scenario)["evoi"]["break_even_accuracy"]
        for accuracy, worth_it in ((threshold - 0.01, False), (threshold + 0.01, True)):
            scenario["decision_model"]["drone"]["accuracy"] = accuracy
            self.assertEqual(worth_it, analyze(scenario)["evoi"]["recommend_inspection"])

    def test_measured_cv_alone_is_reported_alongside_reviewed_evidence(self):
        evoi = analyze(self.scenario)["evoi"]
        self.assertTrue(evoi["recommend_inspection"])
        # Current measured CV error is too high to justify a flight on its own.
        self.assertFalse(evoi["cv_only"]["recommend_inspection"])

    def test_scenario_catalog_contains_demo_pair(self):
        self.assertEqual(["normal", "disruption"], [item["id"] for item in available_scenarios()])


if __name__ == "__main__":
    unittest.main()
