"""Web triage: feed parsing, the agent loop, the Bayes update and the endpoints. No network: live feeds are
replaced by recorded samples below, and the LLM is either disabled, a fake function or the local stub."""
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend import llm, triage, web_feeds
from backend.decision_engine import analyze
from backend.scenario import load_scenario
from scripts import llm_stub
from tests.test_llm import llm_env, tool_reply
from tests.test_server import start_server

POINT = (33.773, -118.237)
CHP_XML = b'''<?xml version="1.0" ?>
<State><Center ID = "LAHB"><Dispatch ID = "LACC">
  <Log ID = "261114LA0001">
    <LogTime>"Nov 14 2026  9:08AM"</LogTime>
    <LogType>"1183-Trfc Collision-Unkn Inj"</LogType>
    <Location>"SR47 N / Ocean Blvd Con"</Location>
    <LocationDesc>"NB CONN"</LocationDesc>
    <LATLON>"33773600:118236400"</LATLON>
    <LogDetails><details><DetailTime>"Nov 14 2026  9:09AM"</DetailTime>
      <IncidentDetail>"[1] BIG RIG ON ITS SIDE BLKG ALL LNS"</IncidentDetail></details>
      <units><UnitTime>"Nov 14 2026  9:10AM"</UnitTime><UnitDetail>"Unit At Scene"</UnitDetail></units></LogDetails>
  </Log>
  <Log ID = "261114LA0002">
    <LogTime>"Nov 14 2026  9:00AM"</LogTime>
    <LogType>"1125-Traffic Hazard"</LogType>
    <Location>"I405 N / Crenshaw Blvd"</Location>
    <LocationDesc>""</LocationDesc>
    <LATLON>"33866803:118347052"</LATLON>
    <LogDetails></LogDetails>
  </Log>
  <Log ID = "261114LA0003">
    <LogTime>"Nov 14 2026  4:00AM"</LogTime>
    <LogType>"1182-Trfc Collision-No Inj"</LogType>
    <Location>"SR47 S / Ocean Blvd"</Location>
    <LocationDesc>""</LocationDesc>
    <LATLON>"33773000:118237000"</LATLON>
    <LogDetails></LogDetails>
  </Log>
</Dispatch></Center></State>'''
SIGNS_JSON = json.dumps({"data": [
    {"cms": {"index": "1", "recordTimestamp": {"recordTime": "09:11:00"},
             "location": {"latitude": "33.7655", "longitude": "-118.2401", "route": "SR-47", "direction": "North",
                          "locationName": "S/O OCEAN BLVD"},
             "message": {"display": "1 Page (Normal)", "phase1": {"phase1Line1": "INCIDENT AHEAD",
                                                                  "phase1Line2": "EXPECT DELAYS", "phase1Line3": ""}}}},
    {"cms": {"index": "2", "location": {"latitude": "33.77", "longitude": "-118.24"},
             "message": {"display": "Blank"}}},
]}).encode()
ALERTS_JSON = json.dumps({"features": [{"properties": {
    "id": "urn:oid:2.49.0.1.840.0.abc", "event": "Flood Advisory", "severity": "Minor",
    "headline": "Flood Advisory until noon", "description": "Minor flooding\nin low-lying areas.",
    "onset": "2026-11-14T08:00:00-08:00"}}]}).encode()


def fake_fetch(url, accept="*/*"):
    if url == web_feeds.CHP_URL:
        return CHP_XML
    if url == web_feeds.SIGNS_URL:
        return SIGNS_JSON
    return ALERTS_JSON


def failing_fetch(url, accept="*/*"):
    raise web_feeds.FeedError("unreachable: no network in tests")


def no_llm():
    return llm_env(PIGEONOPS_LLM_API_KEY="")


class TriageTestCase(unittest.TestCase):
    def setUp(self):
        llm.reset_backoff()
        triage.clear_cache()
        web_feeds.clear_cache()
        self.addCleanup(triage.clear_cache)


class FeedTests(TriageTestCase):
    def test_chp_keeps_recent_logs_near_the_point(self):
        items = web_feeds.parse_chp(CHP_XML, POINT, radius_km=10)
        self.assertEqual(["CHP-261114LA0001"], [item["id"] for item in items])  # far one and 5-hour-old one dropped
        item = items[0]
        self.assertEqual("09:08", item["time"])
        self.assertAlmostEqual(-118.2364, item["lon"])
        self.assertLess(item["distance_km"], 0.2)
        self.assertIn("BLKG ALL LNS", item["detail"])
        self.assertIn("Unit At Scene", item["detail"])
        self.assertEqual("live", item["basis"])

    def test_signs_skip_blank_messages_and_alerts_cover_the_point(self):
        signs = web_feeds.parse_signs(SIGNS_JSON, POINT)
        self.assertEqual(["CMS-D7-1"], [item["id"] for item in signs])
        self.assertEqual("INCIDENT AHEAD / EXPECT DELAYS", signs[0]["detail"])
        alerts = web_feeds.parse_alerts(ALERTS_JSON)
        self.assertEqual("Flood Advisory until noon", alerts[0]["title"])
        self.assertIsNone(alerts[0]["distance_km"])

    def test_unreachable_or_malformed_feeds_report_status_instead_of_raising(self):
        with patch.object(web_feeds, "fetch", failing_fetch):
            self.assertTrue(web_feeds.chp_near(POINT)["status"].startswith("unreachable"))
            self.assertEqual([], web_feeds.alerts_at(POINT)["items"])
        with patch.object(web_feeds, "fetch", lambda url, accept="*/*": b"<not xml"):
            self.assertEqual("malformed response", web_feeds.chp_near(POINT)["status"])
            self.assertEqual("malformed response", web_feeds.signs_near(POINT)["status"])


class ApplyTests(TriageTestCase):
    def test_bayes_update_at_prior_forty_percent(self):
        scenario = load_scenario(variant="disruption")
        expected = {"corroborates": 0.8333, "contradicts": 0.1429, "nothing_found": 0.2448}
        for finding, posterior in expected.items():
            with self.subTest(finding=finding):
                updated = triage.apply_web_evidence(scenario, finding)
                self.assertAlmostEqual(posterior, updated["disruption"]["prior_blocked_probability"], places=4)
                self.assertEqual(0.4, scenario["disruption"]["prior_blocked_probability"])  # original untouched
                analysis = analyze(updated)
                self.assertEqual(finding, analysis["web_evidence"]["finding"])
                self.assertEqual(0.4, analysis["web_evidence"]["prior_before"])
        self.assertIsNone(analyze(scenario)["web_evidence"])

    def test_corroborated_crash_makes_the_drone_not_worth_it(self):
        effect = triage.effect_of(load_scenario(variant="disruption"), "corroborates")
        self.assertTrue(effect["inspection_before"]["recommend"])
        self.assertFalse(effect["inspection_after"]["recommend"])

    def test_bad_finding_and_normal_operations_are_rejected(self):
        with self.assertRaises(ValueError):
            triage.apply_web_evidence(load_scenario(variant="disruption"), "maybe")
        with self.assertRaises(ValueError):
            triage.apply_web_evidence(load_scenario(variant="normal"), "corroborates")
        self.assertFalse(triage.triage(load_scenario(variant="normal"), "normal", "demo")["available"])


class RuleAndModeTests(TriageTestCase):
    def test_demo_feed_without_an_llm_uses_rules(self):
        expected = {"disruption": "corroborates", "disruption_b": "corroborates",
                    "congestion": "corroborates", "flood": "contradicts"}
        with no_llm():
            for variant, finding in expected.items():
                with self.subTest(variant=variant):
                    result = triage.triage(load_scenario(variant=variant), variant, "demo")
                    self.assertEqual(("demo", "rules", "rule_based"), (result["mode"], result["agent"], result["basis"]))
                    self.assertEqual(finding, result["finding"])
                    self.assertTrue(result["cited_ids"])
                    self.assertTrue(all(item["basis"] == "synthetic" for item in result["evidence"]))
                    self.assertTrue(all(item["id"].startswith("SYN-") for item in result["evidence"]))

    def test_live_mode_reads_the_feeds(self):
        with no_llm(), patch.object(web_feeds, "fetch", fake_fetch):
            result = triage.triage(load_scenario(variant="disruption"), "disruption", "live")
        self.assertEqual("live", result["mode"])
        self.assertEqual("corroborates", result["finding"])
        self.assertIn("CHP-261114LA0001", result["cited_ids"])
        self.assertEqual({"ok"}, {source["status"] for source in result["sources"]})

    def test_live_mode_falls_back_to_the_demo_feed_when_every_feed_fails(self):
        with no_llm(), patch.object(web_feeds, "fetch", failing_fetch):
            result = triage.triage(load_scenario(variant="disruption"), "disruption", "live")
        self.assertEqual(("live", "demo"), (result["mode_requested"], result["mode"]))
        self.assertIn("every live feed failed", result["fallback_reason"])

    def test_nothing_nearby_is_nothing_found(self):
        with no_llm(), patch.object(web_feeds, "fetch", lambda url, accept="*/*": b'{"data": [], "features": []}'
                                    if "json" in url or "weather" in url else CHP_XML.replace(b"33773600", b"33873600")):
            result = triage.triage(load_scenario(variant="disruption"), "disruption", "live")
        self.assertEqual("nothing_found", result["finding"])
        self.assertEqual([], result["cited_ids"])


class AgentTests(TriageTestCase):
    def run_agent(self, replies):
        calls = []

        def fake_chat(messages, **kwargs):
            calls.append({"messages": messages, **kwargs})
            return replies[min(len(calls), len(replies)) - 1]

        result = triage.triage(load_scenario(variant="disruption"), "disruption", "demo", chat_fn=fake_chat)
        return result, calls

    def test_agent_searches_then_reports(self):
        result, calls = self.run_agent([
            tool_reply("search_chp_incidents", {"radius_km": 3}),
            tool_reply("report_triage", {"finding": "corroborates", "confidence": "high", "cited_ids": ["SYN-CHP-0914A"],
                                         "summary": "CHP logged a big rig on its side blocking lanes at 09:08."}),
        ])
        self.assertEqual(("llm", "corroborates", "high"), (result["agent"], result["finding"], result["confidence"]))
        self.assertEqual([{"tool": "search_chp_incidents", "radius_km": 3.0, "status": "ok", "found": 1}], result["searches"])
        tool_message = json.loads(calls[1]["messages"][-1]["content"])
        self.assertEqual("SYN-CHP-0914A", tool_message["records"][0]["id"])
        self.assertTrue(result["evidence"][0]["cited"])

    def test_bad_reports_fall_back_to_rules(self):
        search = tool_reply("search_chp_incidents", {})
        cases = {
            "invented id": {"finding": "corroborates", "confidence": "high", "cited_ids": ["SYN-NOPE"], "summary": "Seen."},
            "ungrounded": {"finding": "corroborates", "confidence": "high", "cited_ids": ["SYN-CHP-0914A"],
                           "summary": "Seven units and 45 minutes to clear."},
            "uncited": {"finding": "contradicts", "confidence": "low", "cited_ids": [], "summary": "Looks fine."},
        }
        for label, report in cases.items():
            with self.subTest(label):
                triage.clear_cache()
                result, _ = self.run_agent([search, tool_reply("report_triage", report)])
                self.assertEqual("rules", result["agent"])
                self.assertIn("rejected", result["agent_note"])
        result, _ = self.run_agent([tool_reply("report_triage", cases["uncited"])])
        self.assertIn("without searching", result["agent_note"])

    def test_malformed_tool_calls_and_nan_radius_do_not_crash(self):
        report = tool_reply("report_triage", {"finding": "corroborates", "confidence": "high",
                                              "cited_ids": ["SYN-CHP-0914A"], "summary": "CHP logged a big rig."})
        odd_shapes = {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "function": "search_chp_incidents"}, {"id": "c2", "function": {"name": ["x"]}}]}
        result, _ = self.run_agent([odd_shapes, tool_reply("search_chp_incidents", {"radius_km": float("nan")}), report])
        self.assertIn(result["agent"], ("llm", "rules"))
        self.assertNotIn("NaN", json.dumps(result))
        with self.assertRaises(ValueError):
            triage._radius({"radius_km": float("inf")})

    def test_loop_is_bounded(self):
        result, calls = self.run_agent([tool_reply("weather_alerts", {})])
        self.assertEqual(triage.MAX_ROUNDS + 1, len(calls))
        self.assertEqual({"function": {"name": "report_triage"}, "type": "function"}, calls[-1]["tool_choice"])
        self.assertEqual("rules", result["agent"])

    def test_stub_llm_runs_the_whole_loop(self):
        stub, url = llm_stub.start()
        self.addCleanup(stub.server_close)
        self.addCleanup(stub.shutdown)
        with llm_env(PIGEONOPS_LLM_BASE_URL=url, PIGEONOPS_LLM_MODEL="stub-llm", PIGEONOPS_LLM_API_KEY="stub"):
            result = triage.triage(load_scenario(variant="disruption"), "disruption", "demo")
        self.assertEqual(("llm", "stub-llm", "corroborates"), (result["agent"], result["model"], result["finding"]))
        self.assertEqual(["search_chp_incidents", "read_message_signs"], [s["tool"] for s in result["searches"]])


class EndpointTests(TriageTestCase):
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

    def status_of(self, path, body=None):
        request = Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request) as response:
                return response.status
        except HTTPError as error:
            return error.code

    def test_triage_endpoint(self):
        with no_llm():
            body = self.get("/api/triage?scenario=disruption&mode=demo")
            self.assertEqual(("demo", "corroborates"), (body["mode"], body["finding"]))
            self.assertFalse(self.get("/api/triage?scenario=normal&mode=demo")["available"])
        self.assertEqual(400, self.status_of("/api/triage?scenario=disruption&mode=psychic"))
        self.assertEqual(404, self.status_of("/api/triage?scenario=nope&mode=demo"))

    def test_web_parameter_moves_the_prior(self):
        body = self.get("/api/analysis?scenario=disruption&web=corroborates")
        self.assertAlmostEqual(0.8333, body["prior_blocked_probability"], places=4)
        self.assertFalse(body["evoi"]["recommend_inspection"])
        self.assertEqual("corroborates", body["web_evidence"]["finding"])
        scenario = self.get("/api/scenario?scenario=disruption&web=contradicts")
        self.assertAlmostEqual(0.1429, scenario["disruption"]["prior_blocked_probability"], places=4)
        self.assertEqual(400, self.status_of("/api/analysis?scenario=disruption&web=maybe"))
        self.assertEqual(400, self.status_of("/api/analysis?scenario=normal&web=corroborates"))
        self.assertEqual(400, self.status_of("/api/llm/ask", {"scenario": "disruption", "question": "Why?", "web": "maybe"}))
