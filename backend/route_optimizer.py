"""Offline route-candidate scoring with synthetic time-of-day traffic.

This is an advisory candidate search. Saved OSRM driving routes have not been
checked for truck restrictions, and the traffic multipliers are synthetic.
The existing incident and drone decision model remains the source of the
dispatcher response recommendation.
"""
import json
from itertools import product
from pathlib import Path

from backend.outcomes import METERS_PER_MILE, clock_minutes, clock_text

ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / "config" / "route_candidates.json"
TRAFFIC = ROOT / "config" / "synthetic_traffic.json"
MAX_COMBINATIONS = 10000


def load_inputs(route_path=ROUTES, traffic_path=TRAFFIC):
    catalog = json.loads(Path(route_path).read_text(encoding="utf-8"))
    traffic = json.loads(Path(traffic_path).read_text(encoding="utf-8"))
    if catalog.get("basis") != "estimated" or traffic.get("basis") != "synthetic":
        raise ValueError("route catalog and traffic inputs need provenance labels")
    return catalog, traffic


def traffic_at(traffic, minutes):
    for period in traffic["periods"]:
        start, end = clock_minutes(period["start"])[0], clock_minutes(period["end"])[0]
        if start <= minutes < end:
            return period
    raise ValueError(f"no synthetic traffic period covers {clock_text(minutes)}")


def _candidates(shipment, catalog):
    record = catalog["shipments"].get(shipment["id"])
    if not record or not record["candidates"]:
        raise ValueError(f"no saved route candidates for {shipment['id']}")
    return record["candidates"]


def _baseline(shipment, candidates):
    return next((route for route in candidates if route.get("scenario_route") == shipment["route"]), candidates[0])


def _score(shipment, route, baseline, carry_min, shift_min, period, adjustments, model):
    rates = model["rates"]
    multiplier = period["duration_multiplier"] * adjustments.get(route["id"], 1.0)
    duration_min = route["duration_s"] / 60 * multiplier
    scheduled = clock_minutes(shipment["eta"])[0] + shift_min
    arrival = scheduled + carry_min + duration_min - baseline["duration_s"] / 60
    start, end = clock_minutes(shipment["appointment"])[:2]
    lateness = max(0, arrival - start)
    missed = int(arrival > end)
    miles = route["distance_m"] / METERS_PER_MILE
    emissions = miles * rates["co2_kg_per_mile"]
    trip_cost = miles * rates["cost_per_mile"] + duration_min / 60 * rates["driver_hourly_rate"]
    max_extra = period["extra_delay_max_min"]
    buffer = start - arrival
    risk = 1.0 if buffer < 0 else max(0.0, min(1.0, (max_extra - buffer) / max_extra)) if max_extra else 0.0
    if max_extra == 0 or buffer >= max_extra:
        expected_lateness = lateness
    elif buffer <= 0:
        expected_lateness = -buffer + max_extra / 2
    else:
        expected_lateness = (max_extra - buffer) ** 2 / (2 * max_extra)
    end_buffer = end - arrival
    missed_probability = (max(0.0, min(1.0, (max_extra - end_buffer) / max_extra))
                          if max_extra else float(bool(missed)))
    loss = (trip_cost + expected_lateness * model["lateness_cost_per_minute"]
            + emissions * model["emissions_cost_per_kg"]
            + missed_probability * model["hard_constraint_penalty"])
    return {
        "shipment": shipment["id"], "truck": shipment["truck"], "route": route["id"],
        "scenario_route": route.get("scenario_route"), "arrival": clock_text(arrival),
        "appointment": shipment["appointment"], "slack_min": round(buffer, 1),
        "lateness_min": round(lateness, 1), "missed_appointment": bool(missed),
        "expected_lateness_min": round(expected_lateness, 1),
        "missed_appointment_probability": round(missed_probability, 3),
        "distance_m": route["distance_m"], "duration_min": round(duration_min, 1),
        "co2_kg": round(emissions, 1), "cost": round(trip_cost, 2),
        "loss": round(loss, 2), "risk_after_start": round(risk, 3),
        "basis": "estimated", "traffic_basis": "synthetic",
    }, max(0.0, arrival - scheduled)


def optimize_routes(scenario, at=None, catalog=None, traffic=None):
    """Find the lowest-loss route combination for each truck's ordered loads.

    A late first load delays later loads on the same truck. Other trucks are
    independent because this search does not reassign drivers or shipments.
    `at` is a hypothetical shift start with the same synthetic truck positions,
    so all planned ETAs move by the same amount; it is not a live GPS update.
    """
    if catalog is None or traffic is None:
        saved_catalog, saved_traffic = load_inputs()
        catalog = saved_catalog if catalog is None else catalog
        traffic = saved_traffic if traffic is None else traffic
    now = clock_minutes(at or scenario["generated_at"])[0]
    shift = now - clock_minutes(scenario["generated_at"])[0]
    period = traffic_at(traffic, now)
    adjustments = {item["route"]: item["duration_multiplier"] for item in traffic["route_adjustments"]}
    assigned = [item for item in scenario["shipments"] if item["truck"] != "Unassigned" and clock_minutes(item["eta"])]
    trucks = {}
    for shipment in assigned:
        trucks.setdefault(shipment["truck"], []).append(shipment)
    plans = []
    for truck_id, shipments in sorted(trucks.items()):
        shipments.sort(key=lambda item: clock_minutes(item["eta"])[0])
        choices = [_candidates(item, catalog) for item in shipments]
        combinations = 1
        for options in choices:
            combinations *= len(options)
        if combinations > MAX_COMBINATIONS:
            raise ValueError(f"{truck_id} has too many route combinations: {combinations}")
        best = None
        for selected in product(*choices):
            carry, rows = 0.0, []
            for shipment, route, options in zip(shipments, selected, choices):
                row, carry = _score(shipment, route, _baseline(shipment, options), carry, shift,
                                    period, adjustments, scenario["decision_model"])
                rows.append(row)
            loss = round(sum(row["loss"] for row in rows), 2)
            if best is None or loss < best["estimated_loss"]:
                best = {"truck": truck_id, "shipments": rows, "estimated_loss": loss,
                        "candidates_considered": combinations, "basis": "estimated"}
        plans.append(best)
    return {"at": clock_text(now), "traffic_period": period["id"], "traffic_basis": "synthetic",
            "route_basis": "estimated", "truck_validated": False, "advisory": True,
            "scope": "Hypothetical shift start from saved synthetic positions; incident probability and truck restrictions excluded",
            "trucks": plans}
