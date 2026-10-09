"""
Weather Station — Flask server.

Install:  pip install flask
Run:      python server.py
Pi kiosk: chromium-browser --kiosk --noerrdialogs --disable-infobars http://localhost:5000
"""

import threading
import time
import config
import garage
import homeassistant
import solaredge
import p1
import publicip
import speedtest
import waste
import lanscan
import netstatus
import radar
import sysinfo
import weather
from flask import Flask, render_template, jsonify, request

_STARTED = time.time()

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

_lock = threading.Lock()
_data = None          # WeatherData | None
_state = "loading"   # "loading" | "ok" | "error"
_garage = None        # garage.GarageDoor | None (started in __main__ if configured)
_p1 = None            # p1.Meter | None (started in __main__ if configured)
_speedtest = speedtest.SpeedTest()
_solar = None          # solaredge.Inverter | None (started in __main__ if configured)
_publicip = publicip.PublicIP()
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
    data["public_ip"] = _publicip.snapshot()
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


@app.route("/api/power")
def power():
    if _p1 is None:
        resp = jsonify({"configured": False})
    else:
        resp = jsonify(_p1.snapshot())
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/power/history")
def power_history():
    resp = jsonify(_p1.history() if _p1 is not None else {"points": []})
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _solar_watts():
    """Current solar production in watts: the inverter itself (live, local) first, Home Assistant as a fallback."""
    if _solar is not None:
        w = _solar.live_watts()
        if w is not None:
            return w
    if not (config.HA_TOKEN and config.HA_SOLAR_POWER):
        return None
    res = homeassistant.fetch_states(config.HA_URL, config.HA_TOKEN, [config.HA_SOLAR_POWER])
    st = res["states"].get(config.HA_SOLAR_POWER)
    if not st or not st["ok"] or st["value"] is None:
        return None
    return st["value"] * 1000 if st["unit"] == "kW" else st["value"]


@app.route("/api/speedtest", methods=["GET", "POST"])
def speed_test():
    started = False
    if request.method == "POST":                 # the button: starts a test unless one is already running
        started = _speedtest.start()
    resp = jsonify(dict(_speedtest.snapshot(), started=started))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/waste")
def waste_calendar():
    data = waste.calendar(config.WASTE_COMPANY_CODE, config.WASTE_POSTCODE, config.WASTE_HOUSENUMBER)
    resp = jsonify(waste.reparse_for_today(data) if data else {"error": "unavailable"})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/solar")
def solar():
    resp = jsonify(_solar.snapshot() if _solar is not None else {"configured": False})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/solar/history")
def solar_history():
    resp = jsonify(_solar.history() if _solar is not None else {"points": []})
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

    if config.GARAGE_MQTT_HOST and config.GARAGE_TOPIC:
        _garage = garage.GarageDoor(config.GARAGE_MQTT_HOST, config.GARAGE_MQTT_PORT,
                                    config.GARAGE_MQTT_USER, config.GARAGE_MQTT_PASSWORD,
                                    config.GARAGE_TOPIC)
        _garage.start()

    if config.SOLAR_HOST:
        _solar = solaredge.Inverter(config.SOLAR_HOST, config.SOLAR_PORT, config.SOLAR_UNIT,
                                   rated_w=config.SOLAR_RATED_W)
        _solar.start()

    if config.P1_HOST:
        _p1 = p1.Meter(config.P1_HOST, solar_fn=_solar_watts)
        _p1.start()

    _publicip.start()

    if config.NETWORK_SCAN:
        _scanner = lanscan.Scanner(config.NETWORK_SUBNET, config.NETWORK_LABELS)
        _scanner.start()

    # Kick off the first fetch immediately
    refresh_ev.set()

    print(f"Weather Station running at http://localhost:5000")
    missing = [n for n in ("LATITUDE", "LONGITUDE") if getattr(config, n) is None]
    if missing:
        print(f"WARNING: {', '.join(missing)} not set: no weather or rain data. Set it in config_local.py.")
    if not (config.WASTE_COMPANY_CODE and config.WASTE_POSTCODE and config.WASTE_HOUSENUMBER):
        print("Note: waste calendar not configured (WASTE_COMPANY_CODE / WASTE_POSTCODE / WASTE_HOUSENUMBER).")
    print(f"Location: {config.LOCATION_NAME}  |  Refresh: {config.REFRESH_INTERVAL}s")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
