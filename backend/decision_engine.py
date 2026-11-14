"""Small, auditable expected-value-of-information decision engine."""
from backend.indicators import action_route, compute_indicators, schools_near
from backend.outcomes import INSPECTED, METRICS

SIGNALS = ("blocked", "clear", "inconclusive")


def _loss(metrics, weights):
    return round(
        metrics["lateness_min"] * weights["lateness_cost_per_minute"]
        + metrics["incremental_cost"]
        + metrics["emissions_kg"] * weights["emissions_cost_per_kg"]
        + metrics["constraint_breaches"] * weights["hard_constraint_penalty"],
        2,
    )


def likelihoods_from_accuracy(accuracy):
    """P(signal | road state) for an inspection that is right with probability `accuracy`."""
    return {
        "blocked": {"blocked": accuracy, "clear": 1 - accuracy, "inconclusive": 0.0},
        "open": {"blocked": 1 - accuracy, "clear": accuracy, "inconclusive": 0.0},
    }


def posterior_blocked(prior, observation, likelihoods):
    """Bayes update; `likelihoods` is an accuracy or a P(signal | road state) table."""
    if isinstance(likelihoods, (int, float)):
        likelihoods = likelihoods_from_accuracy(likelihoods)
    if observation not in likelihoods["blocked"]:
        return prior
    blocked = likelihoods["blocked"][observation] * prior
    open_ = likelihoods["open"][observation] * (1 - prior)
    return blocked / (blocked + open_) if blocked + open_ else prior


def _expectation(outcomes, model, blocked_probability):
    """Expected metrics and expected loss over the two road states."""
    open_metrics, blocked_metrics = outcomes["open"], outcomes["blocked"]
    expected_loss = (blocked_probability * _loss(blocked_metrics, model)
                     + (1 - blocked_probability) * _loss(open_metrics, model))
    expected = {
        key: round(blocked_probability * blocked_metrics[key] + (1 - blocked_probability) * open_metrics[key], 1)
        for key in METRICS
    }
    return expected, round(expected_loss, 2)


def _rank(items):
    items.sort(key=lambda item: item["expected_loss"])
    for index, item in enumerate(items, start=1):
        item["rank"] = index
    return items


def rank_actions(model, blocked_probability, inspected=False):
    """Actions ranked by expected loss; `inspected` uses the outcomes that start after the inspection delay."""
    ranked = []
    for action in model["actions"]:
        outcomes = action[INSPECTED if inspected else "outcomes"]
        expected, expected_loss = _expectation(outcomes, model, blocked_probability)
        ranked.append({
            "id": action["id"], "label": action["label"], "summary": action["summary"],
            "stakeholder_effect": action.get("stakeholder_effect", "No additional stakeholder effect recorded."),
            "expected": expected, "expected_loss": expected_loss,
            "outcomes": outcomes
        })
    return _rank(ranked)


def rank_assignments(model, blocked_probability, inspected=False):
    """Every enumerated truck-to-shipment assignment, ranked by expected loss."""
    ranked = []
    for option in model["assignments"]["options"]:
        option = dict(option)
        delayed = option.pop(INSPECTED)
        if inspected:
            option["outcomes"] = delayed
        expected, expected_loss = _expectation(option["outcomes"], model, blocked_probability)
        ranked.append({**option, "expected": expected, "expected_loss": expected_loss})
    return _rank(ranked)


def information_value(model, prior, likelihoods=None):
    """Expected value of one inspection when the road is blocked with probability `prior`.

    Uses the drone's assumed accuracy unless a P(signal | road state) table is given.
    """
    drone = model["drone"]
    likelihoods = likelihoods or likelihoods_from_accuracy(drone["accuracy"])
    best_now = rank_actions(model, prior)[0]["expected_loss"]
    signal_probability = {
        signal: likelihoods["blocked"][signal] * prior + likelihoods["open"][signal] * (1 - prior)
        for signal in SIGNALS
    }
    # Expected loss of the best action after seeing each possible signal. Every action starts after the
    # inspection delay, so the wait shows up as later arrivals and paid waiting time, not as a flat cost.
    responses = []
    for signal, probability in signal_probability.items():
        if not probability:
            continue
        posterior = posterior_blocked(prior, signal, likelihoods)
        best = rank_actions(model, posterior, True)[0]
        responses.append({
            "observation": signal, "probability": probability,
            "posterior": posterior, "action": best["id"], "label": best["label"],
            "expected_loss": best["expected_loss"], "basis": "estimated",
        })
    expected_loss_with_signal = sum(row["probability"] * row["expected_loss"] for row in responses)
    gross_value = best_now - expected_loss_with_signal
    inspection_cost = drone["direct_cost"]
    net_value = gross_value - inspection_cost
    return {
        "best_act_now_loss": best_now,
        "expected_loss_after_inspection": round(expected_loss_with_signal, 2),
        "gross_value_of_information": round(gross_value, 2),
        "inspection_cost": round(inspection_cost, 2),
        "net_value_of_information": round(net_value, 2),
        "recommend_inspection": net_value > 0,
        "response_by_observation": responses,
        "formula": "Net EVI = best expected loss now − expected loss after waiting for the observation − inspection cost",
        "assumptions": {
            "inspection_accuracy": drone["accuracy"],
            "accuracy_basis": drone.get("accuracy_basis", "assumed"),
            "signal_blocked_probability": round(signal_probability["blocked"], 4),
            "launch_and_review_minutes": drone["launch_and_review_minutes"]
        }
    }


def break_even_accuracy(model, prior):
    """Lowest inspection accuracy at which inspecting pays for itself, or None.

    Information value never falls as accuracy rises, so bisection is valid.
    """
    def pays(accuracy):
        return information_value(model, prior, likelihoods_from_accuracy(accuracy))["recommend_inspection"]

    if not pays(1.0):
        return None
    low, high = 0.5, 1.0
    for _ in range(20):
        middle = (low + high) / 2
        low, high = (low, middle) if pays(middle) else (middle, high)
    return round(high, 3)


def sensitivity(model, steps=21):
    """Net inspection value at evenly spaced blockage probabilities from 0 to 1."""
    return [
        {"prior": round(i / (steps - 1), 4), "net_value": information_value(model, i / (steps - 1))["net_value_of_information"]}
        for i in range(steps)
    ]


def analyze(scenario, observation=None):
    model = scenario["decision_model"]
    drone = model["drone"]
    prior = scenario["disruption"]["prior_blocked_probability"]
    posterior = posterior_blocked(prior, observation, drone["accuracy"])
    before = rank_actions(model, prior)
    after = rank_actions(model, posterior, True) if observation else []
    for option in before + after:
        # Fairness measure per response: schools within 300 m of the route it sends the affected load on.
        option["schools_within_300m"] = schools_near([action_route(scenario, option["id"])])
    evoi = information_value(model, prior)
    evoi["break_even_accuracy"] = break_even_accuracy(model, prior)
    # Automated CV alone, using the measured error table, for comparison with reviewed imagery.
    if "cv_only" in drone:
        cv_value = information_value(model, prior, drone["cv_only"]["likelihoods"])
        evoi["cv_only"] = {
            "basis": drone["cv_only"]["basis"],
            "net_value_of_information": cv_value["net_value_of_information"],
            "recommend_inspection": cv_value["recommend_inspection"],
        }
    return {
        "scenario_id": scenario.get("id"),
        "prior_blocked_probability": round(prior, 4),
        "posterior_blocked_probability": round(posterior, 4) if observation else None,
        "observation": observation,
        "web_evidence": scenario["disruption"].get("web_evidence"),
        "before": before,
        "after": after,
        "evoi": evoi,
        "sensitivity": sensitivity(model),
        "indicators": compute_indicators(scenario, before),
        "assignments": {
            **{key: model["assignments"][key] for key in ("shipments", "trucks", "count")},
            "before": rank_assignments(model, prior),
            "after": rank_assignments(model, posterior, True) if observation else [],
        },
    }
