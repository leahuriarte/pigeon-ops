"""Truck-to-shipment assignment enumeration and scoring."""
import unittest
from copy import deepcopy

from backend.assignment import AssignmentModel
from backend.decision_engine import _loss, analyze, rank_actions
from backend.outcomes import RouteModel, apply_route_outcomes
from backend.scenario import load_scenario


class AssignmentTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario(variant="disruption")
        self.model = self.scenario["decision_model"]

    def test_spare_truck_enumerates_every_permutation(self):
        scenario = deepcopy(self.scenario)
        scenario["trucks"].append({"id": "SYN-TRK-99", "available_at": "09:30", "lat": 33.737, "lon": -118.265})
        scenario["decision_model"]["assignment_pool"]["trucks"].append("SYN-TRK-99")
        pool = apply_route_outcomes(scenario)["decision_model"]["assignments"]
        self.assertEqual(6, pool["count"])  # 3 trucks taken 2 at a time, order matters
        combos = {tuple(o["trucks"].values()) for o in pool["options"]}
        self.assertEqual(6, len(combos))
        for combo in combos:
            self.assertEqual(2, len(set(combo)))
        self.assertEqual(1, sum(o["planned"] for o in pool["options"]))

    def test_options_reuse_the_outcomes_of_their_matching_action(self):
        actions = {a["id"]: a for a in self.model["actions"]}
        options = self.model["assignments"]["options"]
        self.assertEqual(["hold-course"], [o["action"] for o in options if o["planned"]])
        for option in options:
            if option["action"]:
                self.assertEqual(actions[option["action"]]["outcomes"], option["outcomes"])

    def test_assignment_loss_uses_the_decision_weights(self):
        result = analyze(self.scenario)
        prior = result["prior_blocked_probability"]
        actions = {a["id"]: a["expected_loss"] for a in rank_actions(self.model, prior)}
        for item in result["assignments"]["before"]:
            with self.subTest(option=item["id"]):
                outcomes = item["outcomes"]
                expected = prior * _loss(outcomes["blocked"], self.model) + (1 - prior) * _loss(outcomes["open"], self.model)
                self.assertAlmostEqual(expected, item["expected_loss"], delta=0.01)
                self.assertEqual(actions[item["action"]], item["expected_loss"])

    def test_invalid_pools_are_rejected(self):
        def drop_affected_shipment(scenario):
            scenario["decision_model"]["assignment_pool"]["shipments"] = ["SYN-SHP-1048"]

        def drop_availability(scenario):
            del next(t for t in scenario["trucks"] if t["id"] == "SYN-TRK-12")["available_at"]

        for change in (drop_affected_shipment, drop_availability):
            with self.subTest(change=change.__name__):
                scenario = deepcopy(self.scenario)
                change(scenario)
                with self.assertRaises(ValueError):
                    AssignmentModel(scenario, RouteModel(scenario))


if __name__ == "__main__":
    unittest.main()
