"""Compute each dispatcher action's outcomes from route data.

Every number produced here is an estimate built from OSRM route distances and
durations plus the assumed rates in decision_model.rates. docs/METHODS.md lists
each formula and assumption.
"""
import math
import re

METERS_PER_MILE = 1609.344
EARTH_RADIUS_M = 6371008.8
METRICS = ("lateness_min", "incremental_cost", "emissions_kg", "constraint_breaches")
STATES = ("open", "blocked")
INSPECTED = "outcomes_after_inspection"  # the same outcomes when an inspection first holds the decision


def clock_minutes(text):
    """Minutes after midnight for every HH:MM in `text`, so '10:15–10:45' gives [615, 645]."""
    return [int(h) * 60 + int(m) for h, m in re.findall(r"(\d{1,2}):(\d{2})", text or "")]


def clock_text(minutes):
    minutes = int(round(minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def fraction_along(geometry, lat, lon):
    """Share of a LineString's length that lies before the point on it nearest to (lat, lon)."""
    coords = geometry["coordinates"]
    scale = math.cos(math.radians(coords[0][1]))

    def xy(c_lon, c_lat):
        # Equirectangular projection in metres; accurate enough over a few kilometres.
        return math.radians(c_lon) * EARTH_RADIUS_M * scale, math.radians(c_lat) * EARTH_RADIUS_M

    points = [xy(c[0], c[1]) for c in coords]
    px, py = xy(lon, lat)
    walked, best_gap, best_along = 0.0, math.inf, 0.0
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length ** 2))
        gap = math.hypot(ax + t * dx - px, ay + t * dy - py)
        if gap < best_gap:
            best_gap, best_along = gap, walked + t * length
        walked += length
    return best_along / walked if walked else 0.0


def straight_line_m(lat1, lon1, lat2, lon2):
    """Straight-line distance in metres, with the same flat approximation as fraction_along."""
    dx = math.radians(lon2 - lon1) * EARTH_RADIUS_M * math.cos(math.radians((lat1 + lat2) / 2))
    dy = math.radians(lat2 - lat1) * EARTH_RADIUS_M
    return math.hypot(dx, dy)


class Trip:
    """Accumulates driving and waiting along whole-route fractions."""

    def __init__(self):
        self.meters, self.drive_min, self.wait_min, self.path = 0.0, 0.0, 0.0, []

    def drive(self, route, start, end, note=None):
        share = abs(end - start)
        self.meters += share * route["distance_m"]
        self.drive_min += share * route["duration_s"] / 60
        self.path.append(note or route["id"])

    def join(self, meters, minutes, note):
        self.meters += meters
        self.drive_min += minutes
        self.path.append(note)

    def wait(self, minutes, note):
        self.wait_min += minutes
        self.path.append(note)

    @property
    def driver_min(self):
        return self.drive_min + self.wait_min

    @property
    def miles(self):
        return self.meters / METERS_PER_MILE


def _by_id(items, key):
    found = next((item for item in items if item["id"] == key), None)
    if found is None:
        raise ValueError(f"unknown id: {key}")
    return found


def _same_start(a, b):
    return all(abs(x - y) < 1e-6 for x, y in zip(a["points"][0], b["points"][0]))


class RouteModel:
    """Everything the outcome formulas need about the affected shipment and the blockage."""

    def __init__(self, scenario):
        model = scenario["decision_model"]
        self.rates = model["rates"]
        blockage = model["blockage"]
        self.routes = {route["id"]: route for route in scenario["routes"]}
        self.shipment = _by_id(scenario["shipments"], scenario["disruption"]["affected_shipment"])
        truck = _by_id(scenario["trucks"], self.shipment["truck"])
        self.current = self.routes[self.shipment["route"]]
        self.blocked_route = self.routes[blockage["route"]]
        self.fallback = self.routes[blockage["fallback_route"]]
        if self.fallback is self.blocked_route:
            raise ValueError("blockage fallback_route must differ from the blocked route")
        self.turnaround_min = blockage["turnaround_min"]
        # A "slowdown" (heavy traffic) holds the truck for slowdown_min and lets it through; a closure turns it back.
        self.slowdown_min = blockage["slowdown_min"] if blockage.get("effect") == "slowdown" else None
        incident = _by_id(scenario["locations"], blockage["location"])
        self.truck = truck
        self.truck_at = fraction_along(self.current["geometry"], truck["lat"], truck["lon"])
        self.incident_at = fraction_along(self.blocked_route["geometry"], incident["lat"], incident["lon"])
        self.planned = self.trip(self.current["id"], "open")

    def trip(self, route_id, state):
        """Remaining trip from the affected truck's position if it follows `route_id` and the road is `state`."""
        return self.own_trip(self.shipment, self.truck, route_id, state)

    def own_trip(self, shipment, truck, route_id, state, trip=None):
        """Remaining trip of a shipment's load from `truck`'s position, projected onto the shipment's route.

        Appends to `trip` when given, so a reassigned truck can drive to the load first.
        """
        current = self.routes[shipment["route"]]
        route = self.routes[route_id]
        position = fraction_along(current["geometry"], truck["lat"], truck["lon"])
        trip = Trip() if trip is None else trip
        if route is not current:
            if not _same_start(route, current):
                raise ValueError(f"route {route_id} must start where {current['id']} starts")
            trip.drive(current, position, 0.0, f"{current['id']} back to start")
            position = 0.0
        self.follow(trip, route, position, state)
        return trip

    def follow(self, trip, route, position, state):
        """Drive `route` from `position` to its end. In the blocked state the truck queues through a slowdown,
        or turns back at a closure and takes the fallback route."""
        if state == "blocked" and route is self.blocked_route and position < self.incident_at:
            trip.drive(route, position, self.incident_at, f"{route['id']} to obstruction")
            if self.slowdown_min is not None:
                trip.wait(self.slowdown_min, "queue through the slowdown")
                trip.drive(route, self.incident_at, 1.0)
                return
            trip.wait(self.turnaround_min, "turnaround at obstruction")
            trip.drive(route, self.incident_at, 0.0, f"{route['id']} back to start")
            trip.drive(self.fallback, 0.0, 1.0)
        else:
            trip.drive(route, position, 1.0)

    def cost(self, trip):
        return trip.miles * self.rates["cost_per_mile"] + trip.driver_min * self.rates["driver_hourly_rate"] / 60

    def planned_summary(self):
        return {
            "shipment": self.shipment["id"],
            "route": self.current["id"],
            "truck_position_share": round(self.truck_at, 3),
            "obstruction_position_share": round(self.incident_at, 3),
            "miles": round(self.planned.miles, 2),
            "driver_min": round(self.planned.driver_min, 1),
            "trip_cost": round(self.cost(self.planned), 2),
            "emissions_kg": round(self.planned.miles * self.rates["co2_kg_per_mile"], 1),
            "basis": "estimated",
        }


def apply_route_outcomes(scenario):
    """Fill each action's outcomes, its outcomes after an inspection, and decision_model.assignments in place."""
    from backend.assignment import AssignmentModel  # assignment builds on RouteModel

    model = scenario["decision_model"]
    routes = RouteModel(scenario)
    pool = AssignmentModel(scenario, routes)
    model["planned_trip"] = routes.planned_summary()
    delay = model.get("drone", {}).get("launch_and_review_minutes", 0)
    for action in model["actions"]:
        for key, minutes in (("outcomes", 0), (INSPECTED, delay)):
            action[key] = {state: pool.outcome(action["plan"], state, minutes) for state in STATES}
    model["assignments"] = pool.enumerate(model["actions"], delay)
    return scenario
