"""Checks that data files match the contracts in AGENTS.md."""
import json
import unittest
from pathlib import Path

from backend.decision_engine import analyze
from backend.scenario import available_scenarios, load_scenario

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "cv" / "review_library" / "library.json"
INDICATOR_IDS = {"shipments_at_risk", "appointments_feasible", "expected_delay_min", "added_miles", "co2_kg", "community_exposure"}
BASES = {"stub", "estimated", "simulated", "measured"}
SHIPMENT_ROW = {"shipment", "truck", "route", "reassigned", "arrival", "appointment", "slack_min", "lateness_min",
                "constraint_breaches", "basis"}


class ScenarioContractTests(unittest.TestCase):
    def check_indicators(self, indicators):
        self.assertEqual(INDICATOR_IDS, {i["id"] for i in indicators})
        for indicator in indicators:
            self.assertLessEqual({"id", "label", "value", "unit", "basis"}, set(indicator))
            self.assertIn(indicator["basis"], BASES)

    def test_every_variant_matches_contract(self):
        for item in available_scenarios():
            with self.subTest(variant=item["id"]):
                s = load_scenario(variant=item["id"])
                for key in ("id", "name", "scenario_type", "disruption", "decision_model", "routes", "shipments"):
                    self.assertIn(key, s)
                self.assertIn("prior_blocked_probability", s["disruption"])
                self.check_indicators(s["indicators_stub"])
                for route in s["routes"]:
                    self.assertEqual("LineString", route["geometry"]["type"])
                    self.assertGreaterEqual(len(route["geometry"]["coordinates"]), 2)
                    self.assertGreater(route["distance_m"], 0)

    def test_every_analysis_matches_contract(self):
        for item in available_scenarios():
            with self.subTest(variant=item["id"]):
                result = analyze(load_scenario(variant=item["id"]), "blocked")
                self.check_indicators(result["indicators"])
                self.assertIn("of", next(i for i in result["indicators"] if i["id"] == "appointments_feasible"))
                assignments = result["assignments"]
                self.assertLessEqual({"shipments", "trucks", "count", "before", "after"}, set(assignments))
                self.assertEqual(assignments["count"], len(assignments["before"]))
                self.assertEqual(assignments["count"], len(assignments["after"]))
                for option in assignments["before"] + assignments["after"]:
                    self.assertLessEqual({"id", "trucks", "planned", "action", "rank", "expected_loss", "outcomes"},
                                         set(option))
                    for state in ("open", "blocked"):
                        for row in option["outcomes"][state]["detail"]["shipments"]:
                            self.assertLessEqual(SHIPMENT_ROW, set(row))
                            self.assertIn(row["basis"], BASES)
                            self.assertRegex(row["arrival"], r"^\d\d:\d\d$")


class InspectionLibraryContractTests(unittest.TestCase):
    CASE = {"id", "title", "visual_label", "review_note", "image_quality", "annotated_image", "source_image",
            "annotated_video", "vehicle_detections", "excluded_detections", "allowed_observations", "cv_assessment",
            "source"}
    SOURCE = {"source_url", "sha256", "credit", "license", "location", "historical"}
    ASSESSMENT = {"suggested_observation", "motion_state", "reason", "vehicles_on_road", "stopped", "moving", "basis"}
    SIGNALS = {"blocked", "clear", "inconclusive"}

    def test_committed_library_matches_contract(self):
        library = json.loads(LIBRARY.read_text(encoding="utf-8"))
        self.assertTrue(library["available"])
        self.assertTrue(library["cases"])
        for case in library["cases"]:
            with self.subTest(case=case["id"]):
                self.assertLessEqual(self.CASE, set(case))
                self.assertLessEqual(self.SOURCE, set(case["source"]))
                self.assertLessEqual(self.ASSESSMENT, set(case["cv_assessment"]))
                self.assertIn(case["cv_assessment"]["suggested_observation"], self.SIGNALS)
                self.assertTrue(case["allowed_observations"])
                self.assertLessEqual(set(case["allowed_observations"]), self.SIGNALS)
                for key in ("source_image", "annotated_image", "annotated_video"):
                    self.assertTrue((LIBRARY.parent / case[key].removeprefix("/inspection-assets/")).is_file())


if __name__ == "__main__":
    unittest.main()
