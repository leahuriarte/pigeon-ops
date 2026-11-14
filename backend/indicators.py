"""Plan-level indicators (contract 2) under the top-ranked option before any inspection."""
import json
from functools import lru_cache
from pathlib import Path

from backend.outcomes import METERS_PER_MILE, STATES

EXPOSURE = Path(__file__).resolve().parents[1] / "config" / "community_exposure.json"


@lru_cache(maxsize=None)
def schools_by_route(path=EXPOSURE):
    """Route id -> ids of the schools within 300 m of it, from scripts/build_community_exposure.py. Empty if not built."""
    if not Path(path).is_file():
        return {}
    routes = json.loads(Path(path).read_text(encoding="utf-8"))["routes"]
    return {route: frozenset(school["osm_id"] for school in schools) for route, schools in routes.items()}


def action_route(scenario, action_id):
    """The route the affected load is sent on by an action: its plan's route, or the shipment's own route."""
    action = next(a for a in scenario["decision_model"]["actions"] if a["id"] == action_id)
    affected = next(s for s in scenario["shipments"] if s["id"] == scenario["disruption"]["affected_shipment"])
    return (action.get("plan") or {}).get("route", affected["route"])


def schools_near(route_ids, exposure=None):
    """Distinct schools within 300 m of any of the routes, or None when a route has no saved data."""
    exposure = schools_by_route() if exposure is None else exposure
    if any(route not in exposure for route in route_ids):
        return None
    return len(frozenset().union(*(exposure[route] for route in route_ids)))


def _indicator(id_, label, value, unit, basis, **extra):
    return {"id": id_, "label": label, "value": value, "unit": unit, "basis": basis, **extra}


def compute_indicators(scenario, ranked, exposure=None):
    model = scenario["decision_model"]
    prior = scenario["disruption"]["prior_blocked_probability"]
    probability = {"open": 1 - prior, "blocked": prior}
    threshold = model.get("indicator_rules", {}).get("at_risk_probability", 0.5)
    co2_per_mile = model["rates"]["co2_kg_per_mile"]
    routes = {route["id"]: route for route in scenario["routes"]}
    truck_ids = {truck["id"] for truck in scenario["trucks"]}

    top = ranked[0]
    outcomes = top["outcomes"]
    basis = "estimated"
    expected_delay = sum(probability[s] * outcomes[s]["lateness_min"] for s in STATES)
    expected_co2 = sum(probability[s] * outcomes[s]["emissions_kg"] for s in STATES)

    # Shipments in the assignment pool are re-simulated one by one; every other assigned shipment runs on plan.
    rows = {state: {row["shipment"]: row for row in outcomes[state]["detail"]["shipments"]} for state in STATES}
    pool = list(rows["open"])

    def pool_count(test):
        return sum(1 for key in pool if sum(probability[s] for s in STATES if test(rows[s][key])) >= threshold)

    pool_late = pool_count(lambda row: row["lateness_min"] > 0)
    pool_missed = pool_count(lambda row: row["constraint_breaches"] > 0)
    assigned = [s for s in scenario["shipments"] if s["truck"] in truck_ids]
    others = [s for s in assigned if s["id"] not in pool]
    others_late = sum(1 for s in others if s.get("slack_min", 0) < 0)
    at_risk = others_late + pool_late
    feasible = len(others) - others_late + len(pool) - pool_missed

    added_miles = round(sum(probability[s] * outcomes[s]["detail"]["added_miles"] for s in STATES), 1)

    # Whole-plan CO2: every assigned shipment's full route, plus the top option's change to the pool.
    base_miles = sum(routes[s["route"]]["distance_m"] for s in assigned) / METERS_PER_MILE
    co2 = round(base_miles * co2_per_mile + expected_co2 - model["planned_trip"]["emissions_kg"], 1)

    # Schools near every route the plan drives, with the affected load on the top option's route.
    affected_id = scenario["disruption"]["affected_shipment"]
    used = {action_route(scenario, top["id"]) if s["id"] == affected_id else s["route"] for s in assigned}
    schools = schools_near(used, exposure)

    return [
        _indicator("shipments_at_risk", "Shipments at risk", at_risk, "shipments", basis),
        _indicator("appointments_feasible", "Appointments feasible", feasible, "appointments", basis,
                   of=len(scenario["shipments"])),
        _indicator("expected_delay_min", "Expected delay", round(expected_delay, 1), "min", basis),
        _indicator("added_miles", "Added miles", added_miles, "mi", basis),
        _indicator("co2_kg", "Estimated CO2", co2, "kg", basis),
        _indicator("community_exposure", "Schools within 300 m of the routes used", schools, "schools",
                   "stub" if schools is None else "estimated"),
    ]
