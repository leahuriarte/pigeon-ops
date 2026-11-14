import unittest

from backend.decision_engine import analyze, likelihoods_from_accuracy, posterior_blocked, rank_actions
from backend.scenario import load_scenario


class DecisionEngineTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario(variant="disruption")

    def test_bayesian_update(self):
        self.assertGreater(posterior_blocked(0.55, "blocked", 0.9), 0.55)
        self.assertLess(posterior_blocked(0.55, "clear", 0.9), 0.55)
        self.assertEqual(0.55, posterior_blocked(0.55, "inconclusive", 0.9))
        for observation in ("blocked", "clear", "inconclusive"):
            self.assertAlmostEqual(posterior_blocked(0.55, observation, 0.9),
                                   posterior_blocked(0.55, observation, likelihoods_from_accuracy(0.9)))
        # An inconclusive result is informative when its rate depends on the road state.
        table = {"blocked": {"blocked": 0.5, "clear": 0.1, "inconclusive": 0.4},
                 "open": {"blocked": 0.1, "clear": 0.7, "inconclusive": 0.2}}
        self.assertGreater(posterior_blocked(0.5, "inconclusive", table), 0.5)

    def test_rank_is_sorted_and_complete(self):
        model = self.scenario["decision_model"]
        ranked = rank_actions(model, 0.55)
        self.assertEqual(list(range(1, len(model["actions"]) + 1)), [item["rank"] for item in ranked])
        self.assertEqual(sorted(x["expected_loss"] for x in ranked), [x["expected_loss"] for x in ranked])

    def test_inspection_is_recommended_in_the_disruption_and_not_on_a_normal_day(self):
        disruption = analyze(self.scenario)["evoi"]
        self.assertTrue(disruption["recommend_inspection"])
        self.assertGreater(disruption["net_value_of_information"], 0)
        self.assertFalse(analyze(load_scenario(variant="normal"))["evoi"]["recommend_inspection"])

    def test_each_kind_of_check_can_change_the_response(self):
        # Keeping the route wins before a traffic check; a detour wins before a crash or road-damage check.
        tops = {}
        for variant in ("disruption", "congestion", "flood"):
            scenario = load_scenario(variant=variant)
            now = analyze(scenario)
            self.assertTrue(now["evoi"]["recommend_inspection"], variant)
            tops[variant] = now["before"][0]["id"]
            self.assertEqual("reroute", analyze(scenario, "blocked")["after"][0]["id"])
            self.assertEqual("hold-course", analyze(scenario, "clear")["after"][0]["id"])
        self.assertEqual({"disruption": "reroute", "congestion": "hold-course", "flood": "reroute"}, tops)

    def test_the_detour_passes_more_schools_than_the_current_route(self):
        options = {o["id"]: o["schools_within_300m"] for o in analyze(self.scenario)["before"]}
        self.assertGreater(options["reroute"], options["hold-course"])
        exposure = {i["id"]: i for i in analyze(self.scenario)["indicators"]}["community_exposure"]
        usual = {i["id"]: i for i in analyze(load_scenario(variant="normal"))["indicators"]}["community_exposure"]
        self.assertGreater(exposure["value"], usual["value"])

    def test_observation_produces_after_ranking(self):
        result = analyze(self.scenario, "clear")
        self.assertEqual(len(result["before"]), len(result["after"]))
        self.assertLess(result["posterior_blocked_probability"], result["prior_blocked_probability"])
        self.assertEqual([], analyze(self.scenario)["after"])

    def test_break_even_accuracy_separates_worth_it_from_not(self):
        threshold = analyze(self.scenario)["evoi"]["break_even_accuracy"]
        for accuracy, worth_it in ((threshold - 0.01, False), (threshold + 0.01, True)):
            self.scenario["decision_model"]["drone"]["accuracy"] = accuracy
            self.assertEqual(worth_it, analyze(self.scenario)["evoi"]["recommend_inspection"])

    def test_sensitivity_spans_zero_to_one(self):
        points = analyze(self.scenario)["sensitivity"]
        self.assertEqual(21, len(points))
        self.assertEqual((0, 1), (points[0]["prior"], points[-1]["prior"]))
        # With no uncertainty an inspection can only cost money.
        self.assertLess(points[0]["net_value"], 0)
        self.assertLess(points[-1]["net_value"], 0)


if __name__ == "__main__":
    unittest.main()
