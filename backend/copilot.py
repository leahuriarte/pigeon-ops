"""LLM roles around the decision engine: a second read of drone footage, a dispatcher brief, and Q&A.

The engine stays the source of every number and every ranking. The LLM only explains, cross-checks
and answers questions, and everything it writes is labelled `basis: "llm_generated"`. Each role
fails to `{"available": False, "reason": ...}` instead of raising, so the page works without it.

Guards:
- the brief must name the engine's rank-1 option and, before an inspection, its inspection advice;
- `ungrounded_numbers` rejects a footage opinion or brief that quotes a figure not in its inputs;
  the Q&A answer is returned with `grounded: false` instead, and the page flags it.
"""
import base64
import json
import re
from pathlib import Path

from backend import llm
from backend.decision_engine import information_value, rank_actions

ROOT = Path(__file__).resolve().parents[1]
REVIEW_LIBRARY = ROOT / "cv" / "review_library"
BASIS = llm.BASIS
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
ROAD_STATES = ("open", "blocked")
CONFIDENCE = ("low", "medium", "high")
MAX_ASK_ROUNDS = 4

_footage_cache = {}
_brief_cache = {}


def clear_caches():
    _footage_cache.clear()
    _brief_cache.clear()


def _unavailable(reason):
    return {"available": False, "reason": reason}


# ---------------------------------------------------------------------------
# Grounding: every figure in LLM text must appear in what it was given.
# ---------------------------------------------------------------------------
def _number_tokens(text):
    for token in NUMBER.findall(text):
        token = token.rstrip(",")
        yield token, float(token.replace(",", ""))


def _allowed_numbers(sources):
    allowed = set()

    def walk(value):
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            for digits in (0, 1, 2):
                rounded = round(float(value), digits)
                allowed.update((rounded, abs(rounded)))
            if 0 <= value <= 1:
                allowed.add(float(round(value * 100)))
        elif isinstance(value, str):
            allowed.update(number for _, number in _number_tokens(value))
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(key)
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    for source in sources:
        walk(source)
    return allowed


def ungrounded_numbers(text, *sources):
    """Number tokens in `text` that do not appear (after rounding) anywhere in `sources`."""
    allowed = _allowed_numbers(sources)
    missing = []
    for token, number in _number_tokens(text or ""):
        if number not in allowed and token not in missing:
            missing.append(token)
    return missing


# ---------------------------------------------------------------------------
# Facts: the only thing the LLM is told about the decision.
# ---------------------------------------------------------------------------
def payoff_windows(points):
    """Ranges of blockage probability where inspecting has positive net value (as insight.js)."""
    windows, start = [], None

    def crossing(a, b):
        return a["prior"] + (0 - a["net_value"]) / (b["net_value"] - a["net_value"]) * (b["prior"] - a["prior"])

    for i, point in enumerate(points):
        previous = points[i - 1] if i else None
        if point["net_value"] > 0 and start is None:
            start = crossing(previous, point) if previous else point["prior"]
        if point["net_value"] <= 0 and start is not None:
            windows.append((start, crossing(previous, point)))
            start = None
    if start is not None:
        windows.append((start, points[-1]["prior"]))
    return windows


def _pct(probability):
    return None if probability is None else round(probability * 100)


def _usd(value):
    return None if value is None else round(value)


def _road_state(metrics):
    return {
        "lateness_min": round(metrics["lateness_min"], 1),
        "missed_appointments": int(metrics["constraint_breaches"]),
        "incremental_cost_usd": _usd(metrics["incremental_cost"]),
        "co2_kg": round(metrics["emissions_kg"], 1),
    }


def decision_facts(scenario, analysis):
    disruption = scenario.get("disruption", {})
    evoi = analysis["evoi"]
    observation = analysis.get("observation")
    before_pct = _pct(analysis["prior_blocked_probability"])
    now_pct = _pct(analysis["posterior_blocked_probability"]) if observation else before_pct
    options = analysis["after"] if observation else analysis["before"]
    best_loss = options[0]["expected_loss"]
    ranked = []
    for option in options:
        open_, blocked = _road_state(option["outcomes"]["open"]), _road_state(option["outcomes"]["blocked"])
        ranked.append({
            "rank": option["rank"], "id": option["id"], "label": option["label"],
            "expected_loss_usd": _usd(option["expected_loss"]),
            "gap_to_best_usd": _usd(option["expected_loss"] - best_loss),
            "stakeholder_effect": option.get("stakeholder_effect"),
            "if_road_open": open_, "if_road_blocked": blocked,
            "extra_lateness_if_blocked_min": round(blocked["lateness_min"] - open_["lateness_min"], 1),
        })
    affected = disruption.get("affected_shipment")
    cv_only = evoi.get("cv_only") or {}
    return {
        "note": "All figures come from the PigeonOps decision engine and are estimated from synthetic data.",
        "incident": {
            "title": disruption.get("title"), "description": disruption.get("description"),
            "affected_shipments": affected if isinstance(affected, list) else [affected] if affected else [],
        },
        "blockage_chance": {"before_pct": before_pct, "now_pct": now_pct, "change_points": now_pct - before_pct},
        "observation": observation,
        "web_triage_applied": analysis.get("web_evidence"),
        "inspection": {
            "recommended": evoi["recommend_inspection"],
            "gross_saving_usd": _usd(evoi["gross_value_of_information"]),
            "cost_usd": _usd(evoi["inspection_cost"]),
            "net_value_usd": _usd(evoi["net_value_of_information"]),
            "wait_min": evoi["assumptions"]["launch_and_review_minutes"],
            "break_even_accuracy_pct": _pct(evoi.get("break_even_accuracy")),
            "assumed_accuracy_pct": _pct(evoi["assumptions"]["inspection_accuracy"]),
            "accuracy_basis": evoi["assumptions"].get("accuracy_basis"),
            "cv_only_net_value_usd": _usd(cv_only.get("net_value_of_information")),
            "pays_off_when_blockage_chance_between_pct": [
                [_pct(low), _pct(high)] for low, high in payoff_windows(analysis["sensitivity"])],
        },
        "ranking_basis": ("after the drone check, every response starts after the inspection wait"
                          if observation else "before any inspection, acting now"),
        "ranked_options": ranked,
        "indicators": [
            {"label": item["label"], "value": item["value"], "unit": item["unit"], "basis": item["basis"]}
            for item in analysis.get("indicators", [])
        ],
    }


# ---------------------------------------------------------------------------
# Structured replies
# ---------------------------------------------------------------------------
def _tool(name, description, properties, required=None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": list(properties) if required is None else required},
    }}


def _force(name):
    return {"type": "function", "function": {"name": name}}


def _json_from_text(text):
    if not isinstance(text, str):
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _function(call):
    """The `function` part of a tool call, or {} when the model sent something other than an object."""
    function = call.get("function")
    return function if isinstance(function, dict) else {}


def _call_name(call):
    name = _function(call).get("name")
    return name if isinstance(name, str) else ""


def _arguments(call):
    raw = _function(call).get("arguments")
    if isinstance(raw, dict):
        return raw
    if raw in (None, ""):
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError("arguments are not valid JSON")
    if not isinstance(value, dict):
        raise ValueError("arguments must be a JSON object")
    return value


def _structured(message, name):
    """The arguments of the `name` tool call, or a JSON object in the reply text."""
    for call in message.get("tool_calls") or []:
        if isinstance(call, dict) and _call_name(call) == name:
            try:
                return _arguments(call)
            except ValueError:
                break
    found = _json_from_text(message.get("content"))
    if found is None:
        raise llm.LLMUnavailable("no structured reply")
    return found


def _text(value, limit=600):
    return isinstance(value, str) and 0 < len(value.strip()) <= limit


def _model():
    return llm.config()["model"]


# ---------------------------------------------------------------------------
# Role 1: second read of the drone footage
# ---------------------------------------------------------------------------
FOOTAGE_SYSTEM = (
    "You are a second reader for a port dispatcher who is reviewing a drone clip of a road. "
    "A computer-vision (CV) rule has suggested a road state. The rule is unvalidated and only measures "
    "vehicle motion on the marked road. Say whether the motion evidence and the scene note support the CV "
    "suggestion. Stopped vehicles are not always a blockage: they may be parked trucks, or a queue whose "
    "cause is not visible. Traffic passing in one frame does not prove the road beyond it is clear. Choose "
    "inconclusive when the evidence does not settle it. Be specific about what the dispatcher should look at "
    "in the clip before confirming. The evidence may name the kind of check: the observation 'blocked' then "
    "stands for the bad state it names (in a traffic check, a standing queue is that bad state) and 'clear' "
    "for the good one. Do not invent figures; use only numbers that appear in the evidence. "
    "Reply by calling report_road_state."
)
CV_FIELDS = ("motion_state", "suggested_observation", "reason", "vehicles_on_road", "stopped", "slow",
             "moving", "stopped_share", "moving_share", "samples", "window_seconds", "rule", "basis")


def _footage_evidence(case, check=None):
    cv = case.get("cv_assessment") or {}
    asked = {} if not check else {"kind_of_check": {
        "label": check.get("label"), "blocked_means": f"road {check.get('bad')}",
        "clear_means": f"road {check.get('good')}"}}
    return {
        **asked,
        "clip": case.get("title"),
        "cv_assessment": {key: cv[key] for key in CV_FIELDS if key in cv},
        "vehicle_detections_on_marked_road": case.get("vehicle_detections"),
        "excluded_detections_off_road": case.get("excluded_detections"),
        "image_quality": case.get("image_quality"),
        "provisional_keyframe_annotation": case.get("review_note"),
        "allowed_observations": case.get("allowed_observations"),
    }


def _source_image(case):
    """The case's unannotated source frame as a data URI, or None. Stays inside the review library."""
    relative = str(case.get("source_image", "")).removeprefix("/inspection-assets/")
    candidate = (REVIEW_LIBRARY / relative).resolve()
    if REVIEW_LIBRARY.resolve() not in candidate.parents or not candidate.is_file():
        return None
    return "data:image/jpeg;base64," + base64.b64encode(candidate.read_bytes()).decode("ascii")


def footage_opinion(case, chat_fn=None, check=None):
    chat_fn = chat_fn or llm.chat
    case_id = case.get("id")
    allowed = list(case.get("allowed_observations") or [])
    suggestion = (case.get("cv_assessment") or {}).get("suggested_observation")
    tool = _tool("report_road_state", "Report your second read of the road state.", {
        "observation": {"type": "string", "enum": allowed},
        "confidence": {"type": "string", "enum": list(CONFIDENCE)},
        "rationale": {"type": "string", "description": "At most two sentences."},
        "check_before_confirming": {"type": "array", "items": {"type": "string"}, "maxItems": 3,
                                    "description": "Up to three short things to look at in the clip."},
    })
    text = "Evidence for this clip, as JSON:\n" + json.dumps(_footage_evidence(case, check), indent=1)
    image = _source_image(case) if llm.config()["vision"] else None
    used_image = image is not None

    def ask(content):
        return chat_fn([{"role": "system", "content": FOOTAGE_SYSTEM}, {"role": "user", "content": content}],
                       tools=[tool], tool_choice=_force("report_road_state"))

    try:
        try:
            message = ask([{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": image}}]
                          if used_image else text)
        except llm.LLMUnavailable:
            if not used_image:
                raise
            used_image = False  # Image input is untested on the hackathon model: fall back to text.
            message = ask(text)
        reply = _structured(message, "report_road_state")
    except llm.LLMUnavailable as error:
        return _unavailable(error.reason)

    observation, confidence = reply.get("observation"), reply.get("confidence")
    rationale, checks = reply.get("rationale"), reply.get("check_before_confirming", [])
    if observation not in allowed:
        return _unavailable("observation outside the clip's allowed observations")
    if confidence not in CONFIDENCE or not _text(rationale):
        return _unavailable("malformed second read")
    if not isinstance(checks, list) or not all(_text(item, 200) for item in checks):
        return _unavailable("malformed checklist")
    checks = [item.strip() for item in checks[:3]]
    rationale = rationale.strip()
    if ungrounded_numbers(" ".join([rationale, *checks]), case, check):
        return _unavailable("ungrounded numbers")
    result = {
        "available": True, "basis": BASIS, "model": _model(), "case_id": case_id,
        "observation": observation, "confidence": confidence, "rationale": rationale, "checks": checks,
        "cv_suggestion": suggestion, "agrees_with_cv": observation == suggestion, "used_image": used_image,
    }
    _footage_cache[(case_id, (check or {}).get("type"))] = result
    return result


def cached_footage_opinion(case_id, check_type=None):
    """The stored second read for a clip under one kind of check (`disruption.check.type`)."""
    return _footage_cache.get((case_id, check_type))


# ---------------------------------------------------------------------------
# Role 2: dispatcher brief
# ---------------------------------------------------------------------------
BRIEF_SYSTEM = (
    "You write a short brief for a port dispatcher under time pressure. Use plain words and short sentences. "
    "The user message is a JSON object of facts from the PigeonOps decision engine. Use only figures that "
    "appear verbatim in those facts and do no arithmetic. Recommend only the option the facts rank first "
    "(rank 1), and set inspect_first to the facts' inspection.recommended value. Make clear it is advice and "
    "the dispatcher decides. Reply by calling write_brief."
)
BRIEF_FIELDS = ("headline", "why", "what_would_change_it", "who_is_affected")


def decision_brief(scenario, analysis, chat_fn=None):
    chat_fn = chat_fn or llm.chat
    key = (scenario.get("id"), analysis.get("observation"), (analysis.get("web_evidence") or {}).get("finding"))
    if key in _brief_cache:
        return _brief_cache[key]
    facts = decision_facts(scenario, analysis)
    option_ids = [option["id"] for option in facts["ranked_options"]]
    tool = _tool("write_brief", "Write the dispatcher brief.", {
        "recommended_action_id": {"type": "string", "enum": option_ids},
        "inspect_first": {"type": "boolean"},
        "headline": {"type": "string", "description": "At most 14 words."},
        "why": {"type": "string", "description": "At most two sentences."},
        "what_would_change_it": {"type": "string", "description": "One sentence."},
        "who_is_affected": {"type": "string", "description": "One sentence, from the stakeholder effect."},
    })
    try:
        message = chat_fn([{"role": "system", "content": BRIEF_SYSTEM},
                           {"role": "user", "content": json.dumps(facts, indent=1)}],
                          tools=[tool], tool_choice=_force("write_brief"))
        reply = _structured(message, "write_brief")
    except llm.LLMUnavailable as error:
        return _unavailable(error.reason)

    if not all(_text(reply.get(field)) for field in BRIEF_FIELDS) or not isinstance(reply.get("inspect_first"), bool):
        return _unavailable("malformed brief")
    if reply.get("recommended_action_id") != option_ids[0]:
        return _unavailable("brief did not recommend the engine's top-ranked option")
    if not analysis.get("observation") and reply["inspect_first"] != analysis["evoi"]["recommend_inspection"]:
        return _unavailable("brief contradicts the engine's inspection advice")
    texts = {field: reply[field].strip() for field in BRIEF_FIELDS}
    if ungrounded_numbers(" ".join(texts.values()), facts):
        return _unavailable("ungrounded numbers")
    result = {"available": True, "basis": BASIS, "model": _model(), **texts,
              "recommended_action_id": reply["recommended_action_id"], "inspect_first": reply["inspect_first"]}
    _brief_cache[key] = result
    return result


# ---------------------------------------------------------------------------
# Role 3: questions answered by calling the engine
# ---------------------------------------------------------------------------
ASK_SYSTEM = (
    "You answer a port dispatcher's questions about a delivery decision. Answer only from the results of "
    "the tools, which call the PigeonOps decision engine. Start with get_decision_summary if you need the "
    "situation. If the tools cannot answer the question, say so. Use at most four sentences. Do no "
    "arithmetic beyond what the tools return. Your answer is advice; the dispatcher decides."
)
PROBABILITY = {"type": "number", "minimum": 0, "maximum": 1,
               "description": "Chance the road is blocked, from 0 to 1 (0.6 means 60%)."}
ASK_TOOLS = [
    _tool("get_decision_summary", "The current incident, blockage chance, inspection economics and ranked responses.", {}),
    _tool("inspection_value_at", "Value of a drone inspection if the chance of a blockage were this value.",
          {"blocked_probability": PROBABILITY}),
    _tool("rank_responses", "Responses ranked by expected loss at this chance of a blockage, acting now.",
          {"blocked_probability": PROBABILITY}),
    _tool("shipment_outcomes", "Per-shipment arrival, lateness and appointment for one response and road state, acting now.",
          {"action_id": {"type": "string"}, "road_state": {"type": "string", "enum": list(ROAD_STATES)}}),
]


def _probability(arguments):
    value = arguments.get("blocked_probability")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError("blocked_probability must be a number from 0 to 1")
    return float(value)


def run_engine_tool(name, arguments, scenario, analysis):
    """Execute one tool call against the real engine. Bad arguments return {"error": ...}."""
    model = scenario["decision_model"]
    try:
        if name == "get_decision_summary":
            return decision_facts(scenario, analysis)
        if name == "inspection_value_at":
            p = _probability(arguments)
            value = information_value(model, p)
            return {
                "blocked_chance_pct": _pct(p),
                "gross_saving_usd": _usd(value["gross_value_of_information"]),
                "cost_usd": _usd(value["inspection_cost"]),
                "net_value_usd": _usd(value["net_value_of_information"]),
                "recommend_inspection": value["recommend_inspection"],
                "best_response_if_acting_now": rank_actions(model, p)[0]["label"],
            }
        if name == "rank_responses":
            p = _probability(arguments)
            return {"blocked_chance_pct": _pct(p), "options": [
                {"rank": item["rank"], "id": item["id"], "label": item["label"],
                 "expected_loss_usd": _usd(item["expected_loss"])}
                for item in rank_actions(model, p)]}
        if name == "shipment_outcomes":
            action = next((item for item in model["actions"] if item["id"] == arguments.get("action_id")), None)
            if action is None:
                raise ValueError("action_id must be one of " + ", ".join(item["id"] for item in model["actions"]))
            road = arguments.get("road_state")
            if road not in ROAD_STATES:
                raise ValueError("road_state must be open or blocked")
            rows = action["outcomes"][road]["detail"]["shipments"]
            keys = ("shipment", "truck", "route", "reassigned", "arrival", "appointment", "slack_min",
                    "lateness_min", "constraint_breaches", "basis")
            return {"action": action["label"], "road_state": road, "timing": "acting now, without an inspection wait",
                    "shipments": [{key: row.get(key) for key in keys} for row in rows]}
        return {"error": f"unknown tool {name}"}
    except (ValueError, KeyError, TypeError) as error:
        return {"error": str(error)}


def validate_question(question):
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be non-empty text")
    if len(question) > 300:
        raise ValueError("question must be at most 300 characters")
    return question.strip()


def ask(scenario, analysis, question, chat_fn=None):
    question = validate_question(question)
    chat_fn = chat_fn or llm.chat
    messages = [{"role": "system", "content": ASK_SYSTEM}, {"role": "user", "content": question}]
    engine_calls, results = [], []
    answer = None
    try:
        for _ in range(MAX_ASK_ROUNDS):
            message = chat_fn(messages, tools=ASK_TOOLS)
            calls = [call for call in message.get("tool_calls") or [] if isinstance(call, dict)]
            if not calls:
                answer = message.get("content")
                break
            messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
            for index, call in enumerate(calls):
                name = _call_name(call)
                try:
                    arguments = _arguments(call)
                    result = run_engine_tool(name, arguments, scenario, analysis)
                except (ValueError, TypeError, KeyError) as error:
                    arguments, result = _function(call).get("arguments"), {"error": str(error)}
                engine_calls.append({"name": name, "arguments": arguments})
                results.append(result)
                messages.append({"role": "tool", "tool_call_id": call.get("id") or f"call_{index}",
                                 "name": name, "content": json.dumps(result)})
        else:
            answer = chat_fn(messages).get("content")  # Rounds used up: one last reply without tools.
    except llm.LLMUnavailable as error:
        return _unavailable(error.reason)
    if not _text(answer, 2000):
        return _unavailable("empty answer")
    answer = answer.strip()
    grounded = not ungrounded_numbers(answer, results, decision_facts(scenario, analysis), question)
    return {"available": True, "basis": BASIS, "model": _model(), "answer": answer,
            "engine_calls": engine_calls, "grounded": grounded}
