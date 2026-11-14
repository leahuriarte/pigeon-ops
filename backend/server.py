#!/usr/bin/env python3
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from backend.decision_engine import analyze
from backend.inspection import ASSETS, list_inspections, review_inspection
from backend.playback import build_playback
from backend.scenario import available_scenarios, load_scenario

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


class DemoHandler(BaseHTTPRequestHandler):
    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return self._json({"status": "ok"})
        if parsed.path == "/api/inspection-cases":
            return self._json(list_inspections())
        if parsed.path.startswith("/inspection-assets/"):
            return self._file(ASSETS, unquote(parsed.path.removeprefix("/inspection-assets/")))
        if parsed.path == "/api/scenarios":
            return self._json({"scenarios": available_scenarios()})
        if parsed.path == "/api/scenario":
            variant = parse_qs(parsed.query).get("scenario", ["disruption"])[0]
            try:
                scenario = load_scenario(variant=variant)
            except ValueError as error:
                return self._json({"error": str(error)}, 404)
            scenario["analysis"] = analyze(scenario)
            return self._json(scenario)
        if parsed.path == "/api/analysis":
            params = parse_qs(parsed.query)
            observation = params.get("observation", [None])[0]
            if observation not in (None, "blocked", "clear", "inconclusive"):
                return self._json({"error": "observation must be blocked, clear, or inconclusive"}, 400)
            variant = params.get("scenario", ["disruption"])[0]
            try:
                scenario = load_scenario(variant=variant)
            except ValueError as error:
                return self._json({"error": str(error)}, 404)
            return self._json(analyze(scenario, observation))
        if parsed.path == "/api/playback":
            params = parse_qs(parsed.query)
            variant = params.get("scenario", ["disruption"])[0]
            try:
                scenario = load_scenario(variant=variant)
                playback = build_playback(
                    scenario,
                    action=params.get("action", ["hold-course"])[0],
                    road=params.get("road", ["open"])[0],
                )
            except ValueError as error:
                return self._json({"error": str(error)}, 400)
            return self._json(playback)
        return self._static(parsed.path)

    def do_POST(self):
        if urlparse(self.path).path != "/api/inspection-review":
            return self._json({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                raise ValueError("Review body must be between 1 and 16384 bytes")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            return self._json(review_inspection(payload))
        except (ValueError, UnicodeDecodeError) as error:
            return self._json({"error": str(error)}, 400)

    def _static(self, request_path):
        relative = "index.html" if request_path == "/" else request_path.lstrip("/")
        return self._file(FRONTEND, relative)

    def _file(self, root, relative):
        candidate = (root / relative).resolve()
        if root.resolve() not in candidate.parents or not candidate.is_file():
            return self._json({"error": "not found"}, 404)
        body = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[demo] {self.address_string()} {fmt % args}")


def run(host="127.0.0.1", port=8000):
    print(f"PigeonOps demo running at http://{host}:{port}")
    ThreadingHTTPServer((host, port), DemoHandler).serve_forever()


if __name__ == "__main__":
    run()
