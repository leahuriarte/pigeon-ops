"""Checks that data files match the contracts in AGENTS.md."""
import json
import unittest
from pathlib import Path

from backend.decision_engine import analyze
from backend.scenario import available_scenarios, load_scenario

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "cv" / "review_library" / "library.json"
HISTORY = ROOT / "config" / "decision_history.json"
INDICATOR_IDS = {"shipments_at_risk", "appointments_feasible", "expected_delay_min", "added_miles", "co2_kg", "community_exposure"}
BASES = {"stub", "estimated", "simulated", "measured"}
CHECK_TYPES = {"crash", "congestion", "environment"}
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
                if s["scenario_type"] != "normal":
                    check = s["disruption"]["check"]
                    self.assertIn(check["type"], CHECK_TYPES)
                    self.assertLessEqual({"label", "bad", "good"}, set(check))
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
                self.assertTrue(all(i["value"] is not None and i["basis"] == "estimated" for i in result["indicators"]))
                for option in result["before"] + result["after"]:
                    self.assertIsInstance(option["schools_within_300m"], int)
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


class WebTriageContractTests(unittest.TestCase):
    def test_likelihoods_match_contract(self):
        model = load_scenario(variant="disruption")["decision_model"]["web_triage"]
        self.assertEqual("assumed", model["basis"])
        for state in ("blocked", "open"):
            row = model["likelihoods"][state]
            self.assertEqual({"corroborates", "contradicts", "nothing_found"}, set(row))
            self.assertAlmostEqual(1.0, sum(row.values()))
        self.assertIn("web_evidence", analyze(load_scenario(variant="disruption")))


class DecisionHistoryContractTests(unittest.TestCase):
    CASE = {"id", "date", "reported_at", "check_type", "variant", "title", "place", "lat", "lon", "reports",
            "estimated_bad_probability", "web_finding", "inspection", "action", "action_label", "recommended_action",
            "override_reason", "actual_road_state", "estimate", "actual", "actual_note", "schools_within_300m",
            "lesson", "pattern_ids", "basis"}
    FIGURES = {"lateness_min", "cost_usd", "co2_kg", "added_miles", "missed_appointments"}
    PATTERNS = {"crash-clearance-time", "public-feed-lag", "driver-report-reliability", "gate-queue-variability",
                "water-main-break", "flood-depth-unknown", "neighborhood-truck-detour", "diesel-idle-vs-miles",
                "drone-verification-limits"}

    def test_committed_history_matches_contract(self):
        data = json.loads(HISTORY.read_text(encoding="utf-8"))
        self.assertEqual("synthetic", data["basis"])
        self.assertIn("not real dispatch records", data["note"])
        self.assertEqual("scripts/build_decision_history.py", data["generated_by"])
        self.assertEqual(self.PATTERNS, {pattern["id"] for pattern in data["patterns"]})
        for pattern in data["patterns"]:
            self.assertLessEqual({"id", "claim", "source", "source_url"}, set(pattern))
        self.assertGreaterEqual(len(data["cases"]), 12)
        rates = {}
        for case in data["cases"]:
            with self.subTest(case=case["id"]):
                self.assertLessEqual(self.CASE, set(case))
                self.assertRegex(case["id"], r"^SYN-HIS-\d{3}$")
                self.assertEqual("synthetic", case["basis"])
                scenario = load_scenario(variant=case["variant"])
                self.assertEqual(scenario["disruption"]["check"]["type"], case["check_type"])
                self.assertIn(case["web_finding"], (None, "corroborates", "contradicts", "nothing_found"))
                self.assertLessEqual({"flown", "observation", "wait_min", "changed_response"}, set(case["inspection"]))
                self.assertIn(case["inspection"]["observation"], (None, "blocked", "clear", "inconclusive"))
                self.assertEqual(case["inspection"]["flown"], case["inspection"]["observation"] is not None)
                actions = {action["id"] for action in scenario["decision_model"]["actions"]}
                self.assertLessEqual({case["action"], case["recommended_action"]}, actions)
                self.assertEqual(case["override_reason"] is None, case["action"] == case["recommended_action"])
                self.assertIn(case["actual_road_state"], ("open", "blocked"))
                self.assertTrue(0 <= case["estimated_bad_probability"] <= 1)
                self.assertEqual(self.FIGURES, set(case["estimate"]))
                self.assertEqual(self.FIGURES, set(case["actual"]))
                self.assertTrue(1 <= len(case["pattern_ids"]) <= 3)
                self.assertLessEqual(set(case["pattern_ids"]), self.PATTERNS)
                self.assertTrue(case["lesson"].endswith("."))
                prior = scenario["disruption"]["prior_blocked_probability"]
                rates.setdefault(case["variant"], (prior, []))[1].append(case["actual_road_state"] == "blocked")
        for variant, (prior, states) in rates.items():
            with self.subTest(variant=variant):
                self.assertLessEqual(abs(sum(states) / len(states) - prior), 0.2)


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
                self.assertTrue(case["check_types"])
                self.assertLessEqual(set(case["check_types"]), CHECK_TYPES)
                for key in ("source_image", "annotated_image", "annotated_video"):
                    self.assertTrue((LIBRARY.parent / case[key].removeprefix("/inspection-assets/")).is_file())


if __name__ == "__main__":
    unittest.main()
