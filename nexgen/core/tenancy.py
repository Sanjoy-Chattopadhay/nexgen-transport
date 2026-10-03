"""The tenant (client) a request or a job acts for, and its effective settings.

There is no login yet (decided 2026-10-03), so every request acts as the
default tenant from services.yaml. The context is still passed everywhere a
tenant matters -- every table's key starts with i_tenant_id -- so adding login
later changes where the tenant comes from (the token), not how it is used.

Effective settings = config/tenants/_default.yaml, then the tenant's own file,
then the overrides saved from the Admin page (nx_platform.tenant_setting).
Each service reads them through `tenant_settings()`, cached for a few seconds,
so one client's change reaches every service without a restart and never
touches another client.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from contextvars import ContextVar

from nexgen.core.config import get_config

_current: ContextVar[int | None] = ContextVar("nexgen_tenant", default=None)
_cache: dict[int, tuple[float, dict, int]] = {}
_lock = threading.Lock()
TTL_S = 5.0


def default_tenant_id() -> int:
    return get_config().tenant_id()


def current_tenant_id() -> int:
    tid = _current.get()
    return tid if tid is not None else default_tenant_id()


def set_tenant(tenant_id: int):
    return _current.set(tenant_id)


def _set_path(tree: dict, dotted: str, value) -> None:
    node = tree
    parts = dotted.split(".")
    for p in parts[:-1]:
        if not isinstance(node.get(p), dict):
            node[p] = {}
        node = node[p]
    node[parts[-1]] = value


def _overrides(tenant_id: int) -> tuple[dict, int]:
    from nexgen.core.db import connect
    try:
        with connect("platform") as conn, conn.cursor() as cur:
            cur.execute("SELECT s_key, j_value FROM tenant_setting WHERE i_tenant_id=%s", (tenant_id,))
            rows = cur.fetchall()
            cur.execute("SELECT i_version FROM tenant_config_version WHERE i_tenant_id=%s", (tenant_id,))
            v = cur.fetchone()
        out = {}
        for r in rows:
            val = r["j_value"]
            out[r["s_key"]] = json.loads(val) if isinstance(val, (str, bytes)) else val
        return out, int(v["i_version"]) if v else 0
    except Exception:
        # Platform schema not there yet (first start) or database down:
        # the file settings still apply.
        return {}, 0


def tenant_settings(tenant_id: int | None = None) -> dict:
    """The tenant's effective settings (files + Admin overrides)."""
    tid = int(tenant_id or current_tenant_id())
    now = time.monotonic()
    with _lock:
        hit = _cache.get(tid)
        if hit and now - hit[0] < TTL_S:
            return hit[1]
    base = copy.deepcopy(get_config().tenant_by_id(tid))
    overrides, version = _overrides(tid)
    for key, value in overrides.items():
        _set_path(base, key, value)
    base["_config_version"] = version
    base["_overrides"] = sorted(overrides)
    with _lock:
        _cache[tid] = (now, base, version)
    return base


def setting(path: str, default=None, tenant_id: int | None = None):
    node = tenant_settings(tenant_id)
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def invalidate(tenant_id: int | None = None) -> None:
    with _lock:
        if tenant_id is None:
            _cache.clear()
        else:
            _cache.pop(int(tenant_id), None)
