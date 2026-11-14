"""Truck-to-shipment assignment enumeration, arrivals and the swap-truck option."""
import unittest
from copy import deepcopy

from backend.assignment import AssignmentModel
from backend.decision_engine import _loss, analyze, rank_actions
from backend.indicators import compute_indicators
from backend.outcomes import METERS_PER_MILE, RouteModel, apply_route_outcomes, clock_minutes, straight_line_m
from backend.scenario import load_scenario


def by_id(items, key):
    return next(item for item in items if item["id"] == key)


def action(scenario, action_id):
    return by_id(scenario["decision_model"]["actions"], action_id)


def row(outcome, shipment_id):
    return next(r for r in outcome["detail"]["shipments"] if r["shipment"] == shipment_id)


def option(scenario, action_id):
    return next(o for o in scenario["decision_model"]["assignments"]["options"] if o["action"] == action_id)


class EnumerationTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario(variant="disruption")
        self.pool = self.scenario["decision_model"]["assignments"]

    def test_two_trucks_over_two_shipments_give_two_assignments(self):
        self.assertEqual(["SYN-SHP-1042", "SYN-SHP-1048"], self.pool["shipments"])
        self.assertEqual(2, self.pool["count"])
        self.assertEqual([True, False], [o["planned"] for o in self.pool["options"]])

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

    def test_planned_assignment_is_hold_course(self):
        planned = option(self.scenario, "hold-course")
        self.assertTrue(planned["planned"])
        self.assertEqual(action(self.scenario, "hold-course")["outcomes"], planned["outcomes"])

    def test_swapped_assignment_is_the_swap_action(self):
        swapped = option(self.scenario, "swap-truck")
        self.assertEqual({"SYN-SHP-1042": "SYN-TRK-12", "SYN-SHP-1048": "SYN-TRK-07"}, swapped["trucks"])
        self.assertEqual(action(self.scenario, "swap-truck")["outcomes"], swapped["outcomes"])


class ArrivalTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario(variant="disruption")
        self.shipments = {s["id"]: s for s in self.scenario["shipments"]}
        self.trucks = {t["id"]: t for t in self.scenario["trucks"]}
        self.pool = self.scenario["decision_model"]["assignment_pool"]
        self.swap = action(self.scenario, "swap-truck")["outcomes"]

    def clock(self, text):
        return clock_minutes(text)[0]

    def fetch_min(self, truck, load_truck):
        meters = straight_line_m(truck["lat"], truck["lon"], load_truck["lat"], load_truck["lon"])
        return meters * self.pool["fetch_circuity"] / METERS_PER_MILE / self.pool["fetch_speed_mph"] * 60

    def test_unchanged_shipment_arrives_at_its_eta(self):
        for action_id in ("hold-course", "reroute"):
            for outcome in action(self.scenario, action_id)["outcomes"].values():
                other = row(outcome, "SYN-SHP-1048")
                self.assertEqual("SYN-TRK-12", other["truck"])
                self.assertEqual(self.shipments["SYN-SHP-1048"]["eta"], other["arrival"])
                self.assertFalse(other["reassigned"])

    def test_swap_arrival_follows_availability_fetch_and_handover(self):
        trk07, trk12 = self.trucks["SYN-TRK-07"], self.trucks["SYN-TRK-12"]
        # SYN-TRK-12 is free at 09:25, 13 min after SYN-TRK-07 would have carried on. It drives to the load and
        # takes it over; the load then continues from SYN-TRK-07's position, so the haul matches the planned trip.
        late_start = self.clock(trk12["available_at"]) - self.clock(trk07["available_at"])
        expected = (self.clock(self.shipments["SYN-SHP-1042"]["eta"]) + late_start
                    + self.fetch_min(trk12, trk07) + self.pool["handover_min"])
        swapped = row(self.swap["open"], "SYN-SHP-1042")
        self.assertEqual("SYN-TRK-12", swapped["truck"])
        self.assertAlmostEqual(expected, self.clock(swapped["arrival"]), delta=1)
        window_start = clock_minutes(self.shipments["SYN-SHP-1042"]["appointment"])[0]
        self.assertAlmostEqual(expected - window_start, swapped["lateness_min"], delta=0.1)

    def test_swap_shows_its_effect_on_the_other_shipment(self):
        planned_eta = self.clock(self.shipments["SYN-SHP-1048"]["eta"])
        for state, outcome in self.swap.items():
            with self.subTest(state=state):
                other = row(outcome, "SYN-SHP-1048")
                self.assertEqual("SYN-TRK-07", other["truck"])
                self.assertTrue(other["reassigned"])
                self.assertGreater(self.clock(other["arrival"]), planned_eta)
                self.assertLess(other["slack_min"], self.shipments["SYN-SHP-1048"]["slack_min"])
                # SYN-TRK-07 reaches the load before SYN-TRK-12 is free at 09:25, so it waits.
                self.assertIn("wait for handover", other["path"])

    def test_swap_cost_covers_both_shipments(self):
        hold = action(self.scenario, "hold-course")["outcomes"]["open"]["detail"]["shipments"]
        baseline = {r["shipment"]: r for r in hold}
        rows = self.swap["open"]["detail"]["shipments"]
        added = sum(r["trip_cost"] - baseline[r["shipment"]]["trip_cost"] for r in rows)
        self.assertAlmostEqual(added, self.swap["open"]["incremental_cost"], delta=0.02)
        knock_on = row(self.swap["open"], "SYN-SHP-1048")["emissions_kg"] - baseline["SYN-SHP-1048"]["emissions_kg"]
        own = row(self.swap["open"], "SYN-SHP-1042")["emissions_kg"]
        self.assertAlmostEqual(own + knock_on, self.swap["open"]["emissions_kg"], delta=0.1)

    def test_swap_still_meets_the_blockage(self):
        # The load is on SYN-TRK-07, before the obstruction, so the new truck hauls it into the blockage too.
        blocked = row(self.swap["blocked"], "SYN-SHP-1042")
        self.assertIn("turnaround at obstruction", blocked["path"])
        self.assertEqual(1, self.swap["blocked"]["constraint_breaches"])

    def test_later_availability_delays_only_the_reassigned_load(self):
        scenario = deepcopy(self.scenario)
        by_id(scenario["trucks"], "SYN-TRK-12")["available_at"] = "09:35"
        apply_route_outcomes(scenario)
        before = self.clock(row(self.swap["open"], "SYN-SHP-1042")["arrival"])
        after = self.clock(row(action(scenario, "swap-truck")["outcomes"]["open"], "SYN-SHP-1042")["arrival"])
        self.assertAlmostEqual(10, after - before, delta=1)
        self.assertEqual(action(self.scenario, "hold-course")["outcomes"], action(scenario, "hold-course")["outcomes"])


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario(variant="disruption")
        self.model = self.scenario["decision_model"]

    def test_assignment_loss_uses_the_decision_weights(self):
        result = analyze(self.scenario)
        prior = result["prior_blocked_probability"]
        for item in result["assignments"]["before"]:
            with self.subTest(option=item["id"]):
                outcomes = item["outcomes"]
                expected = prior * _loss(outcomes["blocked"], self.model) + (1 - prior) * _loss(outcomes["open"], self.model)
                self.assertAlmostEqual(expected, item["expected_loss"], delta=0.01)
        actions = {a["id"]: a["expected_loss"] for a in rank_actions(self.model, prior)}
        for item in result["assignments"]["before"]:
            self.assertEqual(actions[item["action"]], item["expected_loss"])

    def test_swap_ranks_last_and_keeps_the_inspection_story(self):
        result = analyze(self.scenario)
        self.assertEqual(["reroute", "hold-course", "swap-truck"], [a["id"] for a in result["before"]])
        self.assertEqual(["hold-course", "swap-truck"], [o["action"] for o in result["assignments"]["before"]])
        self.assertTrue(result["evoi"]["recommend_inspection"])

    def test_analysis_returns_per_shipment_arrivals(self):
        result = analyze(self.scenario, "clear")
        assignments = result["assignments"]
        self.assertEqual(assignments["count"], len(assignments["before"]))
        self.assertEqual(assignments["count"], len(assignments["after"]))
        for item in assignments["before"] + assignments["after"]:
            for outcome in item["outcomes"].values():
                self.assertEqual(assignments["shipments"], [r["shipment"] for r in outcome["detail"]["shipments"]])
                for r in outcome["detail"]["shipments"]:
                    self.assertRegex(r["arrival"], r"^\d\d:\d\d$")
        self.assertEqual([], analyze(self.scenario)["assignments"]["after"])

    def test_indicators_count_each_pool_shipment(self):
        scenario = deepcopy(self.scenario)
        scenario["decision_model"]["assignment_pool"]["handover_min"] = 40
        apply_route_outcomes(scenario)
        swap = next(a for a in rank_actions(scenario["decision_model"], 0.4) if a["id"] == "swap-truck")
        self.assertGreater(row(swap["outcomes"]["open"], "SYN-SHP-1048")["lateness_min"], 0)
        values = {i["id"]: i["value"] for i in compute_indicators(scenario, [swap])}
        self.assertEqual(2, values["shipments_at_risk"])


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario(variant="disruption")
        self.pool = self.scenario["decision_model"]["assignment_pool"]

    def check_rejected(self, scenario):
        with self.assertRaises(ValueError):
            AssignmentModel(scenario, RouteModel(scenario))

    def test_pool_must_include_the_affected_shipment(self):
        scenario = deepcopy(self.scenario)
        scenario["decision_model"]["assignment_pool"]["shipments"] = ["SYN-SHP-1048"]
        self.check_rejected(scenario)

    def test_truck_with_loads_outside_the_pool_is_rejected(self):
        scenario = deepcopy(self.scenario)
        by_id(scenario["trucks"], "SYN-TRK-19")["available_at"] = "09:20"
        scenario["decision_model"]["assignment_pool"]["trucks"].append("SYN-TRK-19")
        self.check_rejected(scenario)

    def test_pool_trucks_need_an_availability_time(self):
        scenario = deepcopy(self.scenario)
        del by_id(scenario["trucks"], "SYN-TRK-12")["available_at"]
        self.check_rejected(scenario)

    def test_plan_cannot_double_book_a_truck(self):
        scenario = deepcopy(self.scenario)
        action(scenario, "swap-truck")["plan"] = {"trucks": {"SYN-SHP-1042": "SYN-TRK-12"}}
        with self.assertRaises(ValueError):
            apply_route_outcomes(scenario)

    def test_no_pool_falls_back_to_the_affected_shipment(self):
        scenario = deepcopy(self.scenario)
        del scenario["decision_model"]["assignment_pool"]
        scenario["decision_model"]["actions"] = [a for a in scenario["decision_model"]["actions"]
                                                 if a["id"] != "swap-truck"]
        pool = apply_route_outcomes(scenario)["decision_model"]["assignments"]
        self.assertEqual((["SYN-SHP-1042"], 1), (pool["shipments"], pool["count"]))
        self.assertEqual(action(self.scenario, "reroute")["outcomes"]["open"]["lateness_min"],
                         action(scenario, "reroute")["outcomes"]["open"]["lateness_min"])


if __name__ == "__main__":
    unittest.main()
