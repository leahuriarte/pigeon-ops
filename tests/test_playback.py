import unittest

from backend.playback import build_playback, clock, required_legs
from backend.scenario import available_scenarios, load_scenario


def position_kind(track, t):
    for segment in track["segments"]:
        if segment["t0"] <= t <= segment["t1"]:
            return segment["kind"]
    return None


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

    def test_every_truck_is_tracked(self):
        playback = build_playback(self.scenario, "reroute", "open")
        self.assertEqual([t["id"] for t in self.scenario["trucks"]], [t["id"] for t in playback["tracks"]])

    def test_deliveries_match_shipment_etas(self):
        playback = build_playback(self.scenario, "hold-course", "open")
        checkins = {e["text"].split(" with ")[1].split(",")[0]: e["t"] for e in playback["events"] if e["text"].startswith("Checks in")}
        for shipment in self.scenario["shipments"]:
            if shipment["truck"] != "Unassigned":
                self.assertAlmostEqual(clock(shipment["eta"]), checkins[shipment["id"]], delta=1, msg=shipment["id"])

    def test_route_options_arrive_when_the_outcome_model_says(self):
        for action_id in ("hold-course", "reroute"):
            for road in ("open", "blocked"):
                action = next(a for a in self.scenario["decision_model"]["actions"] if a["id"] == action_id)
                expected = clock(action["outcomes"][road]["detail"]["arrival"])
                playback = build_playback(self.scenario, action_id, road)
                checkin = next(e for e in playback["events"] if "SYN-SHP-1042" in e["text"] and e["text"].startswith("Checks in"))
                self.assertAlmostEqual(expected, checkin["t"], delta=1, msg=f"{action_id} {road}")

    def test_blocked_hold_course_turns_around_at_the_obstruction(self):
        playback = build_playback(self.scenario, "hold-course", "blocked")
        focus = next(t for t in playback["tracks"] if t["id"] == "SYN-TRK-07")
        stuck = next(s for s in focus["segments"] if s["kind"] == "blocked")
        turnaround = self.scenario["decision_model"]["blockage"]["turnaround_min"]
        self.assertAlmostEqual(turnaround, stuck["t1"] - stuck["t0"], places=1)

    def test_swap_hands_the_load_to_the_partner_truck(self):
        playback = build_playback(self.scenario, "swap-truck", "open")
        checkin = next(e for e in playback["events"] if "SYN-SHP-1042" in e["text"] and e["text"].startswith("Checks in"))
        self.assertEqual("SYN-TRK-12", checkin["truck"])
        swap = next(a for a in self.scenario["decision_model"]["actions"] if a["id"] == "swap-truck")
        self.assertAlmostEqual(clock("10:15") + swap["outcomes"]["open"]["lateness_min"], checkin["t"], delta=1)

    def test_rejects_unknown_inputs(self):
        with self.assertRaises(ValueError):
            build_playback(self.scenario, "teleport", "open")
        with self.assertRaises(ValueError):
            build_playback(self.scenario, "reroute", "flooded")

    def test_saved_geometry_covers_every_leg(self):
        from backend.playback import load_geometry
        geometry = load_geometry()
        scenarios = [load_scenario(variant=v["id"]) for v in available_scenarios()]
        missing = set(required_legs(scenarios)) - set(geometry)
        self.assertEqual(set(), missing, "run scripts/build_road_geometry.py")


if __name__ == "__main__":
    unittest.main()
