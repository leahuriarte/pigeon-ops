"""Public road and weather feeds near an incident (standard library only).

Three live sources, none needing a key:
- CHP: California Highway Patrol computer-aided dispatch log, every open incident statewide (XML).
- Caltrans District 7 changeable message signs (JSON).
- National Weather Service active alerts for a point (GeoJSON).

Each `*_near` function returns `{"source", "feed_url", "status", "items"}`. `status` is "ok" or a short
reason the feed could not be read; a feed never raises. Items share one shape:
`{id, source, source_url, title, detail, time, lat, lon, distance_km, basis}` with `basis: "live"`.
Responses are cached per URL for CACHE_S seconds so repeated agent searches do not refetch.
"""
import hashlib
import http.client
import json
import math
import re
import socket
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CHP_URL = "https://media.chp.ca.gov/sa_xml/sa.xml"
CHP_PAGE = "https://cad.chp.ca.gov/"
SIGNS_URL = "https://cwwp2.dot.ca.gov/data/d7/cms/cmsStatusD07.json"
SIGNS_PAGE = "https://quickmap.dot.ca.gov/"
NWS_URL = "https://api.weather.gov/alerts/active?point={lat:.4f},{lon:.4f}"
NWS_PAGE = "https://www.weather.gov/lox/"
USER_AGENT = "PigeonOps-prototype (hackathon demo)"  # NWS asks automated clients for a User-Agent.
TIMEOUT_S = 10.0
MAX_BYTES = 2_000_000
CACHE_S = 60.0
MAX_RADIUS_KM = 10.0
CHP_WINDOW_MIN = 180

_cache = {}
_cache_lock = threading.Lock()


class FeedError(Exception):
    pass


def clear_cache():
    with _cache_lock:
        _cache.clear()


def fetch(url, accept="*/*"):
    """Bytes at `url`, from a short cache when fresh. Raises FeedError on any failure."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(url)
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urlopen(request, timeout=TIMEOUT_S) as response:
            body = response.read(MAX_BYTES + 1)
    except HTTPError as error:
        raise FeedError(f"HTTP {error.code}") from None
    except (URLError, socket.timeout, TimeoutError, ConnectionError, OSError, http.client.HTTPException) as error:
        raise FeedError(f"unreachable: {getattr(error, 'reason', error)}") from None
    if len(body) > MAX_BYTES:
        raise FeedError("response too large")
    with _cache_lock:
        _cache[url] = (now, body)
    return body


def distance_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _result(source, url, status, items):
    items.sort(key=lambda item: (item["distance_km"] is None, item["distance_km"] or 0))
    return {"source": source, "feed_url": url, "status": status, "items": items}


# ---------------------------------------------------------------------------
# CHP incident log
# ---------------------------------------------------------------------------
def _unquote(text):
    return (text or "").strip().strip('"').strip()


def _chp_time(text):
    try:
        return datetime.strptime(" ".join(_unquote(text).split()), "%b %d %Y %I:%M%p")
    except ValueError:
        return None


def parse_chp(body, point, radius_km=MAX_RADIUS_KM, window_min=CHP_WINDOW_MIN):
    """CHP logs within `radius_km` of `point`, logged within `window_min` of the newest log in the feed.

    The feed's times are local Pacific time without a zone, so the newest log stands in for "now".
    """
    root = ET.fromstring(body)
    logs = []
    for log in root.iter("Log"):
        match = re.fullmatch(r"\s*(\d+):(\d+)\s*", _unquote(log.findtext("LATLON")))
        logged = _chp_time(log.findtext("LogTime"))
        if not match or logged is None:
            continue
        lat, lon = int(match.group(1)) / 1e6, -int(match.group(2)) / 1e6
        logs.append((log, logged, lat, lon))
    newest = max((logged for _, logged, _, _ in logs), default=None)
    items = []
    for log, logged, lat, lon in logs:
        km = distance_km(point, (lat, lon))
        if km > radius_km or (newest - logged).total_seconds() > window_min * 60:
            continue
        details = [_unquote(d.findtext("IncidentDetail")) for d in log.iter("details")]
        units = [_unquote(u.findtext("UnitDetail")) for u in log.iter("units")]
        items.append({
            "id": f"CHP-{log.get('ID', '').strip()}",
            "source": "CHP incident log",
            "source_url": CHP_PAGE,
            "title": f"{_unquote(log.findtext('LogType'))} at {_unquote(log.findtext('Location'))}",
            "detail": "; ".join(item for item in [_unquote(log.findtext("LocationDesc")), *details[:4], *units[-1:]] if item),
            "time": logged.strftime("%H:%M"),
            "lat": round(lat, 5), "lon": round(lon, 5), "distance_km": round(km, 1),
            "basis": "live",
        })
    return items


def chp_near(point, radius_km=MAX_RADIUS_KM):
    try:
        items = parse_chp(fetch(CHP_URL, "text/xml"), point, radius_km)
    except FeedError as error:
        return _result("CHP incident log", CHP_URL, str(error), [])
    except ET.ParseError:
        return _result("CHP incident log", CHP_URL, "malformed response", [])
    return _result("CHP incident log", CHP_URL, "ok", items)


# ---------------------------------------------------------------------------
# Caltrans changeable message signs
# ---------------------------------------------------------------------------
def parse_signs(body, point, radius_km=MAX_RADIUS_KM):
    """Signs within `radius_km` of `point` that show a message."""
    items = []
    for record in json.loads(body).get("data", []):
        sign = record.get("cms") or {}
        where, message = sign.get("location") or {}, sign.get("message") or {}
        if str(message.get("display", "Blank")).lower() == "blank":
            continue
        lines = []
        for phase in ("phase1", "phase2"):
            block = message.get(phase) or {}
            lines += [block.get(f"{phase}Line{n}", "").strip() for n in (1, 2, 3)]
        text = " / ".join(line for line in lines if line)
        try:
            lat, lon = float(where["latitude"]), float(where["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        km = distance_km(point, (lat, lon))
        if not text or km > radius_km:
            continue
        stamp = sign.get("recordTimestamp") or {}
        items.append({
            "id": f"CMS-D7-{sign.get('index', '').strip()}",
            "source": "Caltrans message sign",
            "source_url": SIGNS_PAGE,
            "title": f"Sign on {where.get('route', '?')} {where.get('direction', '')}, {where.get('locationName', '')}".strip(),
            "detail": text,
            "time": str(stamp.get("recordTime", ""))[:5],
            "lat": round(lat, 5), "lon": round(lon, 5), "distance_km": round(km, 1),
            "basis": "live",
        })
    return items


def signs_near(point, radius_km=MAX_RADIUS_KM):
    try:
        items = parse_signs(fetch(SIGNS_URL, "application/json"), point, radius_km)
    except FeedError as error:
        return _result("Caltrans message signs", SIGNS_URL, str(error), [])
    except (ValueError, AttributeError):
        return _result("Caltrans message signs", SIGNS_URL, "malformed response", [])
    return _result("Caltrans message signs", SIGNS_URL, "ok", items)


# ---------------------------------------------------------------------------
# NWS alerts
# ---------------------------------------------------------------------------
def parse_alerts(body):
    items = []
    for feature in json.loads(body).get("features", []):
        props = feature.get("properties") or {}
        description = " ".join(str(props.get("description") or "").split())
        items.append({
            "id": "NWS-" + hashlib.sha1(str(props.get("id", "")).encode()).hexdigest()[:8].upper(),
            "source": "NWS alert",
            "source_url": props.get("@id") or NWS_PAGE,
            "title": str(props.get("headline") or props.get("event") or "Weather alert"),
            "detail": f"{props.get('event', '')}, severity {props.get('severity', 'unknown')}. {description[:300]}".strip(),
            "time": str(props.get("onset") or props.get("sent") or "")[11:16],
            "lat": None, "lon": None, "distance_km": None,
            "basis": "live",
        })
    return items


def alerts_at(point):
    url = NWS_URL.format(lat=point[0], lon=point[1])
    try:
        items = parse_alerts(fetch(url, "application/geo+json"))
    except FeedError as error:
        return _result("NWS alerts", url, str(error), [])
    except (ValueError, AttributeError):
        return _result("NWS alerts", url, "malformed response", [])
    return _result("NWS alerts", url, "ok", items)
