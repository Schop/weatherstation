"""
Weather Station — Flask server.

Install:  pip install flask
Run:      python server.py
Pi kiosk: chromium-browser --kiosk --noerrdialogs --disable-infobars http://localhost:5000
"""

import threading
import time
import bambu
import config
import garage
import homeassistant
import lanscan
import netstatus
import radar
import sysinfo
import weather
from flask import Flask, render_template, jsonify

_STARTED = time.time()

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

_lock = threading.Lock()
_data = None          # WeatherData | None
_state = "loading"   # "loading" | "ok" | "error"
_printer = None       # bambu.Printer | None (started in __main__ if configured)
_garage = None        # garage.GarageDoor | None (started in __main__ if configured)
_scanner = None       # lanscan.Scanner | None (started in __main__ if enabled)


# ---------------------------------------------------------------------------
# Icon mapping (Weather Icons font — static/style.css maps classes to glyphs)
# ---------------------------------------------------------------------------

_ICON_CLASS = {
    "clear_day":     "wi-day-sunny",
    "clear_night":   "wi-night-clear",
    "partly_cloudy": "wi-day-cloudy",
    "cloudy":        "wi-cloudy",
    "fog":           "wi-fog",
    "drizzle":       "wi-sprinkle",
    "rain":          "wi-rain",
    "snow":          "wi-snow",
    "sleet":         "wi-sleet",
    "storm":         "wi-thunderstorm",
}


_DAY_SHORT = {
    "Monday": "Maandag", "Tuesday": "Dinsdag", "Wednesday": "Woensdag",
    "Thursday": "Donderdag", "Friday": "Vrijdag", "Saturday": "Zaterdag",
    "Sunday": "Zondag",
}


def icon_class(icon_type):
    return _ICON_CLASS.get(icon_type, "wi-cloudy")


# ---------------------------------------------------------------------------
# Background fetch thread
# ---------------------------------------------------------------------------

def _fetch_loop(stop_event, refresh_event):
    global _data, _state
    while not stop_event.is_set():
        refresh_event.clear()
        raw = weather.fetch_weather(config.LATITUDE, config.LONGITUDE)
        with _lock:
            if raw is not None:
                _data  = weather.parse_weather(raw)
                _state = "ok"
            elif _data is None:
                _state = "error"
        # Retry in 30 s if we have no data yet (e.g. DNS not ready at boot)
        interval = 30 if _state == "error" else config.REFRESH_INTERVAL
        refresh_event.wait(interval)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    with _lock:
        data  = _data
        state = _state

    if data is None:
        return render_template("index.html",
                               data=None, state=state, cfg=config,
                               main_icon="",
                               strip_hourly=[], daily_items=[])

    # Main page: next 8 hours
    strip_hourly = [
        {"hour": h, "icon": icon_class(h.icon_type)}
        for h in data.hourly[1:9]
    ]

    # Main page: 7-day forecast
    daily_items = [
        {"day": d, "icon": icon_class(d.icon_type),
         "label": "Vandaag" if i == 0 else _DAY_SHORT.get(d.day_name, d.day_name)}
        for i, d in enumerate(data.forecast[:6])
    ]

    return render_template(
        "index.html",
        data=data,
        state=state,
        cfg=config,
        main_icon=icon_class(data.icon_type),
        strip_hourly=strip_hourly,
        daily_items=daily_items,
    )


@app.route("/api/system")
def system():
    info = sysinfo.snapshot()
    with _lock:
        info["weather_state"] = _state
        info["weather_updated"] = (_data.fetched_at.strftime("%H:%M:%S")
                                   if _data else None)
    info["app_uptime"] = int(time.time() - _STARTED)
    info["refresh_interval"] = config.REFRESH_INTERVAL
    resp = jsonify(info)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/network")
def network():
    data = dict(netstatus.snapshot(config.NETWORK_DEVICES))   # copy: the original is cached
    if _scanner is not None:
        gw = (data.get("gateway") or {}).get("ip")
        data["lan"] = _scanner.snapshot(exclude={d["host"] for d in config.NETWORK_DEVICES},
                                        gateway=gw)
    data["now"] = time.time()
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/rain")
def rain():
    data = radar.rain_forecast(config.LATITUDE, config.LONGITUDE)
    resp = jsonify(data or {"error": "unavailable"})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/garage")
def garage_door():
    if _garage is None:
        resp = jsonify({"configured": False})
    else:
        resp = jsonify(_garage.snapshot())
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/energy")
def energy():
    if not config.HA_TOKEN:
        resp = jsonify({"configured": False})
    else:
        ids = list(config.HA_SOLAR.values()) + [p["entity"] for p in config.HA_P1]
        res = homeassistant.fetch_states(config.HA_URL, config.HA_TOKEN, ids)
        got = res["states"]
        resp = jsonify({
            "configured": True,
            "error": res["error"],
            "solar": {k: got.get(e) for k, e in config.HA_SOLAR.items()},
            "p1": [dict(got.get(p["entity"]) or {}, label=p["label"], entity=p["entity"])
                   for p in config.HA_P1],
            "now": time.time(),
        })
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/printer")
def printer():
    if _printer is None:
        resp = jsonify({"configured": False})
    else:
        resp = jsonify(_printer.snapshot())
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/ping")
def ping():
    with _lock:
        return jsonify({"state": _state, "has_data": _data is not None})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    stop_ev    = threading.Event()
    refresh_ev = threading.Event()

    t = threading.Thread(target=_fetch_loop, args=(stop_ev, refresh_ev),
                         daemon=True, name="fetch")
    t.start()

    if config.BAMBU_SERIAL and config.BAMBU_ACCESS_CODE:
        _printer = bambu.Printer(config.BAMBU_HOST, config.BAMBU_SERIAL,
                                 config.BAMBU_ACCESS_CODE)
        _printer.start()

    if config.GARAGE_MQTT_USER and config.GARAGE_TOPIC:
        _garage = garage.GarageDoor(config.GARAGE_MQTT_HOST, config.GARAGE_MQTT_PORT,
                                    config.GARAGE_MQTT_USER, config.GARAGE_MQTT_PASSWORD,
                                    config.GARAGE_TOPIC)
        _garage.start()

    if config.NETWORK_SCAN:
        _scanner = lanscan.Scanner(config.NETWORK_SUBNET, config.NETWORK_LABELS)
        _scanner.start()

    # Kick off the first fetch immediately
    refresh_ev.set()

    print(f"Weather Station running at http://localhost:5000")
    print(f"Location: {config.LOCATION_NAME}  |  Refresh: {config.REFRESH_INTERVAL}s")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
