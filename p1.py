"""
HomeWizard P1 meter, read directly over the meter's local API (v1).

A background thread polls http://<host>/api/v1/data every couple of seconds (read-only
GET requests, no login needed) and keeps:
  * the latest reading: grid power, per-phase power/voltage/current, totals, health
  * a 24 h power history (30-second averages), saved to a JSON file so it survives restarts
  * the meter totals at the start of the day, so "today" = now minus that baseline
    (the meter only reports lifetime totals)

Power is positive when importing from the grid and negative when exporting.
House usage is derived: solar production (an optional callback, e.g. Home Assistant)
plus the grid power.
"""

import json
import os
import sys
import threading
import time
import urllib.request
from collections import deque
from datetime import date

POLL_SECONDS = 2
BUCKET_SECONDS = 30            # one history point per 30 s (mean of the readings in it)
KEEP_SECONDS = 24 * 3600
SAVE_SECONDS = 300
STALE_SECONDS = 15             # no successful reading for this long = meter offline
SOLAR_SECONDS = 20             # how often the solar callback is asked
PARTIAL_AFTER = 600            # baseline taken >10 min after midnight = "today" is partial
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "p1_data.json")


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _midnight(ts):
    return time.mktime(date.fromtimestamp(ts).timetuple())


class Meter:
    def __init__(self, host, state_file=STATE_FILE, solar_fn=None):
        self.host = host
        self.state_file = state_file
        self.solar_fn = solar_fn

        self._lock = threading.Lock()
        self._data = None
        self._last_ok = None
        self._error = None
        self._history = deque()      # [t, watts]
        self._bucket = None          # [start, sum, count]
        self._baseline = None        # {"date", "t", "imp", "exp", "gas"}
        self._last_save = 0.0
        self._solar = None
        self._solar_at = 0.0
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self):
        try:
            with open(self.state_file, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return
        cutoff = time.time() - KEEP_SECONDS
        for p in saved.get("history", []):
            if (isinstance(p, list) and len(p) == 2 and _num(p[0]) is not None
                    and _num(p[1]) is not None and p[0] > cutoff):
                self._history.append([p[0], p[1]])
        b = saved.get("baseline")
        if isinstance(b, dict) and b.get("date"):
            self._baseline = b

    def _save(self):
        tmp = self.state_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"history": list(self._history), "baseline": self._baseline}, f)
            os.replace(tmp, self.state_file)
        except OSError as exc:
            print(f"[p1] could not save: {exc}", file=sys.stderr)

    # -- polling -------------------------------------------------------------

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="p1").start()

    def _loop(self):
        while True:
            try:
                self._poll_once()
            except Exception as exc:                     # keep the thread alive
                print(f"[p1] poll error: {exc}", file=sys.stderr)
            time.sleep(POLL_SECONDS)

    def _poll_once(self):
        now = time.time()
        if self.solar_fn and now - self._solar_at >= SOLAR_SECONDS:
            self._solar_at = now
            try:
                solar = self.solar_fn()
            except Exception as exc:
                print(f"[p1] solar lookup failed: {exc}", file=sys.stderr)
                solar = None
            with self._lock:
                self._solar = solar

        req = urllib.request.Request(f"http://{self.host}/api/v1/data",
                                     headers={"User-Agent": "weatherstation/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read())
        except Exception as exc:
            with self._lock:
                self._error = f"unreachable ({getattr(exc, 'reason', exc)})"
            return
        self.ingest(data, time.time())

    def ingest(self, d, now):
        """Take one meter reading (also used directly by tests)."""
        w = _num(d.get("active_power_w"))
        with self._lock:
            self._data, self._last_ok, self._error = d, now, None

            if w is not None:                            # 30 s averages for the history
                b = self._bucket
                if b is None:
                    self._bucket = [now, w, 1]
                else:
                    b[1] += w
                    b[2] += 1
                    if now - b[0] >= BUCKET_SECONDS:
                        self._history.append([int(now), round(b[1] / b[2])])
                        self._bucket = None
                cutoff = now - KEEP_SECONDS
                while self._history and self._history[0][0] < cutoff:
                    self._history.popleft()

            today = date.fromtimestamp(now).isoformat()   # new day: remember the totals
            changed = False
            if self._baseline is None or self._baseline.get("date") != today:
                self._baseline = {"date": today, "t": now,
                                  "imp": _num(d.get("total_power_import_kwh")),
                                  "exp": _num(d.get("total_power_export_kwh")),
                                  "gas": _num(d.get("total_gas_m3"))}
                changed = True
            if changed or now - self._last_save >= SAVE_SECONDS:
                self._last_save = now
                self._save()

    # -- readout -------------------------------------------------------------

    def snapshot(self, now=None):
        now = now or time.time()
        with self._lock:
            d, last_ok, error = self._data, self._last_ok, self._error
            base, solar = self._baseline, self._solar
        if d is None:
            return {"configured": True, "ok": False, "error": error}

        age = now - last_ok
        grid = _num(d.get("active_power_w"))

        phases = []
        for n in (1, 2, 3):
            w = _num(d.get(f"active_power_l{n}_w"))
            if w is not None:
                phases.append({"n": n, "w": w, "v": _num(d.get(f"active_voltage_l{n}_v")),
                               "a": _num(d.get(f"active_current_l{n}_a"))})

        house = None
        if grid is not None and solar is not None:
            export = max(0.0, -grid)
            house = {"w": round(max(0.0, solar + grid)), "solar_w": round(solar),
                     "direct_pct": (round(max(0.0, min(100.0, 100 * (solar - export) / solar)))
                                    if solar > 50 else None)}

        today = None
        if base:
            def diff(key, cur):
                cur, start = _num(cur), _num(base.get(key))
                return None if cur is None or start is None else max(0.0, round(cur - start, 3))
            today = {"import": diff("imp", d.get("total_power_import_kwh")),
                     "export": diff("exp", d.get("total_power_export_kwh")),
                     "gas": diff("gas", d.get("total_gas_m3")),
                     "since": base["t"],
                     "partial": base["t"] > _midnight(base["t"]) + PARTIAL_AFTER}

        return {
            "configured": True,
            "ok": age < STALE_SECONDS,
            "error": error if age >= STALE_SECONDS else None,
            "age": round(age),
            "power": grid,
            "phases": phases,
            "house": house,
            "today": today,
            "tariff": d.get("active_tariff"),
            "wifi": d.get("wifi_strength"),
            "sags": [d.get(f"voltage_sag_l{n}_count") for n in (1, 2, 3)],
            "swells": [d.get(f"voltage_swell_l{n}_count") for n in (1, 2, 3)],
            "fails": d.get("any_power_fail_count"),
            "long_fails": d.get("long_power_fail_count"),
        }

    def history(self, now=None):
        with self._lock:
            return {"now": now or time.time(), "points": list(self._history)}
