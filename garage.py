"""
Garage door events from an MQTT topic (e.g. an ESP32 publishing to Home Assistant's
Mosquitto broker). Subscribes read-only, records every open/closed change with its
timestamp, and keeps the history in a JSON file so it survives restarts.

Payloads are matched case-insensitively: OPEN / CLOSED. Anything else is ignored
(and shown as "unknown" for the live state) so a new payload value never records a
wrong event.

Needs:  sudo apt install python3-paho-mqtt   (or: pip install paho-mqtt)
"""

import json
import os
import sys
import threading
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

MAX_EVENTS = 500
FORGET_AFTER = 90 * 86400       # keep three months
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "garage_events.json")


def _ok(rc):
    """True for a successful connect result in both paho 1.x (int) and 2.x (ReasonCode)."""
    return rc == 0 if isinstance(rc, int) else not rc.is_failure


class GarageDoor:
    def __init__(self, host, port, user, password, topic, state_file=STATE_FILE):
        self.host, self.port, self.topic = host, port, topic
        self.user, self.password = user, password
        self.state_file = state_file

        self._lock = threading.Lock()
        self._state = None        # "open" | "closed" | "unknown" | None (nothing received yet)
        self._events = []         # [{"t": epoch, "state": "open"|"closed"}], oldest first
        self._connected = False
        self._error = None
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self):
        try:
            with open(self.state_file, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return
        cutoff = time.time() - FORGET_AFTER
        self._events = [e for e in saved
                        if isinstance(e, dict) and e.get("state") in ("open", "closed")
                        and isinstance(e.get("t"), (int, float)) and e["t"] > cutoff][-MAX_EVENTS:]

    def _save(self):
        tmp = self.state_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._events, f)
            os.replace(tmp, self.state_file)
        except OSError as exc:
            print(f"[garage] could not save events: {exc}", file=sys.stderr)

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        if mqtt is None:
            self._error = "paho-mqtt is not installed"
            print("[garage] paho-mqtt is not installed", file=sys.stderr)
            return
        cid = f"weatherstation-garage-{int(time.time())}"
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
        except AttributeError:               # paho 1.x
            client = mqtt.Client(client_id=cid)
        if self.user:                       # an open broker (no login) is fine too
            client.username_pw_set(self.user, self.password)
        client.reconnect_delay_set(min_delay=2, max_delay=30)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.connect_async(self.host, self.port, keepalive=30)
        client.loop_start()

    # -- callbacks -----------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if not _ok(rc):
            self._error = f"connection refused ({rc})"
            print(f"[garage] connect failed: {rc}", file=sys.stderr)
            return
        self._error = None
        with self._lock:
            self._connected = True
        client.subscribe(self.topic)

    def _on_disconnect(self, client, userdata, *args):
        with self._lock:
            self._connected = False

    def _on_message(self, client, userdata, msg):
        text = msg.payload.decode("utf-8", "replace").strip().upper()
        state = {"OPEN": "open", "CLOSED": "closed"}.get(text, "unknown")
        now = time.time()
        with self._lock:
            self._state = state
            if state == "unknown":
                return
            # The device may repeat its state periodically (or send it retained on
            # every connect): only a different state from the last recorded event
            # is a new event.
            if self._events and self._events[-1]["state"] == state:
                return
            self._events.append({"t": now, "state": state})
            del self._events[:-MAX_EVENTS]
            self._save()

    # -- readout -------------------------------------------------------------

    def snapshot(self, limit=60):
        with self._lock:
            events = list(self._events)
            state, connected, error = self._state, self._connected, self._error

        # newest first; for each event, how long the door then stayed in that state
        out = []
        for i in range(len(events) - 1, -1, -1):
            e = events[i]
            end = events[i + 1]["t"] if i + 1 < len(events) else None
            out.append({"t": e["t"], "state": e["state"],
                        "duration": round(end - e["t"]) if end else None})
        return {
            "configured": True,
            "connected": connected,
            "error": error,
            "state": state,
            "since": events[-1]["t"] if events else None,
            "events": out[:limit],
            "total": len(events),
            "now": time.time(),
        }
