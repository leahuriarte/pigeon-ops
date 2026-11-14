"""Small, auditable expected-value-of-information decision engine."""
from backend.indicators import compute_indicators
from backend.outcomes import METRICS


def _loss(metrics, weights):
    return round(
        metrics["lateness_min"] * weights["lateness_cost_per_minute"]
        + metrics["incremental_cost"]
        + metrics["emissions_kg"] * weights["emissions_cost_per_kg"]
        + metrics["constraint_breaches"] * weights["hard_constraint_penalty"],
        2,
    )


def posterior_blocked(prior, observation, accuracy):
    """Bayesian posterior for an imperfect binary observation."""
    if observation not in ("blocked", "clear"):
        return prior
    if observation == "blocked":
        numerator = accuracy * prior
        denominator = numerator + (1 - accuracy) * (1 - prior)
    else:
        numerator = (1 - accuracy) * prior
        denominator = numerator + accuracy * (1 - prior)
    return numerator / denominator if denominator else prior


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


def rank_actions(model, blocked_probability):
    ranked = []
    for action in model["actions"]:
        expected, expected_loss = _expectation(action["outcomes"], model, blocked_probability)
        ranked.append({
            "id": action["id"], "label": action["label"], "summary": action["summary"],
            "stakeholder_effect": action.get("stakeholder_effect", "No additional stakeholder effect recorded."),
            "expected": expected, "expected_loss": expected_loss,
            "outcomes": action["outcomes"]
        })
    return _rank(ranked)


def rank_assignments(model, blocked_probability):
    """Every enumerated truck-to-shipment assignment, ranked by expected loss."""
    ranked = []
    for option in model["assignments"]["options"]:
        expected, expected_loss = _expectation(option["outcomes"], model, blocked_probability)
        ranked.append({**option, "expected": expected, "expected_loss": expected_loss})
    return _rank(ranked)


def information_value(model, prior):
    """Expected value of one imperfect inspection when the road is blocked with probability `prior`."""
    drone = model["drone"]
    accuracy = drone["accuracy"]
    best_now = rank_actions(model, prior)[0]["expected_loss"]

    # Expected decision loss after each possible imperfect signal.
    p_signal_blocked = accuracy * prior + (1 - accuracy) * (1 - prior)
    loss_after_blocked = rank_actions(model, posterior_blocked(prior, "blocked", accuracy))[0]["expected_loss"]
    loss_after_clear = rank_actions(model, posterior_blocked(prior, "clear", accuracy))[0]["expected_loss"]
    expected_loss_with_signal = p_signal_blocked * loss_after_blocked + (1 - p_signal_blocked) * loss_after_clear
    gross_value = best_now - expected_loss_with_signal
    inspection_cost = drone["direct_cost"] + drone["delay_cost"]
    net_value = gross_value - inspection_cost
    return {
        "best_act_now_loss": best_now,
        "expected_loss_after_inspection": round(expected_loss_with_signal, 2),
        "gross_value_of_information": round(gross_value, 2),
        "inspection_cost": round(inspection_cost, 2),
        "net_value_of_information": round(net_value, 2),
        "recommend_inspection": net_value > 0,
        "formula": "Net EVI = best expected loss now − expected loss with observation − inspection cost",
        "assumptions": {
            "inspection_accuracy": accuracy,
            "signal_blocked_probability": round(p_signal_blocked, 4),
            "launch_and_review_minutes": drone["launch_and_review_minutes"]
        }
    }


def sensitivity(model, steps=21):
    """Net inspection value at evenly spaced blockage probabilities from 0 to 1."""
    return [
        {"prior": round(i / (steps - 1), 4), "net_value": information_value(model, i / (steps - 1))["net_value_of_information"]}
        for i in range(steps)
    ]


def analyze(scenario, observation=None):
    model = scenario["decision_model"]
    prior = scenario["disruption"]["prior_blocked_probability"]
    posterior = posterior_blocked(prior, observation, model["drone"]["accuracy"])
    before = rank_actions(model, prior)
    return {
        "scenario_id": scenario.get("id"),
        "prior_blocked_probability": round(prior, 4),
        "posterior_blocked_probability": round(posterior, 4) if observation else None,
        "observation": observation,
        "before": before,
        "after": rank_actions(model, posterior) if observation else [],
        "evoi": information_value(model, prior),
        "sensitivity": sensitivity(model),
        "indicators": compute_indicators(scenario, before),
        "assignments": {
            **{key: model["assignments"][key] for key in ("shipments", "trucks", "count")},
            "before": rank_assignments(model, prior),
            "after": rank_assignments(model, posterior) if observation else [],
        },
    }
