"""LLM client, copilot roles and endpoints. Only 127.0.0.1 is contacted (a local stub); a developer's
real .env is never read because every test points ENV_FILE at a temporary path."""
import json
import os
import socket
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend import copilot, llm
from backend.decision_engine import analyze
from backend.inspection import list_inspections, review_inspection
from backend.scenario import load_scenario
from scripts import llm_stub
from scripts.llm_stub import StubHandler, reply_to
from tests.test_server import start_server

KEYS = ("PIGEONOPS_LLM_BASE_URL", "PIGEONOPS_LLM_MODEL", "PIGEONOPS_LLM_API_KEY",
        "PIGEONOPS_LLM_TIMEOUT", "PIGEONOPS_LLM_VISION")


@contextmanager
def llm_env(env_file_text=None, **values):
    """Patch the LLM configuration: no developer .env, only the given variables."""
    with tempfile.TemporaryDirectory() as temp:
        env_file = Path(temp) / ".env"
        if env_file_text is not None:
            env_file.write_text(env_file_text, encoding="utf-8")
        with patch.object(llm, "ENV_FILE", env_file), patch.dict(os.environ, {}):
            for key in KEYS:
                os.environ.pop(key, None)
            os.environ.update(values)
            yield


def tool_reply(name, arguments):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}


def scenario_and_analysis(observation=None):
    scenario = load_scenario(variant="disruption")
    return scenario, analyze(scenario, observation)


class LLMTestCase(unittest.TestCase):
    def setUp(self):
        llm.reset_backoff()
        copilot.clear_caches()
        self.addCleanup(llm.reset_backoff)
        self.addCleanup(copilot.clear_caches)


class ConfigTests(LLMTestCase):
    def test_env_file_is_parsed_and_environment_wins(self):
        text = "# comment\n\nexport PIGEONOPS_LLM_MODEL='file-model'\nPIGEONOPS_LLM_API_KEY = \"file-key\"\nnot a line\n"
        with llm_env(text):
            settings = llm.config()
            self.assertEqual(("file-model", "file-key"), (settings["model"], settings["api_key"]))
            self.assertEqual(llm.DEFAULT_BASE_URL, settings["base_url"])
            self.assertEqual(30.0, settings["timeout"])
            self.assertFalse(settings["vision"])
        with llm_env(text, PIGEONOPS_LLM_MODEL="env-model", PIGEONOPS_LLM_VISION="1"):
            self.assertEqual("env-model", llm.config()["model"])
            self.assertTrue(llm.config()["vision"])

    def test_disabled_without_a_key(self):
        with llm_env():
            self.assertEqual({"enabled": False, "model": "bolt-instruct-v2",
                              "provider_host": "aisquared-bolt-hackathon-proxy.hf.space", "basis": "llm_generated"},
                             llm.status())
            with self.assertRaises(llm.LLMUnavailable):
                llm.chat([{"role": "user", "content": "hi"}])
            case = list_inspections()["cases"][0]
            self.assertFalse(copilot.footage_opinion(case)["available"])

    def test_failure_short_circuits_later_calls(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]  # closed again before use: nothing listens here
        with llm_env(PIGEONOPS_LLM_BASE_URL=f"http://127.0.0.1:{port}/v1", PIGEONOPS_LLM_API_KEY="k",
                     PIGEONOPS_LLM_TIMEOUT="3"):
            with self.assertRaises(llm.LLMUnavailable):
                llm.chat([{"role": "user", "content": "hi"}])
            with self.assertRaises(llm.LLMUnavailable) as second:
                llm.chat([{"role": "user", "content": "hi"}])
            self.assertIn("recently failed", second.exception.reason)


class GroundingTests(unittest.TestCase):
    def test_numbers_must_come_from_the_sources(self):
        facts = {"chance": 0.4, "loss": 1227.11, "id": "SYN-SHP-1042", "road": "CA-47", "at": "09:30", "net": -534.27}
        self.assertEqual([], copilot.ungrounded_numbers("A 40% chance.", facts))
        self.assertEqual([], copilot.ungrounded_numbers("It costs $1,227 or 1227.11 or 1,227.1.", facts))
        self.assertEqual([], copilot.ungrounded_numbers("SYN-SHP-1042 on CA-47 at 09:30 loses $534.", facts))
        self.assertEqual(["999", "41"], copilot.ungrounded_numbers("It saves $999 at a 41% chance.", facts))
        self.assertEqual([], copilot.ungrounded_numbers("No figures here.", facts))


class StubRoleTests(LLMTestCase):
    @classmethod
    def setUpClass(cls):
        cls.stub, cls.url = llm_stub.start()

    @classmethod
    def tearDownClass(cls):
        cls.stub.shutdown()
        cls.stub.server_close()

    def env(self, **extra):
        return llm_env(PIGEONOPS_LLM_BASE_URL=self.url, PIGEONOPS_LLM_MODEL="stub-llm", PIGEONOPS_LLM_API_KEY="stub", **extra)

    def test_footage_opinion_echoes_the_cv_suggestion_and_is_cached(self):
        case = next(item for item in list_inspections()["cases"] if item["id"] == "crash-lanes-blocked")
        with self.env():
            result = copilot.footage_opinion(case)
        self.assertTrue(result["available"], result)
        self.assertEqual(("llm_generated", "stub-llm", "blocked", True, False),
                         (result["basis"], result["model"], result["observation"], result["agrees_with_cv"], result["used_image"]))
        self.assertLessEqual(len(result["checks"]), 3)
        self.assertEqual(result, copilot.cached_footage_opinion("crash-lanes-blocked"))

    def test_brief_names_the_top_option_and_the_inspection_advice(self):
        scenario, analysis = scenario_and_analysis()
        with self.env():
            brief = copilot.decision_brief(scenario, analysis)
        self.assertTrue(brief["available"], brief)
        self.assertEqual(analysis["before"][0]["id"], brief["recommended_action_id"])
        self.assertEqual(analysis["evoi"]["recommend_inspection"], brief["inspect_first"])
        for field in ("headline", "why", "what_would_change_it", "who_is_affected"):
            self.assertTrue(brief[field])

    def test_ask_calls_the_engine(self):
        scenario, analysis = scenario_and_analysis("clear")
        with self.env():
            result = copilot.ask(scenario, analysis, "Why not reroute right away?")
        self.assertTrue(result["available"], result)
        self.assertEqual([{"name": "get_decision_summary", "arguments": {}}], result["engine_calls"])
        self.assertTrue(result["grounded"])

    def test_tool_choice_is_dropped_after_a_client_error(self):
        class NoToolChoice(StubHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if "tool_choice" in body:
                    return self._send({"error": {"message": "tool_choice not supported"}}, 400)
                self._send({"choices": [{"message": reply_to(body)}]})

        from http.server import ThreadingHTTPServer
        import threading
        server = ThreadingHTTPServer(("127.0.0.1", 0), NoToolChoice)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        scenario, analysis = scenario_and_analysis()
        with llm_env(PIGEONOPS_LLM_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1", PIGEONOPS_LLM_API_KEY="k"):
            self.assertTrue(copilot.decision_brief(scenario, analysis)["available"])


class GuardTests(LLMTestCase):
    def brief_args(self, analysis, **changes):
        return {"recommended_action_id": analysis["before"][0]["id"],
                "inspect_first": analysis["evoi"]["recommend_inspection"],
                "headline": "Reroute now.", "why": "Lowest expected loss.",
                "what_would_change_it": "A clear road.", "who_is_affected": "Neighborhood streets.", **changes}

    def brief(self, **changes):
        scenario, analysis = scenario_and_analysis()
        args = self.brief_args(analysis, **changes)
        with llm_env(PIGEONOPS_LLM_API_KEY="k"):
            return copilot.decision_brief(scenario, analysis, chat_fn=lambda *a, **k: tool_reply("write_brief", args))

    def test_brief_guards(self):
        self.assertTrue(self.brief()["available"])
        copilot.clear_caches()
        self.assertIn("top-ranked", self.brief(recommended_action_id="swap-truck")["reason"])
        self.assertIn("inspection advice", self.brief(inspect_first=False)["reason"])
        self.assertEqual("ungrounded numbers", self.brief(why="It saves $4,321 overall.")["reason"])
        self.assertEqual("ungrounded numbers", self.brief(headline="Reroute: 99% sure.")["reason"])
        # Quoting a figure from the facts is fine: the net value of inspecting is $116.
        self.assertTrue(self.brief(why="Checking first is worth $116 net.")["available"])

    def test_footage_opinion_outside_allowed_observations_is_rejected(self):
        case = {"id": "only-inconclusive", "allowed_observations": ["inconclusive"],
                "cv_assessment": {"suggested_observation": "inconclusive"}}
        args = {"observation": "blocked", "confidence": "high", "rationale": "Looks blocked.", "check_before_confirming": []}
        with llm_env(PIGEONOPS_LLM_API_KEY="k"):
            result = copilot.footage_opinion(case, chat_fn=lambda *a, **k: tool_reply("report_road_state", args))
            self.assertFalse(result["available"])
            junk = copilot.footage_opinion(case, chat_fn=lambda *a, **k: {"role": "assistant", "content": "no idea"})
            self.assertFalse(junk["available"])
        self.assertIsNone(copilot.cached_footage_opinion("only-inconclusive"))

    def test_footage_opinion_reads_json_content_and_falls_back_to_text_without_the_image(self):
        case = next(item for item in list_inspections()["cases"] if item["id"] == "crash-orbit")
        sent = []

        def fake_chat(messages, **kwargs):
            sent.append(messages[-1]["content"])
            if isinstance(messages[-1]["content"], list):
                raise llm.LLMUnavailable("HTTP 400")
            return {"role": "assistant", "content": 'Sure: {"observation": "inconclusive", "confidence": "low", '
                    '"rationale": "The orbit hides the lanes.", "check_before_confirming": ["Far lanes"]}'}

        with llm_env(PIGEONOPS_LLM_API_KEY="k", PIGEONOPS_LLM_VISION="on"):
            result = copilot.footage_opinion(case, chat_fn=fake_chat)
        self.assertTrue(result["available"], result)
        self.assertFalse(result["used_image"])
        self.assertTrue(sent[0][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertIsInstance(sent[1], str)

    def test_ask_runs_engine_tools_and_flags_invented_numbers(self):
        scenario, analysis = scenario_and_analysis()
        replies = iter([tool_reply("inspection_value_at", {"blocked_probability": 0.6}),
                        {"role": "assistant", "content": "At 60% a check would lose $304, so decide now."}])
        with llm_env(PIGEONOPS_LLM_API_KEY="k"):
            result = copilot.ask(scenario, analysis, "What if it were 60%?", chat_fn=lambda *a, **k: next(replies))
            self.assertTrue(result["grounded"], result)
            self.assertEqual([{"name": "inspection_value_at", "arguments": {"blocked_probability": 0.6}}], result["engine_calls"])
            made_up = copilot.ask(scenario, analysis, "Hi?",
                                  chat_fn=lambda *a, **k: {"role": "assistant", "content": "It costs $8,888."})
            self.assertFalse(made_up["grounded"])
            with self.assertRaises(ValueError):
                copilot.ask(scenario, analysis, " ")
            with self.assertRaises(ValueError):
                copilot.ask(scenario, analysis, "x" * 301)

    def test_ask_bounds_the_tool_loop_and_reports_bad_arguments(self):
        scenario, analysis = scenario_and_analysis()
        calls = []

        def always_tools(messages, tools=None, **kwargs):
            calls.append(tools)
            if tools is None:
                return {"role": "assistant", "content": "The tools could not answer that."}
            return tool_reply("rank_responses", {"blocked_probability": 7})

        with llm_env(PIGEONOPS_LLM_API_KEY="k"):
            result = copilot.ask(scenario, analysis, "Rank them?", chat_fn=always_tools)
        self.assertEqual(copilot.MAX_ASK_ROUNDS + 1, len(calls))
        self.assertIsNone(calls[-1])
        self.assertEqual(copilot.MAX_ASK_ROUNDS, len(result["engine_calls"]))
        error = copilot.run_engine_tool("rank_responses", {"blocked_probability": 7}, scenario, analysis)
        self.assertIn("error", error)
        self.assertIn("error", copilot.run_engine_tool("shipment_outcomes", {"action_id": "fly", "road_state": "open"}, scenario, analysis))
        rows = copilot.run_engine_tool("shipment_outcomes", {"action_id": "hold-course", "road_state": "blocked"}, scenario, analysis)
        self.assertEqual("SYN-SHP-1042", rows["shipments"][0]["shipment"])


class EndpointTests(LLMTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.base = start_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_disabled_llm_is_a_quiet_200(self):
        with llm_env():
            self.assertEqual((200, False), (lambda r: (r[0], r[1]["enabled"]))(self.request("/api/llm/status")))
            for path in ("/api/llm/footage?case_id=crash-lanes-blocked", "/api/llm/brief?scenario=disruption",
                         "/api/llm/brief?scenario=disruption&observation=clear"):
                status, body = self.request(path)
                self.assertEqual((200, False), (status, body["available"]), path)
            status, body = self.request("/api/llm/ask", {"scenario": "disruption", "observation": None, "question": "Why?"})
            self.assertEqual((200, False), (status, body["available"]))

    def test_bad_input_is_rejected(self):
        with llm_env():
            self.assertEqual(404, self.request("/api/llm/footage?case_id=nope")[0])
            self.assertEqual(404, self.request("/api/llm/brief?scenario=nope")[0])
            self.assertEqual(400, self.request("/api/llm/brief?observation=maybe")[0])
            self.assertEqual(400, self.request("/api/llm/ask", {"scenario": "disruption", "question": ""})[0])
            self.assertEqual(400, self.request("/api/llm/ask", {"scenario": "disruption", "observation": "maybe", "question": "Why?"})[0])
            self.assertEqual(400, self.request("/api/llm/ask", {"scenario": "nope", "question": "Why?"})[0])
            self.assertEqual(400, self.request("/api/llm/ask", ["not", "an", "object"])[0])


class ReviewRecordTests(LLMTestCase):
    def test_review_records_the_llm_suggestion(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        library, audit = Path(temp.name) / "library.json", Path(temp.name) / "reviews.jsonl"
        case = {"id": "road", "allowed_observations": ["blocked", "clear", "inconclusive"],
                "cv_assessment": {"suggested_observation": "blocked"},
                "source": {"source_url": "https://example.com/road", "sha256": "abc"}}
        library.write_text(json.dumps({"available": True, "cases": [case]}), encoding="utf-8")
        payload = {"case_id": "road", "observation": "clear", "scenario": "disruption", "simulated_application": True}

        first = review_inspection(payload, library, audit)["review"]
        self.assertEqual((None, None), (first["llm_suggestion"], first["agrees_with_llm"]))
        args = {"observation": "inconclusive", "confidence": "low", "rationale": "Cannot tell.", "check_before_confirming": []}
        with llm_env(PIGEONOPS_LLM_API_KEY="k"):
            # A second read is stored per kind of check, so one made for a traffic check does not count here.
            copilot.footage_opinion(case, chat_fn=lambda *a, **k: tool_reply("report_road_state", args),
                                    check={"type": "congestion", "label": "Traffic check", "bad": "jammed", "good": "flowing"})
            self.assertIsNone(review_inspection(payload, library, audit)["review"]["llm_suggestion"])
            copilot.footage_opinion(case, chat_fn=lambda *a, **k: tool_reply("report_road_state", args),
                                    check={"type": "crash", "label": "Crash check", "bad": "blocked", "good": "open"})
        second = review_inspection(payload, library, audit)["review"]
        self.assertEqual(("inconclusive", False), (second["llm_suggestion"], second["agrees_with_llm"]))


if __name__ == "__main__":
    unittest.main()
