import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend import history
from backend.server import DemoHandler


class QuietHandler(DemoHandler):
    def log_message(self, fmt, *args):
        pass


def start_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.base = start_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get(self, path):
        with urlopen(self.base + path) as response:
            return json.loads(response.read())

    def status_of(self, path):
        try:
            with urlopen(self.base + path) as response:
                return response.status
        except HTTPError as error:
            return error.code

    def test_endpoints_carry_contract_fields(self):
        scenarios = self.get("/api/scenarios")["scenarios"]
        self.assertLessEqual({"normal", "disruption"}, {s["id"] for s in scenarios})
        default = self.get("/api/scenario")
        self.assertEqual("disruption", default["scenario_type"])
        self.assertTrue(default["analysis"]["indicators"])
        body = self.get("/api/analysis?scenario=normal&observation=blocked")
        for key in ("scenario_id", "prior_blocked_probability", "posterior_blocked_probability", "observation",
                    "before", "after", "evoi", "sensitivity", "indicators", "assignments"):
            self.assertIn(key, body)
        self.assertEqual(len(body["before"]), len(body["after"]))

    def test_bad_input_is_rejected(self):
        self.assertEqual(404, self.status_of("/api/analysis?scenario=nope"))
        self.assertEqual(400, self.status_of("/api/analysis?observation=maybe"))

    def test_unexpected_error_is_a_json_500(self):
        with patch("backend.server.analyze", side_effect=RuntimeError("boom")), patch("traceback.print_exc"):
            self.assertEqual(500, self.status_of("/api/analysis"))


class HistoryRouteTests(unittest.TestCase):
    """GET and POST /api/history, with the saved log in a temporary folder."""

    @classmethod
    def setUpClass(cls):
        cls.server, cls.base = start_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.log = Path(folder.name) / "decision-log.jsonl"
        patcher = patch.object(history, "LOG", self.log)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_get_history(self):
        status, body = self.call("/api/history?scenario=disruption")
        self.assertEqual(200, status)
        self.assertTrue(body["available"])
        self.assertEqual(("disruption", "crash", "synthetic"), (body["scenario_id"], body["check"]["type"], body["basis"]))
        summary = body["summary"]
        self.assertEqual({"count", "bad_count", "current_estimate", "line", "by_action", "inspection"}, set(summary))
        self.assertEqual(len(body["cases"]), summary["count"])
        self.assertEqual({"synthetic"}, {case["source"] for case in body["cases"]})
        self.assertEqual(["reroute", "hold-course", "swap-truck"], [item["action"] for item in summary["by_action"]])
        self.assertEqual(0.4, summary["current_estimate"])
        status, web = self.call("/api/history?scenario=disruption&web=corroborates")
        self.assertAlmostEqual(0.8333, web["summary"]["current_estimate"], places=4)
        self.assertEqual(0.4, self.call("/api/analysis?scenario=disruption")[1]["prior_blocked_probability"])

    def test_get_history_errors(self):
        status, body = self.call("/api/history?scenario=normal")
        self.assertEqual((200, False), (status, body["available"]))
        self.assertEqual(404, self.call("/api/history?scenario=nope")[0])
        self.assertEqual(400, self.call("/api/history?scenario=disruption&web=maybe")[0])
        with patch.object(history, "HISTORY", self.log.with_name("missing.json")):
            status, body = self.call("/api/history?scenario=flood")
        self.assertEqual((200, False), (status, body["available"]))

    def test_post_saves_and_replaces(self):
        decision = {"run_id": "run-1", "scenario": "disruption", "action": "hold-course", "road": "blocked",
                    "observation": None, "web": "nothing_found", "override_reason": "No CHP entry."}
        status, body = self.call("/api/history", decision)
        self.assertEqual(200, status)
        record = body["record"]
        self.assertEqual(("SYN-LOG-1", "simulated", "blocked", 1),
                         (record["id"], record["basis"], record["actual_road_state"], record["actual"]["missed_appointments"]))
        self.assertAlmostEqual(0.2448, record["estimated_bad_probability"], places=4)
        _, again = self.call("/api/history", {**decision, "action": "reroute", "override_reason": None})
        self.assertEqual("SYN-LOG-1", again["record"]["id"])
        self.assertEqual(1, len(history.read_log(self.log)))
        _, listed = self.call("/api/history?scenario=disruption")
        saved = [case for case in listed["cases"] if case["source"] == "saved"]
        self.assertEqual([("SYN-LOG-1", "reroute")], [(case["id"], case["action"]) for case in saved])

    def test_post_rejects_bad_input(self):
        good = {"run_id": "run-1", "scenario": "flood", "action": "reroute", "road": "open",
                "observation": None, "web": None, "override_reason": None}
        for change in ({"run_id": ""}, {"scenario": "nope"}, {"scenario": "normal"}, {"action": "fly"},
                       {"road": "maybe"}, {"observation": "foggy"}, {"web": "maybe"}, {"override_reason": 7}):
            with self.subTest(change=change):
                self.assertEqual(400, self.call("/api/history", {**good, **change})[0])
        self.assertEqual(400, self.call("/api/history", ["not", "an", "object"])[0])
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
