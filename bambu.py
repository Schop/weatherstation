"""
Bambu Lab printer status over the local MQTT interface (LAN).

The printer exposes MQTT over TLS on port 8883 (user "bblp", password = access
code) and publishes JSON status to  device/<serial>/report.  Reports can be
partial, so they are merged into one state dict; a "pushall" request on connect
asks the printer for the complete state.

Needs:  sudo apt install python3-paho-mqtt   (or: pip install paho-mqtt)
"""

import json
import ssl
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


class Printer:
    def __init__(self, host, serial, access_code):
        self.host = host
        self.serial = serial
        self.access_code = access_code
        self.topic_report = f"device/{serial}/report"
        self.topic_request = f"device/{serial}/request"

        self._lock = threading.Lock()
        self._print = {}          # merged "print" object from the reports
        self._connected = False
        self._last_msg = 0.0
        self._error = None
        self._client = None

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        if mqtt is None:
            self._error = "paho-mqtt is not installed"
            print("[bambu] paho-mqtt is not installed", file=sys.stderr)
            return
        cid = f"weatherstation-{int(time.time())}"
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
        except AttributeError:               # paho 1.x
            client = mqtt.Client(client_id=cid)
        client.username_pw_set("bblp", self.access_code)
        client.tls_set(cert_reqs=ssl.CERT_NONE)      # printer uses a self-signed cert
        client.tls_insecure_set(True)
        client.reconnect_delay_set(min_delay=2, max_delay=30)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        self._client = client
        client.connect_async(self.host, 8883, keepalive=30)
        client.loop_start()

    # -- callbacks -----------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if not _ok(rc):
            self._error = f"connection refused ({rc})"
            print(f"[bambu] connect failed: {rc}", file=sys.stderr)
            return
        self._error = None
        with self._lock:
            self._connected = True
        client.subscribe(self.topic_report)
        client.publish(self.topic_request,
                       json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))

    def _on_disconnect(self, client, userdata, *args):
        with self._lock:
            self._connected = False

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        p = data.get("print")
        if not isinstance(p, dict):
            return
        with self._lock:
            self._print.update(p)
            self._last_msg = time.time()

    # -- readout -------------------------------------------------------------

    def snapshot(self):
        with self._lock:
            p = dict(self._print)
            connected = self._connected
            age = time.time() - self._last_msg if self._last_msg else None
            error = self._error

        def num(key, cast=float):
            v = p.get(key)
            try:
                return cast(v)
            except (TypeError, ValueError):
                return None

        return {
            "configured": True,
            "connected": connected,
            "has_data": bool(p),
            "age": round(age) if age is not None else None,
            "error": error,
            "state": p.get("gcode_state"),
            "percent": num("mc_percent", int),
            "remaining_min": num("mc_remaining_time", int),
            "layer": num("layer_num", int),
            "total_layers": num("total_layer_num", int),
            "file": p.get("subtask_name") or p.get("gcode_file") or None,
            "nozzle": num("nozzle_temper"),
            "nozzle_target": num("nozzle_target_temper"),
            "bed": num("bed_temper"),
            "bed_target": num("bed_target_temper"),
            "chamber": num("chamber_temper"),
            "wifi": p.get("wifi_signal"),
            "filament": self._filament(p),
        }

    @staticmethod
    def _filament(p):
        ams = p.get("ams")
        if not isinstance(ams, dict):
            return []
        active = str(ams.get("tray_now", ""))
        trays = []
        for unit_idx, unit in enumerate(ams.get("ams", [])):
            for tray in unit.get("tray", []):
                try:
                    slot = int(tray.get("id", 0))
                except (TypeError, ValueError):
                    slot = 0
                color = str(tray.get("tray_color") or "")[:6]
                trays.append({
                    "type": tray.get("tray_type") or None,
                    "color": color if len(color) == 6 else None,
                    "remain": tray.get("remain"),
                    "active": active == str(int(unit.get("id", unit_idx)) * 4 + slot),
                })
        return trays
