"""
LAN device discovery — stdlib only.

A background thread sweeps the local subnet every SCAN_INTERVAL seconds: every
address is pinged (which also makes the kernel resolve its MAC address), then the
neighbour table (`ip neigh`, /proc/net/arp or `arp -a`) is read. Devices that block
ping still answer ARP, so they show up too. Devices are remembered (by MAC) with a
"last seen" time, so phones that come and go stay in the list.

Names, best first: a label from config.NETWORK_LABELS (by MAC or IP), the reverse-DNS
hostname, the MAC vendor ("Samsung"), else "Unknown device".
Vendor lookup needs the IEEE list:  sudo apt install ieee-data
"""

import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import netstatus
import sysinfo

SCAN_INTERVAL = 90          # seconds between sweeps
FORGET_AFTER = 7 * 86400    # drop devices not seen for a week
MAX_PREFIX_HOSTS = 1022     # never sweep more than a /22
RDNS_RETRY = 3600
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lan_devices.json")

_OUI_FILES = [
    "/usr/share/ieee-data/oui.txt",
    "/var/lib/ieee-data/oui.txt",
    "/usr/share/nmap/nmap-mac-prefixes",
    "/usr/share/arp-scan/ieee-oui.txt",
]
_LEGAL_SUFFIX = {"inc", "ltd", "co", "corp", "corporation", "gmbh", "llc", "limited",
                 "bv", "sa", "ag", "plc", "pte", "oy", "ab", "srl"}
_MAC_RE = re.compile(r"[0-9a-f]{2}(?:[-:][0-9a-f]{2}){5}", re.I)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""


def norm_mac(mac):
    return mac.strip().lower().replace("-", ":")


def is_private_mac(mac):
    """Locally administered bit set: a randomised address (modern phones use these)."""
    try:
        return bool(int(mac[:2], 16) & 2)
    except ValueError:
        return False


def _short_vendor(name):
    words = name.split(",")[0].split()
    while len(words) > 1 and words[-1].strip(".").lower() in _LEGAL_SUFFIX:
        words.pop()
    return " ".join(words)[:28]


def load_oui(paths=_OUI_FILES):
    """{'aabbcc': 'Vendor'} from the first readable IEEE/nmap prefix list."""
    table = {}
    for path in paths:
        try:
            f = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with f:
            for line in f:
                if "(hex)" in line:
                    prefix, _, rest = line.partition("(hex)")
                    table[prefix.strip().replace("-", "").lower()] = _short_vendor(rest.strip())
                elif "(base 16)" not in line and re.match(r"^[0-9A-Fa-f]{6}\s", line):
                    table[line[:6].lower()] = _short_vendor(line[7:].strip())
        if table:
            break
    return table


def local_network(override=None):
    """The IPv4 network this machine is on (override: 'a.b.c.d/nn'), or None."""
    if override:
        try:
            return ipaddress.ip_network(override, strict=False)
        except ValueError:
            pass
    ip = sysinfo.ip_address()
    if not ip:
        return None
    prefix = 24
    ipcmd = shutil.which("ip")
    if ipcmd:
        m = re.search(rf"inet {re.escape(ip)}/(\d+)", _run([ipcmd, "-o", "-4", "addr", "show"]))
        if m:
            prefix = int(m.group(1))
    net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    if net.num_addresses - 2 > MAX_PREFIX_HOSTS or prefix >= 31:
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
    return net


def read_neighbours(network):
    """{ip: (mac, state)} for resolved neighbours inside `network`.
    state is the kernel's (REACHABLE/STALE/DELAY/PROBE...) where known, else 'STALE'."""
    found = {}

    def add(ip, mac, state):
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return
        mac = norm_mac(mac)
        if (addr not in network or addr in (network.network_address, network.broadcast_address)
                or mac == "ff:ff:ff:ff:ff:ff" or int(mac[:2], 16) & 1):   # broadcast / multicast
            return
        found[ip] = (mac, state)

    ipcmd = shutil.which("ip")
    if ipcmd:
        for line in _run([ipcmd, "-4", "neigh", "show"]).splitlines():
            parts = line.split()
            if len(parts) > 3 and "lladdr" in parts:
                add(parts[0], parts[parts.index("lladdr") + 1], parts[-1].upper())
    elif os.path.exists("/proc/net/arp"):
        with open("/proc/net/arp") as f:
            next(f, None)
            for line in f:
                c = line.split()
                if len(c) >= 4 and int(c[2], 16) & 2 and c[3] != "00:00:00:00:00:00":
                    add(c[0], c[3], "STALE")
    else:                                                    # Windows, macOS
        for line in _run(["arp", "-a"]).splitlines():
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+(" + _MAC_RE.pattern + r")", line, re.I)
            if m and "static" not in line.lower():
                add(m.group(1), m.group(2), "STALE")
    return found


def sweep(network, workers=48):
    """Ping every host; return ({ip: ms|None}, {ip: (mac, state)})."""
    hosts = [str(h) for h in network.hosts()]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        pings = dict(zip(hosts, ex.map(lambda h: netstatus.ping(h, 1.0), hosts)))
    neigh = read_neighbours(network)
    # a device that blocks ping is still being probed by the kernel; give it a moment
    if any(s in ("DELAY", "PROBE") and pings.get(ip) is None for ip, (_, s) in neigh.items()):
        time.sleep(6)
        neigh = read_neighbours(network)
    return pings, neigh


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class Scanner:
    def __init__(self, subnet=None, labels=None, interval=SCAN_INTERVAL,
                 state_file=STATE_FILE, oui_paths=None):
        self.subnet = subnet
        self.interval = interval
        self.state_file = state_file
        self.labels = {norm_mac(k) if _MAC_RE.fullmatch(k.strip()) else k.strip(): v
                       for k, v in (labels or {}).items()}
        self._oui_paths = oui_paths
        self._oui = None

        self._lock = threading.Lock()
        self._entries = {}      # key (mac or "ip:x") -> {ip, mac, last_seen, up, ms, self}
        self._hosts = {}        # ip -> (hostname, resolved_at)
        self._resolving = set()
        self._rdns = ThreadPoolExecutor(max_workers=6)
        self._network = None
        self._scanned = None
        self._scanning = False
        self._error = None
        self._load()

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="lanscan").start()

    def _loop(self):
        while True:
            try:
                self.scan()
            except Exception as exc:                         # keep the thread alive
                self._error = str(exc)
                print(f"[lanscan] scan error: {exc}", file=sys.stderr)
            time.sleep(self.interval)

    # -- persistence ---------------------------------------------------------

    def _load(self):
        try:
            with open(self.state_file, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return
        now = time.time()
        for key, e in saved.items():
            if isinstance(e, dict) and e.get("ip") and now - e.get("last_seen", 0) < FORGET_AFTER:
                self._entries[key] = {"ip": e["ip"], "mac": e.get("mac"),
                                      "last_seen": e.get("last_seen"), "up": False,
                                      "ms": None, "self": False}

    def _save(self):
        data = {k: {"ip": e["ip"], "mac": e["mac"], "last_seen": e["last_seen"]}
                for k, e in self._entries.items() if e["last_seen"] and not e["self"]}
        tmp = self.state_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.state_file)
        except OSError as exc:
            print(f"[lanscan] could not save state: {exc}", file=sys.stderr)

    # -- scanning ------------------------------------------------------------

    def scan(self):
        network = local_network(self.subnet)
        if network is None:
            self._error = "no network found"
            return
        self._scanning = True
        try:
            pings, neigh = sweep(network)
        finally:
            self._scanning = False

        own = sysinfo.ip_address()
        online = {ip: ms for ip, ms in pings.items() if ms is not None}
        for ip, (_, state) in neigh.items():
            if state == "REACHABLE":
                online.setdefault(ip, None)
        now = time.time()

        with self._lock:
            self._network, self._error = network, None
            for e in self._entries.values():
                e["up"], e["ms"] = False, None
            for ip, ms in online.items():
                mac = neigh.get(ip, (None, None))[0]
                if ip == own:
                    key, mac = "self", None
                else:
                    key = mac or f"ip:{ip}"
                e = self._entries.setdefault(key, {"ip": ip, "mac": mac, "last_seen": None,
                                                   "up": False, "ms": None, "self": ip == own})
                e.update(ip=ip, mac=mac or e["mac"], last_seen=now, up=True, ms=ms)
            for key in [k for k, e in self._entries.items()
                        if not e["up"] and now - (e["last_seen"] or 0) > FORGET_AFTER]:
                del self._entries[key]
            self._scanned = time.strftime("%H:%M:%S")
            self._save()
            todo = [e["ip"] for e in self._entries.values()
                    if e["up"] and not e["self"] and self._needs_rdns(e["ip"], now)]
        for ip in todo:
            self._resolving.add(ip)
            self._rdns.submit(self._resolve, ip)

    def _needs_rdns(self, ip, now):
        if ip in self._resolving:
            return False
        hit = self._hosts.get(ip)
        return hit is None or (not hit[0] and now - hit[1] > RDNS_RETRY)

    def _resolve(self, ip):
        try:
            name = socket.gethostbyaddr(ip)[0].split(".")[0]
            if re.fullmatch(r"[\d-]+", name):               # "192-168-1-5": no information
                name = ""
        except OSError:
            name = ""
        with self._lock:
            self._hosts[ip] = (name, time.time())
            self._resolving.discard(ip)

    # -- readout -------------------------------------------------------------

    def _vendor(self, mac):
        if self._oui is None:
            self._oui = load_oui(self._oui_paths) if self._oui_paths else load_oui()
        return self._oui.get(mac.replace(":", "")[:6]) if mac else None

    def snapshot(self, exclude=(), gateway=None):
        with self._lock:
            entries = [dict(e) for e in self._entries.values()]
            hosts = {ip: h[0] for ip, h in self._hosts.items()}
            network, scanned, error = self._network, self._scanned, self._error

        devices = []
        for e in entries:
            ip, mac = e["ip"], e["mac"]
            if ip in exclude:
                continue
            label = self.labels.get(mac) or self.labels.get(ip)
            host = hosts.get(ip) or None
            vendor = self._vendor(mac) if mac else None
            private = bool(mac) and is_private_mac(mac)

            is_gw = ip == gateway
            if e["self"]:
                name, rank = (socket.gethostname(), 0)
                parts = [ip, "this display"]
            else:
                name = label or host or vendor or ("Router" if is_gw else None)
                rank = 0 if label else (1 if name else 2)
                parts = [ip]
                if is_gw:
                    parts.append("gateway")
                elif label and (vendor or host):
                    parts.append(vendor or host)
                elif host and vendor:
                    parts.append(vendor)
                elif rank == 2 and mac:          # unknown: show the MAC so it can be labelled
                    parts.append(mac)
            devices.append({
                "name": name or "Unknown device", "host": ip, "mac": mac,
                "detail": " · ".join(parts), "private": private, "vendor": vendor,
                "up": e["up"], "ms": e["ms"], "last_seen": e["last_seen"],
                "unknown": rank == 2, "_rank": rank,
            })

        def ip_key(d):
            try:
                return int(ipaddress.ip_address(d["host"]))
            except ValueError:
                return 0
        devices.sort(key=lambda d: (d["_rank"], not d["up"], d["name"].lower(), ip_key(d)))
        for d in devices:
            del d["_rank"]
        return {"subnet": str(network) if network else None, "scanned": scanned,
                "scanning": self._scanning, "error": error, "devices": devices}
