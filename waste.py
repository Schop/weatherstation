"""
Waste collection calendar from Ximmio's public API (used by e.g. Blink / Mijn Blink).

Two read-only requests: an address lookup (postcode + house number -> unique address id)
and the pickup calendar for that address. Results are cached for a few hours; if the
service is unreachable the last good data is kept (the calendar changes rarely).

Config (config.py): WASTE_COMPANY_CODE, WASTE_POSTCODE, WASTE_HOUSENUMBER
"""

import json
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

BASE = "https://wasteprod2api.ximmio.com"
CACHE_SECONDS = 6 * 3600
LOOKAHEAD_DAYS = 400

# Ximmio type name -> (Dutch label, colour key used by the page, show on the Home page?)
TYPES = {
    "GREY":                ("Restafval",         "grey",    True),
    "GREEN":               ("GFT",               "green",   True),
    "PAPER":               ("Papier",            "blue",    True),
    "PACKAGES":            ("Plastic & Verpakkingen", "orange",  True),
    "BRANCHES":            ("Snoeiafval",        "brown",   False),
    "MOBILETRANSFERPOINT": ("Mobiel afvalpunt",  "muted",   False),
}

_lock = threading.Lock()
_address_id = None
_cache = None          # (timestamp, data)


def _post(path, fields):
    req = urllib.request.Request(
        BASE + path, data=urllib.parse.urlencode(fields).encode(),
        headers={"User-Agent": "weatherstation/1.0", "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def _lookup(company, postcode, number):
    global _address_id
    if _address_id:
        return _address_id
    res = _post("/api/FetchAdress", {"companyCode": company, "postCode": postcode.replace(" ", "").upper(),
                                     "houseNumber": str(number), "houseLetter": "", "houseNumberAddition": ""})
    rows = res.get("dataList") or []
    if not rows:
        raise ValueError("address not found")
    _address_id = rows[0]["UniqueId"]
    return _address_id


def _parse(rows, today):
    """[{key, label, color, home, dates:[iso...], next, days}] sorted by next pickup."""
    out = []
    for r in rows:
        name = r.get("_pickupTypeText") or ""
        label, color, home = TYPES.get(name, (name.title() or "Onbekend", "muted", False))
        dates = []
        for raw in r.get("pickupDates") or []:
            try:
                d = datetime.strptime(raw[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if d >= today:
                dates.append(d)
        dates.sort()
        if not dates:
            continue
        out.append({"key": name, "label": label, "color": color, "home": home,
                    "dates": [d.isoformat() for d in dates[:6]],
                    "next": dates[0].isoformat(), "days": (dates[0] - today).days})
    out.sort(key=lambda x: (x["days"], x["label"]))
    return out


def calendar(company, postcode, number):
    """{'items': [...], 'updated': 'HH:MM', 'stale': bool} or None if never loaded."""
    global _cache
    with _lock:
        now = time.time()
        if _cache and now - _cache[0] < CACHE_SECONDS:
            return _cache[1]
        try:
            addr = _lookup(company, postcode, number)
            today = date.today()
            res = _post("/api/GetCalendar", {"companyCode": company, "uniqueAddressID": addr,
                                             "startDate": today.isoformat(),
                                             "endDate": (today + timedelta(days=LOOKAHEAD_DAYS)).isoformat()})
            if not res.get("status"):
                raise ValueError(f"calendar request refused (code {res.get('messageCode')})")
            data = {"items": _parse(res.get("dataList") or [], today),
                    "updated": time.strftime("%H:%M"), "stale": False}
            _cache = (now, data)
            return data
        except Exception as exc:
            print(f"[waste] fetch error: {exc}", file=sys.stderr)
            if _cache:                                    # keep the last good calendar
                stale = dict(_cache[1], stale=True)
                _cache = (now - CACHE_SECONDS + 300, stale)   # retry in 5 minutes
                return stale
            return None


def reparse_for_today(data):
    """Recompute 'days until' for cached data (the cache outlives midnight)."""
    today = date.today()
    items = []
    for it in data["items"]:
        dates = [d for d in it["dates"] if d >= today.isoformat()]
        if not dates:
            continue
        nxt = datetime.strptime(dates[0], "%Y-%m-%d").date()
        items.append(dict(it, dates=dates, next=dates[0], days=(nxt - today).days))
    items.sort(key=lambda x: (x["days"], x["label"]))
    return dict(data, items=items)
