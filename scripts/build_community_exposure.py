#!/usr/bin/env python3
"""Find the schools within 300 m of each scenario route and save them to config/community_exposure.json.

Schools come from OpenStreetMap (amenity=school) through the public Overpass API. Run this once after
route geometry changes; the app reads only the saved file. Run from the repo root:

    python scripts/build_community_exposure.py
"""
import json
import math
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "config" / "port_scenario.json"
OUTPUT = ROOT / "config" / "community_exposure.json"
RADIUS_M = 300
ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
EARTH_RADIUS_M = 6371008.8


def fetch_schools(bounds):
    box = f"{bounds['south']},{bounds['west']},{bounds['north']},{bounds['east']}"
    query = f'[out:json][timeout:60];nwr["amenity"="school"]({box});out center tags;'
    body = urllib.parse.urlencode({"data": query}).encode()
    last_error = None
    for endpoint in ENDPOINTS:
        request = urllib.request.Request(endpoint, data=body, headers={"User-Agent": "route-exposure-script/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                elements = json.load(response)["elements"]
        except (OSError, ValueError, KeyError) as error:
            last_error = error
            continue
        schools = []
        for element in elements:
            center = element.get("center", element)
            if "lat" in center:
                schools.append({
                    "osm_id": f"{element['type']}/{element['id']}",
                    "name": element.get("tags", {}).get("name", "Unnamed school"),
                    "lat": center["lat"], "lon": center["lon"],
                })
        return schools, endpoint
    raise SystemExit(f"Overpass request failed: {last_error}")


def distance_to_line_m(lat, lon, coords):
    """Shortest distance in metres from a point to a [lon, lat] LineString (flat approximation)."""
    scale = math.cos(math.radians(lat))

    def xy(c_lon, c_lat):
        return math.radians(c_lon) * EARTH_RADIUS_M * scale, math.radians(c_lat) * EARTH_RADIUS_M

    px, py = xy(lon, lat)
    points = [xy(c[0], c[1]) for c in coords]
    best = math.inf
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
        best = min(best, math.hypot(ax + t * dx - px, ay + t * dy - py))
    return best


def main():
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    schools, endpoint = fetch_schools(scenario["map_bounds"])
    routes = {}
    for route in scenario["routes"]:
        near = []
        for school in schools:
            gap = distance_to_line_m(school["lat"], school["lon"], route["geometry"]["coordinates"])
            if gap <= RADIUS_M:
                near.append({**school, "distance_m": round(gap)})
        near.sort(key=lambda school: school["distance_m"])
        routes[route["id"]] = near
        print(f"{route['id']}: {len(near)} schools within {RADIUS_M} m")
    OUTPUT.write_text(json.dumps({
        "basis": "estimated",
        "radius_m": RADIUS_M,
        "source": f"OpenStreetMap amenity=school through the Overpass API ({endpoint}), fetched {date.today().isoformat()}",
        "note": "A school counts when its OpenStreetMap point, or the centre of its mapped outline, lies within "
                "radius_m of the route line. OpenStreetMap may miss schools or list closed ones.",
        "schools_in_map_area": len(schools),
        "routes": routes,
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)} from {len(schools)} schools in the map area")


if __name__ == "__main__":
    sys.exit(main())
