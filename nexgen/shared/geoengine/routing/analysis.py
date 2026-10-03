"""Every loaded trip against its plan: distance, deviations, reroutes, cost.

    python -m nexgen.shared.geoengine.cli analyse-routes [--run N] [--trips 1,2] [--mode learned|osrm]

For each trip with a loading and an unloading place (geo_trip_phase), the
transit -- loading exit to unloading arrival -- is measured against the
planned route (routing/plan.py): its distance on the fitted trail, every
deviation from the plan (routing/deviation.py), and the money
(routing/economics.py). Results go to geo_trip_route, one row per trip, and
geo_route_deviation, one row per episode. The scheduler runs this for the
trips each refresh re-evaluates.

With OSRM configured the plan is OSRM's route from the truck's actual exit
point to its actual arrival point, and a confirmed deviation asks OSRM for a
new route from where the truck is, as a navigation app would. Without it the
plan is the lane's learned corridor, and a deviation that never returns is
an alternate route, since there is no router to replan with.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict

import numpy as np

from nexgen.shared.geoengine.config import settings
from nexgen.shared.geoengine.pipeline import phases
from nexgen.shared.geoengine.prep import codec
from nexgen.shared.geoengine.routing import economics as E
from nexgen.shared.geoengine.routing import plan as PL
from nexgen.shared.geoengine.routing.deviation import DeviationConfig, detect

logger = logging.getLogger(__name__)


def rates() -> E.Rates:
    """The cost rates in force: services.yaml's, overridden by the client's own
    (tenant file `cost_rates` / `thresholds.detention_free_h`, or the Admin
    page), so one client's rates never change another's figures."""
    r = settings.routing
    from nexgen.core.tenancy import setting
    cr = setting("cost_rates", {}) or {}
    free = setting("thresholds.detention_free_h", None)
    revenue = cr.get("revenue_per_km", r.revenue_per_km)
    return E.Rates(per_km=float(cr.get("per_km", r.cost_per_km)),
                   per_hour=float(cr.get("per_hour", r.cost_per_hour)),
                   detention_per_hour=float(cr.get("detention_per_hour", r.detention_per_hour)),
                   free_hours=float(free if free is not None else r.free_hours),
                   revenue_per_km=(float(revenue) if revenue else None),
                   tolerance_pct=r.tolerance_pct)


def deviation_config(mode: str) -> DeviationConfig:
    r = settings.routing
    if mode == "osrm":
        return DeviationConfig(off_m=r.off_m, on_m=r.on_m, confirm_s=r.confirm_s, escape_m=r.escape_m,
                               terminal_m=r.terminal_m, long_stop_s=r.long_stop_s)
    # A learned corridor is another truck's GPS: chords between fixes a
    # minute apart cut corners on curves, so it is judged more loosely.
    return DeviationConfig(off_m=r.learned_off_m, on_m=r.learned_on_m, confirm_s=r.confirm_s,
                           escape_m=max(r.escape_m, 3 * r.learned_off_m), terminal_m=r.learned_terminal_m,
                           long_stop_s=r.long_stop_s)


class Trails:
    """Decoded fitted trails, kept fixes only, loaded as needed."""

    def __init__(self, conn, run_id: int, max_gap_s: float):
        self.conn, self.run_id, self.max_gap_s = conn, run_id, max_gap_s
        self._cache: dict[int, tuple] = {}

    def load(self, trips) -> None:
        need = [t for t in trips if t not in self._cache]
        for i in range(0, len(need), 300):
            chunk = need[i:i + 300]
            with self.conn.cursor() as cur:
                cur.execute(f"""SELECT i_trip_no, m_data FROM geo_fit_trail WHERE i_run_id=%s
                                  AND i_trip_no IN ({','.join(['%s'] * len(chunk))})""", (self.run_id, *chunk))
                for r in cur.fetchall():
                    arr = codec.decode(r["m_data"])
                    kept = arr[(arr["role"] != codec.ROLES.index("reject")) & ~np.isnan(arr["lat"])]
                    odo = phases.Odometer(kept["t"], kept["lat"], kept["lon"], self.max_gap_s)
                    self._cache[r["i_trip_no"]] = (odo.t, odo.lat, odo.lon, odo.cum_m)
            for t in chunk:
                self._cache.setdefault(t, None)

    def get(self, trip: int):
        if trip not in self._cache:
            self.load([trip])
        return self._cache[trip]

    def window(self, trip: int, a, b):
        tr = self.get(trip)
        if tr is None or a is None or b is None:
            return None
        t, lat, lon, cum = tr
        ea, eb = phases._epoch(a), phases._epoch(b)
        sel = (t >= ea) & (t <= eb)
        if sel.sum() < 2:
            return None
        return t[sel], lat[sel], lon[sel], cum[sel]

    def drop(self, trips) -> None:
        for t in trips:
            self._cache.pop(t, None)


def _lane_signature(rows: list[dict]) -> str:
    """Which trips, and which method, a lane's learned plan came from: a new
    trip on the lane or a change to how plans are learned relearns it."""
    return (f"{PL.PLAN_VERSION}:{len(rows)}:{max(r['i_trip_no'] for r in rows)}:"
            f"{round(sum(float(r['d_transit_km'] or 0) for r in rows))}")


def analyse_trips(conn, run_id: int, trip_nos=None, mode: str | None = None) -> dict:
    """Analyse some trips of a run (all loaded trips when `trip_nos` is None)."""
    t0 = time.perf_counter()
    conn.commit()                      # a fresh snapshot: the trips may have just been re-evaluated
    with conn.cursor() as cur:
        max_gap = phases.run_max_gap(cur, run_id)
    if mode is None:
        mode = "osrm" if settings.osrm.enabled else "learned"
    client = None
    if mode == "osrm":
        from nexgen.shared.geoengine.osrm.client import OsrmClient
        client = OsrmClient(settings.osrm)
        if not client.enabled:
            mode = "learned"

    base = """SELECT p.i_trip_no, p.s_asset_id, p.i_loading_site_id, p.s_loading_site, p.i_unloading_site_id,
                     p.s_unloading_site, p.dt_loading_out, p.dt_unloading_in, p.i_loading_s, p.i_unloading_s,
                     p.i_transit_s, p.i_transit_moving_s, p.i_transit_silent_s, p.i_transit_stop_s,
                     p.i_transit_halt_s, p.d_transit_km, m.s_trans_name, m.s_origin, m.s_destination
                FROM geo_trip_phase p LEFT JOIN geo_trip_meta m ON m.i_trip_no = p.i_trip_no
               WHERE p.i_run_id=%s AND p.s_shape='loaded'"""
    with conn.cursor() as cur:
        if trip_nos is None:
            cur.execute(base, (run_id,))
            rows = list(cur.fetchall())
        else:
            rows = []
            trip_nos = sorted(set(trip_nos))
            for i in range(0, len(trip_nos), 1000):
                chunk = trip_nos[i:i + 1000]
                cur.execute(base + f" AND p.i_trip_no IN ({','.join(['%s'] * len(chunk))})", (run_id, *chunk))
                rows += list(cur.fetchall())
    lanes: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        lanes[(r["i_loading_site_id"], r["i_unloading_site_id"])].append(r)

    trails = Trails(conn, run_id, max_gap)
    cfg = deviation_config(mode)
    rt = rates()
    router = PL.osrm_router(client, settings.routing.truck_time_factor) if client else None
    trip_rows: list[tuple] = []
    dev_rows: list[tuple] = []
    counts = defaultdict(int)
    for lane, members in lanes.items():
        plan = None
        if mode == "learned":
            with conn.cursor() as cur:
                cur.execute("""SELECT i_trip_no, d_transit_km FROM geo_trip_phase
                                WHERE i_run_id=%s AND i_loading_site_id=%s AND i_unloading_site_id=%s
                                  AND s_shape='loaded'""", (run_id, lane[0], lane[1]))
                lane_all = list(cur.fetchall())

            def transit_paths(nos, _members=None):
                with conn.cursor() as cur:
                    cur.execute(f"""SELECT i_trip_no, dt_loading_out, dt_unloading_in FROM geo_trip_phase
                                     WHERE i_run_id=%s AND i_trip_no IN ({','.join(['%s'] * len(nos))})""",
                                (run_id, *nos))
                    spans = {r["i_trip_no"]: r for r in cur.fetchall()}
                trails.load(nos)
                out = {}
                for n in nos:
                    s = spans.get(n)
                    w = trails.window(n, s["dt_loading_out"], s["dt_unloading_in"]) if s else None
                    if w is not None:
                        out[n] = (w[1], w[2])
                return out
            plan = PL.learned_plan(conn, run_id, lane, transit_paths, _lane_signature(lane_all),
                                   settings.routing.rest_min_per_4h)
        trails.load([r["i_trip_no"] for r in members])
        for r in members:
            w = trails.window(r["i_trip_no"], r["dt_loading_out"], r["dt_unloading_in"])
            status = "ok"
            p = plan
            if w is None:
                status, p = "no_trail", None
            elif mode == "osrm":
                p = PL.osrm_plan(conn, client, (float(w[1][0]), float(w[2][0])), (float(w[1][-1]), float(w[2][-1])),
                                 settings.routing.truck_time_factor, settings.routing.rest_min_per_4h, lane)
                if p is None:
                    status = "no_route"
            elif p is None:
                status = "short_history"
            if p is not None and p.distance_m < settings.routing.min_plan_km * 1000:
                status = "local"            # a move inside a works, not a journey: not judged, not priced
            counts[status] += 1
            actual_km = float(r["d_transit_km"]) if r["d_transit_km"] is not None else None
            actual_h = (r["i_transit_s"] or 0) / 3600 if r["i_transit_s"] is not None else None
            drive_s = (r["i_transit_moving_s"] or 0) + (r["i_transit_silent_s"] or 0)
            res = None
            if p is not None and w is not None and status == "ok":
                t, lat, lon, odo = w
                stops = []
                with conn.cursor() as cur:
                    cur.execute("""SELECT dt_start, dt_end FROM geo_stop WHERE i_run_id=%s AND i_trip_no=%s""",
                                (run_id, r["i_trip_no"]))
                    stops = [(phases._epoch(s["dt_start"]), phases._epoch(s["dt_end"])) for s in cur.fetchall()]
                res = detect(t, lat, lon, odo, p.line, cfg, stops, router,
                             (float(lat[-1]), float(lon[-1])))
            planned_km = p.distance_m / 1000 if p else None
            planned_h = p.transit_s / 3600 if (p and p.transit_s) else None
            money = E.trip_cost(planned_km if status == "ok" else None, planned_h, actual_km, actual_h,
                                (r["i_loading_s"] or 0) / 3600, (r["i_unloading_s"] or 0) / 3600, rt)
            eps = res.episodes if res else []
            trip_rows.append((
                run_id, r["i_trip_no"], r["s_asset_id"], p.mode if p else mode, status,
                p.plan_id if p else None, r["i_loading_site_id"], r["s_loading_site"], r["i_unloading_site_id"],
                r["s_unloading_site"], r["s_trans_name"], r["dt_loading_out"], r["dt_unloading_in"],
                round(planned_km, 2) if planned_km is not None else None,
                int(p.drive_s) if (p and p.drive_s is not None) else None,
                int(p.transit_s) if (p and p.transit_s is not None) else None,
                actual_km, drive_s, r["i_transit_s"], r["i_transit_stop_s"], r["i_transit_halt_s"],
                round(actual_km - planned_km, 2) if (actual_km is not None and planned_km is not None) else None,
                round(100 * (actual_km - planned_km) / planned_km, 1) if (actual_km is not None and planned_km) else None,
                int(r["i_transit_s"] - p.transit_s) if (p and p.transit_s and r["i_transit_s"] is not None) else None,
                round(res.offroute_m / 1000, 2) if res else None, int(res.offroute_s) if res else None,
                len(eps), sum(1 for e in eps if e.kind == "detour"),
                sum(1 for e in eps if e.kind == "off_route_stop"), sum(1 for e in eps if e.reroute),
                round(max((e.max_offset_m for e in eps), default=0.0), 0) if res else None,
                res.adherence_pct if res else None,
                money["cost_plan"], money["cost_actual"], money["variance"], money["variance_km"],
                money["variance_time"], money["detention_h"], money["detention_cost"], money["revenue"],
                money["margin"], money["verdict"], p.ref_trip if p else None))
            if res:
                t, lat, lon, _ = w
                for k, e in enumerate(eps):
                    ib = e.i_back if e.i_back is not None else len(t) - 1
                    dev_rows.append((
                        run_id, r["i_trip_no"], k + 1, e.kind, phases._from_epoch(e.t_leave),
                        phases._from_epoch(e.t_back) if e.t_back is not None else None,
                        float(lat[e.i_leave]), float(lon[e.i_leave]), float(lat[ib]), float(lon[ib]),
                        round(e.chain_leave_m / 1000, 2),
                        round(e.chain_back_m / 1000, 2) if e.chain_back_m is not None else None,
                        round(e.actual_m / 1000, 2), round(e.planned_m / 1000, 2), round(e.extra_m / 1000, 2),
                        int(e.duration_s), int(e.stop_s), round(e.max_offset_m, 0), 1 if e.silent else 0,
                        1 if e.reroute else 0,
                        round(e.reroute["new_route_m"] / 1000, 2) if e.reroute else None, e.plan_index))
        trails.drop([r["i_trip_no"] for r in members])

    done = [r["i_trip_no"] for r in rows]
    with conn.cursor() as cur:
        if trip_nos is None:
            cur.execute("DELETE FROM geo_trip_route WHERE i_run_id=%s", (run_id,))
            cur.execute("DELETE FROM geo_route_deviation WHERE i_run_id=%s", (run_id,))
        else:
            for i in range(0, len(trip_nos), 1000):
                chunk = trip_nos[i:i + 1000]
                marks = ",".join(["%s"] * len(chunk))
                cur.execute(f"DELETE FROM geo_trip_route WHERE i_run_id=%s AND i_trip_no IN ({marks})", (run_id, *chunk))
                cur.execute(f"DELETE FROM geo_route_deviation WHERE i_run_id=%s AND i_trip_no IN ({marks})",
                            (run_id, *chunk))
        for i in range(0, len(trip_rows), 2000):
            cur.executemany(f"""INSERT INTO geo_trip_route ({','.join(TRIP_COLS)})
                                VALUES ({','.join(['%s'] * len(TRIP_COLS))})""", trip_rows[i:i + 2000])
        for i in range(0, len(dev_rows), 5000):
            cur.executemany(f"""INSERT INTO geo_route_deviation ({','.join(DEV_COLS)})
                                VALUES ({','.join(['%s'] * len(DEV_COLS))})""", dev_rows[i:i + 5000])
    conn.commit()
    out = {"run_id": run_id, "mode": mode, "trips": len(done), "lanes": len(lanes), "deviations": len(dev_rows),
           "status": dict(counts), "seconds": round(time.perf_counter() - t0, 1)}
    if client is not None:
        out["osrm"] = client.stats()
    logger.info("route analysis: %s", out)
    return out


TRIP_COLS = ("i_run_id", "i_trip_no", "s_asset_id", "s_mode", "s_status", "i_plan_id", "i_from_site",
             "s_from_site", "i_to_site", "s_to_site", "s_trans_name", "dt_transit_from", "dt_transit_to",
             "d_planned_km", "i_planned_drive_s", "i_planned_transit_s", "d_actual_km", "i_actual_drive_s",
             "i_actual_transit_s", "i_stop_s", "i_halt_s", "d_extra_km", "d_extra_pct", "i_extra_s",
             "d_offroute_km", "i_offroute_s", "i_deviations", "i_detours", "i_offroute_stops", "i_reroutes",
             "d_max_offset_m", "d_adherence_pct", "d_cost_plan", "d_cost_actual", "d_variance",
             "d_variance_km", "d_variance_time", "d_detention_h", "d_detention_cost", "d_revenue", "d_margin",
             "s_verdict", "i_ref_trip")

DEV_COLS = ("i_run_id", "i_trip_no", "i_seq", "s_kind", "dt_leave", "dt_back", "d_leave_lat", "d_leave_long",
            "d_back_lat", "d_back_long", "d_chain_leave_km", "d_chain_back_km", "d_actual_km", "d_planned_km",
            "d_extra_km", "i_duration_s", "i_stop_s", "d_max_offset_m", "b_silent", "b_reroute",
            "d_new_route_km", "i_plan_index")
