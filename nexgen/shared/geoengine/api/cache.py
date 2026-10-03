"""Responses cached until the data changes.

Every figure the application shows is read from the published run, and the
published run changes only when something writes to it: the scheduler's
refresh, a run being published, summaries being rebuilt. Between those the
same request always gets the same answer. So a response is computed once per
*data version* and served from memory after that, and the browser is given an
ETag so a page it already holds costs a 304 and no query at all.

On a fleet of hundreds of vehicles and years of history this is what keeps
pages fast: the heavy aggregations run once after each refresh instead of on
every visit, and the refresh itself only touches what changed
(pipeline/incremental.py).

The version is a few small values read from the database at most every
`TTL_S` seconds, so a refresh done by another process (the scheduler) is seen
within that time, and a stale page is never served for longer.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict

TTL_S = 5.0
MAX_ENTRIES = 600
MAX_BYTES = 256 * 1024 * 1024
MAX_ENTRY_BYTES = 8 * 1024 * 1024

# Never cached: live state, uploads, the refresh machinery itself, and the
# list of runs (a new unpublished run changes it without changing the data).
EXCLUDE = ("/api/v1/geo/live", "/api/v1/geo/uploads", "/api/v1/geo/status", "/api/v1/geo/jobs",
           "/api/v1/geo/refresh", "/api/v1/geo/runs", "/api/v1/geo/osrm", "/api/v1/geo/engine")

_lock = threading.Lock()
_version: tuple[str, float] = ("", 0.0)
_store: "OrderedDict[str, tuple[str, bytes, dict]]" = OrderedDict()
_bytes = 0
stats = {"hits": 0, "misses": 0, "not_modified": 0}


def read_version() -> str:
    """The data version, straight from the database."""
    from nexgen.shared.geoengine.db import geo_session

    with geo_session() as conn, conn.cursor() as cur:
        cur.execute("""SELECT i_run_id, dt_summarised FROM geo_run WHERE s_status='ok'
                        ORDER BY b_published DESC, i_run_id DESC LIMIT 1""")
        run = cur.fetchone()
        try:
            cur.execute("SELECT s_value FROM geo_state WHERE s_key='data_version'")
            row = cur.fetchone()
        except Exception:                      # before init-db has created geo_state
            row = None
        # NexGen: the routing service caches on its own version too.
        try:
            cur.execute("SELECT s_value FROM route_state WHERE s_key='data_version'")
            route = cur.fetchone()
        except Exception:                      # the geofence schema has no route_state
            route = None
    return (f"{run['i_run_id'] if run else 0}:{run['dt_summarised'] if run else ''}:"
            f"{row['s_value'] if row else 0}:{route['s_value'] if route else 0}")


def current_version() -> str:
    global _version
    now = time.monotonic()
    v, at = _version
    if v and now - at < TTL_S:
        return v
    try:
        v = read_version()
    except Exception:
        return v or "unknown"
    with _lock:
        if v != _version[0]:
            _clear()
        _version = (v, now)
    return v


def cacheable(path: str) -> bool:
    return path.startswith("/api/v1/geo/") and not path.startswith(EXCLUDE)


def key_of(path: str, query: str, gzip: bool) -> str:
    q = "&".join(sorted(query.split("&"))) if query else ""
    return f"{'gz' if gzip else 'id'} {path}?{q}"


def etag(version: str, key: str) -> str:
    return 'W/"' + hashlib.blake2b(f"{version}|{key}".encode(), digest_size=10).hexdigest() + '"'


def get(key: str, version: str):
    with _lock:
        hit = _store.get(key)
        if hit and hit[0] == version:
            _store.move_to_end(key)
            stats["hits"] += 1
            return hit[1], hit[2]
    stats["misses"] += 1
    return None


def put(key: str, version: str, body: bytes, headers: dict) -> None:
    global _bytes
    if len(body) > MAX_ENTRY_BYTES:
        return
    with _lock:
        if version != _version[0]:
            return
        old = _store.pop(key, None)
        if old:
            _bytes -= len(old[1])
        _store[key] = (version, body, headers)
        _bytes += len(body)
        while _store and (len(_store) > MAX_ENTRIES or _bytes > MAX_BYTES):
            _, (_, b, _) = _store.popitem(last=False)
            _bytes -= len(b)


def _clear() -> None:
    global _bytes
    _store.clear()
    _bytes = 0


def invalidate() -> None:
    """Forget everything now, e.g. after this process published a run."""
    global _version
    with _lock:
        _clear()
        _version = ("", 0.0)


def info() -> dict:
    with _lock:
        return {"entries": len(_store), "bytes": _bytes, "version": _version[0], **stats}
