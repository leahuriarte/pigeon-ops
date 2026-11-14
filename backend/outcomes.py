"""Compute each dispatcher action's outcomes from route data.

Every number produced here is an estimate built from OSRM route distances and
durations plus the assumed rates in decision_model.rates. docs/METHODS.md lists
each formula and assumption.
"""
import math
import re
from copy import deepcopy

METERS_PER_MILE = 1609.344
EARTH_RADIUS_M = 6371008.8
METRICS = ("lateness_min", "incremental_cost", "emissions_kg", "constraint_breaches")
STATES = ("open", "blocked")


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


class _Trip:
    """Accumulates driving and waiting along whole-route fractions."""

    def __init__(self):
        self.meters, self.drive_min, self.wait_min, self.path = 0.0, 0.0, 0.0, []

    def drive(self, route, start, end, note=None):
        share = abs(end - start)
        self.meters += share * route["distance_m"]
        self.drive_min += share * route["duration_s"] / 60
        self.path.append(note or route["id"])

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
        incident = _by_id(scenario["locations"], blockage["location"])
        self.truck_at = fraction_along(self.current["geometry"], truck["lat"], truck["lon"])
        self.incident_at = fraction_along(self.blocked_route["geometry"], incident["lat"], incident["lon"])
        self.eta = clock_minutes(self.shipment["eta"])[0]
        self.window_start, self.window_end = clock_minutes(self.shipment["appointment"])[:2]
        self.planned = self.trip(self.current["id"], "open")

    def trip(self, route_id, state):
        """Remaining trip from the truck's position if it follows `route_id` and the road is `state`."""
        route = self.routes[route_id]
        trip = _Trip()
        if route is self.current:
            position = self.truck_at
        else:
            if not _same_start(route, self.current):
                raise ValueError(f"route {route_id} must start where {self.current['id']} starts")
            trip.drive(self.current, self.truck_at, 0.0, f"{self.current['id']} back to start")
            position = 0.0
        if state == "blocked" and route is self.blocked_route and position < self.incident_at:
            trip.drive(route, position, self.incident_at, f"{route['id']} to obstruction")
            trip.wait(self.turnaround_min, "turnaround at obstruction")
            trip.drive(route, self.incident_at, 0.0, f"{route['id']} back to start")
            trip.drive(self.fallback, 0.0, 1.0)
        else:
            trip.drive(route, position, 1.0)
        return trip

    def cost(self, trip):
        return trip.miles * self.rates["cost_per_mile"] + trip.driver_min * self.rates["driver_hourly_rate"] / 60

    def outcome(self, trip):
        # The shipment's planned ETA anchors the clock; route data supplies the change.
        arrival = self.eta + trip.driver_min - self.planned.driver_min
        return {
            "lateness_min": round(max(0.0, arrival - self.window_start), 1),
            "incremental_cost": round(self.cost(trip) - self.cost(self.planned), 2),
            "emissions_kg": round(trip.miles * self.rates["co2_kg_per_mile"], 1),
            "constraint_breaches": 1 if arrival > self.window_end else 0,
            "basis": "estimated",
            "detail": {
                "arrival": clock_text(arrival),
                "miles": round(trip.miles, 2),
                "added_miles": round(trip.miles - self.planned.miles, 2),
                "driver_min": round(trip.driver_min, 1),
                "trip_cost": round(self.cost(trip), 2),
                "path": trip.path,
            },
        }

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


def apply_override(computed, override):
    """Merge an action's hand-entered override over its computed outcomes; the override wins."""
    outcomes = deepcopy(computed)
    for state in STATES:
        values = {k: v for k, v in (override or {}).get(state, {}).items() if k in METRICS}
        if not values:
            continue
        outcome = outcomes.setdefault(state, {})
        outcome.update(values)
        outcome["basis"] = "override"
        outcome["override_basis"] = override.get("basis", "assumed")
    for state in STATES:
        missing = [k for k in METRICS if k not in outcomes.get(state, {})]
        if missing:
            raise ValueError(f"{state} outcome is missing {missing}; give a plan or a complete override")
    return outcomes


def apply_route_outcomes(scenario):
    """Fill decision_model.actions[*].outcomes in place from route data and overrides."""
    model = scenario["decision_model"]
    routes = RouteModel(scenario)
    model["planned_trip"] = routes.planned_summary()
    for action in model["actions"]:
        plan = action.get("plan")
        computed = {state: routes.outcome(routes.trip(plan["route"], state)) for state in STATES} if plan else {}
        action["outcomes"] = apply_override(computed, action.get("override"))
    return scenario
