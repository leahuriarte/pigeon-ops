"""Truck-to-shipment assignments for the shipments a disruption can touch.

decision_model.assignment_pool names the shipments and trucks that may be
reassigned. Every assignment of pool trucks to pool shipments is enumerated with
itertools.permutations, each shipment's arrival is computed from truck
availability times and route data, and the plan is scored with the decision
weights. docs/METHODS.md section 4 lists the formulas. Every number is an
estimate built on synthetic data.
"""
from itertools import permutations
from math import perm

from backend.outcomes import INSPECTED, METERS_PER_MILE, STATES, Trip, _by_id, clock_minutes, clock_text, straight_line_m

MAX_ASSIGNMENTS = 5040  # seven trucks over seven shipments; keeps every API call fast


def _clock(text, what):
    found = clock_minutes(text)
    if not found:
        raise ValueError(f"{what} needs an HH:MM time, got {text!r}")
    return found[0]


class AssignmentModel:
    """Arrivals and outcomes for every way of putting the pool's trucks on the pool's shipments."""

    def __init__(self, scenario, routes):
        self.routes = routes
        self.affected = routes.shipment
        config = scenario["decision_model"].get("assignment_pool")
        if config:
            shipment_ids, truck_ids = config["shipments"], config["trucks"]
            self.handover_min = config["handover_min"]
            self.fetch_circuity = config["fetch_circuity"]
            self.fetch_speed_mph = config["fetch_speed_mph"]
        else:
            shipment_ids, truck_ids = [self.affected["id"]], [self.affected["truck"]]
        self.shipments = [_by_id(scenario["shipments"], key) for key in shipment_ids]
        self.trucks = {key: _by_id(scenario["trucks"], key) for key in truck_ids}
        self._validate(scenario, bool(config))
        self.now = clock_minutes(scenario.get("generated_at"))[:1]
        self.planned = {s["id"]: s["truck"] for s in self.shipments}
        self.baseline = {s["id"]: self._row(s, s["truck"], s["route"], "open") for s in self.shipments}

    def _validate(self, scenario, configured):
        ids = [s["id"] for s in self.shipments]
        if self.affected["id"] not in ids:
            raise ValueError(f"assignment_pool must include the affected shipment {self.affected['id']}")
        planned = [s["truck"] for s in self.shipments]
        for shipment in self.shipments:
            if shipment["truck"] not in self.trucks:
                raise ValueError(f"{shipment['id']}'s truck {shipment['truck']} must be in assignment_pool.trucks")
        if len(set(planned)) != len(planned):
            raise ValueError("each pool truck may carry only one pool shipment; load sequencing is not modeled")
        for shipment in scenario["shipments"]:
            if shipment["truck"] in self.trucks and shipment["id"] not in ids:
                raise ValueError(f"{shipment['truck']} also carries {shipment['id']}, which is outside the pool")
        count = perm(len(self.trucks), len(self.shipments))
        if count > MAX_ASSIGNMENTS:
            raise ValueError(f"assignment_pool has {count} assignments; the limit is {MAX_ASSIGNMENTS}")
        if configured:
            for truck in self.trucks.values():
                _clock(truck.get("available_at"), f"{truck['id']}.available_at")

    def _available(self, truck_id):
        return _clock(self.trucks[truck_id].get("available_at"), f"{truck_id}.available_at")

    def _held(self, truck_id, delay):
        """A new trip that starts with `truck_id` waiting for an inspection that ends `delay` minutes from now.

        A truck that is not free until after the inspection ends does not wait.
        """
        trip = Trip()
        if delay:
            free = clock_minutes(self.trucks[truck_id].get("available_at"))[:1]
            if free and not self.now:
                raise ValueError("an inspection delay needs the scenario's generated_at time")
            hold = delay if not free else self.now[0] + delay - max(free[0], self.now[0])
            if hold > 0:
                trip.wait(hold, "hold for inspection")
        return trip

    def _row(self, shipment, truck_id, route_id, state, delay=0):
        """One shipment's trip, arrival and lateness when `truck_id` carries it on `route_id`.

        `delay` is how long an inspection holds the decision before any truck acts on it.
        """
        own_id = shipment["truck"]
        own = self.trucks[own_id]
        planned_trip = self.routes.own_trip(shipment, own, shipment["route"], "open")
        if truck_id == own_id:
            trip = self.routes.own_trip(shipment, own, route_id, state, self._held(own_id, delay))
            shift = 0.0
        else:
            trip, shift = self._fetch(shipment, self.trucks[truck_id], own, route_id, state, delay)
        # The planned ETA anchors the clock; route data and availability times supply the change.
        arrival = _clock(shipment["eta"], f"{shipment['id']}.eta") + shift + trip.driver_min - planned_trip.driver_min
        window_start, window_end = clock_minutes(shipment["appointment"])[:2]
        return {
            "shipment": shipment["id"],
            "truck": truck_id,
            "route": route_id,
            "reassigned": truck_id != own_id,
            "arrival_min": arrival,
            "appointment": shipment["appointment"],
            "slack_min": window_start - arrival,
            "lateness_min": max(0.0, arrival - window_start),
            "constraint_breaches": 1 if arrival > window_end else 0,
            "miles": trip.miles,
            "driver_min": trip.driver_min,
            "trip_cost": self.routes.cost(trip),
            "emissions_kg": trip.miles * self.routes.rates["co2_kg_per_mile"],
            "path": trip.path,
        }

    def _fetch(self, shipment, truck, own, route_id, state, delay=0):
        """Trip of a reassigned truck that drives to the load on `own`, takes it over and hauls it.

        Returns the trip and how much later than planned the trip starts.
        """
        ready = clock_minutes(shipment.get("readiness"))[:1]
        planned_start = max([self._available(own["id"])] + ready)
        trip = self._held(truck["id"], delay)
        available = self._available(truck["id"])
        leaves = available + trip.wait_min
        meters = straight_line_m(truck["lat"], truck["lon"], own["lat"], own["lon"]) * self.fetch_circuity
        minutes = meters / METERS_PER_MILE / self.fetch_speed_mph * 60
        trip.join(meters, minutes, f"drive to load on {own['id']}")
        idle = max([leaves + minutes, self._available(own["id"])] + ready) - (leaves + minutes)
        if idle > 0:
            trip.wait(idle, "wait for handover")
        trip.wait(self.handover_min, "load handover")
        self.routes.own_trip(shipment, own, route_id, state, trip)
        return trip, available - planned_start

    def _rows(self, trucks, routes, state, delay=0):
        return [self._row(s, trucks[s["id"]], routes.get(s["id"], s["route"]), state, delay) for s in self.shipments]

    def _score(self, rows):
        """Outcome in the action format: the affected shipment's values plus the change to every other pool shipment."""
        affected = next(r for r in rows if r["shipment"] == self.affected["id"])
        others = [r for r in rows if r is not affected]

        def with_knock_on(key):
            return affected[key] + sum(r[key] - self.baseline[r["shipment"]][key] for r in others)

        def change(key):
            return sum(r[key] - self.baseline[r["shipment"]][key] for r in rows)

        return {
            "lateness_min": round(with_knock_on("lateness_min"), 1),
            "incremental_cost": round(change("trip_cost"), 2),
            "emissions_kg": round(with_knock_on("emissions_kg"), 1),
            "constraint_breaches": with_knock_on("constraint_breaches"),
            "basis": "estimated",
            "detail": {
                "arrival": clock_text(affected["arrival_min"]),
                "miles": round(affected["miles"], 2),
                "added_miles": round(change("miles"), 2),
                "driver_min": round(affected["driver_min"], 1),
                "trip_cost": round(affected["trip_cost"], 2),
                "path": affected["path"],
                "shipments": [self._public(r) for r in rows],
            },
        }

    @staticmethod
    def _public(row):
        out = {key: value for key, value in row.items() if key != "arrival_min"}
        out["arrival"] = clock_text(row["arrival_min"])
        for key in ("slack_min", "lateness_min", "driver_min", "emissions_kg"):
            out[key] = round(row[key], 1)
        out["miles"] = round(row["miles"], 2)
        out["trip_cost"] = round(row["trip_cost"], 2)
        out["basis"] = "estimated"
        return out

    def _trucks_for(self, plan):
        trucks = dict(self.planned)
        trucks.update(plan.get("trucks", {}))
        unknown = set(trucks) - set(self.planned)
        if unknown or not set(trucks.values()) <= set(self.trucks) or len(set(trucks.values())) != len(trucks):
            raise ValueError(f"plan trucks {plan.get('trucks')} must give each pool shipment its own pool truck")
        return trucks

    def outcome(self, plan, state, delay=0):
        """Outcome of an action plan: optional `route` for the affected shipment, optional `trucks` reassignment."""
        trucks = self._trucks_for(plan)
        route = plan.get("route", self.affected["route"])
        return self._score(self._rows(trucks, {self.affected["id"]: route}, state, delay))

    def enumerate(self, actions, delay=0):
        """Every assignment of pool trucks to pool shipments, each shipment on its own route.

        Each option also gets its outcomes when an inspection first holds the decision for `delay` minutes.
        """
        ids = [s["id"] for s in self.shipments]
        named = {}
        for action in actions:
            plan = action.get("plan")
            if not plan or action.get("override") or plan.get("route", self.affected["route"]) != self.affected["route"]:
                continue
            trucks = self._trucks_for(plan)
            named.setdefault(tuple(trucks[key] for key in ids), action["id"])
        options = []
        for combo in permutations(self.trucks, len(ids)):
            trucks = dict(zip(ids, combo))
            options.append({
                "id": "|".join(f"{key}:{truck}" for key, truck in trucks.items()),
                "label": ", ".join(f"{truck} carries {key}" for key, truck in trucks.items()),
                "trucks": trucks,
                "planned": trucks == self.planned,
                "action": named.get(combo),
                "outcomes": {state: self._score(self._rows(trucks, {}, state)) for state in STATES},
                INSPECTED: {state: self._score(self._rows(trucks, {}, state, delay)) for state in STATES},
            })
        return {"shipments": ids, "trucks": list(self.trucks), "count": len(options), "options": options}
