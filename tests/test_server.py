import json
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

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


if __name__ == "__main__":
    unittest.main()
