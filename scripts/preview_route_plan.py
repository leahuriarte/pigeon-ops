#!/usr/bin/env python3
"""Print the offline route search for a scenario and synthetic traffic time.

Usage: python3 scripts/preview_route_plan.py [normal|disruption|...] [HH:MM]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.route_optimizer import optimize_routes  # noqa: E402
from backend.scenario import load_scenario  # noqa: E402


def main(argv):
    variant = argv[0] if argv else "normal"
    at = argv[1] if len(argv) > 1 else None
    plan = optimize_routes(load_scenario(variant=variant), at=at)
    print(f"Hypothetical shift start {plan['at']} with synthetic traffic {plan['traffic_period']}; estimated driving routes, not truck-validated.")
    print("Advisory route search only. Incident probability and drone response are scored by the existing decision engine.")
    for truck in plan["trucks"]:
        print(f"{truck['truck']}: {truck['candidates_considered']} route combination(s), estimated loss ${truck['estimated_loss']:.2f}")
        for shipment in truck["shipments"]:
            print(f"  {shipment['shipment']}: {shipment['route']}, ETA {shipment['arrival']}, "
                  f"{shipment['risk_after_start']:.0%} assumed risk after appointment start")


if __name__ == "__main__":
    main(sys.argv[1:])
