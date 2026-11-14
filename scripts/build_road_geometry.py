#!/usr/bin/env python3
"""Fetch road-following paths for every playback leg and save them to config/road_geometry.json.

Run this after changing locations, truck positions, or shipments:

    python3 scripts/build_road_geometry.py

It calls the public OSRM demo server (OpenStreetMap data, car profile) once per leg,
about one request per second. The demo app reads only the saved file, so it runs offline.
"""

import json
import math
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.playback import ROAD_GEOMETRY, load_geometry, required_legs  # noqa: E402
from backend.scenario import available_scenarios, load_scenario  # noqa: E402

OSRM = "https://router.project-osrm.org/route/v1/driving/"
TOLERANCE = 0.00003  # Degrees, about 3 m. Removes redundant vertices on straight roads.


def simplify(points, tolerance=TOLERANCE):
    """Ramer–Douglas–Peucker line simplification on [lon, lat] points."""
    if len(points) < 3:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = math.hypot(dx, dy) or 1e-12
    worst, index = 0.0, 0
    for i, (x, y) in enumerate(points[1:-1], start=1):
        distance = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / norm
        if distance > worst:
            worst, index = distance, i
    if worst <= tolerance:
        return [points[0], points[-1]]
    return simplify(points[: index + 1], tolerance)[:-1] + simplify(points[index:], tolerance)


def fetch(waypoints):
    coordinates = ";".join(f"{lon:.5f},{lat:.5f}" for lat, lon in waypoints)
    url = f"{OSRM}{coordinates}?overview=full&geometries=geojson&continue_straight=true"
    request = urllib.request.Request(url, headers={"User-Agent": "pigeon-ops-demo/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
    if payload.get("code") != "Ok":
        raise RuntimeError(f"OSRM error {payload.get('code')}: {payload.get('message')}")
    route = payload["routes"][0]
    path = [[round(lon, 5), round(lat, 5)] for lon, lat in route["geometry"]["coordinates"]]
    return {"path": simplify(path), "distance_m": round(route["distance"]), "duration_s": round(route["duration"])}


def main():
    scenarios = [load_scenario(variant=item["id"]) for item in available_scenarios()]
    legs = required_legs(scenarios)
    refresh = "--refresh" in sys.argv
    existing = {} if refresh else load_geometry()
    output = {}
    for key, waypoints in sorted(legs.items()):
        if key in existing:
            output[key] = existing[key]
            continue
        print(f"Routing {key}")
        output[key] = fetch(waypoints)
        time.sleep(1.1)
    ROAD_GEOMETRY.write_text(json.dumps({
        "source": "OSRM demo server, OpenStreetMap contributors (ODbL), car profile",
        "legs": output,
    }, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Saved {len(output)} legs to {ROAD_GEOMETRY.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
