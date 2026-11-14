"""Checks that data files match the contracts in AGENTS.md."""
import json
import unittest
from pathlib import Path

from backend.decision_engine import analyze
from backend.scenario import available_scenarios, load_scenario

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "cv" / "outputs" / "review-cases" / "library.json"
INDICATOR_IDS = {"shipments_at_risk", "appointments_feasible", "expected_delay_min", "added_miles", "co2_kg", "community_exposure"}
BASES = {"stub", "estimated", "simulated", "measured"}
SHIPMENT_ROW = {"shipment", "truck", "route", "reassigned", "arrival", "appointment", "slack_min", "lateness_min",
                "constraint_breaches", "basis"}


class ScenarioContractTests(unittest.TestCase):
    def test_every_variant_matches_contract(self):
        for item in available_scenarios():
            with self.subTest(variant=item["id"]):
                s = load_scenario(variant=item["id"])
                for key in ("id", "name", "scenario_type", "disruption", "decision_model", "routes", "shipments"):
                    self.assertIn(key, s)
                self.assertIn("prior_blocked_probability", s["disruption"])
                indicators = s["indicators_stub"]
                self.assertEqual(INDICATOR_IDS, {i["id"] for i in indicators})
                for indicator in indicators:
                    self.assertIn(indicator["basis"], BASES)

    def test_every_route_has_network_geometry(self):
        for item in available_scenarios():
            for route in load_scenario(variant=item["id"])["routes"]:
                with self.subTest(variant=item["id"], route=route["id"]):
                    geometry = route.get("geometry")
                    self.assertIsNotNone(geometry)
                    self.assertEqual("LineString", geometry["type"])
                    self.assertGreaterEqual(len(geometry["coordinates"]), 2)
                    self.assertGreater(route.get("distance_m", 0), 0)

    def test_every_action_has_a_plan_or_override(self):
        for item in available_scenarios():
            for action in load_scenario(variant=item["id"])["decision_model"]["actions"]:
                with self.subTest(variant=item["id"], action=action["id"]):
                    self.assertTrue(action.get("plan") or action.get("override"))
                    for state in ("open", "blocked"):
                        self.assertIn(action["outcomes"][state]["basis"], ("estimated", "override"))

    def test_analysis_indicators_match_contract(self):
        for item in available_scenarios():
            with self.subTest(variant=item["id"]):
                indicators = analyze(load_scenario(variant=item["id"]))["indicators"]
                self.assertEqual(INDICATOR_IDS, {i["id"] for i in indicators})
                for indicator in indicators:
                    self.assertIn(indicator["basis"], BASES)
                self.assertIn("of", next(i for i in indicators if i["id"] == "appointments_feasible"))

    def test_analysis_assignments_match_contract(self):
        for item in available_scenarios():
            with self.subTest(variant=item["id"]):
                assignments = analyze(load_scenario(variant=item["id"]), "blocked")["assignments"]
                self.assertLessEqual({"shipments", "trucks", "count", "before", "after"}, set(assignments))
                self.assertEqual(assignments["count"], len(assignments["before"]))
                for option in assignments["before"] + assignments["after"]:
                    self.assertLessEqual({"id", "trucks", "planned", "action", "rank", "expected_loss", "outcomes"},
                                         set(option))
                    for state in ("open", "blocked"):
                        for row in option["outcomes"][state]["detail"]["shipments"]:
                            self.assertLessEqual(SHIPMENT_ROW, set(row))
                            self.assertIn(row["basis"], BASES)


class InspectionLibraryContractTests(unittest.TestCase):
    CASE = {"id", "title", "visual_label", "review_note", "image_quality", "annotated_image", "source_image",
            "vehicle_detections", "excluded_detections", "allowed_observations", "source"}
    SOURCE = {"source_url", "sha256", "credit", "license", "location", "historical"}

    @unittest.skipUnless(LIBRARY.is_file(), "build it with python -m cv.prepare_review")
    def test_prepared_library_matches_contract(self):
        library = json.loads(LIBRARY.read_text(encoding="utf-8"))
        self.assertTrue(library["available"])
        for case in library["cases"]:
            with self.subTest(case=case["id"]):
                self.assertLessEqual(self.CASE, set(case))
                self.assertLessEqual(self.SOURCE, set(case["source"]))
                self.assertTrue(case["allowed_observations"])
                self.assertLessEqual(set(case["allowed_observations"]), {"blocked", "clear", "inconclusive"})


if __name__ == "__main__":
    unittest.main()
