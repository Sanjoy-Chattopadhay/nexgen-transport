"""The developer page's API, answered by the supervisor itself.

Everything an operator needs to see and steer the system in one place:
every service and role with its health, start / stop / restart for each,
recent job runs and a run-now button, event consumers and their lag, logs,
database schemas and migrations, and the effective configuration with
secrets masked.

It lives in the supervisor rather than in a service so it keeps working when
every service is stopped -- which is exactly when it is needed.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from nexgen.core.config import get_config, reload_config
from nexgen.core.logs import tail

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/dev", tags=["developer"])


def _sup(request: Request):
    return request.app.state.supervisor


def _svc(request: Request, name: str):
    sup = _sup(request)
    if name not in sup.services:
        raise HTTPException(404, f"no service named {name!r}")
    return sup.services[name]


async def _blocking(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


@router.get("/overview")
def overview(request: Request):
    """Every service with its process, health and roles, plus the gateway."""
    cfg = get_config()
    sup = _sup(request)
    snap = sup.snapshot()
    db = {"reachable": False}
    try:
        from nexgen.core.db import connect
        with connect("events") as conn, conn.cursor() as cur:
            cur.execute("SELECT VERSION() AS v")
            db = {"reachable": True, "version": cur.fetchone()["v"]}
    except Exception as exc:
        db = {"reachable": False, "error": str(exc)}
    return {
        "product": cfg.product.get("name"),
        "gateway": {"port": cfg.gateway_port, "pid": snap["pid"], "started_at": snap["started_at"]},
        "database": db,
        "auth": (cfg.get("auth") or {}).get("mode", "none"),
        "services": snap["services"],
        "generated_at": datetime.now().isoformat(),
    }


@router.post("/services/{name}/{action}")
async def service_action(request: Request, name: str, action: str):
    sup = _sup(request)
    _svc(request, name)
    if action not in ("start", "stop", "restart"):
        raise HTTPException(400, "action must be start, stop or restart")
    fn = getattr(sup, action)
    try:
        return await _blocking(fn, name)
    except Exception as exc:
        raise HTTPException(500, f"could not {action} {name}: {exc}") from exc


@router.post("/shutdown")
def shutdown(request: Request):
    """Stop every service, then the gateway itself (what stop.bat calls)."""
    server = getattr(request.app.state, "server", None)
    if server is None:
        raise HTTPException(409, "this gateway was not started by `python -m nexgen run`")
    server.should_exit = True   # cmd_run's finally-block stops the services
    return {"shutting_down": True}


@router.post("/start-all")
async def start_all(request: Request):
    sup = _sup(request)
    out = {}
    for name in sup.services:
        try:
            out[name] = (await _blocking(sup.start, name))["state"]
        except Exception as exc:
            out[name] = f"error: {exc}"
    return out


@router.post("/stop-all")
async def stop_all(request: Request):
    sup = _sup(request)
    out = {}
    for name in reversed(list(sup.services)):
        try:
            out[name] = (await _blocking(sup.stop, name))["state"]
        except Exception as exc:
            out[name] = f"error: {exc}"
    return out


async def _internal(request: Request, name: str, method: str, path: str, params: dict | None = None):
    svc = _svc(request, name)
    if not svc.alive():
        raise HTTPException(409, f"{svc.spec.title} is not running; start the service first")
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.request(method, f"{svc.url}{path}", params=params)
        except httpx.HTTPError as exc:
            raise HTTPException(503, f"{svc.spec.title} did not answer: {exc}") from exc
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text[:500])
    return r.json()


@router.post("/services/{name}/roles/{role}/{action}")
async def role_action(request: Request, name: str, role: str, action: str):
    if action not in ("start", "stop"):
        raise HTTPException(400, "action must be start or stop")
    return await _internal(request, name, "POST", f"/internal/v1/roles/{role}/{action}")


@router.post("/services/{name}/jobs/{job}/run")
async def run_job(request: Request, name: str, job: str):
    return await _internal(request, name, "POST", f"/internal/v1/jobs/{job}/run")


@router.post("/services/{name}/consumers/{consumer}/retry")
async def consumer_retry(request: Request, name: str, consumer: str):
    return await _internal(request, name, "POST", f"/internal/v1/consumers/{consumer}/retry")


@router.post("/services/{name}/consumers/{consumer}/skip")
async def consumer_skip(request: Request, name: str, consumer: str, event_id: int):
    return await _internal(request, name, "POST", f"/internal/v1/consumers/{consumer}/skip",
                           params={"event_id": event_id})


@router.get("/services/{name}/ready")
async def service_ready(request: Request, name: str):
    svc = _svc(request, name)
    if not svc.alive():
        return {"service": name, "ready": False, "checks": {"process": "not running"}}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(f"{svc.url}/ready")
            return r.json()
        except httpx.HTTPError as exc:
            return {"service": name, "ready": False, "checks": {"process": f"no answer: {exc}"}}


@router.get("/services/{name}/logs")
def service_logs(request: Request, name: str, lines: int = Query(200, ge=1, le=5000),
                 console: bool = False):
    _svc(request, name)
    return {"service": name, "console": console, "lines": tail(name, lines, console=console)}


@router.get("/logs/supervisor")
def supervisor_logs(lines: int = Query(200, ge=1, le=5000)):
    return {"service": "supervisor", "lines": tail("supervisor", lines)}


@router.get("/jobs")
def jobs(service: str | None = None, job: str | None = None, limit: int = Query(100, ge=1, le=1000)):
    from nexgen.core.jobs import recent_runs
    return {"runs": recent_runs(service, job, limit)}


@router.get("/events")
def events(limit: int = Query(50, ge=1, le=500), event_type: str | None = None):
    from nexgen.core.events import consumer_overview, recent_events
    return {"consumers": consumer_overview(), "recent": recent_events(limit, event_type)}


@router.get("/database")
def database():
    """Schemas, their size, their tables and migration status."""
    from nexgen.core.db import server_connection
    from nexgen.core.migrate import status as migration_status
    cfg = get_config()
    names = {cfg.schema(k): k for k in cfg.schema_keys}
    legacy = {}
    for key in ("smart_truck", "geofencing"):
        try:
            legacy[cfg.legacy_database(key)] = f"legacy:{key}"
        except KeyError:
            pass
    conn = server_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_schema AS s, table_name AS t, table_type AS k, table_rows AS r, "
                "data_length AS d, index_length AS i, create_options AS o "
                "FROM information_schema.tables WHERE table_schema IN %s "
                "ORDER BY table_schema, data_length + index_length DESC",
                (tuple(list(names) + list(legacy)),))
            rows = cur.fetchall()
    finally:
        conn.close()
    schemas: dict[str, dict] = {}
    for r in rows:
        s = schemas.setdefault(r["s"], {"database": r["s"], "key": names.get(r["s"]) or legacy.get(r["s"]),
                                        "tables": [], "bytes": 0, "views": 0})
        if r["k"] == "VIEW":
            s["views"] += 1
            s["tables"].append({"name": r["t"], "kind": "view"})
            continue
        size = int(r["d"] or 0) + int(r["i"] or 0)
        s["bytes"] += size
        s["tables"].append({"name": r["t"], "kind": "table", "rows": int(r["r"] or 0),
                            "data_mb": round(int(r["d"] or 0) / 1048576, 1),
                            "index_mb": round(int(r["i"] or 0) / 1048576, 1),
                            "partitioned": "partitioned" in (r["o"] or "")})
    try:
        migrations = migration_status()
    except Exception as exc:
        migrations = [{"error": str(exc)}]
    return {"schemas": list(schemas.values()), "migrations": migrations}


@router.post("/database/migrate")
async def run_migrations():
    from nexgen.core.migrate import migrate
    try:
        report = await asyncio.get_running_loop().run_in_executor(None, migrate)
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"applied": report}


@router.get("/config")
def config():
    cfg = get_config()
    return cfg.masked()


@router.post("/config/reload")
def config_reload():
    """Re-read the YAML files in the supervisor. Services re-read on restart."""
    try:
        reload_config()
    except Exception as exc:
        raise HTTPException(400, f"configuration rejected: {exc}") from exc
    return {"reloaded": True}


@router.get("/routes")
def routes(request: Request):
    table = request.app.state.route_table
    return {"routes": [{"pattern": r.pattern, "service": r.service} for r in table]}
