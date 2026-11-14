import unittest

from backend.decision_engine import analyze, posterior_blocked, rank_actions
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
