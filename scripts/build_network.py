"""Fetch road geometry for every scenario route from the public OSRM demo server.

Usage: python scripts/build_network.py [scenario.json ...]

With no arguments it updates config/port_scenario.json and any config/scenarios/*.json
that contain routes. Each route gains a GeoJSON LineString `geometry` ([lon, lat]),
`distance_m` and `duration_s`. Only the "routes" array in each file is rewritten, so the
rest of the hand-formatted JSON is left as it is.
"""
import json
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OSRM_URL = "https://router.project-osrm.org/route/v1/driving/"
USER_AGENT = "pigeon-ops-build-network/0.1"
SOURCE = "OSRM demo server (router.project-osrm.org), OpenStreetMap data"
PAUSE_S = 1.0


def default_files():
    files = [ROOT / "config" / "port_scenario.json"]
    files += sorted((ROOT / "config" / "scenarios").glob("*.json"))
    return files


def fetch_route(points):
    # Scenario points are [lat, lon]; OSRM wants lon,lat.
    coords = ";".join(f"{lon},{lat}" for lat, lon in points)
    url = f"{OSRM_URL}{coords}?overview=full&geometries=geojson"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.load(response)
    if body.get("code") != "Ok" or not body.get("routes"):
        raise RuntimeError(f"OSRM returned {body.get('code')}: {body.get('message')}")
    best = body["routes"][0]
    return {
        "geometry": best["geometry"],
        "distance_m": round(best["distance"], 1),
        "duration_s": round(best["duration"], 1),
    }


def format_routes(routes, indent):
    pad = " " * indent
    lines = []
    for route in routes:
        fields = [f'{pad}    {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}' for k, v in route.items()]
        lines.append(f"{pad}  {{\n" + ",\n".join(fields) + f"\n{pad}  }}")
    return "[\n" + ",\n".join(lines) + f"\n{pad}]"


def update_file(path, first_call):
    text = path.read_text(encoding="utf-8")
    match = re.search(r'^( *)"routes"\s*:\s*', text, re.MULTILINE)
    if not match:
        print(f"skip {path.relative_to(ROOT)}: no routes")
        return first_call
    routes, end = json.JSONDecoder().raw_decode(text, match.end())
    fetched_on = date.today().isoformat()
    for route in routes:
        if not first_call:
            time.sleep(PAUSE_S)
        first_call = False
        route.update(fetch_route(route["points"]))
        route["routing_basis"] = "estimated"
        route["routing_source"] = f"{SOURCE}, fetched {fetched_on}"
        print(f"{route['id']}: {route['distance_m']:.0f} m, {route['duration_s']:.0f} s, "
              f"{len(route['geometry']['coordinates'])} vertices")
    newline = "\r\n" if "\r\n" in text else "\n"
    block = format_routes(routes, len(match.group(1))).replace("\n", newline)
    path.write_text(text[:match.end()] + block + text[end:], encoding="utf-8", newline="")
    json.loads(path.read_text(encoding="utf-8"))
    print(f"wrote {path.relative_to(ROOT)}")
    return first_call


def main(argv):
    files = [Path(a).resolve() for a in argv] or default_files()
    first_call = True
    for path in files:
        first_call = update_file(path, first_call)


if __name__ == "__main__":
    main(sys.argv[1:])
