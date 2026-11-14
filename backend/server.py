#!/usr/bin/env python3
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from backend import copilot, llm, triage
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
        params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        # LLM routes: an unavailable LLM is a 200 with available: false, never an error status.
        if parsed.path == "/api/llm/status":
            return self._json(llm.status())
        if parsed.path == "/api/llm/footage":
            case = next((item for item in list_inspections()["cases"] if item["id"] == params.get("case_id")), None)
            if case is None:
                return self._json({"error": "unknown inspection clip"}, 404)
            try:  # The incident names the kind of check; without a valid one the read is generic.
                check = load_scenario(variant=params["scenario"])["disruption"].get("check")
            except (KeyError, ValueError):
                check = None
            return self._json(copilot.footage_opinion(case, check=check))
        if parsed.path not in ("/api/scenario", "/api/analysis", "/api/playback", "/api/llm/brief", "/api/triage"):
            return self._static(parsed.path)

        variant = params.get("scenario", "disruption")
        try:
            triage.validate_finding(params.get("web"))
            if params.get("mode", "live") not in triage.MODES:
                raise ValueError("mode must be live or demo")
        except ValueError as error:
            return self._json({"error": str(error)}, 400)
        try:
            scenario = load_scenario(variant=variant)
        except ValueError as error:
            return self._json({"error": str(error)}, 404)
        if parsed.path == "/api/triage":
            return self._json(triage.triage(scenario, variant, params.get("mode", "live")))
        try:  # The dispatcher applied the web triage finding: start from the updated blockage chance.
            scenario = triage.apply_web_evidence(scenario, params.get("web"))
        except ValueError as error:
            return self._json({"error": str(error)}, 400)
        if parsed.path == "/api/scenario":
            return self._json({**scenario, "analysis": analyze(scenario)})
        if parsed.path in ("/api/analysis", "/api/llm/brief"):
            observation = params.get("observation")
            if observation not in (None, "blocked", "clear", "inconclusive"):
                return self._json({"error": "observation must be blocked, clear, or inconclusive"}, 400)
            if parsed.path == "/api/llm/brief":
                return self._json(copilot.decision_brief(scenario, analyze(scenario, observation)))
            return self._json(analyze(scenario, observation))
        try:
            return self._json(build_playback(
                scenario, action=params.get("action", "hold-course"), road=params.get("road", "open"),
                inspected=params.get("inspected") == "1"))
        except ValueError as error:
            return self._json({"error": str(error)}, 400)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/inspection-review", "/api/llm/ask"):
            return self._json({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                raise ValueError("Request body must be between 1 and 16384 bytes")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if path == "/api/llm/ask":
                return self._json(self._ask(payload))
            return self._json(review_inspection(payload))
        except (ValueError, UnicodeDecodeError) as error:
            return self._json({"error": str(error)}, 400)

    @staticmethod
    def _ask(payload):
        if not isinstance(payload, dict):
            raise ValueError("Request must be a JSON object")
        variant = payload.get("scenario", "disruption")
        observation = payload.get("observation")
        if not isinstance(variant, str):
            raise ValueError("Scenario must be a name")
        if observation not in (None, "blocked", "clear", "inconclusive"):
            raise ValueError("observation must be blocked, clear, or inconclusive")
        question = copilot.validate_question(payload.get("question"))
        web = triage.validate_finding(payload.get("web"))
        scenario = triage.apply_web_evidence(load_scenario(variant=variant), web)  # ValueError for an unknown variant
        return copilot.ask(scenario, analyze(scenario, observation), question)

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


class DemoServer(ThreadingHTTPServer):
    # Windows lets a second server bind a port that is already in use when address reuse is on. Requests
    # then reach either process, so a server left running from earlier can answer with old code.
    allow_reuse_address = os.name != "nt"


def run(host="127.0.0.1", port=None):
    port = port or int(os.environ.get("PORT", 8000))  # preview tools assign a free port through PORT
    try:
        server = DemoServer((host, port), DemoHandler)
    except OSError as error:
        raise SystemExit(f"Port {port} is not available ({error}). Stop the PigeonOps server that is already running, then start again.")
    print(f"PigeonOps demo running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
