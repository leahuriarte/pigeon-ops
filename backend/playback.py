"""Build a minute-by-minute playback of the simulated fleet for the map timeline.

Every truck gets a list of segments. A segment is either a drive along a road path
or a stop at one place, with a start and end time in minutes after midnight. The
frontend interpolates truck positions from these segments.

Arrival times come from the shipment ETAs in the scenario, so the playback agrees
with the shipments table. Road paths come from config/road_geometry.json, which
scripts/build_road_geometry.py fills from OpenStreetMap routing. A leg missing from
that file falls back to straight lines between its waypoints.
"""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROAD_GEOMETRY = ROOT / "config" / "road_geometry.json"

FOCUS_TRUCK = "SYN-TRK-07"
SWAP_TRUCK = "SYN-TRK-12"
TRANSFER_SITE = "warehouse-6"
# Anaheim St waypoints that keep the detour off the CA-47 connector.
DETOUR_VIA = [(33.7797, -118.2639), (33.7815, -118.2165)]
LOAD_MIN = 15
UNLOAD_MIN = 20
TRANSFER_MIN = 10
TAIL_MIN = 15
MAX_STRETCH = 1.5  # A leg may run this much slower than network speed before the truck waits instead.


def clock(value):
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def fmt(minutes):
    minutes = int(round(minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def leg_key(points):
    return ";".join(f"{lat:.4f},{lon:.4f}" for lat, lon in points)


def _haversine_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


def load_geometry(path=ROAD_GEOMETRY):
    if not Path(path).is_file():
        return {}
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle).get("legs", {})


class Planner:
    def __init__(self, scenario, geometry, action, road):
        self.scenario = scenario
        self.geometry = geometry
        self.action = action
        self.road = road
        self.speed_m_per_min = scenario["operations"]["network_speed_mph"] * 1609.344 / 60
        self.start = clock(scenario["generated_at"][11:16])
        self.places = {loc["id"]: loc for loc in scenario["locations"]}
        self.requested = {}

    # Geometry -----------------------------------------------------------------
    def point(self, place):
        if isinstance(place, tuple):
            return place
        loc = self.places[place]
        return (loc["lat"], loc["lon"])

    def name(self, place):
        return self.places[place]["name"] if isinstance(place, str) else "the roadside"

    def leg(self, waypoints):
        waypoints = [self.point(p) for p in waypoints]
        key = leg_key(waypoints)
        self.requested[key] = waypoints
        stored = self.geometry.get(key)
        if stored:
            return stored["path"], stored["distance_m"]
        path = [[lon, lat] for lat, lon in waypoints]
        distance = sum(_haversine_m(a, b) for a, b in zip(waypoints, waypoints[1:])) * 1.3
        return path, distance

    def minutes(self, waypoints):
        return self.leg(waypoints)[1] / self.speed_m_per_min

    # Track building -----------------------------------------------------------
    def new_track(self, truck_id, place, time):
        return {"id": truck_id, "place": place, "t": time, "segments": [], "events": []}

    def stop(self, track, until, kind, text, shipment=None):
        if until <= track["t"] + 0.01:
            return
        lat, lon = self.point(track["place"])
        track["segments"].append({
            "t0": round(track["t"], 2), "t1": round(until, 2), "kind": kind, "text": text,
            "shipment": shipment, "path": [[lon, lat]],
        })
        track["t"] = until

    def drive(self, track, to, text, shipment=None, via=(), arrive=None, kind="driving", stretch=None):
        """Drive to a place. With `arrive`, the leg ends then: the truck drives up to
        `stretch` times slower than network speed (traffic), then waits at the destination."""
        waypoints = [track["place"], *via, to]
        path, distance = self.leg(waypoints)
        duration = distance / self.speed_m_per_min
        depart = track["t"]
        end = depart + duration if arrive is None else max(arrive, depart + duration)
        limit = MAX_STRETCH if stretch is None else stretch
        drive_end = min(end, depart + duration * limit)
        self._segment(track, depart, drive_end, kind, text, shipment, path, distance)
        track["t"] = drive_end
        track["place"] = to
        self.stop(track, end, "waiting", f"Parked at {self.name(to)} ahead of its check-in", shipment)

    def _segment(self, track, t0, t1, kind, text, shipment, path, distance):
        track["segments"].append({
            "t0": round(t0, 2), "t1": round(t1, 2), "kind": kind, "text": text,
            "shipment": shipment, "path": path, "distance_m": round(distance),
        })

    def event(self, track, text, tone="info"):
        track["events"].append({"t": round(track["t"], 2), "truck": track["id"], "text": text, "tone": tone})

    def deliver(self, track, shipment, dest):
        window_start, window_end = (clock(x) for x in shipment["appointment"].split("–"))
        arrival = track["t"]
        late = arrival - window_start
        if arrival > window_end:
            note, tone = "after its appointment window closed", "risk"
        elif late > 0.5:
            note, tone = f"{round(late)} min late for its {fmt(window_start)} appointment", "watch"
        else:
            note, tone = f"on time for its {fmt(window_start)} appointment", "ok"
        self.event(track, f"Checks in at {self.name(dest)} with {shipment['id']}, {note}", tone)
        self.stop(track, window_start, "waiting", f"Waiting for the {fmt(window_start)} appointment at {self.name(dest)}", shipment["id"])
        self.stop(track, track["t"] + UNLOAD_MIN, "unloading", f"Unloading {shipment['id']} at {self.name(dest)}", shipment["id"])
        self.event(track, f"Finishes unloading {shipment['id']}")

    def pickup(self, track, shipment, depart_at=None):
        origin = shipment["origin"]
        if track["place"] != origin:
            self.drive(track, origin, f"Heading to {self.name(origin)} for {shipment['id']}", shipment["id"], kind="empty")
            self.event(track, f"Reaches {self.name(origin)} to collect {shipment['id']}")
        readiness = shipment.get("readiness", "")
        if readiness.startswith("Hold until"):
            self.stop(track, clock(readiness.split()[-1]), "waiting", f"Waiting for {shipment['id']} to be released", shipment["id"])
        if depart_at is not None:
            self.stop(track, depart_at - LOAD_MIN, "waiting", f"Queued at {self.name(origin)} for {shipment['id']}", shipment["id"])
        self.stop(track, track["t"] + LOAD_MIN, "loading", f"Loading {shipment['id']} at {self.name(origin)}", shipment["id"])
        self.event(track, f"Leaves {self.name(origin)} with {shipment['id']}")

    def run_shipments(self, track, shipments, first_loaded):
        for index, shipment in enumerate(shipments):
            dest = shipment["destination"]
            eta = clock(shipment["eta"])
            if not (index == 0 and first_loaded):
                self.pickup(track, shipment, depart_at=eta - self.minutes([shipment["origin"], dest]))
            self.drive(track, dest, f"Carrying {shipment['id']} to {self.name(dest)}", shipment["id"], arrive=eta)
            self.deliver(track, shipment, dest)

    def truck_start(self, truck, shipments):
        if truck["status"] in ("at-terminal", "waiting") and shipments:
            place = shipments[0]["origin"]
        else:
            place = (truck["lat"], truck["lon"])
        track = self.new_track(truck["id"], place, self.start)
        if truck.get("available_at"):
            self.stop(track, clock(truck["available_at"]), "busy", "Finishing its previous job")
            self.event(track, "Becomes available for new work")
        return track

    # Focus truck options ------------------------------------------------------
    def plan_focus(self, truck, shipments, tracks_by_id, all_shipments):
        """SYN-TRK-07 and the affected load play out the selected dispatch option."""
        disruption = self.scenario["disruption"]
        affected = next(s for s in shipments if s["id"] == disruption["affected_shipment"])
        rest = [s for s in shipments if s is not affected]
        window_start = clock(affected["appointment"].split("–")[0])
        action = next(a for a in self.scenario["decision_model"]["actions"] if a["id"] == self.action)
        lateness = action["outcomes"][self.road]["lateness_min"]
        target = window_start + lateness if lateness > 0 else clock(affected["eta"])
        dest = affected["destination"]
        track = self.truck_start(truck, shipments)
        has_incident = self.scenario.get("scenario_type") == "disruption" and "incident" in self.places

        if self.action == "hold-course":
            if has_incident:
                self.drive(track, "incident", f"Carrying {affected['id']} toward the CA-47 connector", affected["id"],
                           arrive=None if self.road == "blocked" else target - self.minutes(["incident", dest]), stretch=math.inf)
                if self.road == "blocked":
                    self.event(track, "Stopped by the obstruction on the CA-47 connector", "risk")
                    self.stop(track, target - self.minutes(["incident", dest]), "blocked", "Stuck behind the obstruction on CA-47", affected["id"])
                    self.event(track, "CA-47 reopens and traffic starts moving", "watch")
                self.drive(track, dest, f"Carrying {affected['id']} to {self.name(dest)}", affected["id"], arrive=target, stretch=math.inf)
            else:
                self.drive(track, dest, f"Carrying {affected['id']} to {self.name(dest)}", affected["id"], arrive=target, stretch=math.inf)
            self.deliver(track, affected, dest)
            self.run_shipments(track, rest, first_loaded=False)
            return [track]

        if self.action == "reroute":
            self.event(track, "Turns off CA-47 onto the Anaheim St detour", "watch")
            self.drive(track, dest, f"Carrying {affected['id']} to {self.name(dest)} via Anaheim St", affected["id"],
                       via=DETOUR_VIA, arrive=target, kind="detour", stretch=math.inf)
            self.deliver(track, affected, dest)
            self.run_shipments(track, rest, first_loaded=False)
            return [track]

        # swap-truck: both trucks meet at the transfer yard and trade loads.
        partner = next(t for t in self.scenario["trucks"] if t["id"] == SWAP_TRUCK)
        partner_loads = sorted((s for s in all_shipments if s["truck"] == SWAP_TRUCK), key=lambda s: clock(s["eta"]))
        handed_over = partner_loads[0] if partner_loads else None
        helper = self.truck_start(partner, partner_loads)
        own_arrival = self.start + self.minutes([track["place"], DETOUR_VIA[0], TRANSFER_SITE])
        helper_arrival = helper["t"] + self.minutes([helper["place"], TRANSFER_SITE])
        meet = max(own_arrival, helper_arrival)

        self.event(track, f"Diverts to {self.name(TRANSFER_SITE)} to hand {affected['id']} to {SWAP_TRUCK}", "watch")
        self.drive(track, TRANSFER_SITE, f"Taking {affected['id']} to {self.name(TRANSFER_SITE)} for the swap", affected["id"],
                   via=[DETOUR_VIA[0]], kind="detour")
        self.stop(track, meet, "waiting", f"Waiting for {SWAP_TRUCK} at {self.name(TRANSFER_SITE)}", affected["id"])
        self.drive(helper, TRANSFER_SITE, f"Heading to {self.name(TRANSFER_SITE)} to take {affected['id']}", affected["id"], kind="empty")
        self.stop(helper, meet, "waiting", f"Waiting for {FOCUS_TRUCK} at {self.name(TRANSFER_SITE)}", affected["id"])
        for t in (track, helper):
            self.event(t, f"Transfers {affected['id']} at {self.name(TRANSFER_SITE)}", "watch")
            self.stop(t, meet + TRANSFER_MIN, "transfer", f"Transferring {affected['id']} between trucks", affected["id"])

        self.drive(helper, dest, f"Carrying {affected['id']} to {self.name(dest)} via Anaheim St", affected["id"],
                   via=[DETOUR_VIA[1]], arrive=target, kind="detour", stretch=math.inf)
        self.deliver(helper, affected, dest)
        if handed_over:
            self.pickup(track, handed_over)
            self.drive(track, handed_over["destination"], f"Carrying {handed_over['id']} to {self.name(handed_over['destination'])}", handed_over["id"])
            self.deliver(track, handed_over, handed_over["destination"])
        self.run_shipments(track, rest, first_loaded=False)
        self.run_shipments(helper, partner_loads[1:], first_loaded=False)
        tracks_by_id[SWAP_TRUCK] = helper
        return [track, helper]

    # Entry point --------------------------------------------------------------
    def build(self):
        shipments_by_truck = {}
        for shipment in self.scenario["shipments"]:
            if shipment["truck"] == "Unassigned":
                continue
            shipments_by_truck.setdefault(shipment["truck"], []).append(shipment)
        for loads in shipments_by_truck.values():
            loads.sort(key=lambda s: clock(s["eta"]))

        tracks = {}
        for truck in self.scenario["trucks"]:
            if truck["id"] in tracks:
                continue
            loads = shipments_by_truck.get(truck["id"], [])
            if truck["id"] == FOCUS_TRUCK:
                for track in self.plan_focus(truck, loads, tracks, self.scenario["shipments"]):
                    tracks[track["id"]] = track
                continue
            if truck["id"] == SWAP_TRUCK and self.action == "swap-truck":
                continue  # Planned together with the focus truck.
            track = self.truck_start(truck, loads)
            self.run_shipments(track, loads, first_loaded=truck["status"] == "en-route")
            tracks[truck["id"]] = track

        if self.action == "swap-truck" and SWAP_TRUCK not in tracks:
            raise ValueError("swap partner missing from plan")

        end = math.ceil(max(t["t"] for t in tracks.values()) + TAIL_MIN)
        ordered = []
        for truck in self.scenario["trucks"]:
            track = tracks[truck["id"]]
            self.stop(track, end, "idle", f"Idle at {self.name(track['place'])}")
            ordered.append({"id": track["id"], "segments": track["segments"]})
        events = sorted((e for t in tracks.values() for e in t["events"]), key=lambda e: (e["t"], e["truck"]))
        return {
            "start": self.start,
            "end": end,
            "action": self.action,
            "road": self.road,
            "focus_truck": FOCUS_TRUCK,
            "tracks": ordered,
            "events": events,
            "alternatives": self.alternatives(),
            "road_geometry": bool(self.geometry),
        }

    def alternatives(self):
        """The two road choices for the focus truck, drawn as context on the map."""
        truck = next(t for t in self.scenario["trucks"] if t["id"] == FOCUS_TRUCK)
        affected = next(s for s in self.scenario["shipments"] if s["id"] == self.scenario["disruption"]["affected_shipment"])
        here = (truck["lat"], truck["lon"])
        dest = affected["destination"]
        current = [here, "incident", dest] if "incident" in self.places and self.scenario.get("scenario_type") == "disruption" else [here, dest]
        if current[1] == "incident":
            first, _ = self.leg(current[:2])
            second, _ = self.leg(current[1:])
            current_path = first + second[1:]
        else:
            current_path, _ = self.leg(current)
        detour_path, _ = self.leg([here, *DETOUR_VIA, dest])
        return [
            {"id": "current", "name": "Current route via CA-47", "path": current_path},
            {"id": "detour", "name": "Detour via Anaheim St", "path": detour_path},
        ]


def default_choice(scenario, analysis, observation=None):
    ranked = analysis["after"] if observation and analysis.get("after") else analysis["before"]
    probability = analysis.get("posterior_blocked_probability", analysis.get("prior_blocked_probability", 0))
    return ranked[0]["id"], "blocked" if probability >= 0.5 else "open"


def build_playback(scenario, action="hold-course", road="open", geometry=None):
    actions = {a["id"] for a in scenario["decision_model"]["actions"]}
    if action not in actions:
        raise ValueError(f"unknown action: {action}")
    if road not in ("open", "blocked"):
        raise ValueError("road must be open or blocked")
    planner = Planner(scenario, load_geometry() if geometry is None else geometry, action, road)
    return planner.build()


def required_legs(scenarios):
    """Every leg the planner can ask for, keyed like road_geometry.json."""
    legs = {}
    for scenario in scenarios:
        for action in scenario["decision_model"]["actions"]:
            for road in ("open", "blocked"):
                planner = Planner(scenario, {}, action["id"], road)
                planner.build()
                legs.update(planner.requested)
    return legs
