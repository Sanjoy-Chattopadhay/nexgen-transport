"""
In-process TTL cache
--------------------
A tiny, dependency-free time-to-live cache for expensive read endpoints whose
inputs (filter params + consignor) repeat often and whose data changes slowly.

Why in-process (not Redis): the dashboard aggregates run against a huge `trips`
table, and the same 7-day default window is requested on every page load. A short
TTL (minutes) keeps those hits off the database while letting the numbers refresh
on their own as the TTL lapses — exactly the "cache it so it updates regularly"
behaviour the dashboard needs. Best-effort: a miss or expiry simply recomputes.

Usage:
    from nexgen.shared.common.cache import cache_get_or_set, make_key

    key = make_key("dashboard:summary", date_from, date_to, scope.summary_id)
    row = cache_get_or_set(key, lambda: _expensive_query(...), ttl=600)
"""
import threading
import time
from typing import Any, Callable

DEFAULT_TTL = 600  # 10 minutes

_store: "dict[str, tuple[float, Any]]" = {}
_lock = threading.Lock()


def make_key(*parts: Any) -> str:
    """Build a stable cache key from arbitrary parts (None-safe)."""
    return "|".join("" if p is None else str(p) for p in parts)


def cache_get_or_set(key: str, producer: Callable[[], Any], ttl: float = DEFAULT_TTL) -> Any:
    """Return the cached value for `key` if still fresh; otherwise call `producer`,
    store its result with an expiry `ttl` seconds from now, and return it.

    The producer runs outside the lock so a slow query never blocks other keys.
    Two concurrent misses on the same key may both compute — acceptable for
    idempotent read aggregates.
    """
    now = time.monotonic()
    with _lock:
        hit = _store.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
    value = producer()
    with _lock:
        _store[key] = (now + ttl, value)
    return value


def invalidate(prefix: str = "") -> int:
    """Drop cached entries whose key starts with `prefix` ('' clears everything).
    Returns how many entries were removed. Call after a data upload/refresh so
    stale aggregates don't linger until their TTL lapses."""
    with _lock:
        keys = [k for k in _store if k.startswith(prefix)]
        for k in keys:
            del _store[k]
    return len(keys)
