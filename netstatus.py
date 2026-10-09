"""
Network status helpers — stdlib only.

Reachability uses the system `ping`; devices with a "port" are probed with a TCP
connect instead (a refused connection still counts as up: the host answered).
Throughput and the default gateway come from /proc (Linux); both degrade to None.
"""

import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

INTERNET_HOST = "1.1.1.1"
CACHE_SECONDS = 3
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "device_seen.json")

_PING = shutil.which("ping")
_IS_WIN = sys.platform.startswith("win")
_TIME_RE = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.I)

_lock = threading.Lock()
_cache = None
_cache_ts = 0.0
_prev_traffic = None   # (timestamp, iface, rx_bytes, tx_bytes)
_seen = None           # "host[:port]" -> epoch of the last successful probe (loaded lazily)
_seen_saved = 0.0       # when the file was last written


def tcp_probe(host, port, timeout=1.0):
    """Latency in ms to a TCP port, or None. 'Connection refused' counts as up."""
    start = time.perf_counter()
    try:
        socket.create_connection((host, port), timeout).close()
    except ConnectionRefusedError:
        pass
    except OSError:
        return None
    return round((time.perf_counter() - start) * 1000, 1)


def ping(host, timeout=1.0):
    """Round-trip time in ms, or None if the host does not answer."""
    if not _PING:
        return tcp_probe(host, 80, timeout)
    if _IS_WIN:
        cmd = [_PING, "-n", "1", "-w", str(int(timeout * 1000)), host]
    else:
        cmd = [_PING, "-c", "1", "-W", str(max(1, int(timeout))), host]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout + 1.5).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    m = _TIME_RE.search(out)
    return float(m.group(1)) if m else None


def arp_alive(host):
    """True if the kernel has just confirmed this host on the local network (ARP/neighbour table).

    Devices that drop ping (typically a PC's firewall) still answer ARP, which cannot be
    blocked. Only a REACHABLE entry counts: STALE ones linger for minutes after a device
    has left. Linux only (`ip neigh`); elsewhere returns False.
    """
    ipcmd = shutil.which("ip")
    if not ipcmd:
        return False
    try:
        out = subprocess.run([ipcmd, "-4", "neigh", "show", host], capture_output=True,
                             text=True, timeout=3).stdout
    except (subprocess.TimeoutExpired, OSError):
        return False
    return any("lladdr" in line and line.split()[-1].upper() == "REACHABLE"
               for line in out.splitlines())


ARP_ONLY = -1.0      # marker: up according to ARP, but no timing (the device ignores ping)


def _check(host, port=None):
    """Latency in ms, or None if down. Ping (or TCP when a port is given); if a pinged host does
    not answer but the kernel has just confirmed it via ARP, it counts as up with no timing."""
    ms = tcp_probe(host, port) if port else ping(host)
    if ms is None and not port and arp_alive(host):
        return ARP_ONLY
    return ms


def default_gateway():
    """(interface, gateway ip) of the default route, or None."""
    try:
        with open("/proc/net/route") as f:
            next(f)
            for line in f:
                iface, dest, gw = line.split()[:3]
                if dest == "00000000":
                    return iface, socket.inet_ntoa(struct.pack("<L", int(gw, 16)))
    except (OSError, ValueError, StopIteration):
        pass
    return None


def traffic(iface):
    """Receive/transmit rate in bytes/s since the previous call."""
    global _prev_traffic
    if not iface:
        return None
    try:
        with open("/proc/net/dev") as f:
            for line in f:
                name, _, rest = line.partition(":")
                if name.strip() == iface:
                    cols = rest.split()
                    rx, tx = int(cols[0]), int(cols[8])
                    break
            else:
                return None
    except (OSError, ValueError, IndexError):
        return None

    now = time.time()
    prev, _prev_traffic = _prev_traffic, (now, iface, rx, tx)
    rate = {"iface": iface, "rx_bps": 0, "tx_bps": 0}
    if prev and prev[1] == iface and now > prev[0]:
        dt = now - prev[0]
        rate["rx_bps"] = max(0, (rx - prev[2]) / dt)
        rate["tx_bps"] = max(0, (tx - prev[3]) / dt)
    return rate


def _seen_key(d):
    return d["host"] + (f":{d['port']}" if d.get("port") else "")


def _load_seen():
    global _seen
    if _seen is None:
        try:
            with open(SEEN_FILE, encoding="utf-8") as f:
                data = json.load(f)
            _seen = {k: v for k, v in data.items() if isinstance(v, (int, float))}
        except (OSError, ValueError, AttributeError):
            _seen = {}
    return _seen


def _save_seen(now):
    """Write the times to disk, at most once a minute (they only matter while a device is down)."""
    global _seen_saved
    if now - _seen_saved < 60:
        return
    _seen_saved = now
    tmp = SEEN_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_seen, f)
        os.replace(tmp, SEEN_FILE)
    except OSError as exc:
        print(f"[netstatus] could not save last-seen times: {exc}", file=sys.stderr)


def snapshot(devices):
    """Probe internet, gateway and configured devices (cached for a few seconds)."""
    global _cache, _cache_ts
    with _lock:
        if _cache is not None and time.time() - _cache_ts < CACHE_SECONDS:
            return _cache

        gw = default_gateway()
        with ThreadPoolExecutor(max_workers=12) as ex:
            f_net = ex.submit(_check, INTERNET_HOST)
            f_gw = ex.submit(_check, gw[1]) if gw else None
            f_dev = [ex.submit(_check, d["host"], d.get("port")) for d in devices]
            net_ms = f_net.result()
            gw_ms = f_gw.result() if f_gw else None
            dev_ms = [f.result() for f in f_dev]

        # remember when each configured device last answered ("last seen" while it is down)
        seen, now = _load_seen(), time.time()
        for d, ms in zip(devices, dev_ms):
            if ms is not None:
                seen[_seen_key(d)] = now
        _save_seen(now)

        _cache = {
            "internet": {"up": net_ms is not None, "ms": net_ms, "host": INTERNET_HOST},
            "gateway": {"ip": gw[1], "iface": gw[0], "up": gw_ms is not None,
                        "ms": gw_ms} if gw else None,
            "traffic": traffic(gw[0] if gw else None),
            "devices": [
                {"name": d["name"], "host": d["host"], "port": d.get("port"),
                 "up": ms is not None, "ms": ms if ms != ARP_ONLY else None, "via": "arp" if ms == ARP_ONLY else None,
                 "last_seen": now if ms is not None else seen.get(_seen_key(d))}
                for d, ms in zip(devices, dev_ms)
            ],
            "checked": time.strftime("%H:%M:%S"),
        }
        _cache_ts = time.time()
        return _cache
