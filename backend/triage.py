"""Web triage: an agent searches public feeds near an incident before the drone decision.

The agent (an LLM with search tools) reads the CHP incident log, Caltrans message signs and NWS alerts
around the reported spot and reports one finding: `corroborates`, `contradicts` or `nothing_found`.
Without a usable LLM reply a keyword rule gives the finding instead, labelled `agent: "rules"`.

Nothing changes until the dispatcher applies the finding. `apply_web_evidence` then updates the blockage
chance with Bayes' rule, using the assumed table `decision_model.web_triage.likelihoods`, and every later
analysis (including the drone's value) starts from the updated chance.

Modes: `live` reads the real feeds, which describe real roads now while the incident is fictional; if every
feed is unreachable it falls back to `demo`. `demo` uses the synthetic records in
`config/web_triage_fixtures.json`.
"""
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from backend import llm, web_feeds
from backend.copilot import (CONFIDENCE, _arguments, _call_name, _force, _model, _structured, _text, _tool,
                             ungrounded_numbers)
from backend.decision_engine import information_value, posterior_blocked

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "config" / "web_triage_fixtures.json"
FINDINGS = ("corroborates", "contradicts", "nothing_found")
MODES = ("live", "demo")
FEEDS = ("chp", "signs", "weather")
DEFAULT_RADIUS_KM = 2.0
MIN_RADIUS_KM = 0.5
MAX_ROUNDS = 5
MAX_ITEMS_PER_SEARCH = 8
CACHE_S = 60.0

# Words that tie a record to the bad or good road state, per kind of check (disruption.check.type).
BAD_WORDS = {
    "crash": ("collision", "overturn", "on its side", "blkg", "blocked", "blocking", "sigalert", "closed", "clsd"),
    "congestion": ("backed up", "queue", "delay", "stopped", "congestion", "slow traffic"),
    "environment": ("flood", "water", "sinkhole", "washout", "debris", "closed", "clsd"),
}
GOOD_WORDS = ("passing", "lanes open", "lns open", "reopened", "cleared", "clear of", "moving freely")

_cache = {}


def clear_cache():
    _cache.clear()


def _unavailable(reason):
    return {"available": False, "reason": reason}


def validate_finding(value):
    if value is not None and value not in FINDINGS:
        raise ValueError("web must be corroborates, contradicts or nothing_found")
    return value


def _check(scenario):
    return {"type": "crash", "label": "Drone check", "bad": "blocked", "good": "open",
            **(scenario["disruption"].get("check") or {})}


def incident_point(scenario):
    location_id = scenario["decision_model"].get("blockage", {}).get("location", "incident")
    place = next((item for item in scenario["locations"] if item["id"] == location_id), None)
    if place is None:
        raise ValueError("the scenario has no incident location")
    return place


# ---------------------------------------------------------------------------
# Applying a finding to the decision
# ---------------------------------------------------------------------------
def web_posterior(scenario, finding):
    table = scenario["decision_model"]["web_triage"]["likelihoods"]
    return posterior_blocked(scenario["disruption"]["prior_blocked_probability"], finding, table)


def apply_web_evidence(scenario, finding):
    """A copy of `scenario` whose blockage chance is updated by the dispatcher-applied web finding."""
    if validate_finding(finding) is None:
        return scenario
    if scenario.get("scenario_type") != "disruption":
        raise ValueError("web evidence applies only to an incident")
    updated = deepcopy(scenario)
    before = updated["disruption"]["prior_blocked_probability"]
    after = web_posterior(updated, finding)
    updated["disruption"]["prior_blocked_probability"] = after
    updated["disruption"]["web_evidence"] = {
        "finding": finding, "prior_before": round(before, 4), "prior_after": round(after, 4),
        "basis": updated["decision_model"]["web_triage"].get("basis", "assumed"),
    }
    return updated


def _inspection(model, prior):
    value = information_value(model, prior)
    return {"net_value": value["net_value_of_information"], "recommend": value["recommend_inspection"]}


def effect_of(scenario, finding):
    model = scenario["decision_model"]
    before = scenario["disruption"]["prior_blocked_probability"]
    after = web_posterior(scenario, finding)
    return {
        "finding": finding, "prior_before": round(before, 4), "prior_after": round(after, 4),
        "inspection_before": _inspection(model, before), "inspection_after": _inspection(model, after),
        "likelihoods_basis": model["web_triage"].get("basis", "assumed"),
    }


# ---------------------------------------------------------------------------
# Gathering the feeds
# ---------------------------------------------------------------------------
def _live_sources(point):
    calls = {
        "chp": lambda: web_feeds.chp_near(point),
        "signs": lambda: web_feeds.signs_near(point),
        "weather": lambda: web_feeds.alerts_at(point),
    }
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = {feed: pool.submit(call) for feed, call in calls.items()}
        return {feed: future.result() for feed, future in futures.items()}


def _demo_sources(variant, point, fixtures_path=FIXTURES):
    data = json.loads(Path(fixtures_path).read_text(encoding="utf-8"))
    names = {"chp": "CHP incident log", "signs": "Caltrans message signs", "weather": "NWS alerts"}
    sources = {feed: {"source": f"{names[feed]} (synthetic demo feed)", "feed_url": str(fixtures_path.name),
                      "status": "ok", "items": []} for feed in FEEDS}
    for record in data["variants"].get(variant, []):
        item = {key: value for key, value in record.items() if key != "feed"}
        has_place = item.get("lat") is not None and item.get("lon") is not None
        item["distance_km"] = round(web_feeds.distance_km(point, (item["lat"], item["lon"])), 1) if has_place else None
        item["basis"] = "synthetic"
        sources[record["feed"]]["items"].append(item)
    return sources


def gather(variant, point, mode, live_fn=None):
    """(sources by feed, mode used, fallback reason or None)."""
    if mode == "demo":
        return _demo_sources(variant, point), "demo", None
    sources = (live_fn or _live_sources)(point)
    if all(source["status"] != "ok" for source in sources.values()):
        reasons = "; ".join(f"{source['source']}: {source['status']}" for source in sources.values())
        return _demo_sources(variant, point), "demo", f"every live feed failed ({reasons})"
    return sources, "live", None


def _within(items, radius_km):
    return [item for item in items if item["distance_km"] is None or item["distance_km"] <= radius_km]


# ---------------------------------------------------------------------------
# Rule-based finding (no LLM)
# ---------------------------------------------------------------------------
def _matches(item, words):
    text = f"{item['title']} {item['detail']}".lower()
    return any(word in text for word in words)


def rule_finding(sources, check):
    """Records within DEFAULT_RADIUS_KM that mention the check's bad state decide; a good-state word wins."""
    near = [item for feed in ("chp", "signs") for item in _within(sources[feed]["items"], DEFAULT_RADIUS_KM)
            if item["distance_km"] is not None]
    relevant = [item for item in near if _matches(item, BAD_WORDS.get(check["type"], BAD_WORDS["crash"]))]
    good = [item for item in relevant if _matches(item, GOOD_WORDS)]
    bad = [item for item in relevant if item not in good]
    searches = [{"tool": tool, "radius_km": DEFAULT_RADIUS_KM, "status": sources[feed]["status"],
                 "found": len(_within(sources[feed]["items"], DEFAULT_RADIUS_KM))}
                for tool, feed in (("search_chp_incidents", "chp"), ("read_message_signs", "signs"))]
    if good:
        finding, cited = "contradicts", good
        summary = (f"{good[0]['source']} {good[0]['id']} mentions the hazard but also traffic getting through, "
                   f"which points to the road being {check['good']}.")
    elif bad:
        finding, cited = "corroborates", bad
        summary = (f"{len(bad)} record(s) within {DEFAULT_RADIUS_KM:g} km mention the hazard, "
                   f"starting with {bad[0]['source']} {bad[0]['id']}: {bad[0]['title']}.")
    else:
        finding, cited = "nothing_found", []
        summary = (f"No CHP log or message sign within {DEFAULT_RADIUS_KM:g} km mentions the hazard. "
                   "A fresh incident may not be logged yet, so this is weak evidence.")
    return {"finding": finding, "confidence": "low", "summary": summary,
            "cited_ids": [item["id"] for item in cited], "searches": searches}


# ---------------------------------------------------------------------------
# LLM agent
# ---------------------------------------------------------------------------
TRIAGE_SYSTEM = (
    "You triage a road incident report for a port dispatcher before they decide whether to fly a drone to "
    "check it. Search the public feeds with the tools: the CHP incident log, Caltrans message signs and NWS "
    "weather alerts near the reported spot. Then call report_triage once. Set finding to 'corroborates' if a "
    "record near the spot shows the road in the bad state the check names, 'contradicts' if a record shows "
    "the good state (for example traffic getting through, lanes reopened, the cause cleared), and "
    "'nothing_found' if nothing relevant turned up. A missing report is weak evidence: a fresh incident may "
    "not be logged yet. Records more than about 2 km away or on another road rarely speak to this spot. "
    "Cite the ids of the records you relied on. Use only figures that appear in the records or the incident. "
    "Write the summary in at most three short, plain sentences. The dispatcher decides whether to use it."
)
RADIUS = {"type": "number", "minimum": MIN_RADIUS_KM, "maximum": web_feeds.MAX_RADIUS_KM,
          "description": f"Search radius in km around the reported spot (default {DEFAULT_RADIUS_KM:g})."}
SEARCH_TOOLS = [
    _tool("search_chp_incidents", "Open CHP incident logs near the reported spot from the last few hours.",
          {"radius_km": RADIUS}, required=[]),
    _tool("read_message_signs", "Caltrans changeable message signs showing a message near the reported spot.",
          {"radius_km": RADIUS}, required=[]),
    _tool("weather_alerts", "Active National Weather Service alerts covering the reported spot.", {}),
]
TOOL_FEEDS = {"search_chp_incidents": "chp", "read_message_signs": "signs", "weather_alerts": "weather"}


def _report_tool():
    return _tool("report_triage", "Report what the public feeds say about the incident.", {
        "finding": {"type": "string", "enum": list(FINDINGS)},
        "confidence": {"type": "string", "enum": list(CONFIDENCE)},
        "summary": {"type": "string", "description": "At most three short sentences."},
        "cited_ids": {"type": "array", "items": {"type": "string"}, "description": "Ids of the records relied on."},
    })


def _radius(arguments):
    value = arguments.get("radius_km", DEFAULT_RADIUS_KM)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("radius_km must be a finite number")
    return min(max(float(value), MIN_RADIUS_KM), web_feeds.MAX_RADIUS_KM)


def run_search(name, arguments, sources, seen, searches):
    """Execute one search tool against the gathered feeds and record it."""
    feed = TOOL_FEEDS.get(name)
    if feed is None:
        return {"error": f"unknown tool {name}"}
    try:
        radius = None if feed == "weather" else _radius(arguments)
    except ValueError as error:
        return {"error": str(error)}
    source = sources[feed]
    items = source["items"] if radius is None else _within(source["items"], radius)
    shown = items[:MAX_ITEMS_PER_SEARCH]
    seen.update((item["id"], item) for item in shown)
    searches.append({"tool": name, "radius_km": radius, "status": source["status"], "found": len(items)})
    return {"source": source["source"], "status": source["status"], "radius_km": radius,
            "found": len(items), "records": [{key: item[key] for key in ("id", "title", "detail", "time", "distance_km")}
                                             for item in shown]}


def _validate_report(reply, seen, incident, results):
    finding, confidence = reply.get("finding"), reply.get("confidence")
    summary, cited = reply.get("summary"), reply.get("cited_ids", [])
    if finding not in FINDINGS or confidence not in CONFIDENCE or not _text(summary):
        raise ValueError("malformed triage report")
    if not isinstance(cited, list) or not all(isinstance(item, str) for item in cited):
        raise ValueError("cited_ids must be a list of record ids")
    cited = list(dict.fromkeys(item.strip() for item in cited))
    if any(item not in seen for item in cited):
        raise ValueError("cited a record the searches did not return")
    if finding != "nothing_found" and not cited:
        raise ValueError("a corroborating or contradicting finding must cite a record")
    if ungrounded_numbers(summary, incident, results):
        raise ValueError("ungrounded numbers")
    return {"finding": finding, "confidence": confidence, "summary": summary.strip(), "cited_ids": cited}


def llm_triage(incident, sources, chat_fn=None):
    """Run the search-then-report loop. Raises LLMUnavailable or ValueError when no usable report comes back."""
    chat_fn = chat_fn or llm.chat
    report = _report_tool()
    messages = [{"role": "system", "content": TRIAGE_SYSTEM},
                {"role": "user", "content": "Incident report, as JSON:\n" + json.dumps(incident, indent=1)}]
    seen, searches, results = {}, [], []
    for round_ in range(MAX_ROUNDS + 1):
        last = round_ == MAX_ROUNDS
        message = chat_fn(messages, tools=[report] if last else [*SEARCH_TOOLS, report],
                          tool_choice=_force("report_triage") if last else None)
        calls = [call for call in message.get("tool_calls") or [] if isinstance(call, dict)]
        names = [_call_name(call) for call in calls]
        if "report_triage" in names or not calls:
            if not searches:
                raise ValueError("reported without searching any feed")
            reply = _structured(message, "report_triage")
            return {**_validate_report(reply, seen, incident, results), "searches": searches}
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        for index, (call, name) in enumerate(zip(calls, names)):
            try:
                result = run_search(name, _arguments(call), sources, seen, searches)
            except ValueError as error:
                result = {"error": str(error)}
            results.append(result)
            messages.append({"role": "tool", "tool_call_id": call.get("id") or f"call_{index}",
                             "name": name, "content": json.dumps(result)})
    raise ValueError("no report")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def triage(scenario, variant, mode="live", chat_fn=None, live_fn=None):
    if mode not in MODES:
        raise ValueError("mode must be live or demo")
    if scenario.get("scenario_type") != "disruption":
        return _unavailable("no incident to triage")
    key = (variant, mode)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_S and chat_fn is None and live_fn is None:
        return hit[1]

    place = incident_point(scenario)
    point = (place["lat"], place["lon"])
    check = _check(scenario)
    sources, mode_used, fallback = gather(variant, point, mode, live_fn)
    disruption = scenario["disruption"]
    incident = {
        "title": disruption.get("title"), "description": disruption.get("description"),
        "reported_at": disruption.get("reported_at"),
        "kind_of_check": {"label": check["label"], "bad_state": f"road {check['bad']}",
                          "good_state": f"road {check['good']}"},
        "reported_spot": {"name": "CA-47 connector", "lat": place["lat"], "lon": place["lon"]},
        "feeds": ("synthetic demo records written to match this incident" if mode_used == "demo" else
                  "live public feeds describing real roads right now; the incident itself is a fictional scenario"),
    }

    agent, agent_note = "llm", None
    try:
        report = llm_triage(incident, sources, chat_fn)
    except llm.LLMUnavailable as error:
        agent, agent_note, report = "rules", f"LLM unavailable: {error.reason}", rule_finding(sources, check)
    except (ValueError, TypeError, KeyError, AttributeError) as error:  # Any malformed reply falls back to rules.
        agent, agent_note, report = "rules", f"LLM reply rejected: {error}", rule_finding(sources, check)

    cited = set(report["cited_ids"])
    evidence = sorted(
        ({**item, "cited": item["id"] in cited} for source in sources.values() for item in source["items"]),
        key=lambda item: (not item["cited"], item["distance_km"] is None, item["distance_km"] or 0))
    result = {
        "available": True, "scenario_id": variant, "incident_id": disruption.get("id"),
        "mode_requested": mode, "mode": mode_used, "fallback_reason": fallback,
        "agent": agent, "agent_note": agent_note,
        "basis": llm.BASIS if agent == "llm" else "rule_based", "model": _model() if agent == "llm" else None,
        "finding": report["finding"], "confidence": report["confidence"], "summary": report["summary"],
        "cited_ids": report["cited_ids"], "searches": report["searches"],
        "sources": [{"feed": feed, "source": source["source"], "status": source["status"],
                     "feed_url": source["feed_url"], "count": len(source["items"])}
                    for feed, source in sources.items()],
        "evidence": evidence[:12],
        "effect": effect_of(scenario, report["finding"]),
        "check": check,
        "point": {"lat": place["lat"], "lon": place["lon"], "name": place.get("name")},
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if chat_fn is None and live_fn is None:
        _cache[key] = (time.monotonic(), result)
    return result
