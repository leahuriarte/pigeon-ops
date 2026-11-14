import unittest

from backend.playback import build_playback, clock, required_legs
from backend.scenario import available_scenarios, load_scenario


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario(variant="disruption")

    def test_segments_are_continuous_and_cover_the_timeline(self):
        for action in ("hold-course", "reroute", "swap-truck"):
            for road in ("open", "blocked"):
                playback = build_playback(self.scenario, action, road)
                for track in playback["tracks"]:
                    segments = track["segments"]
                    self.assertAlmostEqual(playback["start"], segments[0]["t0"], places=1)
                    self.assertAlmostEqual(playback["end"], segments[-1]["t1"], places=1)
                    for before, after in zip(segments, segments[1:]):
                        self.assertAlmostEqual(before["t1"], after["t0"], places=1, msg=f"{track['id']} {action} {road}")
                        self.assertLessEqual(after["t0"], after["t1"])

    def test_route_options_arrive_when_the_outcome_model_says(self):
        for action_id in ("hold-course", "reroute"):
            for road in ("open", "blocked"):
                action = next(a for a in self.scenario["decision_model"]["actions"] if a["id"] == action_id)
                expected = clock(action["outcomes"][road]["detail"]["arrival"])
                playback = build_playback(self.scenario, action_id, road)
                checkin = next(e for e in playback["events"] if "SYN-SHP-1042" in e["text"] and e["text"].startswith("Checks in"))
                self.assertAlmostEqual(expected, checkin["t"], delta=1, msg=f"{action_id} {road}")

    def test_after_an_inspection_the_truck_holds_and_arrives_when_the_delayed_outcome_says(self):
        delay = self.scenario["decision_model"]["drone"]["launch_and_review_minutes"]
        for action_id in ("hold-course", "reroute", "swap-truck"):
            for road in ("open", "blocked"):
                action = next(a for a in self.scenario["decision_model"]["actions"] if a["id"] == action_id)
                expected = clock(action["outcomes_after_inspection"][road]["detail"]["arrival"])
                playback = build_playback(self.scenario, action_id, road, inspected=True)
                self.assertTrue(playback["inspected"])
                checkin = next(e for e in playback["events"] if "SYN-SHP-1042" in e["text"] and e["text"].startswith("Checks in"))
                self.assertAlmostEqual(expected, checkin["t"], delta=1, msg=f"{action_id} {road}")
                focus = next(t for t in playback["tracks"] if t["id"] == "SYN-TRK-07")
                hold = focus["segments"][0]
                self.assertEqual("waiting", hold["kind"], msg=f"{action_id} {road}")
                self.assertAlmostEqual(delay, hold["t1"] - hold["t0"], places=1)
                for a, b in zip(focus["segments"], focus["segments"][1:]):
                    self.assertAlmostEqual(a["t1"], b["t0"], places=1)
        self.assertFalse(build_playback(self.scenario, "reroute", "open")["inspected"])

    def test_saved_geometry_covers_every_leg(self):
        from backend.playback import load_geometry
        geometry = load_geometry()
        scenarios = [load_scenario(variant=v["id"]) for v in available_scenarios()]
        missing = set(required_legs(scenarios)) - set(geometry)
        self.assertEqual(set(), missing, "run scripts/build_road_geometry.py")

    def test_each_incident_playback_agrees_with_its_model_and_location(self):
        for variant in ("disruption", "disruption_b", "congestion", "flood"):
            scenario = load_scenario(variant=variant)
            for inspected in (False, True):
                for action_id in ("hold-course", "reroute"):
                    for road in ("open", "blocked"):
                        playback = build_playback(scenario, action_id, road, inspected=inspected)
                        action = next(a for a in scenario["decision_model"]["actions"] if a["id"] == action_id)
                        key = "outcomes_after_inspection" if inspected else "outcomes"
                        checkin = next(e for e in playback["events"] if "SYN-SHP-1042" in e["text"] and e["text"].startswith("Checks in"))
                        self.assertAlmostEqual(clock(action[key][road]["detail"]["arrival"]), checkin["t"], delta=1)
                        if action_id == "hold-course" and road == "blocked":
                            events = " ".join(e["text"] for e in playback["events"])
                            self.assertIn(scenario["disruption"]["location_label"], events)
                            if variant == "congestion":
                                self.assertNotIn("queue on CA-47", events)


if __name__ == "__main__":
    unittest.main()
