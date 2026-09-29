"""
Weather Station — Flask server.

Install:  pip install flask
Run:      python server.py
Pi kiosk: chromium-browser --kiosk --noerrdialogs --disable-infobars http://localhost:5000
"""

import threading
import config
import weather
from flask import Flask, render_template, jsonify

app = Flask(__name__)

_lock = threading.Lock()
_data = None          # WeatherData | None
_state = "loading"   # "loading" | "ok" | "error"


# ---------------------------------------------------------------------------
# Icon mapping (emoji — rendered natively by Chromium / Noto Color Emoji)
# ---------------------------------------------------------------------------

_ICON_EMOJI = {
    "clear_day":     "☀️",
    "clear_night":   "🌙",
    "partly_cloudy": "⛅",
    "cloudy":        "☁️",
    "fog":           "🌫️",
    "drizzle":       "🌦️",
    "rain":          "🌧️",
    "snow":          "❄️",
    "sleet":         "🌨️",
    "storm":         "⛈️",
}


def icon_emoji(icon_type):
    return _ICON_EMOJI.get(icon_type, "☁️")


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
        refresh_event.wait(config.REFRESH_INTERVAL)


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
                               strip_hourly=[], strip_tomorrow=None,
                               hourly_items=[])

    # Bottom strip: next 4 hours + tomorrow
    strip_hourly = [
        {"hour": h, "icon": icon_emoji(h.icon_type)}
        for h in data.hourly[1:5]
    ]
    tomorrow = data.forecast[1] if len(data.forecast) > 1 else None
    strip_tomorrow = {"day": tomorrow, "icon": icon_emoji(tomorrow.icon_type)} if tomorrow else None

    # Hourly detail page (10 hours from now)
    hourly_items = [
        {"hour": h, "icon": icon_emoji(h.icon_type)}
        for h in data.hourly[:10]
    ]

    return render_template(
        "index.html",
        data=data,
        state=state,
        cfg=config,
        main_icon=icon_emoji(data.icon_type),
        strip_hourly=strip_hourly,
        strip_tomorrow=strip_tomorrow,
        hourly_items=hourly_items,
    )


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

    # Kick off the first fetch immediately
    refresh_ev.set()

    print(f"Weather Station running at http://localhost:5000")
    print(f"Location: {config.LOCATION_NAME}  |  Refresh: {config.REFRESH_INTERVAL}s")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
