import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.inspection import list_inspections, review_inspection
from backend.server import DemoHandler


class InspectionReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.assets = root / "assets"
        self.assets.mkdir()
        self.library = self.assets / "library.json"
        self.audit = root / "reviews.jsonl"
        self.library.write_text(json.dumps({"available": True, "cases": [
            {"id": "road", "allowed_observations": ["blocked", "clear", "inconclusive"],
             "source": {"source_url": "https://example.com/road", "sha256": "abc"}},
            {"id": "unavailable", "allowed_observations": ["inconclusive"],
             "source": {"source_url": "https://example.com/unavailable", "sha256": "def"}},
        ]}), encoding="utf-8")
        self.payload = {"case_id": "road", "observation": "clear", "scenario": "disruption", "simulated_application": True}

    def test_review_updates_the_demo_and_records_its_source(self):
        result = review_inspection(self.payload, self.library, self.audit)
        self.assertEqual("clear", result["analysis"]["observation"])
        self.assertLess(result["analysis"]["posterior_blocked_probability"], result["analysis"]["prior_blocked_probability"])
        record = json.loads(self.audit.read_text(encoding="utf-8"))
        self.assertEqual("abc", record["source_sha256"])
        self.assertTrue(record["simulated_application"])

    def test_unavailable_camera_cannot_be_submitted_as_clear(self):
        self.payload["case_id"] = "unavailable"
        with self.assertRaisesRegex(ValueError, "inconclusive"):
            review_inspection(self.payload, self.library, self.audit)
        self.assertFalse(self.audit.exists())

    def test_review_requires_explicit_synthetic_application(self):
        self.payload.pop("simulated_application")
        with self.assertRaisesRegex(ValueError, "synthetic"):
            review_inspection(self.payload, self.library, self.audit)
        self.assertFalse(self.audit.exists())

    def test_unknown_image_and_invalid_notes_do_not_create_audit_records(self):
        for changes in [{"case_id": "missing"}, {"note": "x" * 501}, {"note": 4}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                review_inspection({**self.payload, **changes}, self.library, self.audit)
        self.assertFalse(self.audit.exists())

    def test_base_app_works_without_optional_cv_artifacts(self):
        self.assertEqual([], list_inspections(Path(self.temp.name) / "missing.json")["cases"])

    def test_http_review_and_asset_boundary(self):
        (self.assets / "photo.jpg").write_bytes(b"test-photo")
        (self.assets.parent / "private.txt").write_text("private", encoding="utf-8")
        with patch("backend.server.ASSETS", self.assets), \
             patch("backend.server.list_inspections", lambda: list_inspections(self.library)), \
             patch("backend.server.review_inspection", lambda payload: review_inspection(payload, self.library, self.audit)):
            server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base + "/api/inspection-cases") as response:
                    self.assertEqual(2, len(json.load(response)["cases"]))
                with urlopen(base + "/inspection-assets/photo.jpg") as response:
                    self.assertEqual(b"test-photo", response.read())
                with self.assertRaises(HTTPError) as caught:
                    urlopen(base + "/inspection-assets/%2e%2e/private.txt")
                self.assertEqual(404, caught.exception.code)
                request = Request(base + "/api/inspection-review", data=json.dumps(self.payload).encode(), headers={"Content-Type": "application/json"})
                with urlopen(request) as response:
                    self.assertEqual("road", json.load(response)["review"]["case_id"])
                malformed = Request(base + "/api/inspection-review", data=b"{", headers={"Content-Type": "application/json"})
                with self.assertRaises(HTTPError) as caught:
                    urlopen(malformed)
                self.assertEqual(400, caught.exception.code)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
