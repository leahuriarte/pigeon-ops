import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

from backend.server import DemoHandler


class _QuietHandler(DemoHandler):
    def log_message(self, fmt, *args):
        pass


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

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

    def test_lists_scenarios(self):
        body = self.get("/api/scenarios")
        self.assertEqual(["normal", "disruption"], [s["id"] for s in body["scenarios"]])

    def test_default_scenario_is_disruption(self):
        body = self.get("/api/scenario")
        self.assertEqual("disruption", body["scenario_type"])
        self.assertTrue(body["analysis"]["indicators"])

    def test_analysis_carries_contract_fields(self):
        body = self.get("/api/analysis?scenario=normal&observation=blocked")
        for key in ("scenario_id", "before", "after", "evoi", "sensitivity", "indicators"):
            self.assertIn(key, body)
        self.assertEqual(3, len(body["after"]))

    def test_bad_input_is_rejected(self):
        self.assertEqual(404, self.status_of("/api/analysis?scenario=nope"))
        self.assertEqual(400, self.status_of("/api/analysis?observation=maybe"))

    def test_serves_evidence_stub(self):
        self.assertEqual("stub", self.get("/evidence/stub.json")["clip_id"])


if __name__ == "__main__":
    unittest.main()
