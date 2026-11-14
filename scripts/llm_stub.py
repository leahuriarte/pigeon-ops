"""Local STUB of an OpenAI-compatible chat completions server, for development and tests.

It returns canned replies; it is not a real model and does not read or reason about anything
beyond picking values out of the request. Model name: `stub-llm`.

    python -m scripts.llm_stub [port]      (default 8765)

then point PigeonOps at it, for example in `.env`:

    PIGEONOPS_LLM_BASE_URL=http://127.0.0.1:8765/v1
    PIGEONOPS_LLM_MODEL=stub-llm
    PIGEONOPS_LLM_API_KEY=stub

Canned behaviour, by the tools a request offers:
- report_road_state: echoes the CV suggestion found in the evidence JSON;
- write_brief: picks the rank-1 option and the inspection advice from the facts JSON;
- the Q&A tools: first calls get_decision_summary, then answers with a fixed sentence.
No canned text contains digits, so the number-grounding guards pass.
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "stub-llm"


def _text_of(content):
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""


def _json_in(text):
    start, end = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[start:end + 1]) if 0 <= start < end else {}
    except ValueError:
        return {}


def _call(name, arguments, index=0):
    return {"id": f"stub_call_{index}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


def reply_to(body):
    """The canned assistant message for one chat completions request body."""
    messages = body.get("messages") or []
    tools = {tool.get("function", {}).get("name") for tool in body.get("tools") or []}
    user = _json_in(_text_of(next((m.get("content") for m in reversed(messages) if m.get("role") == "user"), "")))

    if "report_road_state" in tools:
        cv = user.get("cv_assessment") or {}
        allowed = user.get("allowed_observations") or ["inconclusive"]
        observation = cv.get("suggested_observation")
        observation = observation if observation in allowed else ("inconclusive" if "inconclusive" in allowed else allowed[0])
        return {"role": "assistant", "content": None, "tool_calls": [_call("report_road_state", {
            "observation": observation, "confidence": "medium",
            "rationale": "Stub reply: the motion evidence is consistent with the CV suggestion.",
            "check_before_confirming": ["Look at the marked road for vehicles that never move.",
                                        "Check whether the far lanes are visible."],
        })]}
    if "write_brief" in tools:
        options = user.get("ranked_options") or [{}]
        top = min(options, key=lambda option: option.get("rank", 0))
        return {"role": "assistant", "content": None, "tool_calls": [_call("write_brief", {
            "recommended_action_id": top.get("id"),
            "inspect_first": bool((user.get("inspection") or {}).get("recommended")),
            "headline": "Stub brief: follow the top-ranked response.",
            "why": "Stub reply: it has the lowest expected loss in the engine's ranking.",
            "what_would_change_it": "A different chance of a blockage could change the ranking.",
            "who_is_affected": "The affected shipment and its warehouse appointment.",
        })]}
    if "get_decision_summary" in tools and not any(m.get("role") == "tool" for m in messages):
        return {"role": "assistant", "content": None, "tool_calls": [_call("get_decision_summary", {})]}
    return {"role": "assistant", "content": "Stub reply: the engine's ranking above answers this; the dispatcher decides."}


class StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path.rstrip("/") != "/v1/chat/completions":
            return self._send({"error": {"message": "not found"}}, 404)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        except ValueError:
            return self._send({"error": {"message": "invalid JSON"}}, 400)
        message = reply_to(body)
        self._send({"id": "stub", "object": "chat.completion", "model": MODEL, "choices": [{
            "index": 0, "message": message, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}]})

    def _send(self, payload, status=200):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass


def start(port=0):
    """Start the stub in a daemon thread; returns (server, base_url ending in /v1)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), StubHandler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/v1"


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = ThreadingHTTPServer(("127.0.0.1", port), StubHandler)
    print(f"LLM stub (canned replies, not a real model) at http://127.0.0.1:{port}/v1")
    server.serve_forever()
