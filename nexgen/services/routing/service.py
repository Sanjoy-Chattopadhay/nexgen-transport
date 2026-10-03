"""Routes and cost: each journey against the route it should have taken.

Owns nx_route (plans, trip routes, deviations). Geo-Fencing's routing code,
unchanged in what it computes; it reads trails, stops and phases from the
geofence service's published views (migrations/routing/R__aliases.sql).

The worker re-analyses the trips a geofence refresh re-evaluated
(geo.visits.changed), so a journey's deviation and cost move with its visits.
Plans are learned from each lane's own history (the medoid of its trips), or
from OSRM when integrations.osrm.url is set. Money is "at the configured
rates": services.yaml defaults, overridden per client by the tenant file.
"""

from __future__ import annotations

import logging

from nexgen.core.config import get_config
from nexgen.core.db import connect
from nexgen.core.service import Service

logger = logging.getLogger(__name__)


def _mode() -> str:
    cfg = get_config()
    mode = str(cfg.setting("routing", "mode", "learned"))
    if mode == "osrm" and not (cfg.get("integrations.osrm.url") or ""):
        return "learned"          # OSRM is optional and off unless configured
    return mode


def _bump_version(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO route_state (s_key, s_value) VALUES ('data_version', '1') "
                    "ON DUPLICATE KEY UPDATE s_value = s_value + 1")
    conn.commit()


def analyse(trip_nos=None, run_id=None) -> dict:
    from nexgen.shared.geoengine.pipeline import runner
    from nexgen.shared.geoengine.routing import analysis
    run_id = run_id or runner.published_run_id()
    if run_id is None:
        return {"status": "idle", "reason": "no published geofence run yet"}
    with connect("routing") as conn:
        out = analysis.analyse_trips(conn, run_id, trip_nos, mode=_mode())
        _bump_version(conn)
    return {"run_id": run_id, "trips": "all" if trip_nos is None else len(trip_nos), **(out or {})}


def _on_visits_changed(events) -> None:
    by_run: dict[int, set] = {}
    for ev in events:
        p = ev.payload or {}
        trips = p.get("reevaluated") or p.get("trip_nos") or []
        if trips:
            by_run.setdefault(int(p["run_id"]), set()).update(int(t) for t in trips)
    for run_id, trips in by_run.items():
        out = analyse(sorted(trips), run_id)
        logger.info("routes re-analysed for %d trip(s) of run %s: %s", len(trips), run_id,
                    {k: out.get(k) for k in ("trips", "status")})


def build() -> Service:
    from nexgen.shared.geoengine.api.v1 import routes_router

    svc = Service("routing")
    svc.include(routes_router)
    worker = svc.worker("worker")
    worker.consumer("worker", ["geo.visits.changed"], _on_visits_changed, batch=50)
    worker.jobs.add("relearn-all", lambda: analyse(None), cron="0 3 * * *",
                    description="re-plan every lane and re-judge every journey of the published run")
    return svc
