"""Platform: tenants, their settings, module switches, and housekeeping.

The control plane. It owns nx_platform (tenants and every per-client
override) and runs the housekeeping no single domain owns: database backups,
trimming the event log and job history.

No login yet (2026-10-03): /me answers with the default tenant and
`auth: none`, which is what the web shell reads to decide what to show.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Body, HTTPException

from nexgen.core import tenancy
from nexgen.core.config import get_config
from nexgen.core.db import connect
from nexgen.core.events import publish, purge_old
from nexgen.core.service import Service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/platform", tags=["platform"])


def _tenant_or_404(code: str) -> dict:
    try:
        return get_config().tenant(code)
    except KeyError:
        raise HTTPException(404, f"no tenant {code!r}") from None


def sync_tenants() -> int:
    """Mirror config/tenants/*.yaml into nx_platform.tenant (ids are stable)."""
    cfg = get_config()
    n = 0
    with connect("platform") as conn, conn.cursor() as cur:
        for code, t in cfg.tenants.items():
            info = t["tenant"]
            cur.execute(
                "INSERT INTO tenant (i_tenant_id, s_code, s_name, s_timezone) VALUES (%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE s_code=VALUES(s_code), s_name=VALUES(s_name), "
                "s_timezone=VALUES(s_timezone)",
                (int(info["id"]), code, info.get("name") or code, info.get("timezone") or "Asia/Kolkata"))
            cur.execute("INSERT IGNORE INTO tenant_config_version (i_tenant_id, i_version) VALUES (%s, 1)",
                        (int(info["id"]),))
            n += 1
        conn.commit()
    return n


@router.get("/me")
def me():
    cfg = get_config()
    t = tenancy.tenant_settings()
    info = t["tenant"]
    return {
        "auth": (cfg.get("auth") or {}).get("mode", "none"),
        "user": {"name": "Operator", "roles": ["admin"]},
        "tenant": {"id": info["id"], "code": info["code"], "name": info.get("name"),
                   "timezone": info.get("timezone")},
        "product": cfg.product.get("name"),
        "branding": t.get("branding", {}),
        "modules": t.get("modules", {}),
        "features": t.get("features", {}),
        "labels": t.get("labels", {}),
        "thresholds": t.get("thresholds", {}),
        "config_version": t.get("_config_version", 0),
    }


@router.get("/tenants")
def tenants():
    cfg = get_config()
    return [{"id": t["tenant"]["id"], "code": code, "name": t["tenant"].get("name")}
            for code, t in cfg.tenants.items()]


@router.get("/tenants/{code}/settings")
def tenant_settings(code: str):
    t = _tenant_or_404(code)
    eff = tenancy.tenant_settings(int(t["tenant"]["id"]))
    return {"tenant": code, "effective": {k: v for k, v in eff.items() if not k.startswith("_")},
            "overrides": eff.get("_overrides", []), "config_version": eff.get("_config_version", 0)}


@router.put("/tenants/{code}/settings/{key}")
def set_setting(code: str, key: str, value=Body(..., embed=True), changed_by: str = "admin"):
    t = _tenant_or_404(code)
    tid = int(t["tenant"]["id"])
    if key.startswith("tenant.") or key == "tenant":
        raise HTTPException(400, "the tenant's identity is set in its config file, not overridden")
    with connect("platform") as conn, conn.cursor() as cur:
        cur.execute("SELECT j_value FROM tenant_setting WHERE i_tenant_id=%s AND s_key=%s", (tid, key))
        old = cur.fetchone()
        body = json.dumps(value)
        cur.execute("INSERT INTO tenant_setting (i_tenant_id, s_key, j_value, s_changed_by) VALUES (%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE j_value=VALUES(j_value), i_version=i_version+1, "
                    "s_changed_by=VALUES(s_changed_by)", (tid, key, body, changed_by))
        cur.execute("INSERT INTO setting_audit (i_tenant_id, s_key, j_old, j_new, s_changed_by) "
                    "VALUES (%s,%s,%s,%s,%s)", (tid, key, old["j_value"] if old else None, body, changed_by))
        cur.execute("INSERT INTO tenant_config_version (i_tenant_id, i_version) VALUES (%s, 1) "
                    "ON DUPLICATE KEY UPDATE i_version=i_version+1", (tid,))
        publish(conn, "platform.config.changed", {"keys": [key]}, tenant_id=tid, source="platform")
        conn.commit()
    tenancy.invalidate(tid)
    return tenant_settings(code)


@router.delete("/tenants/{code}/settings/{key}")
def clear_setting(code: str, key: str, changed_by: str = "admin"):
    t = _tenant_or_404(code)
    tid = int(t["tenant"]["id"])
    with connect("platform") as conn, conn.cursor() as cur:
        cur.execute("SELECT j_value FROM tenant_setting WHERE i_tenant_id=%s AND s_key=%s", (tid, key))
        old = cur.fetchone()
        if not old:
            raise HTTPException(404, f"{key} is not overridden for {code}")
        cur.execute("DELETE FROM tenant_setting WHERE i_tenant_id=%s AND s_key=%s", (tid, key))
        cur.execute("INSERT INTO setting_audit (i_tenant_id, s_key, j_old, j_new, s_changed_by) "
                    "VALUES (%s,%s,%s,NULL,%s)", (tid, key, old["j_value"], changed_by))
        cur.execute("UPDATE tenant_config_version SET i_version=i_version+1 WHERE i_tenant_id=%s", (tid,))
        publish(conn, "platform.config.changed", {"keys": [key]}, tenant_id=tid, source="platform")
        conn.commit()
    tenancy.invalidate(tid)
    return tenant_settings(code)


@router.get("/tenants/{code}/audit")
def audit(code: str, limit: int = 100):
    t = _tenant_or_404(code)
    with connect("platform") as conn, conn.cursor() as cur:
        cur.execute("SELECT i_audit_id, s_key, j_old, j_new, s_changed_by, dt_changed FROM setting_audit "
                    "WHERE i_tenant_id=%s ORDER BY i_audit_id DESC LIMIT %s", (int(t["tenant"]["id"]), limit))
        return cur.fetchall()


def _trim_job_history(days: int = 90) -> dict:
    with connect("events") as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM job_run WHERE dt_started < NOW() - INTERVAL %s DAY LIMIT 50000", (days,))
        n = cur.rowcount
        conn.commit()
    return {"job_runs_deleted": n}


def build() -> Service:
    svc = Service("platform")
    svc.include(router)
    from nexgen.services.platform import maintenance
    svc.include(maintenance.router)
    svc.on_startup(lambda: logger.info("tenants synced: %s", sync_tenants()))
    ops = svc.worker("ops")
    sched = svc.cfg.setting("platform", "schedules", {}) or {}
    ops.jobs.add("backup", maintenance.run_backup, cron=sched.get("backup", "0 1 * * *"),
                 description="mysqldump of every NexGen schema, newest N kept")
    ops.jobs.add("events-purge", lambda: {"events_deleted": purge_old()}, cron="15 1 * * *",
                 description="drop events older than the retention window")
    ops.jobs.add("job-history-trim", _trim_job_history, cron="20 1 * * *",
                 description="drop job runs older than 90 days")
    return svc
