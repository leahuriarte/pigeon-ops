#!/usr/bin/env python3
"""Write config/decision_history.json: synthetic past dispatcher decisions scored by the decision engine.

Each case below is hand-written: the report, the web finding, the drone check, the response chosen and the
road state that followed. The figures come from the real engine (load_scenario, triage.apply_web_evidence,
analyze and history.decision_record), so they agree with what the app shows. A few cases apply a stated
deviation to the actual figures, where the assumed model was wrong in a believable direction.

There are no random draws, so a rerun gives the same file. Run from the repo root:

    python scripts/build_decision_history.py
"""
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import history  # noqa: E402
from backend.decision_engine import analyze  # noqa: E402
from backend.scenario import load_scenario  # noqa: E402
from backend.triage import apply_web_evidence  # noqa: E402

OUTPUT = ROOT / "config" / "decision_history.json"
DRIVER_RATE_PER_MIN = 35 / 60  # decision_model.rates.driver_hourly_rate, for deviations in driver time
KEEP = ("id", "date", "reported_at", "check_type", "variant", "title", "place", "lat", "lon", "reports",
        "estimated_bad_probability", "web_finding", "inspection", "action", "action_label", "recommended_action",
        "override_reason", "actual_road_state", "estimate", "actual", "actual_note", "schools_within_300m",
        "lesson", "pattern_ids", "basis")

PATTERNS = [
    {"id": "crash-clearance-time",
     "claim": "In FHWA's Atlanta freeway data, crashes lasted a median 40 minutes, the 95th percentile was "
              "106 minutes, and 87% blocked at least one lane.",
     "source": "FHWA Office of Operations, FHWA-HOP-12-045, Tables 4 and 5 (the 87% is summed from Table 4)",
     "source_url": "https://ops.fhwa.dot.gov/publications/fhwahop12045/4_incdnt.htm"},
    {"id": "public-feed-lag",
     "claim": "In a 2018 California study, Waze crash reports arrived on average 2 minutes 41 seconds before "
              "the CHP received reports of the same crashes.",
     "source": "UCLA Newsroom, on a UCLA and UC Irvine study in JAMA Surgery",
     "source_url": "https://newsroom.ucla.edu/releases/waze-accident-reports-emergency-response"},
    {"id": "driver-report-reliability",
     "claim": "In Tennessee, only 26% of Waze crash reports matched an official record; the rest had no "
              "official counterpart.",
     "source": "University of Tennessee for Tennessee DOT and FHWA, Waze Data Reporting, RES2019-11 (2022)",
     "source_url": "https://rosap.ntl.bts.gov/view/dot/66932/dot_66932_DS1.pdf"},
    {"id": "gate-queue-variability",
     "claim": "A GPS study at Los Angeles and Long Beach terminals found a median 20-minute queue and "
              "51-minute visit; trucks arriving 17:00 to 18:00 had 90-minute median visits.",
     "source": "PierPass Turn Time Study (2011)",
     "source_url": "https://www.pierpass.org/news/groundbreaking-study-provides-first-comprehensive-measurement-of-truck-queuing-and-visit-times-at-the-ports/"},
    {"id": "water-main-break",
     "claim": "The LADWP water system has 2 to 4 water pipe breaks per day.",
     "source": "LADWP News, Fast Facts About Trunk Lines, Mainlines and Leaks (2026)",
     "source_url": "https://ladwpnews.com/fast-facts-about-trunk-lines-mainlines-and-leaks-in-the-ladwp-water-system/"},
    {"id": "flood-depth-unknown",
     "claim": "The NWS warns that 2 feet of rushing water can carry away SUVs and trucks, and that the road "
              "beneath the water may have collapsed.",
     "source": "National Weather Service, Turn Around Don't Drown",
     "source_url": "https://www.weather.gov/safety/flood-turn-around-dont-drown"},
    {"id": "neighborhood-truck-detour",
     "claim": "CARB recommends siting schools, playgrounds and housing at least 500 feet from freeways and "
              "from urban roads carrying 100,000 vehicles per day.",
     "source": "California Air Resources Board fact sheet citing the 2005 Air Quality and Land Use Handbook",
     "source_url": "https://ww2.arb.ca.gov/resources/fact-sheets/strategies-reduce-air-pollution-exposure-near-high-volume-roadways"},
    {"id": "diesel-idle-vs-miles",
     "claim": "An idling heavy truck burns about 0.8 gallons per hour, about 8 kg CO2; a combination truck "
              "at 6.7 mpg emits about 1.5 kg CO2 per mile.",
     "source": "DOE Clean Cities idling fact sheet; FHWA Highway Statistics 2023, Table VM-1; the CO2 figures "
               "are calculated at EPA's 10,180 g per diesel gallon",
     "source_url": "https://afdc.energy.gov/files/u/publication/hdv_idling_2015.pdf"},
    {"id": "drone-verification-limits",
     "claim": "In FHWA case studies drones mainly document crash scenes, and FHWA calls most of the evidence "
              "for their benefit anecdotal.",
     "source": "FHWA Office of Operations, FHWA-HOP-20-063, Chapter 3",
     "source_url": "https://ops.fhwa.dot.gov/publications/fhwahop20063/chap3.htm"},
]

CRASH = {"place": "CA-47 connector", "lat": 33.773, "lon": -118.237}
QUEUE = {"place": "Pier 400 outbound access road", "lat": 33.74372, "lon": -118.260285}
WATER = {"place": "Northern connector approach", "lat": 33.780881, "lon": -118.226268}

# Lesson templates are filled from the computed figures: est and act (this record), alt[action] and
# arrive[action] (every response under the actual road state), rows and planned_rows (per-shipment rows for
# the chosen response and for keeping the route on an open road), more_co2[action], drone, p and schools.
CASES = [
    {"date": "2026-08-04", "reported_at": "08:40", "variant": "disruption", **CRASH,
     "title": "Possible wreck on the CA-47 connector", "reports": "Two drivers disagreed",
     "web": None, "observation": "blocked", "action": "reroute", "road": "blocked",
     "expect": {"recommended": "reroute", "changed_response": False},
     "lesson": "Two drivers disagreed, the ${drone[cost]:.0f} drone check showed the wreck, and rerouting arrived "
               "{act[lateness_min]:.0f} min late where holding course would have arrived "
               "{alt[hold-course][lateness_min]:.0f} min late and missed the appointment.",
     "pattern_ids": ["driver-report-reliability", "drone-verification-limits", "crash-clearance-time"]},
    {"date": "2026-08-13", "reported_at": "07:55", "variant": "congestion", **QUEUE,
     "title": "Queue outside the Pier 400 gate", "reports": "One driver saw a queue",
     "web": "nothing_found", "observation": None, "action": "hold-course", "road": "blocked",
     "expect": {"recommended": "hold-course"},
     "deviation": {"cost_usd": -12 * DRIVER_RATE_PER_MIN},
     "actual_note": "Queue cleared in 18 min, not the assumed 30.",
     "lesson": "The queue cleared in 18 min, not the assumed 30, so holding course cost ${act[cost_usd]:.2f} in "
               "driver time and no lateness.",
     "pattern_ids": ["gate-queue-variability"]},
    {"date": "2026-08-20", "reported_at": "08:50", "variant": "flood", **WATER,
     "title": "Water main break on the connector", "reports": "Utility report and a CHP log",
     "web": "corroborates", "observation": None, "action": "reroute", "road": "blocked",
     "expect": {"recommended": "reroute"},
     "lesson": "A utility report and a CHP log agreed, so the load avoided the water at once and arrived "
               "{act[lateness_min]:.0f} min late, where holding course would have cost "
               "{alt[hold-course][lateness_min]:.0f} min and the appointment.",
     "pattern_ids": ["water-main-break", "flood-depth-unknown"]},
    {"date": "2026-08-26", "reported_at": "09:20", "variant": "disruption_b", **CRASH,
     "title": "Closure reports on CA-47", "reports": "Three drivers; no CHP log entry",
     "web": "nothing_found", "observation": None, "action": "hold-course", "road": "blocked",
     "override_reason": "No CHP log entry, so the dispatcher judged the driver reports stale.",
     "expect": {"recommended": "reroute"},
     "lesson": "A missing CHP log entry did not mean the road was open: the truck held course into the closure, "
               "arrived {act[lateness_min]:.0f} min late and missed its {rows[SYN-SHP-1042][appointment]} window.",
     "pattern_ids": ["public-feed-lag", "crash-clearance-time"]},
    {"date": "2026-09-03", "reported_at": "09:25", "variant": "flood", **WATER,
     "title": "Standing water on the connector", "reports": "One driver; no feed entry",
     "web": "nothing_found", "observation": "inconclusive", "action": "reroute", "road": "open",
     "expect": {"recommended": "reroute", "changed_response": False},
     "lesson": "The footage could not show the water's depth, so the read was inconclusive and the load avoided "
               "the water, paying {act[added_miles]:.0f} extra miles on a road that was passable.",
     "pattern_ids": ["flood-depth-unknown", "drone-verification-limits"]},
    {"date": "2026-09-09", "reported_at": "07:50", "variant": "congestion", **QUEUE,
     "title": "Pier 400 exit queue at shift start", "reports": "Two drivers and a message sign",
     "web": "corroborates", "observation": None, "action": "reroute", "road": "blocked",
     "override_reason": "The dispatcher wanted no queue risk before the 10:15 appointment.",
     "expect": {"recommended": "hold-course"},
     "lesson": "Rerouting around a real queue drove {act[added_miles]:.0f} extra miles and "
               "{more_co2[hold-course]:.0f} kg more CO2 to skip a wait that would still have arrived on time.",
     "pattern_ids": ["diesel-idle-vs-miles", "neighborhood-truck-detour"]},
    {"date": "2026-09-15", "reported_at": "08:55", "variant": "disruption", **CRASH,
     "title": "Truck crash reported on CA-47", "reports": "One driver and a CHP log",
     "web": "corroborates", "observation": None, "action": "reroute", "road": "open",
     "expect": {"recommended": "reroute"},
     "lesson": "The crash had been cleared to the shoulder, so rerouting paid {act[added_miles]:.0f} extra miles, "
               "{more_co2[hold-course]:.0f} kg more CO2 and {act[lateness_min]:.0f} min of lateness, and passed "
               "{schools} schools, for nothing.",
     "pattern_ids": ["crash-clearance-time", "neighborhood-truck-detour"]},
    {"date": "2026-09-22", "reported_at": "08:20", "variant": "flood", **WATER,
     "title": "Burst main floods the connector", "reports": "City report and a CHP log",
     "web": "corroborates", "observation": None, "action": "hold-course", "road": "blocked",
     "override_reason": "The driver said trucks were getting through.",
     "expect": {"recommended": "reroute"},
     "deviation": {"lateness_min": 10, "cost_usd": 10 * DRIVER_RATE_PER_MIN},
     "actual_note": "Backing out of the water took 25 min, not the assumed 15.",
     "lesson": "Holding course into deep water missed the appointment: backing out took 25 min, not 15, and the "
               "load arrived {act[lateness_min]:.0f} min late.",
     "pattern_ids": ["water-main-break", "flood-depth-unknown"]},
    {"date": "2026-09-24", "reported_at": "09:10", "variant": "congestion", **QUEUE,
     "title": "Possible queue at the Pier 400 exit", "reports": "One driver; signs blank",
     "web": None, "observation": "clear", "action": "hold-course", "road": "open",
     "expect": {"recommended": "hold-course", "changed_response": False, "inspection_recommended": False},
     "lesson": "The drone showed traffic moving, but holding course was the pick either way, so the "
               "${drone[cost]:.0f} check and its {drone[wait]} min wait bought nothing.",
     "pattern_ids": ["drone-verification-limits", "gate-queue-variability"]},
    {"date": "2026-09-29", "reported_at": "09:05", "variant": "disruption_b", **CRASH,
     "title": "CA-47 connector closed", "reports": "Four drivers and a traffic alert agreed",
     "web": "corroborates", "observation": "blocked", "action": "reroute", "road": "blocked",
     "expect": {"recommended": "reroute", "changed_response": False, "inspection_recommended": False},
     "lesson": "Four reports already agreed, so the ${drone[cost]:.0f} drone check and its {drone[wait]} min wait "
               "changed nothing: the load rerouted either way.",
     "pattern_ids": ["drone-verification-limits", "driver-report-reliability"]},
    {"date": "2026-10-07", "reported_at": "08:15", "variant": "congestion", **QUEUE,
     "title": "Shift-change queue at Pier 400", "reports": "One driver at shift change",
     "web": "nothing_found", "observation": None, "action": "hold-course", "road": "blocked",
     "expect": {"recommended": "hold-course"},
     "deviation": {"lateness_min": 17, "cost_usd": 22 * DRIVER_RATE_PER_MIN},
     "actual_note": "Queue lasted 52 min, not the assumed 30.",
     "lesson": "The queue lasted 52 min, not 30, so holding course arrived {act[lateness_min]:.0f} min after the "
               "window opened, where the alternate exit would have arrived at {arrive[reroute]}.",
     "pattern_ids": ["gate-queue-variability"]},
    {"date": "2026-10-14", "reported_at": "09:30", "variant": "disruption_b", **CRASH,
     "title": "Closure reports, CHP says lanes reopened", "reports": "Four drivers; CHP said lanes reopened",
     "web": "contradicts", "observation": None, "action": "hold-course", "road": "open",
     "override_reason": "The dispatcher trusted the newer CHP entry over the driver reports.",
     "expect": {"recommended": "reroute"},
     "deviation": {"cost_usd": 4 * DRIVER_RATE_PER_MIN},
     "actual_note": "Cleanup crews slowed the connector by 4 min.",
     "lesson": "The CHP note that lanes had reopened was right: the load kept its route, arrived before its "
               "window opened and cost ${act[cost_usd]:.2f} more than planned, without a drone check.",
     "pattern_ids": ["public-feed-lag", "crash-clearance-time"]},
    {"date": "2026-10-21", "reported_at": "09:40", "variant": "flood", **WATER,
     "title": "Water across the connector approach", "reports": "One driver; feeds not checked",
     "web": None, "observation": "clear", "action": "reroute", "road": "blocked",
     "expect": {"recommended": "reroute", "changed_response": False},
     "lesson": "The drone read the water as passable, but at {drone[accuracy]:.0%} assumed accuracy the chance "
               "only fell to {p:.0%}, and avoiding the water kept the load off a road that was impassable.",
     "pattern_ids": ["drone-verification-limits", "flood-depth-unknown"]},
    {"date": "2026-10-28", "reported_at": "09:00", "variant": "congestion", **QUEUE,
     "title": "Queue report at the Pier 400 exit", "reports": "One driver; a sign said traffic was moving",
     "web": "contradicts", "observation": None, "action": "swap-truck", "road": "open",
     "override_reason": "SYN-TRK-07's driver was close to the duty-hour limit.",
     "expect": {"recommended": "hold-course"},
     "lesson": "The swap got SYN-SHP-1042 in {rows[SYN-SHP-1042][lateness_min]:.0f} min after its window opened "
               "but cut SYN-SHP-1048's buffer from {planned_rows[SYN-SHP-1048][slack_min]:.0f} min to "
               "{rows[SYN-SHP-1048][slack_min]:.0f}.",
     "pattern_ids": ["gate-queue-variability"]},
    {"date": "2026-11-03", "reported_at": "08:45", "variant": "disruption", **CRASH,
     "title": "Conflicting wreck reports on CA-47", "reports": "Driver reports conflicted",
     "web": None, "observation": "clear", "action": "hold-course", "road": "open",
     "expect": {"recommended": "hold-course", "changed_response": True, "inspection_recommended": True},
     "lesson": "The reports conflicted, so the ${drone[cost]:.0f} drone check paid for itself: it showed the road "
               "open and the load kept its route instead of driving {alt[reroute][added_miles]:.0f} extra miles "
               "on Anaheim St.",
     "pattern_ids": ["driver-report-reliability", "drone-verification-limits"]},
    {"date": "2026-11-05", "reported_at": "08:35", "variant": "flood", **WATER,
     "title": "Water reported at the underpass", "reports": "One driver; no feed entry",
     "web": "nothing_found", "observation": None, "action": "reroute", "road": "open",
     "expect": {"recommended": "reroute"},
     "deviation": {"lateness_min": 5, "cost_usd": 5 * DRIVER_RATE_PER_MIN},
     "actual_note": "Anaheim St ran 5 min slower than estimated behind school drop-off traffic.",
     "lesson": "The connector was passable, so avoiding the water paid {act[added_miles]:.0f} extra miles, "
               "{more_co2[hold-course]:.0f} kg more CO2 and {act[lateness_min]:.0f} min of lateness, and sent "
               "the truck past {schools} schools.",
     "pattern_ids": ["neighborhood-truck-detour", "water-main-break"]},
]


def _context(spec, scenario, analysis, record):
    flown = spec["observation"] is not None
    options = analysis["after"] if flown else analysis["before"]
    road = spec["road"]
    drone = scenario["decision_model"]["drone"]
    by_id = {option["id"]: option for option in options}
    alt = {key: history.metrics(option["outcomes"][road]) for key, option in by_id.items()}
    planned = next(option for option in analysis["before"] if option["id"] == "hold-course")["outcomes"]["open"]
    chosen = by_id[spec["action"]]["outcomes"][road]
    return {
        "est": record["estimate"], "act": record["actual"], "alt": alt,
        "arrive": {key: option["outcomes"][road]["detail"]["arrival"] for key, option in by_id.items()},
        "rows": {row["shipment"]: row for row in chosen["detail"]["shipments"]},
        "planned_rows": {row["shipment"]: row for row in planned["detail"]["shipments"]},
        "more_co2": {key: record["actual"]["co2_kg"] - value["co2_kg"] for key, value in alt.items()},
        "drone": {"cost": drone["direct_cost"], "wait": drone["launch_and_review_minutes"],
                  "accuracy": drone["accuracy"]},
        "p": record["estimated_bad_probability"], "schools": record["schools_within_300m"],
    }


def build_case(number, spec):
    if date.fromisoformat(spec["date"]).weekday() >= 5:
        raise SystemExit(f"{spec['date']} is not a weekday")
    scenario = apply_web_evidence(load_scenario(variant=spec["variant"]), spec["web"])
    analysis = analyze(scenario, spec["observation"])
    record = history.decision_record(scenario, analysis, spec["action"], spec["road"], spec["observation"],
                                     spec["web"], spec.get("override_reason"), run_id=None, variant=spec["variant"])
    expect = spec["expect"]
    if record["recommended_action"] != expect["recommended"]:
        raise SystemExit(f"{spec['date']}: engine recommends {record['recommended_action']}, "
                         f"case expects {expect['recommended']}")
    if "changed_response" in expect and record["inspection"]["changed_response"] != expect["changed_response"]:
        raise SystemExit(f"{spec['date']}: changed_response is {record['inspection']['changed_response']}")
    if "inspection_recommended" in expect and             analysis["evoi"]["recommend_inspection"] != expect["inspection_recommended"]:
        raise SystemExit(f"{spec['date']}: the engine's inspection advice differs from the case")
    if (record["override_reason"] is None) != (spec["action"] == record["recommended_action"]):
        raise SystemExit(f"{spec['date']}: give an override_reason exactly when the action is not the pick")
    for key, delta in (spec.get("deviation") or {}).items():
        record["actual"][key] = round(record["actual"][key] + delta, history.DIGITS[key])
    record.update({
        "id": f"SYN-HIS-{number:03d}",
        **{key: spec[key] for key in ("date", "reported_at", "title", "place", "lat", "lon", "reports",
                                      "pattern_ids")},
        "actual_note": spec.get("actual_note"),
        "basis": "synthetic",
    })
    record["lesson"] = spec["lesson"].format(**_context(spec, scenario, analysis, record))
    return {key: record[key] for key in KEEP}


def check_base_rates(cases):
    """Each variant's share of cases with the bad road state stays within 0.2 of its prior."""
    by_variant = defaultdict(list)
    for case in cases:
        by_variant[case["variant"]].append(case["actual_road_state"] == "blocked")
    for variant, states in by_variant.items():
        prior = load_scenario(variant=variant)["disruption"]["prior_blocked_probability"]
        rate = sum(states) / len(states)
        if abs(rate - prior) > 0.2:
            raise SystemExit(f"{variant}: bad state in {rate:.2f} of cases, prior {prior}")


def build():
    specs = sorted(CASES, key=lambda spec: (spec["date"], spec["reported_at"]))
    cases = [build_case(number, spec) for number, spec in enumerate(specs, start=1)]
    check_base_rates(cases)
    known = {pattern["id"] for pattern in PATTERNS}
    for case in cases:
        if not 1 <= len(case["pattern_ids"]) <= 3 or not set(case["pattern_ids"]) <= known:
            raise SystemExit(f"{case['id']}: needs one to three known pattern_ids")
    return {
        "basis": "synthetic",
        "note": "Synthetic past decisions modelled on public patterns, not real dispatch records.",
        "generated_by": "scripts/build_decision_history.py",
        "patterns": PATTERNS,
        "cases": cases,
    }


def main():
    data = build()
    OUTPUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(data['cases'])} synthetic cases to {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
