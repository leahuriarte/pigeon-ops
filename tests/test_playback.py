import unittest

from backend.playback import build_playback, clock, required_legs
from backend.scenario import load_scenario


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

    def test_blocked_hold_course_strands_the_truck(self):
        playback = build_playback(self.scenario, "hold-course", "blocked")
        focus = next(t for t in playback["tracks"] if t["id"] == "SYN-TRK-07")
        self.assertEqual("blocked", position_kind(focus, clock("10:00")))
        late = 42 + clock("10:15")
        checkin = next(e for e in playback["events"] if e["truck"] == "SYN-TRK-07" and e["text"].startswith("Checks in"))
        self.assertAlmostEqual(late, checkin["t"], delta=1)

    def test_swap_hands_the_load_to_the_partner_truck(self):
        playback = build_playback(self.scenario, "swap-truck", "open")
        checkin = next(e for e in playback["events"] if "SYN-SHP-1042" in e["text"] and e["text"].startswith("Checks in"))
        self.assertEqual("SYN-TRK-12", checkin["truck"])
        self.assertAlmostEqual(clock("10:15") + 12, checkin["t"], delta=1)

    def test_rejects_unknown_inputs(self):
        with self.assertRaises(ValueError):
            build_playback(self.scenario, "teleport", "open")
        with self.assertRaises(ValueError):
            build_playback(self.scenario, "reroute", "flooded")

    def test_saved_geometry_covers_every_leg(self):
        from backend.playback import load_geometry
        geometry = load_geometry()
        scenarios = [load_scenario(variant=v) for v in ("normal", "disruption")]
        missing = set(required_legs(scenarios)) - set(geometry)
        self.assertEqual(set(), missing, "run scripts/build_road_geometry.py")


if __name__ == "__main__":
    unittest.main()
