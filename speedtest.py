"""
On-demand internet speed test against Cloudflare's public speed-test endpoints
(plain HTTPS, stdlib only). Runs only when started (a button on the display), one
test at a time, in a background thread.

Limits the load it puts on the shared connection: download then upload (never
together), each for at most PHASE_SECONDS or MAX_BYTES, whichever comes first.
A short warm-up is discarded because small transfers read far below the real speed.

Results (the last HISTORY_MAX tests) are kept in speedtest_history.json.
"""

import json
import os
import statistics
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HOST = "https://speed.cloudflare.com"
STREAMS = 4                     # parallel connections: one stream rarely fills a fast line
CHUNK = 25_000_000              # bytes requested per download stream request
PHASE_SECONDS = 8
WARMUP_SECONDS = 1.5
MAX_BYTES = 400_000_000         # per direction, a hard cap whatever the speed
UP_BLOCK = 1_000_000
HISTORY_MAX = 20
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "speedtest_history.json")
_UA = {"User-Agent": "weatherstation/1.0"}


class SpeedTest:
    def __init__(self, history_file=HISTORY_FILE, host=HOST):
        self.history_file = history_file
        self.host = host
        self._lock = threading.Lock()
        self._running = False
        self._phase = None          # "ping" | "download" | "upload" | None
        self._error = None
        self._history = []          # newest last: {"t","down","up","ping"} (Mbit/s, ms)
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self):
        try:
            with open(self.history_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        self._history = [r for r in data if isinstance(r, dict) and "t" in r][-HISTORY_MAX:]

    def _save(self):
        tmp = self.history_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._history, f)
            os.replace(tmp, self.history_file)
        except OSError as exc:
            print(f"[speedtest] could not save history: {exc}", file=sys.stderr)

    # -- control -------------------------------------------------------------

    def start(self):
        """Begin a test. Returns False (and does nothing) if one is already running."""
        with self._lock:
            if self._running:
                return False
            self._running, self._phase, self._error = True, "ping", None
        threading.Thread(target=self._run, daemon=True, name="speedtest").start()
        return True

    def _run(self):
        try:
            ping = self._ping()
            self._set_phase("download")
            down = self._download()
            self._set_phase("upload")
            up = self._upload()
            result = {"t": time.time(), "down": round(down, 1), "up": round(up, 1),
                      "ping": round(ping, 1) if ping is not None else None}
            with self._lock:
                self._history.append(result)
                del self._history[:-HISTORY_MAX]
                self._save()
        except Exception as exc:
            print(f"[speedtest] failed: {exc}", file=sys.stderr)
            with self._lock:
                self._error = str(getattr(exc, "reason", exc))[:80]
        finally:
            with self._lock:
                self._running, self._phase = False, None

    def _set_phase(self, phase):
        with self._lock:
            self._phase = phase

    # -- measurements --------------------------------------------------------

    def _ping(self):
        """Median time to first byte of a tiny request, in ms (includes TLS: a connection-level figure)."""
        times = []
        for _ in range(5):
            t = time.perf_counter()
            with urllib.request.urlopen(urllib.request.Request(f"{self.host}/__down?bytes=0", headers=_UA), timeout=8) as r:
                r.read()
            times.append((time.perf_counter() - t) * 1000)
        return statistics.median(times[1:])         # first one pays for DNS/TLS setup

    def _phase_rate(self, worker):
        """Run `worker(stop_at, count)` on STREAMS threads; return Mbit/s after the warm-up."""
        start = time.perf_counter()
        stop_at = start + PHASE_SECONDS
        total = [0]                                 # bytes after warm-up
        lock = threading.Lock()
        mark = {"t": None}

        def count(n):
            now = time.perf_counter()
            with lock:
                if now - start >= WARMUP_SECONDS:
                    if mark["t"] is None:
                        mark["t"] = now
                    total[0] += n
                return total[0] < MAX_BYTES

        with ThreadPoolExecutor(max_workers=STREAMS) as ex:
            for f in [ex.submit(worker, stop_at, count) for _ in range(STREAMS)]:
                f.result()
        elapsed = time.perf_counter() - (mark["t"] or start)
        if mark["t"] is None or elapsed <= 0 or total[0] == 0:
            raise RuntimeError("no data transferred")
        return total[0] * 8 / elapsed / 1e6

    def _download(self):
        def worker(stop_at, count):
            while time.perf_counter() < stop_at:
                req = urllib.request.Request(f"{self.host}/__down?bytes={CHUNK}", headers=_UA)
                with urllib.request.urlopen(req, timeout=10) as r:
                    while time.perf_counter() < stop_at:
                        chunk = r.read(65536)
                        if not chunk:
                            break
                        if not count(len(chunk)):
                            return
        return self._phase_rate(worker)

    def _upload(self):
        block = b"0" * UP_BLOCK

        def worker(stop_at, count):
            while time.perf_counter() < stop_at:
                req = urllib.request.Request(f"{self.host}/__up", data=block, headers=_UA, method="POST")
                with urllib.request.urlopen(req, timeout=10) as r:
                    r.read()
                if not count(UP_BLOCK):
                    return
        return self._phase_rate(worker)

    # -- readout -------------------------------------------------------------

    def snapshot(self):
        with self._lock:
            return {"running": self._running, "phase": self._phase, "error": self._error,
                    "last": self._history[-1] if self._history else None,
                    "history": list(self._history), "now": time.time()}
