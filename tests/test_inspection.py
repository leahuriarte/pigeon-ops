import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.inspection import list_inspections, review_inspection
from tests.test_server import start_server


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
             "cv_assessment": {"suggested_observation": "blocked"},
             "source": {"source_url": "https://example.com/road", "sha256": "abc"}},
            {"id": "unavailable", "allowed_observations": ["inconclusive"],
             "source": {"source_url": "https://example.com/unavailable", "sha256": "def"}},
            {"id": "flood-only", "allowed_observations": ["blocked", "clear", "inconclusive"], "check_types": ["environment"],
             "source": {"source_url": "https://example.com/flood", "sha256": "ghi"}},
        ]}), encoding="utf-8")
        self.payload = {"case_id": "road", "observation": "clear", "scenario": "disruption", "simulated_application": True}

    def test_review_updates_the_analysis_and_is_recorded(self):
        result = review_inspection(self.payload, self.library, self.audit)
        self.assertEqual("clear", result["analysis"]["observation"])
        self.assertLess(result["analysis"]["posterior_blocked_probability"], result["analysis"]["prior_blocked_probability"])
        review_inspection({**self.payload, "observation": "blocked"}, self.library, self.audit)
        records = [json.loads(line) for line in self.audit.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([("blocked", False), ("blocked", True)], [(r["cv_suggestion"], r["agrees_with_cv"]) for r in records])
        self.assertEqual("abc", records[0]["source_sha256"])
        self.assertTrue(records[0]["simulated_application"])

    def test_invalid_reviews_are_rejected_and_not_recorded(self):
        for changes in ({"case_id": "unavailable"}, {"case_id": "missing"}, {"simulated_application": False},
                        {"case_id": "flood-only", "scenario": "disruption"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                review_inspection({**self.payload, **changes}, self.library, self.audit)
        self.assertFalse(self.audit.exists())

    def test_clip_must_fit_the_incident_check(self):
        result = review_inspection({**self.payload, "case_id": "flood-only", "scenario": "flood"}, self.library, self.audit)
        self.assertEqual("flood-only", result["review"]["case_id"])
        with self.assertRaisesRegex(ValueError, "congestion"):
            review_inspection({**self.payload, "case_id": "flood-only", "scenario": "congestion"}, self.library, self.audit)

    def test_base_app_works_without_optional_cv_artifacts(self):
        self.assertEqual([], list_inspections(Path(self.temp.name) / "missing.json")["cases"])

    def test_http_review_and_asset_boundary(self):
        (self.assets / "photo.jpg").write_bytes(b"test-photo")
        (self.assets.parent / "private.txt").write_text("private", encoding="utf-8")
        with patch("backend.server.ASSETS", self.assets), \
             patch("backend.server.list_inspections", lambda: list_inspections(self.library)), \
             patch("backend.server.review_inspection", lambda payload: review_inspection(payload, self.library, self.audit)):
            server, base = start_server()
            try:
                with urlopen(base + "/api/inspection-cases") as response:
                    self.assertEqual(3, len(json.load(response)["cases"]))
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
                mismatch = Request(base + "/api/inspection-review", headers={"Content-Type": "application/json"},
                                   data=json.dumps({**self.payload, "case_id": "flood-only"}).encode())
                with self.assertRaises(HTTPError) as caught:
                    urlopen(mismatch)
                self.assertEqual(400, caught.exception.code)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
