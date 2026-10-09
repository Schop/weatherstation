"""
Buienradar 5-minute rain forecast for the configured location. The result is
cached so repeated page opens are cheap and a short outage serves the last good
data. (The radar map itself is Buienradar's embeddable widget, loaded by the page.)
"""

import sys
import threading
import time
import urllib.request

RAIN_URL = "https://gpsgadget.buienradar.nl/data/raintext?lat={lat:.2f}&lon={lon:.2f}"

RAIN_TTL = 120     # seconds
WET_MM = 0.1       # mm/h at or above this counts as rain

_lock = threading.Lock()
_cache = {}        # key -> (timestamp, value)


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "weatherstation/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read()


def _cached(key, ttl, loader):
    """Return a fresh cached value, else load; fall back to stale data on failure."""
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            value = loader()
        except Exception as exc:
            print(f"[radar] {key} fetch error: {exc}", file=sys.stderr)
            return hit[1] if hit else None
        _cache[key] = (time.time(), value)
        return value


def _mm_per_hour(raw):
    """Buienradar intensity (0-255) to mm/h."""
    return 0.0 if raw == 0 else round(10 ** ((raw - 109) / 32), 2)


def _parse_rain(text):
    points = []
    for line in text.splitlines():
        value, _, label = line.strip().partition("|")
        if value.isdigit() and label:
            points.append({"t": label, "mm": _mm_per_hour(int(value))})
    return points


def _summary(points):
    wet = [p["mm"] >= WET_MM for p in points]
    if not any(wet):
        return "dry", "Droog komende 2 uur"
    if wet[0]:
        for i, w in enumerate(wet):
            if not w:
                return "wet", f"Regen tot {points[i]['t']}"
        return "wet", "Regen komende 2 uur"
    first = wet.index(True)
    return "soon", f"Regen vanaf {points[first]['t']}"


def rain_forecast(lat, lon):
    """{'points': [{'t','mm'}...], 'state', 'summary', 'updated'} or None."""
    if lat is None or lon is None:                    # location not configured
        return None

    def load():
        points = _parse_rain(_get(RAIN_URL.format(lat=lat, lon=lon)).decode("utf-8", "replace"))
        if not points:
            raise ValueError("empty rain forecast")
        state, text = _summary(points)
        return {"points": points, "state": state, "summary": text,
                "updated": time.strftime("%H:%M")}
    return _cached("rain", RAIN_TTL, load)

