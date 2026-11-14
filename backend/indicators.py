"""Plan-level indicators (contract 2) under the top-ranked option before any inspection."""
from backend.outcomes import METERS_PER_MILE, STATES


def _indicator(id_, label, value, unit, basis, **extra):
    return {"id": id_, "label": label, "value": value, "unit": unit, "basis": basis, **extra}


def compute_indicators(scenario, ranked):
    model = scenario["decision_model"]
    prior = scenario["disruption"]["prior_blocked_probability"]
    probability = {"open": 1 - prior, "blocked": prior}
    threshold = model.get("indicator_rules", {}).get("at_risk_probability", 0.5)
    co2_per_mile = model["rates"]["co2_kg_per_mile"]
    planned = model["planned_trip"]
    routes = {route["id"]: route for route in scenario["routes"]}
    truck_ids = {truck["id"] for truck in scenario["trucks"]}

    top = ranked[0]
    outcomes = top["outcomes"]
    from_routes = all(outcomes[state].get("basis") == "estimated" for state in STATES)
    basis = "estimated" if from_routes else "stub"

    def chance(test):
        return sum(probability[state] for state in STATES if test(outcomes[state]))

    p_late = chance(lambda o: o["lateness_min"] > 0)
    p_missed = chance(lambda o: o["constraint_breaches"] > 0)
    expected_delay = sum(probability[s] * outcomes[s]["lateness_min"] for s in STATES)
    expected_co2 = sum(probability[s] * outcomes[s]["emissions_kg"] for s in STATES)

    # Only the affected shipment is re-simulated; every other assigned shipment runs on plan.
    assigned = [s for s in scenario["shipments"] if s["truck"] in truck_ids]
    others = [s for s in assigned if s["id"] != planned["shipment"]]
    others_late = sum(1 for s in others if s.get("slack_min", 0) < 0)
    at_risk = others_late + (1 if p_late >= threshold else 0)
    feasible = len(others) - others_late + (0 if p_missed >= threshold else 1)

    if from_routes:
        added_miles = round(sum(probability[s] * outcomes[s]["detail"]["added_miles"] for s in STATES), 1)
    else:
        added_miles = None

    # Whole-plan CO2: every assigned shipment's full route, plus the change for the affected one.
    base_miles = sum(routes[s["route"]]["distance_m"] for s in assigned) / METERS_PER_MILE
    co2 = round(base_miles * co2_per_mile + expected_co2 - planned["emissions_kg"], 1)

    return [
        _indicator("shipments_at_risk", "Shipments at risk", at_risk, "shipments", basis),
        _indicator("appointments_feasible", "Appointments feasible", feasible, "appointments", basis,
                   of=len(scenario["shipments"])),
        _indicator("expected_delay_min", "Expected delay", round(expected_delay, 1), "min", basis),
        _indicator("added_miles", "Added miles", added_miles, "mi", basis),
        _indicator("co2_kg", "Estimated CO2", co2, "kg", basis),
        _indicator("community_exposure", "Schools within 300 m of routes used", None, "schools", "stub"),
    ]
