"""
Garage door state from an MQTT topic (e.g. an ESP32 publishing to Home Assistant's
Mosquitto broker). Subscribes read-only and keeps the last state and when it changed.

Payloads are matched case-insensitively: OPEN / CLOSED. Anything else is passed
through as "unknown" so a new payload value never shows as a wrong state.

Needs:  sudo apt install python3-paho-mqtt   (or: pip install paho-mqtt)
"""

import sys
import threading
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None


def _ok(rc):
    """True for a successful connect result in both paho 1.x (int) and 2.x (ReasonCode)."""
    return rc == 0 if isinstance(rc, int) else not rc.is_failure


class GarageDoor:
    def __init__(self, host, port, user, password, topic):
        self.host, self.port, self.topic = host, port, topic
        self.user, self.password = user, password

        self._lock = threading.Lock()
        self._state = None        # "open" | "closed" | "unknown" | None (nothing received yet)
        self._since = None        # when WE saw it change (None until a change is observed)
        self._last_msg = None
        self._connected = False
        self._error = None

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
            if self._state is not None and state != self._state:
                self._since = now            # a change we actually observed
            self._state = state
            self._last_msg = now

    # -- readout -------------------------------------------------------------

    def snapshot(self):
        with self._lock:
            return {
                "configured": True,
                "connected": self._connected,
                "error": self._error,
                "state": self._state,
                "since": self._since,
                "age": round(time.time() - self._since) if self._since else None,
            }
