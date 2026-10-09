"""
Public (WAN) IP address of this network, looked up from an outside service.

Runs in a background thread every CHECK_SECONDS, so the Network page never waits on it
and the services see one small request every few minutes. Several services are tried in
turn; a malformed answer is rejected. The last known address and when it changed are
kept in public_ip.json, so a restart does not forget them.
"""

import ipaddress
import json
import os
import sys
import threading
import time
import urllib.request

CHECK_SECONDS = 300
SERVICES = ["https://api.ipify.org", "https://icanhazip.com", "https://ifconfig.me/ip"]
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public_ip.json")


def _fetch_one(url):
    req = urllib.request.Request(url, headers={"User-Agent": "weatherstation/1.0"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        text = resp.read(100).decode("ascii", "replace").strip()
    return str(ipaddress.ip_address(text))               # raises on anything that is not an IP address


def lookup(services=SERVICES, fetch=_fetch_one):
    """First valid answer from the services, or None."""
    for url in services:
        try:
            return fetch(url)
        except Exception as exc:
            print(f"[publicip] {url}: {exc}", file=sys.stderr)
    return None


class PublicIP:
    def __init__(self, state_file=STATE_FILE, fetch=_fetch_one, interval=CHECK_SECONDS):
        self.state_file, self.fetch, self.interval = state_file, fetch, interval
        self._lock = threading.Lock()
        self._ip = None
        self._since = None          # when we first saw this address
        self._checked = None        # last SUCCESSFUL lookup
        self._previous = None
        self._load()

    def _load(self):
        try:
            with open(self.state_file, encoding="utf-8") as f:
                d = json.load(f)
            self._ip, self._since, self._previous = d.get("ip"), d.get("since"), d.get("previous")
        except (OSError, ValueError, AttributeError):
            pass

    def _save(self):
        tmp = self.state_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"ip": self._ip, "since": self._since, "previous": self._previous}, f)
            os.replace(tmp, self.state_file)
        except OSError as exc:
            print(f"[publicip] could not save: {exc}", file=sys.stderr)

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="publicip").start()

    def _loop(self):
        while True:
            try:
                self.check()
            except Exception as exc:
                print(f"[publicip] check failed: {exc}", file=sys.stderr)
            time.sleep(self.interval)

    def check(self, now=None):
        """One lookup. A failed lookup changes nothing: the last known address stays."""
        now = now or time.time()
        ip = lookup(fetch=self.fetch)
        if ip is None:
            return
        with self._lock:
            if ip != self._ip:
                self._previous = self._ip if self._ip else self._previous
                self._ip, self._since = ip, now
                self._save()
            self._checked = now

    def snapshot(self, now=None):
        now = now or time.time()
        with self._lock:
            return {"ip": self._ip, "since": self._since, "previous": self._previous,
                    "checked": self._checked,
                    "stale": self._checked is None or now - self._checked > 3 * self.interval}
