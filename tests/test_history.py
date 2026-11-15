"""Decision history: similarity, summary arithmetic, records built from the engine, and the saved log.
Every test that touches the saved log points it at a temporary file."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import copilot, history, triage
from backend.decision_engine import analyze
from backend.scenario import load_scenario

FIGURES = ("lateness_min", "cost_usd", "co2_kg", "added_miles", "missed_appointments")


def case(case_id, check_type, probability, action, road, day, late=10.0, cost=20.0, missed=0):
    figures = {"lateness_min": late, "cost_usd": cost, "co2_kg": 15.0, "added_miles": 2.0,
               "missed_appointments": missed}
    return {"id": case_id, "date": day, "reported_at": "09:00", "check_type": check_type, "variant": "disruption",
            "estimated_bad_probability": probability, "web_finding": None,
            "inspection": {"flown": False, "observation": None, "wait_min": 0, "changed_response": False},
            "action": action, "action_label": action, "actual_road_state": road,
            "estimate": dict(figures), "actual": dict(figures), "pattern_ids": ["p1"], "basis": "synthetic"}


class HistoryTestCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.log = self.folder / "outputs" / "decision-log.jsonl"
        self.file = self.folder / "history.json"
        self.write_history([
            case("SYN-HIS-001", "crash", 0.45, "reroute", "blocked", "2026-09-01", late=14, cost=30),
            case("SYN-HIS-002", "crash", 0.35, "hold-course", "open", "2026-10-01", late=0, cost=0),
            case("SYN-HIS-003", "crash", 0.9, "hold-course", "blocked", "2026-08-01", late=48, cost=60, missed=1),
            case("SYN-HIS-004", "congestion", 0.4, "hold-course", "blocked", "2026-08-02"),
        ])

    def write_history(self, cases):
        self.file.write_text(json.dumps({"basis": "synthetic", "note": "Synthetic.", "cases": cases,
                                         "patterns": [{"id": "p1", "claim": "A claim."},
                                                      {"id": "p2", "claim": "Unused."}]}), encoding="utf-8")


class SimilarityTests(HistoryTestCase):
    def test_same_check_type_closest_chance_then_newest(self):
        cases = history.similar(load_scenario(variant="disruption"), self.file, self.log)
        # 0.45 and 0.35 are both 0.05 from 0.4, so the newer one comes first.
        self.assertEqual(["SYN-HIS-002", "SYN-HIS-001", "SYN-HIS-003"], [item["id"] for item in cases])
        self.assertEqual({"synthetic"}, {item["source"] for item in cases})

    def test_saved_records_join_the_committed_cases(self):
        history.save({**case(None, "crash", 0.4, "reroute", "open", "2026-11-14"), "run_id": "r1"}, self.log)
        cases = history.similar(load_scenario(variant="disruption"), self.file, self.log)
        self.assertEqual(("SYN-LOG-1", "saved"), (cases[0]["id"], cases[0]["source"]))

    def test_missing_or_malformed_file_is_unavailable(self):
        scenario = load_scenario(variant="disruption")
        missing = history.report(scenario, "disruption", self.folder / "nope.json", self.log)
        self.assertEqual({"available": False, "reason": "no decision history file"}, missing)
        self.file.write_text("{not json", encoding="utf-8")
        self.assertFalse(history.report(scenario, "disruption", self.file, self.log)["available"])
        self.write_history([{"id": "SYN-HIS-001"}])
        self.assertFalse(history.report(scenario, "disruption", self.file, self.log)["available"])
        self.assertFalse(history.report(load_scenario(variant="normal"), "normal", self.file, self.log)["available"])


class SummaryTests(HistoryTestCase):
    def test_counts_means_and_rank_order(self):
        found = history.report(load_scenario(variant="disruption"), "disruption", self.file, self.log)
        summary = found["summary"]
        self.assertEqual((3, 2, 0.4), (summary["count"], summary["bad_count"], summary["current_estimate"]))
        self.assertEqual(["reroute", "hold-course", "swap-truck"], [item["action"] for item in summary["by_action"]])
        reroute, hold, swap = summary["by_action"]
        self.assertTrue(reroute["recommended"])
        self.assertFalse(hold["recommended"])
        self.assertEqual((2, 1, 1), (hold["count"], hold["bad_count"], hold["missed_appointments"]))
        self.assertEqual({"lateness_min": 24.0, "cost_usd": 30.0, "co2_kg": 15.0, "added_miles": 2.0}, hold["actual"])
        self.assertEqual((0, None, None), (swap["count"], swap["estimate"], swap["actual"]))
        self.assertEqual("Road was blocked in 2 of 3 similar reports. Rerouting (1 case) averaged 14 min late and "
                         "15 kg CO2; keeping the route (2 cases) averaged 24 min late and 15 kg CO2, with "
                         "1 missed appointment.", summary["line"])
        self.assertEqual({"flown": 0, "changed_response": 0}, summary["inspection"])
        self.assertEqual(["p1"], [pattern["id"] for pattern in found["patterns"]])

    def test_web_finding_moves_the_current_estimate(self):
        scenario = triage.apply_web_evidence(load_scenario(variant="disruption"), "corroborates")
        summary = history.report(scenario, "disruption", self.file, self.log)["summary"]
        self.assertAlmostEqual(0.8333, summary["current_estimate"], places=4)
        self.assertEqual("SYN-HIS-003", history.similar(scenario, self.file, self.log)[0]["id"])

    def test_no_cases(self):
        self.write_history([])
        summary = history.report(load_scenario(variant="flood"), "flood", self.file, self.log)["summary"]
        self.assertEqual("No similar past reports.", summary["line"])


class RecordTests(HistoryTestCase):
    def test_record_after_a_drone_check_matches_the_engine(self):
        scenario = load_scenario(variant="disruption")
        analysis = analyze(scenario, "clear")
        record = history.decision_record(scenario, analysis, "hold-course", "open", "clear", None, None, "r1")
        p = analysis["posterior_blocked_probability"]
        option = next(item for item in analysis["after"] if item["id"] == "hold-course")
        open_, blocked = option["outcomes"]["open"], option["outcomes"]["blocked"]
        self.assertAlmostEqual(p, record["estimated_bad_probability"], places=4)
        self.assertAlmostEqual(p * blocked["lateness_min"] + (1 - p) * open_["lateness_min"],
                               record["estimate"]["lateness_min"], places=1)
        self.assertAlmostEqual(open_["incremental_cost"] + 90, record["actual"]["cost_usd"], places=2)
        self.assertEqual(0, record["actual"]["missed_appointments"])
        self.assertEqual({"flown": True, "observation": "clear", "wait_min": 6, "changed_response": True},
                         record["inspection"])
        self.assertEqual(("hold-course", "disruption", "crash", "2026-11-14", "simulated"),
                         (record["recommended_action"], record["variant"], record["check_type"], record["date"],
                          record["basis"]))
        self.assertEqual(set(FIGURES), set(record["estimate"]))

    def test_record_without_a_drone_uses_the_web_updated_prior(self):
        scenario = triage.apply_web_evidence(load_scenario(variant="flood"), "corroborates")
        analysis = analyze(scenario)
        record = history.decision_record(scenario, analysis, "hold-course", "blocked", None, "corroborates",
                                         "Driver said water was shallow.", "r1")
        self.assertAlmostEqual(analysis["prior_blocked_probability"], record["estimated_bad_probability"], places=4)
        self.assertEqual(("reroute", "Driver said water was shallow.", 1),
                         (record["recommended_action"], record["override_reason"], record["actual"]["missed_appointments"]))
        self.assertEqual({"flown": False, "observation": None, "wait_min": 0, "changed_response": False},
                         record["inspection"])
        self.assertIn("impassable", record["lesson"])

    def test_bad_input_is_rejected(self):
        scenario = load_scenario(variant="disruption")
        analysis = analyze(scenario)
        for args in (("fly", "open"), ("reroute", "maybe")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                history.decision_record(scenario, analysis, *args, None, None, None, "r1")
        with self.assertRaises(ValueError):  # The analysis must match the stated web finding.
            history.decision_record(scenario, analysis, "reroute", "open", None, "corroborates", None, "r1")


class SaveTests(HistoryTestCase):
    def record(self, variant, run_id="run-1", action="reroute"):
        scenario = load_scenario(variant=variant)
        return history.decision_record(scenario, analyze(scenario), action, "open", None, None, None, run_id,
                                       variant=variant)

    def test_save_appends_and_replaces_by_run_and_variant(self):
        first = history.save(self.record("disruption"), self.log)
        second = history.save(self.record("flood"), self.log)
        self.assertEqual(("SYN-LOG-1", "SYN-LOG-2"), (first["id"], second["id"]))
        again = history.save(self.record("disruption", action="hold-course"), self.log)
        self.assertEqual("SYN-LOG-1", again["id"])
        records = history.read_log(self.log)
        self.assertEqual(["SYN-LOG-2", "SYN-LOG-1"], [item["id"] for item in records])
        self.assertEqual("hold-course", records[-1]["action"])
        self.assertEqual("SYN-LOG-3", history.save(self.record("disruption", run_id="run-2"), self.log)["id"])

    def test_unreadable_log_lines_are_skipped(self):
        history.save(self.record("disruption"), self.log)
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write("not json\n{\"id\": \"half\"}\n")
        self.assertEqual(["SYN-LOG-1"], [item["id"] for item in history.read_log(self.log)])
        self.assertEqual([], history.read_log(self.folder / "missing.jsonl"))


class FactsTests(HistoryTestCase):
    def test_facts_block_is_compact_and_never_raises(self):
        with patch.object(history, "HISTORY", self.file), patch.object(history, "LOG", self.log):
            block = history.facts_block(load_scenario(variant="disruption"))
        self.assertEqual(("synthetic", 3, 2), (block["basis"], block["count"], block["bad_count"]))
        self.assertEqual({"action", "label", "count", "bad_count", "missed_appointments", "estimate", "actual"},
                         set(block["by_action"][0]))
        with patch.object(history, "report", side_effect=ValueError("boom")):
            self.assertFalse(history.facts_block(load_scenario(variant="disruption"))["available"])

    def test_copilot_sees_the_history(self):
        scenario = load_scenario(variant="disruption")
        analysis = analyze(scenario)
        with patch.object(history, "HISTORY", self.file), patch.object(history, "LOG", self.log):
            facts = copilot.decision_facts(scenario, analysis)
            past = copilot.run_engine_tool("past_decisions", {}, scenario, analysis)
        self.assertEqual(3, facts["similar_past_decisions"]["count"])
        self.assertEqual("synthetic", past["basis"])
        self.assertEqual(["SYN-HIS-002", "SYN-HIS-001", "SYN-HIS-003"], [item["id"] for item in past["cases"]])
        self.assertFalse(copilot.decision_facts(load_scenario(variant="normal"),
                                                analyze(load_scenario(variant="normal")))["similar_past_decisions"]["available"])


if __name__ == "__main__":
    unittest.main()
