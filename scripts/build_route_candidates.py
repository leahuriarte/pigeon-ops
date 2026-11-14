#!/usr/bin/env python3
"""Save OSRM route candidates for each synthetic shipment.

Run offline with ``python3 scripts/build_route_candidates.py``. The demo reads
the committed catalog and never calls OSRM at request time. OSRM's public
driving profile is an estimated road baseline, not truck clearance validation.
"""
import json
import re
import time
import urllib.request
from datetime import date
from pathlib import Path

from build_road_geometry import simplify

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "config" / "port_scenario.json"
OUTPUT = ROOT / "config" / "route_candidates.json"
OSRM = "https://router.project-osrm.org/route/v1/driving/"


def shipment_starts(scenario):
    """Use the current position for a truck's first en-route load, then origins."""
    trucks = {item["id"]: item for item in scenario["trucks"]}
    first = {}
    for shipment in scenario["shipments"]:
        if shipment["truck"] in trucks:
            first.setdefault(shipment["truck"], []).append(shipment)
    def eta_minutes(item):
        hour, minute = re.search(r"(\d\d):(\d\d)", item["eta"]).groups()
        return int(hour) * 60 + int(minute)
    first = {truck: min(loads, key=eta_minutes)
             for truck, loads in first.items()}
    starts = {}
    for shipment in scenario["shipments"]:
        truck = trucks.get(shipment["truck"])
        en_route = truck and truck["status"] == "en-route" and first[truck["id"]] is shipment
        starts[shipment["id"]] = (truck["lat"], truck["lon"]) if en_route else None
    return starts


def fetch_candidates(shipment_id, start, end):
    coordinates = f"{start['lon']},{start['lat']};{end['lon']},{end['lat']}"
    url = f"{OSRM}{coordinates}?alternatives=3&overview=full&geometries=geojson"
    request = urllib.request.Request(url, headers={"User-Agent": "pigeon-ops-candidates/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RuntimeError(f"OSRM could not route {shipment_id}: {payload.get('code')}")
    candidates = []
    for index, route in enumerate(payload["routes"]):
        path = [[round(lon, 5), round(lat, 5)] for lon, lat in route["geometry"]["coordinates"]]
        candidates.append({
            "id": f"SYN-ROUTE-{shipment_id}-osrm-{index + 1}",
            "distance_m": round(route["distance"]),
            "duration_s": round(route["duration"]),
            "geometry": {"type": "LineString", "coordinates": simplify(path)},
            "basis": "estimated",
        })
    return candidates


def planned_candidates(shipment, start, end, routes):
    """Keep saved corridors only when they match the entire planned leg."""
    near = lambda point, location: abs(point[0] - location["lat"]) < 0.0001 and abs(point[1] - location["lon"]) < 0.0001
    return [{
        "id": f"SYN-ROUTE-{shipment['id']}-{route['id']}",
        "scenario_route": route["id"],
        "distance_m": round(route["distance_m"]),
        "duration_s": round(route["duration_s"]),
        "geometry": route["geometry"],
        "basis": "estimated",
    } for route in routes if near(route["points"][0], start) and near(route["points"][-1], end)]


def main():
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    locations = {item["id"]: item for item in scenario["locations"]}
    starts = shipment_starts(scenario)
    shipments = {}
    for index, shipment in enumerate(scenario["shipments"]):
        if index:
            time.sleep(1.1)
        start_pos = starts[shipment["id"]]
        start = {"lat": start_pos[0], "lon": start_pos[1]} if start_pos else locations[shipment["origin"]]
        end = locations[shipment["destination"]]
        candidates = [] if start_pos else planned_candidates(shipment, start, end, scenario["routes"])
        candidates += fetch_candidates(shipment["id"], start, end)
        shipments[shipment["id"]] = {
            "start": [start["lon"], start["lat"]], "destination": shipment["destination"],
            "start_basis": "synthetic truck position" if start_pos else "synthetic shipment origin",
            "candidates": candidates,
        }
        print(f"{shipment['id']}: {len(candidates)} candidate(s)")
    OUTPUT.write_text(json.dumps({
        "basis": "estimated",
        "source": "OSRM demo server and OpenStreetMap, driving profile; not truck-validated",
        "generated_on": date.today().isoformat(),
        "shipments": shipments,
    }, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Saved {len(shipments)} shipments to {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
