"""
Read-only access to Home Assistant entity states over its REST API.

GET <url>/api/states/<entity_id> with a long-lived access token (Home Assistant:
profile > Security > Long-lived access tokens). Only GET requests are made, so
nothing in Home Assistant can be changed from here. Results are cached for a few
seconds so several displays/refreshes cost one round of requests.
"""

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

CACHE_SECONDS = 5
TIMEOUT = 6

_lock = threading.Lock()
_cache = {}     # (base, entities) -> (timestamp, result)


class HomeAssistantError(Exception):
    """A problem that affects every entity (bad token, unreachable, ...)."""


def _get_state(base, token, entity):
    req = urllib.request.Request(
        f"{base}/api/states/{entity}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise HomeAssistantError("token rejected (check HA_TOKEN)")
        if exc.code == 404:
            return {"entity": entity, "ok": False, "state": None, "value": None,
                    "unit": None, "name": None, "error": "entity not found"}
        raise HomeAssistantError(f"HTTP {exc.code}")
    except urllib.error.URLError as exc:
        raise HomeAssistantError(f"unreachable ({exc.reason})")
    except (OSError, ValueError) as exc:
        raise HomeAssistantError(str(exc))

    state = str(data.get("state", ""))
    attrs = data.get("attributes") or {}
    try:
        value = float(state)
    except ValueError:
        value = None
    ok = state.lower() not in ("unavailable", "unknown", "")
    return {"entity": entity, "ok": ok, "state": state, "value": value,
            "unit": attrs.get("unit_of_measurement"), "name": attrs.get("friendly_name"),
            "updated": data.get("last_updated"), "error": None if ok else state or "no value"}


def fetch_states(base, token, entities):
    """{'error': str|None, 'states': {entity_id: {...}}} for the given entity ids."""
    base = base.rstrip("/")
    entities = tuple(dict.fromkeys(entities))          # unique, order kept
    key = (base, entities)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
        states, error = {}, None
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {e: ex.submit(_get_state, base, token, e) for e in entities}
            for entity, fut in futures.items():
                try:
                    states[entity] = fut.result()
                except HomeAssistantError as exc:
                    error = error or str(exc)
                except Exception as exc:               # never let one entity break the page
                    error = error or str(exc)
        if error:
            print(f"[homeassistant] {error}", file=sys.stderr)
        result = {"error": error, "states": states}
        _cache[key] = (time.time(), result)
        return result
