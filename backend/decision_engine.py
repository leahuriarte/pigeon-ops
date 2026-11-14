"""Small, auditable expected-value-of-information decision engine."""


def _loss(metrics, weights):
    return round(
        metrics["lateness_min"] * weights["lateness_cost_per_minute"]
        + metrics["incremental_cost"]
        + metrics["emissions_kg"] * weights["emissions_cost_per_kg"]
        + metrics["constraint_breaches"] * weights["hard_constraint_penalty"],
        2,
    )


SIGNALS = ("blocked", "clear", "inconclusive")


def symmetric_likelihoods(accuracy):
    """P(signal | road state) for a binary observation that is never inconclusive."""
    return {
        "blocked": {"blocked": accuracy, "clear": 1 - accuracy, "inconclusive": 0.0},
        "open": {"blocked": 1 - accuracy, "clear": accuracy, "inconclusive": 0.0},
    }


def observation_likelihoods(drone):
    """P(signal | road state), composed from measured CV error and an assumed traffic model.

    P(signal | road) = sum over visual states v of P(signal | v) * P(v | road).
    Falls back to a single symmetric accuracy when no observation model is configured.
    """
    model = drone.get("observation_model")
    if not model:
        return symmetric_likelihoods(drone["accuracy"])
    measured = model["signal_given_visual_state"]
    return {
        road: {
            signal: sum(measured[visual][signal] * share for visual, share in visual_shares.items())
            for signal in SIGNALS
        }
        for road, visual_shares in model["visual_state_given_road"].items()
    }


def posterior_blocked(prior, observation, likelihoods):
    """Bayesian posterior for an imperfect observation.

    likelihoods is a P(signal | road state) table or, for the binary case, an accuracy.
    """
    if isinstance(likelihoods, (int, float)):
        likelihoods = symmetric_likelihoods(likelihoods)
    if observation not in SIGNALS:
        return prior
    blocked = likelihoods["blocked"][observation] * prior
    open_ = likelihoods["open"][observation] * (1 - prior)
    return blocked / (blocked + open_) if blocked + open_ else prior


def rank_actions(model, blocked_probability):
    ranked = []
    for action in model["actions"]:
        open_metrics = action["outcomes"]["open"]
        blocked_metrics = action["outcomes"]["blocked"]
        open_loss = _loss(open_metrics, model)
        blocked_loss = _loss(blocked_metrics, model)
        expected_loss = blocked_probability * blocked_loss + (1 - blocked_probability) * open_loss
        expected = {
            key: round(blocked_probability * blocked_metrics[key] + (1 - blocked_probability) * open_metrics[key], 1)
            for key in ("lateness_min", "incremental_cost", "emissions_kg", "constraint_breaches")
        }
        ranked.append({
            "id": action["id"], "label": action["label"], "summary": action["summary"],
            "stakeholder_effect": action.get("stakeholder_effect", "No additional stakeholder effect recorded."),
            "expected": expected, "expected_loss": round(expected_loss, 2),
            "outcomes": action["outcomes"]
        })
    ranked.sort(key=lambda item: item["expected_loss"])
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    return ranked


def _loss_with_signal(model, prior, likelihoods):
    """Signal probabilities and the expected best-action loss after observing one."""
    signal_probability = {
        signal: likelihoods["blocked"][signal] * prior + likelihoods["open"][signal] * (1 - prior)
        for signal in SIGNALS
    }
    expected_loss = sum(
        probability * rank_actions(model, posterior_blocked(prior, signal, likelihoods))[0]["expected_loss"]
        for signal, probability in signal_probability.items() if probability
    )
    return signal_probability, expected_loss


def break_even_accuracy(model, prior, inspection_cost, act_now_loss):
    """Lowest symmetric accuracy at which inspecting pays for itself, or None.

    A more accurate signal is never worth less, so bisection is valid.
    """
    def net_value(accuracy):
        return act_now_loss - _loss_with_signal(model, prior, symmetric_likelihoods(accuracy))[1] - inspection_cost

    low, high = 0.5, 1.0
    if net_value(high) <= 0:
        return None
    for _ in range(30):
        middle = (low + high) / 2
        low, high = (low, middle) if net_value(middle) > 0 else (middle, high)
    return round(high, 3)


def analyze(scenario, observation=None):
    disruption = scenario["disruption"]
    model = scenario["decision_model"]
    drone = model["drone"]
    prior = disruption["prior_blocked_probability"]
    before = rank_actions(model, prior)

    likelihoods = observation_likelihoods(drone)
    signal_probability, expected_loss_with_signal = _loss_with_signal(model, prior, likelihoods)
    gross_value = before[0]["expected_loss"] - expected_loss_with_signal
    inspection_cost = drone["direct_cost"] + drone["delay_cost"]
    net_value = gross_value - inspection_cost
    cv_only = None
    if drone.get("cv_only_model"):
        cv_likelihoods = observation_likelihoods({"observation_model": drone["cv_only_model"]})
        cv_net = before[0]["expected_loss"] - _loss_with_signal(model, prior, cv_likelihoods)[1] - inspection_cost
        cv_only = {
            "source": drone["cv_only_model"]["source"],
            "net_value_of_information": round(cv_net, 2),
            "recommend_inspection": cv_net > 0,
            "detects_blockage_probability": round(cv_likelihoods["blocked"]["blocked"], 4),
            "missed_blockage_probability": round(cv_likelihoods["blocked"]["clear"], 4),
            "false_alarm_probability": round(cv_likelihoods["open"]["blocked"], 4),
        }

    posterior = posterior_blocked(prior, observation, likelihoods)
    after = rank_actions(model, posterior) if observation else []
    return {
        "prior_blocked_probability": round(prior, 4),
        "posterior_blocked_probability": round(posterior, 4) if observation else None,
        "observation": observation,
        "before": before,
        "after": after,
        "evoi": {
            "best_act_now_loss": before[0]["expected_loss"],
            "expected_loss_after_inspection": round(expected_loss_with_signal, 2),
            "gross_value_of_information": round(gross_value, 2),
            "inspection_cost": round(inspection_cost, 2),
            "net_value_of_information": round(net_value, 2),
            "recommend_inspection": net_value > 0,
            "formula": "Net EVI = best expected loss now − expected loss with observation − inspection cost",
            "break_even_accuracy": break_even_accuracy(model, prior, inspection_cost, before[0]["expected_loss"]),
            "cv_only": cv_only,
            "assumptions": {
                "observation_source": drone.get("observation_model", {}).get(
                    "source", drone.get("accuracy_basis", "Assumed symmetric accuracy")),
                "detects_blockage_probability": round(likelihoods["blocked"]["blocked"], 4),
                "false_alarm_probability": round(likelihoods["open"]["blocked"], 4),
                "missed_blockage_probability": round(likelihoods["blocked"]["clear"], 4),
                "signal_probability": {signal: round(value, 4) for signal, value in signal_probability.items()},
                "signal_blocked_probability": round(signal_probability["blocked"], 4),
                "launch_and_review_minutes": drone["launch_and_review_minutes"]
            }
        }
    }
