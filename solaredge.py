"""
SolarEdge inverter over Modbus TCP (SunSpec), strictly read-only.

Only Modbus function 3 (read holding registers) is ever sent; there is no write code in
this module. Each poll opens a short-lived connection and closes it again, because a
SolarEdge inverter accepts one Modbus connection at a time and this keeps the slot free.

The register layout is not hard-coded: the SunSpec model list is walked once (marker "SunS"
at register 40000, then [model id, length] pairs) and the inverter model (101/102/103),
the common block (manufacturer / model / version) and the nameplate (rated power) are
located from it.

A background thread keeps:
  * the latest reading (power, AC/DC values, temperatures, status, events)
  * a 24 h power history (30 s averages)
  * the lifetime-energy reading at the start of each day, so "today" = now minus that, and
    a list of finished days (the inverter itself only reports lifetime energy)
  * today's peak power
all saved to solar_data.json so a restart does not lose them.
"""

import json
import os
import socket
import struct
import sys
import threading
import time
from collections import deque
from datetime import date, timedelta

POLL_SECONDS = 5
CONNECT_TIMEOUT = 4
IO_TIMEOUT = 4
BUCKET_SECONDS = 30
KEEP_SECONDS = 24 * 3600
SAVE_SECONDS = 300
STALE_SECONDS = 30               # no good reading for this long = inverter not answering
DAYS_KEEP = 60
SUNSPEC_BASE = 40000
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "solar_data.json")

STATUS = {1: "Off", 2: "Sleeping", 3: "Starting", 4: "Producing", 5: "Throttled",
          6: "Shutting down", 7: "Fault", 8: "Standby"}
IDLE_CODES = (1, 2, 3, 6, 8)     # states in which no power is produced
EVENTS = ["Ground fault", "DC over-voltage", "AC disconnect", "DC disconnect", "Grid disconnect",
          "Cabinet open", "Manual shutdown", "Over-temperature", "Over-frequency", "Under-frequency",
          "AC over-voltage", "AC under-voltage", "Blown string fuse", "Under-temperature",
          "Memory loss", "Hardware test failure"]
EXCEPTIONS = {1: "illegal function", 2: "illegal data address", 3: "illegal data value",
              4: "device failure", 5: "acknowledge", 6: "device busy"}


class ModbusError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Minimal Modbus TCP client (function 3 only)
# ---------------------------------------------------------------------------

def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ModbusError("connection closed by the inverter")
        buf += chunk
    return buf


class ModbusClient:
    def __init__(self, host, port, unit=1, connect_timeout=CONNECT_TIMEOUT, io_timeout=IO_TIMEOUT):
        self.host, self.port, self.unit = host, port, unit
        self.connect_timeout, self.io_timeout = connect_timeout, io_timeout
        self._sock = None
        self._tid = 0

    def connect(self):
        self._sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        self._sock.settimeout(self.io_timeout)

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def read_holding(self, address, count):
        """Registers address .. address+count-1 as a list of unsigned 16-bit ints."""
        if not 1 <= count <= 125:
            raise ValueError("count must be 1..125")
        self._tid = (self._tid + 1) & 0xFFFF
        self._sock.sendall(struct.pack(">HHHBBHH", self._tid, 0, 6, self.unit, 3, address, count))

        tid, proto, length, unit = struct.unpack(">HHHB", _recv_exact(self._sock, 7))
        if proto != 0 or not 2 <= length <= 253:
            raise ModbusError("malformed reply header")
        if tid != self._tid:
            raise ModbusError("reply does not match the request")
        pdu = _recv_exact(self._sock, length - 1)
        function = pdu[0]
        if function == 0x83:
            code = pdu[1] if len(pdu) > 1 else None
            raise ModbusError(f"modbus exception: {EXCEPTIONS.get(code, code)}", code)
        if function != 3 or len(pdu) != 2 + count * 2 or pdu[1] != count * 2:
            raise ModbusError("unexpected reply")
        return list(struct.unpack(f">{count}H", pdu[2:]))


# ---------------------------------------------------------------------------
# SunSpec decoding
# ---------------------------------------------------------------------------

def _s16(v):
    return v - 65536 if v >= 32768 else v


def _scaled(raw, sf, signed=False):
    """SunSpec value = raw * 10^sf. A raw 0xFFFF (unsigned) / 0x8000 (signed) or a scale factor
    of 0x8000 means 'not implemented' -> None."""
    if sf == 0x8000:
        return None
    if signed:
        if raw == 0x8000:
            return None
        raw = _s16(raw)
    elif raw == 0xFFFF:
        return None
    return round(raw * 10 ** _s16(sf), 6)


def _text(regs):
    data = b"".join(struct.pack(">H", r) for r in regs)
    return data.split(b"\x00")[0].decode("ascii", "replace").strip()


def decode_inverter(r):
    """Decode a SunSpec inverter model 101/102/103 block (r[0] is the model id)."""
    if len(r) < 40 or r[0] not in (101, 102, 103):
        raise ModbusError("not an inverter model block")
    model = r[0]
    phase_count = {101: 1, 102: 2, 103: 3}[model]
    phases = []
    for n in range(phase_count):
        phases.append({"n": n + 1, "a": _scaled(r[3 + n], r[6]), "v": _scaled(r[10 + n], r[13])})

    acc = (r[24] << 16) | r[25]
    lifetime = None if r[26] == 0x8000 or acc == 0 else round(acc * 10 ** _s16(r[26]), 3)
    status = r[38] if r[38] != 0xFFFF else None
    evt = (r[40] << 16 | r[41]) if len(r) > 41 else 0
    if evt == 0xFFFFFFFF:                        # SunSpec 'not implemented': not a set of active events
        evt = 0
    return {
        "model": model,
        "power": _scaled(r[14], r[15], signed=True),
        "apparent": _scaled(r[18], r[19], signed=True),
        "reactive": _scaled(r[20], r[21], signed=True),
        "pf": _scaled(r[22], r[23], signed=True),
        "frequency": _scaled(r[16], r[17]),
        "ac_current": _scaled(r[2], r[6]),
        "phases": phases,
        "lifetime_wh": lifetime,
        "dc_current": _scaled(r[27], r[28]),
        "dc_voltage": _scaled(r[29], r[30]),
        "dc_power": _scaled(r[31], r[32], signed=True),
        "temps": {"sink": _scaled(r[34], r[37], signed=True), "cabinet": _scaled(r[33], r[37], signed=True),
                  "transformer": _scaled(r[35], r[37], signed=True), "other": _scaled(r[36], r[37], signed=True)},
        "status_code": status,
        "status": STATUS.get(status, "Unknown") if status is not None else None,
        "events": [name for bit, name in enumerate(EVENTS) if evt & (1 << bit)],
    }


# ---------------------------------------------------------------------------
# Inverter: polling thread + derived data
# ---------------------------------------------------------------------------

class Inverter:
    def __init__(self, host, port=1502, unit=1, state_file=STATE_FILE, rated_w=None):
        self.host, self.port, self.unit = host, port, unit
        self.rated_w = rated_w            # used when the inverter does not report its own nameplate
        self.state_file = state_file
        self._lock = threading.Lock()
        self._layout = None          # {"inverter": (id, addr, len), "models": [...]}
        self._info = {}              # manufacturer / model / version / rated_w
        self._data = None
        self._data_t = None
        self._error = None
        self._history = deque()      # [t, watts]
        self._bucket = None          # [start, sum, count]
        self._baseline = None        # {"date", "wh", "t", "partial"}
        self._last = None            # {"date", "wh"}: the latest lifetime reading
        self._days = []              # finished days: {"date", "wh", "partial"}
        self._peak = {"date": None, "w": 0, "t": None}
        self._last_save = 0.0
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self):
        try:
            with open(self.state_file, encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, ValueError):
            return
        cutoff = time.time() - KEEP_SECONDS
        for p in s.get("history", []):
            if isinstance(p, list) and len(p) == 2 and p[0] > cutoff:
                self._history.append([p[0], p[1]])
        self._baseline, self._last = s.get("baseline"), s.get("last")
        self._days = [d for d in s.get("days", []) if isinstance(d, dict) and "date" in d][-DAYS_KEEP:]
        self._peak = s.get("peak") or self._peak

    def _save(self):
        tmp = self.state_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"history": list(self._history), "baseline": self._baseline, "last": self._last,
                           "days": self._days, "peak": self._peak}, f)
            os.replace(tmp, self.state_file)
        except OSError as exc:
            print(f"[solar] could not save: {exc}", file=sys.stderr)

    # -- polling -------------------------------------------------------------

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="solar").start()

    def _loop(self):
        while True:
            try:
                self.poll_once()
            except Exception as exc:                      # keep the thread alive
                print(f"[solar] poll error: {exc}", file=sys.stderr)
            time.sleep(POLL_SECONDS)

    def _discover(self, c):
        marker = c.read_holding(SUNSPEC_BASE, 2)
        if marker != [0x5375, 0x6E53]:                    # "SunS"
            raise ModbusError("not a SunSpec device (marker missing)")
        models, addr = {}, SUNSPEC_BASE + 2
        for _ in range(40):
            try:
                mid, length = c.read_holding(addr, 2)
            except ModbusError as exc:
                if exc.code == 2:                         # ran off the end of the model list
                    break
                raise
            if mid in (0, 0xFFFF) or length == 0:
                break
            models.setdefault(mid, (addr, length))
            addr += 2 + length
        inv = next((m for m in (103, 102, 101) if m in models), None)
        if inv is None:
            raise ModbusError("no inverter model in the SunSpec list")

        info = {}
        if 1 in models:
            a, n = models[1]
            regs = c.read_holding(a, min(n + 2, 125))
            info.update(manufacturer=_text(regs[2:18]), model=_text(regs[18:34]), version=_text(regs[42:50]))
        if 120 in models:
            a, _ = models[120]
            regs = c.read_holding(a, 5)
            info["rated_w"] = _scaled(regs[3], regs[4])
        self._info = info
        return {"inverter": (inv, *models[inv]), "models": sorted(models)}

    def read(self):
        """One complete read of the inverter. Raises OSError / ModbusError on failure."""
        c = ModbusClient(self.host, self.port, self.unit)
        c.connect()
        try:
            if self._layout is None:
                self._layout = self._discover(c)
            _, addr, length = self._layout["inverter"]
            return decode_inverter(c.read_holding(addr, min(length + 2, 125)))
        except ModbusError as exc:
            if exc.code in (2, None):                     # layout moved or garbled: look again next time
                self._layout = None
            raise
        finally:
            c.close()

    def poll_once(self):
        try:
            reading = self.read()
        except (OSError, ModbusError) as exc:
            with self._lock:
                self._error = f"{type(exc).__name__}: {getattr(exc, 'strerror', None) or exc}"[:90]
            return
        self.ingest(reading, time.time())

    # -- bookkeeping ---------------------------------------------------------

    def ingest(self, r, now):
        """Take one reading (also used directly by tests)."""
        with self._lock:
            self._data, self._data_t, self._error = r, now, None
            w = r.get("power")
            if w is None and r.get("status_code") in IDLE_CODES:
                w = 0
            if w is not None:
                w = max(0, w)
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

            today, changed = date.fromtimestamp(now).isoformat(), False
            wh = r.get("lifetime_wh")
            if wh is not None:
                changed = self._roll_day(today, wh, now)
            if w is not None and self._peak.get("date") == today and w > self._peak["w"]:
                self._peak.update(w=round(w), t=int(now))
            if changed or now - self._last_save >= SAVE_SECONDS:
                self._last_save = now
                self._save()

    def _roll_day(self, today, wh, now):
        """Close the previous day and open a new one when the date changes."""
        b, last, changed = self._baseline, self._last, False
        if b is None or b["date"] != today:
            if b is not None and last is not None and last["date"] == b["date"]:
                self._days.append({"date": b["date"], "wh": round(max(0.0, last["wh"] - b["wh"])),
                                   "partial": bool(b.get("partial"))})
                del self._days[:-DAYS_KEEP]
            yesterday = (date.fromtimestamp(now) - timedelta(days=1)).isoformat()
            if last is not None and last["date"] == yesterday:
                # exact: today's energy counts from yesterday evening's reading
                self._baseline = {"date": today, "wh": last["wh"], "t": now, "partial": False}
            else:
                self._baseline = {"date": today, "wh": wh, "t": now, "partial": True}
            self._peak = {"date": today, "w": 0, "t": None}
            changed = True
        self._last = {"date": today, "wh": wh}
        return changed

    # -- readout -------------------------------------------------------------

    def live_watts(self, now=None):
        """Current production in watts for other modules, or None if unknown."""
        now = now or time.time()
        with self._lock:
            r, t = self._data, self._data_t
        if r is None:
            return None
        w, asleep = r.get("power"), r.get("status_code") in IDLE_CODES
        if now - t < STALE_SECONDS:
            if w is None:
                return 0.0 if asleep else None
            return float(max(0, w))
        return 0.0 if asleep else None    # stopped answering after going to sleep: no production

    def snapshot(self, now=None):
        now = now or time.time()
        with self._lock:
            r, t, error = self._data, self._data_t, self._error
            info, b, days, peak = dict(self._info), self._baseline, list(self._days), dict(self._peak)
        info.setdefault("rated_w", self.rated_w)
        base = {"configured": True, "now": now, "info": info}
        if r is None:
            return dict(base, ok=False, error=error)

        today_iso = date.fromtimestamp(now).isoformat()
        today = None
        if b and b["date"] == today_iso and r.get("lifetime_wh") is not None:
            today = {"wh": round(max(0.0, r["lifetime_wh"] - b["wh"])), "partial": b.get("partial", False), "since": b["t"]}
        dc_w, ac_w = r.get("dc_power"), r.get("power")
        efficiency = round(100 * ac_w / dc_w, 1) if dc_w and dc_w > 50 and ac_w is not None and 0 <= ac_w <= dc_w * 1.05 else None
        return dict(base, ok=now - t < STALE_SECONDS, error=error if now - t >= STALE_SECONDS else None,
                    age=round(now - t), power=r.get("power"), rated=info.get("rated_w"),
                    status=r.get("status"), status_code=r.get("status_code"), events=r.get("events", []),
                    ac={"current": r.get("ac_current"), "frequency": r.get("frequency"), "apparent": r.get("apparent"),
                        "reactive": r.get("reactive"), "pf": r.get("pf"), "phases": r.get("phases", [])},
                    dc={"voltage": r.get("dc_voltage"), "current": r.get("dc_current"), "power": dc_w},
                    efficiency=efficiency, temps=r.get("temps", {}),
                    lifetime_wh=r.get("lifetime_wh"), today=today,
                    peak=peak if peak.get("date") == today_iso and peak.get("w") else None,
                    days=[d for d in reversed(days) if d["date"] != today_iso][:7],
                    model=r.get("model"), layout=(self._layout or {}).get("models"))

    def history(self, now=None):
        with self._lock:
            return {"now": now or time.time(), "points": list(self._history)}
