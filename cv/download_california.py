"""Snapshot official Caltrans camera information near the LA/LB port corridors."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cv.common import ROOT


FEED = "https://cwwp2.dot.ca.gov/data/d7/cctv/cctvStatusD07.json"
ROUTES = ["SR-47", "I-710", "I-110", "I-405", "SR-91"]
CLOSURES = "https://cwwp2.dot.ca.gov/data/d7/lcs/lcsStatusD07.json"


def fetch(url):
    request = Request(url, headers={"User-Agent": "PigeonOps/0.1 (educational traffic imagery research)"})
    with urlopen(request, timeout=45) as response:
        return response.read(), dict(response.headers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-route", type=int, default=5)
    args = parser.parse_args()
    if args.per_route <= 0:
        parser.error("per-route must be positive")
    fetched = datetime.now(timezone.utc)
    output = ROOT / "data" / "california" / fetched.strftime("%Y%m%dT%H%M%SZ")
    images = output / "images"
    images.mkdir(parents=True, exist_ok=True)
    payload, _ = fetch(FEED)
    (output / "district7-cameras.json").write_bytes(payload)
    records = [item["cctv"] for item in json.loads(payload)["data"]]
    relevant = [item for item in records if item["location"]["route"] in ROUTES
                and 33.65 <= float(item["location"]["latitude"]) <= 34.05
                and -118.55 <= float(item["location"]["longitude"]) <= -117.95]
    features = []
    for item in relevant:
        location = item["location"]
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(location["longitude"]), float(location["latitude"])]},
            "properties": {**location, "inService": item.get("inService"),
                           "camera_record_updated": item.get("recordTimestamp"),
                           "image_url": item["imageData"]["static"]["currentImageURL"]}})
    (output / "port-corridor-cameras.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}, indent=2) + "\n", encoding="utf-8")
    downloaded, failures = [], []
    for route in ROUTES:
        candidates = [item for item in relevant if item["location"]["route"] == route and str(item.get("inService")).lower() == "true"]
        candidates.sort(key=lambda item: (float(item["location"]["latitude"]) - 33.77) ** 2 + (float(item["location"]["longitude"]) + 118.22) ** 2)
        successes = 0
        for item in candidates:
            if successes >= args.per_route:
                break
            url = item["imageData"]["static"]["currentImageURL"]
            filename = Path(url).name
            try:
                image, headers = fetch(url)
                if not image.startswith(b"\xff\xd8"):
                    raise ValueError("Response is not a JPEG snapshot")
                (images / filename).write_bytes(image)
                downloaded.append({"image": filename, "source_url": url, "location": item["location"],
                    "camera_record_updated": item.get("recordTimestamp"),
                    "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                    "http_last_modified": headers.get("Last-Modified"),
                    "sha256": hashlib.sha256(image).hexdigest(),
                    "capture_time": "Not parsed; inspect the camera overlay. Record timestamp is not image capture time."})
                successes += 1
                print(f"{route}: {item['location']['locationName']}", flush=True)
            except (HTTPError, URLError, ValueError, TimeoutError) as error:
                failures.append({"source_url": url, "error": str(error)})
    (output / "snapshots.json").write_text(json.dumps({"images": downloaded, "failures": failures}, indent=2) + "\n", encoding="utf-8")
    closure_count = 0
    context_errors = []
    try:
        closure_payload, _ = fetch(CLOSURES)
        (output / "district7-lane-closures.json").write_bytes(closure_payload)
        corridor_closures = []
        for record in json.loads(closure_payload)["data"]:
            closure = record["lcs"]
            begin = closure["location"]["begin"]
            if begin.get("beginRoute") in ROUTES and 33.65 <= float(begin["beginLatitude"] or 0) <= 34.05 and -118.55 <= float(begin["beginLongitude"] or 0) <= -117.95:
                corridor_closures.append(closure)
        closure_count = len(corridor_closures)
        (output / "port-corridor-lane-closures.json").write_text(json.dumps({
            "source": CLOSURES, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "interpretation": "Feed includes scheduled, ended, cancelled and potentially active closures. Inspect times and status flags; presence does not establish a current closure or validate a camera observation.",
            "records": corridor_closures,
        }, indent=2) + "\n", encoding="utf-8")
    except (HTTPError, URLError, ValueError, TimeoutError) as error:
        context_errors.append(str(error))
    manifest = {
        "dataset": "Caltrans District 7 port-corridor camera snapshots", "source": FEED,
        "creators": ["California Department of Transportation (Caltrans)"],
        "license": "Caltrans CWWP public data; Caltrans Conditions of Use apply",
        "terms_url": "https://dot.ca.gov/conditions-of-use",
        "documentation": "https://cwwp2.dot.ca.gov/documentation/cctv/cctv.htm",
        "geography": "California: SR-47, I-710, I-110, I-405 and SR-91 near Los Angeles/Long Beach",
        "retrieved_at_utc": fetched.isoformat(), "camera_locations": len(relevant),
        "images_downloaded": len(downloaded), "download_failures": len(failures),
        "lane_closure_feed": CLOSURES, "corridor_lane_closure_records": closure_count,
        "context_download_errors": context_errors,
        "additional_sources": {
            "highway_conditions": "https://roads.dot.ca.gov/",
            "quickmap": "https://quickmap.dot.ca.gov/",
            "truck_access": "https://dot.ca.gov/programs/traffic-operations/legal-truck-access/truck-network-map",
            "traffic_volumes": "https://dot.ca.gov/programs/traffic-operations/census",
        },
        "limitations": ["Roadside CCTV, not drone imagery; snapshots are not labeled ground truth.",
                        "Images may be stale, obscured or unavailable even for in-service cameras.",
                        "Record-update time and HTTP Last-Modified do not establish capture time.",
                        "Do not associate these observations with the synthetic demo incident.",
                        "No model accuracy claims can be made on these unlabeled snapshots."],
    }
    (output / "source.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"California snapshot ready: {output}; {len(downloaded)} images, {len(relevant)} mapped camera locations", flush=True)


if __name__ == "__main__":
    main()
