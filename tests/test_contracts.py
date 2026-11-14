"""Checks that data files match the contracts in AGENTS.md."""
import json
import unittest
from pathlib import Path

from backend.scenario import available_scenarios, load_scenario

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "frontend" / "evidence"
INDICATOR_IDS = {"shipments_at_risk", "appointments_feasible", "expected_delay_min", "added_miles", "co2_kg", "community_exposure"}
BASES = {"stub", "estimated", "simulated", "measured"}


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
                for clip_id in s["disruption"].get("evidence_clips", []):
                    self.assertTrue((EVIDENCE_DIR / f"{clip_id}.json").is_file(), clip_id)

    def test_disruption_lists_evidence(self):
        self.assertTrue(load_scenario(variant="disruption")["disruption"]["evidence_clips"])


class EvidenceContractTests(unittest.TestCase):
    REQUIRED = {"clip_id", "basis", "label", "confidence", "vehicle_count", "stationary_count", "flow_per_min",
                "frames_analyzed", "model", "video", "location", "location_simulated", "source", "license"}

    def test_every_evidence_file_matches_contract(self):
        files = sorted(EVIDENCE_DIR.glob("*.json"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(file=path.name):
                evidence = json.loads(path.read_text(encoding="utf-8"))
                self.assertLessEqual(self.REQUIRED, set(evidence))
                self.assertEqual(path.stem, evidence["clip_id"])
                self.assertIn(evidence["label"], ("blocked", "clear", "inconclusive"))
                self.assertTrue(0 <= evidence["confidence"] <= 1)
                self.assertIn(evidence["basis"], BASES)
                if evidence["video"] is not None:
                    self.assertTrue((ROOT / "frontend" / evidence["video"]).is_file(), evidence["video"])


if __name__ == "__main__":
    unittest.main()
