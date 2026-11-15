"""Decision history: how past incident reports turned out, as a reference for the dispatcher.

Two sources feed it. `config/decision_history.json` holds synthetic past decisions written by
`scripts/build_decision_history.py`, which scores each one with the real engine. `outputs/decision-log.jsonl`
holds the decisions made in the app, saved at the end of the simulated morning.

For each decision a record keeps what the engine estimated when the decision was made and what then
happened. The history never changes the blockage chance: the Bayes update still uses only the web finding
and the drone observation. Synthetic records are not evidence.

A missing or malformed history file makes the feature report itself unavailable; nothing here raises to
the client.
"""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from backend.decision_engine import rank_actions
from backend.indicators import action_route, schools_near

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "config" / "decision_history.json"
LOG = ROOT / "outputs" / "decision-log.jsonl"
SCENARIO = ROOT / "config" / "port_scenario.json"

METRICS = ("lateness_min", "cost_usd", "co2_kg", "added_miles", "missed_appointments")
MEAN_METRICS = METRICS[:-1]
DIGITS = {"lateness_min": 1, "cost_usd": 2, "co2_kg": 1, "added_miles": 2, "missed_appointments": 2}
ROAD_STATES = ("open", "blocked")
OBSERVATIONS = ("blocked", "clear", "inconclusive")
CASE_KEYS = {"id", "date", "check_type", "variant", "estimated_bad_probability", "inspection", "action",
             "actual_road_state", "estimate", "actual"}
ACTION_PHRASES = {"hold-course": "keeping the route", "reroute": "rerouting", "swap-truck": "swapping trucks"}
TOOL_CASES = 8  # Most similar cases an LLM tool sees; the summary covers them all.

_lock = threading.Lock()


class Unavailable(Exception):
    """The history cannot be used; the message says why."""


def _unavailable(reason):
    return {"available": False, "reason": reason}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _valid_case(case):
    return (isinstance(case, dict) and CASE_KEYS <= set(case)
            and isinstance(case["estimate"], dict) and isinstance(case["actual"], dict)
            and set(METRICS) <= set(case["estimate"]) and set(METRICS) <= set(case["actual"])
            and isinstance(case["inspection"], dict) and case["actual_road_state"] in ROAD_STATES
            and isinstance(case["estimated_bad_probability"], (int, float)))


def load(path=None):
    """The committed history file. Raises Unavailable when it is missing or malformed."""
    path = Path(path or HISTORY)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Unavailable("no decision history file")
    except (OSError, UnicodeDecodeError, ValueError):
        raise Unavailable("the decision history file is malformed")
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list) \
            or not all(_valid_case(case) for case in data["cases"]):
        raise Unavailable("the decision history file is malformed")
    data.setdefault("patterns", [])
    return data


def read_log(path=None):
    """Decisions saved in the app. Unreadable lines are skipped."""
    path = Path(path or LOG)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    records = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if _valid_case(record):
            records.append(record)
    return records


def _check(scenario):
    return {"type": "crash", "label": "Drone check", "bad": "blocked", "good": "open",
            **(scenario["disruption"].get("check") or {})}


def current_chance(scenario):
    return scenario["disruption"]["prior_blocked_probability"]


def similar(scenario, history_path=None, log_path=None, data=None):
    """Committed and saved cases with the scenario's kind of check, most similar first.

    Closeness is the gap between a case's estimated chance of the bad road state and the scenario's chance
    now; ties go to the most recent case.
    """
    data = data or load(history_path)
    check_type = _check(scenario)["type"]
    now = current_chance(scenario)
    cases = [{**case, "source": "synthetic"} for case in data["cases"]]
    cases += [{**record, "source": "saved"} for record in read_log(log_path)]
    cases = [case for case in cases if case["check_type"] == check_type]
    cases.sort(key=lambda case: (case["date"], case.get("reported_at") or ""), reverse=True)
    cases.sort(key=lambda case: round(abs(case["estimated_bad_probability"] - now), 4))
    return cases


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def _mean(cases, part):
    if not cases:
        return None
    return {key: round(sum(case[part][key] for case in cases) / len(cases), DIGITS[key]) for key in MEAN_METRICS}


def _plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def summary_line(check, count, bad_count, by_action):
    if not count:
        return "No similar past reports."
    parts = []
    for entry in by_action:
        if not entry["count"]:
            continue
        phrase = ACTION_PHRASES.get(entry["action"], entry["label"].lower())
        actual = entry["actual"]
        part = (f"{phrase} ({_plural(entry['count'], 'case')}) averaged {actual['lateness_min']:.0f} min late "
                f"and {actual['co2_kg']:.0f} kg CO2")
        if entry["missed_appointments"]:
            part += f", with {_plural(entry['missed_appointments'], 'missed appointment')}"
        parts.append(part)
    responses = "; ".join(parts)
    return (f"Road was {check['bad']} in {bad_count} of {_plural(count, 'similar report')}. "
            f"{responses[:1].upper()}{responses[1:]}.")


def summary(scenario, cases):
    """Counts and per-response means over `cases`, with responses in the engine's current rank order."""
    check = _check(scenario)
    now = current_chance(scenario)
    ranked = rank_actions(scenario["decision_model"], now)
    by_action = []
    for option in ranked:
        mine = [case for case in cases if case["action"] == option["id"]]
        by_action.append({
            "action": option["id"], "label": option["label"], "count": len(mine),
            "bad_count": sum(case["actual_road_state"] == "blocked" for case in mine),
            "missed_appointments": int(sum(case["actual"]["missed_appointments"] for case in mine)),
            "recommended": option["rank"] == 1,
            "estimate": _mean(mine, "estimate"), "actual": _mean(mine, "actual"),
        })
    count = len(cases)
    bad_count = sum(case["actual_road_state"] == "blocked" for case in cases)
    flown = [case for case in cases if case["inspection"].get("flown")]
    return {
        "count": count, "bad_count": bad_count, "current_estimate": round(now, 4),
        "line": summary_line(check, count, bad_count, by_action),
        "by_action": by_action,
        "inspection": {"flown": len(flown),
                       "changed_response": sum(bool(case["inspection"].get("changed_response")) for case in flown)},
    }


def report(scenario, variant, history_path=None, log_path=None):
    """The GET /api/history body."""
    if scenario.get("scenario_type") != "disruption":
        return _unavailable("no incident to compare")
    try:
        data = load(history_path)
    except Unavailable as error:
        return _unavailable(str(error))
    cases = similar(scenario, log_path=log_path, data=data)
    cited = {pattern for case in cases for pattern in case.get("pattern_ids") or []}
    return {
        "available": True, "scenario_id": variant, "check": _check(scenario),
        "basis": data.get("basis", "synthetic"), "note": data.get("note"),
        "summary": summary(scenario, cases), "cases": cases,
        "patterns": [pattern for pattern in data["patterns"] if pattern.get("id") in cited],
    }


TOOL_CASE_FIELDS = ("id", "date", "reports", "web_finding", "inspection", "action_label", "actual_road_state",
                    "estimate", "actual", "lesson", "source")


def tool_result(found):
    """What an LLM tool sees: the summary and the most similar cases, from a `report` result."""
    if not found.get("available"):
        return {"available": False, "reason": found.get("reason")}
    summary_ = found["summary"]
    return {
        "basis": "synthetic",
        "note": "Synthetic past decisions, plus any saved from this app. A reference only; "
                "they do not change the blockage chance.",
        "check": found["check"],
        "summary": {key: summary_[key] for key in ("count", "bad_count", "line", "inspection")}
                   | {"by_action": [{key: entry[key] for key in ("action", "label", "count", "bad_count",
                                                                 "missed_appointments", "actual")}
                                    for entry in summary_["by_action"]]},
        "cases": [{key: case.get(key) for key in TOOL_CASE_FIELDS} for case in found["cases"][:TOOL_CASES]],
    }


# ---------------------------------------------------------------------------
# Building and saving a record
# ---------------------------------------------------------------------------
def metrics(outcome, drone_cost=0.0):
    """The record's figures for one computed outcome."""
    return {
        "lateness_min": round(outcome["lateness_min"], 1),
        "cost_usd": round(outcome["incremental_cost"] + drone_cost, 2),
        "co2_kg": round(outcome["emissions_kg"], 1),
        "added_miles": round(outcome["detail"]["added_miles"], 2),
        "missed_appointments": int(outcome["constraint_breaches"]),
    }


def expected_metrics(outcomes, probability, drone_cost=0.0):
    """Probability-weighted figures over the two road states, `probability` being the bad state's chance."""
    open_, blocked = metrics(outcomes["open"], drone_cost), metrics(outcomes["blocked"], drone_cost)
    return {key: round(probability * blocked[key] + (1 - probability) * open_[key], DIGITS[key]) for key in METRICS}


def _variant_of(scenario):
    try:
        variants = json.loads(SCENARIO.read_text(encoding="utf-8"))["scenario_variants"]
    except (OSError, ValueError, KeyError):
        return scenario.get("id")
    return next((key for key, value in variants.items() if value.get("id") == scenario.get("id")), scenario.get("id"))


def _incident(scenario):
    location_id = scenario["decision_model"].get("blockage", {}).get("location", "incident")
    return next((item for item in scenario["locations"] if item["id"] == location_id), {})


def _lesson(check, estimate, actual, road):
    state = check["bad"] if road == "blocked" else check["good"]
    text = (f"Estimated {estimate['lateness_min']:.0f} min late at ${estimate['cost_usd']:.0f}; "
            f"with the road {state} the simulated result was {actual['lateness_min']:.0f} min late "
            f"at ${actual['cost_usd']:.0f}")
    if actual["missed_appointments"]:
        text += f" and {_plural(actual['missed_appointments'], 'missed appointment')}"
    return text + "."


def decision_record(scenario, analysis, action, road, observation, web, override_reason, run_id, variant=None):
    """A record in the history's case shape, built from the live engine.

    `scenario` already carries any applied web finding and `analysis` is `analyze(scenario, observation)`.
    The estimate is the chosen response's probability-weighted figures when the decision was made (after the
    drone wait when it was flown); the actual figures are that response's outcome for `road`. `save` assigns
    the id.
    """
    if scenario.get("scenario_type") != "disruption":
        raise ValueError("a decision record needs an incident")
    if road not in ROAD_STATES:
        raise ValueError("road must be open or blocked")
    if observation not in (None, *OBSERVATIONS):
        raise ValueError("observation must be blocked, clear, inconclusive or null")
    if (analysis.get("web_evidence") or {}).get("finding") != web or analysis.get("observation") != observation:
        raise ValueError("the analysis does not match the web finding and observation")
    if override_reason is not None and not isinstance(override_reason, str):
        raise ValueError("override_reason must be text or null")
    flown = observation is not None
    options = analysis["after"] if flown else analysis["before"]
    chosen = next((option for option in options if option["id"] == action), None)
    if chosen is None:
        raise ValueError("action must be one of " + ", ".join(option["id"] for option in options))
    drone = scenario["decision_model"]["drone"]
    drone_cost = drone["direct_cost"] if flown else 0.0
    probability = analysis["posterior_blocked_probability"] if flown else analysis["prior_blocked_probability"]
    check = _check(scenario)
    disruption = scenario["disruption"]
    place = _incident(scenario)
    estimate = expected_metrics(chosen["outcomes"], probability, drone_cost)
    actual = metrics(chosen["outcomes"][road], drone_cost)
    schools = chosen.get("schools_within_300m")
    if schools is None:
        schools = schools_near([action_route(scenario, action)])
    reason = (override_reason or "").strip() or None
    return {
        "id": None, "run_id": run_id,
        "date": str(scenario.get("generated_at", ""))[:10], "reported_at": disruption.get("reported_at"),
        "check_type": check["type"], "variant": variant or _variant_of(scenario),
        "title": disruption.get("title"), "place": disruption.get("location_label") or place.get("name"),
        "lat": place.get("lat"), "lon": place.get("lon"),
        "reports": disruption.get("evidence_summary") or disruption.get("description"),
        "estimated_bad_probability": round(probability, 4),
        "web_finding": web,
        "inspection": {"flown": flown, "observation": observation,
                       "wait_min": drone["launch_and_review_minutes"] if flown else 0,
                       "changed_response": flown and options[0]["id"] != analysis["before"][0]["id"]},
        "action": action, "action_label": chosen["label"], "recommended_action": options[0]["id"],
        "override_reason": reason[:300] if reason else None,
        "actual_road_state": road,
        "estimate": estimate, "actual": actual, "actual_note": None,
        "schools_within_300m": schools,
        "lesson": _lesson(check, estimate, actual, road),
        "pattern_ids": [],
        "basis": "simulated",
        "basis_note": ("Estimate from the decision engine when the decision was made. Actual figures are the "
                       "engine's outcome for the road state the simulated playback showed, not a real trip."),
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _number(record_id):
    try:
        return int(str(record_id).removeprefix("SYN-LOG-"))
    except ValueError:
        return 0


def save(record, path=None):
    """Append `record` to the decision log, replacing an earlier record with the same run and variant.

    Assigns the record's id (SYN-LOG-<n>), keeping the replaced record's id.
    """
    path = Path(path or LOG)
    key = (record.get("run_id"), record.get("variant"))
    with _lock:
        records = read_log(path)
        earlier = next((item for item in records if (item.get("run_id"), item.get("variant")) == key), None)
        number = _number(earlier["id"]) if earlier else 1 + max((_number(item.get("id")) for item in records), default=0)
        record["id"] = f"SYN-LOG-{number}"
        kept = [item for item in records if (item.get("run_id"), item.get("variant")) != key]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text("".join(json.dumps(item) + "\n" for item in [*kept, record]), encoding="utf-8")
        temporary.replace(path)
    return record


# ---------------------------------------------------------------------------
# Compact block for the LLM facts
# ---------------------------------------------------------------------------
def facts_block(scenario, history_path=None, log_path=None):
    """Counts and per-response means for copilot.decision_facts. Never raises."""
    try:
        found = report(scenario, None, history_path, log_path)
    except (KeyError, TypeError, ValueError, OSError) as error:
        return {"available": False, "reason": str(error)}
    if not found.get("available"):
        return {"available": False, "reason": found.get("reason")}
    summary_ = found["summary"]
    return {
        "basis": "synthetic",
        "note": "Synthetic past decisions, a reference only. They do not change the blockage chance.",
        "count": summary_["count"], "bad_count": summary_["bad_count"], "line": summary_["line"],
        "by_action": [{key: entry[key] for key in ("action", "label", "count", "bad_count", "missed_appointments",
                                                   "estimate", "actual")}
                      for entry in summary_["by_action"]],
    }
