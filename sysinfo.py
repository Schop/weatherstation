"""
System status helpers — stdlib only, reads /proc and /sys on Linux.
Every value degrades to None when unavailable (e.g. when developing on Windows).
"""

import glob
import os
import platform
import shutil
import socket
import threading
import time

_cpu_lock = threading.Lock()
_cpu_prev = None   # (total, idle) from the previous call


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _cpu_times():
    txt = _read("/proc/stat")
    if not txt:
        return None
    parts = [int(x) for x in txt.splitlines()[0].split()[1:]]
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)   # idle + iowait
    return sum(parts), idle


def cpu_percent():
    """CPU usage since the previous call (first call samples over 0.2 s)."""
    global _cpu_prev
    with _cpu_lock:
        cur = _cpu_times()
        if cur is None:
            return None
        if _cpu_prev is None:
            time.sleep(0.2)
            prev, cur = cur, _cpu_times()
        else:
            prev = _cpu_prev
        _cpu_prev = cur
    d_total = cur[0] - prev[0]
    d_idle = cur[1] - prev[1]
    return round(100 * (1 - d_idle / d_total), 1) if d_total > 0 else 0.0


def memory():
    txt = _read("/proc/meminfo")
    if not txt:
        return None
    info = {}
    for line in txt.splitlines():
        key, _, rest = line.partition(":")
        info[key] = int(rest.split()[0]) * 1024
    total = info.get("MemTotal")
    avail = info.get("MemAvailable")
    if not total or avail is None:
        return None
    return {"total": total, "used": total - avail}


def disk(path="/"):
    try:
        u = shutil.disk_usage(path if os.path.exists(path) else os.path.abspath(os.sep))
        return {"total": u.total, "used": u.used}
    except OSError:
        return None


def cpu_temp():
    temps = []
    for p in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        raw = _read(p)
        try:
            temps.append(int(raw) / 1000)
        except (TypeError, ValueError):
            pass
    return round(max(temps), 1) if temps else None


def uptime_seconds():
    txt = _read("/proc/uptime")
    return int(float(txt.split()[0])) if txt else None


def load_average():
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        return None


def os_name():
    txt = _read("/etc/os-release")
    if txt:
        for line in txt.splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    return platform.platform()


def cpu_model():
    txt = _read("/proc/cpuinfo")
    if txt:
        for line in txt.splitlines():
            if line.lower().startswith(("model name", "hardware")):
                return line.split(":", 1)[1].strip()
    return platform.processor() or None


def ip_address():
    """Local address of the default route (a UDP connect sends no packets)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def snapshot():
    return {
        "cpu_percent": cpu_percent(),
        "memory":      memory(),
        "disk":        disk(),
        "temp":        cpu_temp(),
        "load":        load_average(),
        "uptime":      uptime_seconds(),
        "hostname":    socket.gethostname(),
        "os":          os_name(),
        "kernel":      platform.release(),
        "cpu_model":   cpu_model(),
        "cpu_cores":   os.cpu_count(),
        "ip":          ip_address(),
        "python":      platform.python_version(),
    }
