"""Vehicles, transporters, lanes and drivers -- each seen through its geofences.

All four are the same computation cut four ways: the run's per-trip summary
joined to the trip master, aggregated by an attribute. It is done in Python
over the run's trips rather than in SQL because the figures that matter here
are medians (time at the loading plant, transit between places), and a mean
over detention times is dominated by the handful of trucks held for days.

Counting each real event once
---------------------------------------------------------------------------
The fleet system opens one trip per consignment, and a truck carrying three
invoices carries its GPS into all three (pipeline/physical.py). So:

* **counts and totals** -- facility visits, time at facilities, alerts,
  kilometres -- sum each trip's *share* (geo_trip_share), in which every
  physical event belongs to exactly one trip;
* **medians** take one sample per physical event: two consignments that left
  the plant at the same moment are one departure, not two votes;
* **trip counts** still count trips, because a consignment is what the fleet
  system and the client bill by;
* **site lists, daily series and alert lists** read the physical ledger.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from nexgen.shared.geoengine.api.v1.common import clean, day_bounds, paged, percentile, resolve_run, rows
from nexgen.shared.geoengine.config import settings
from nexgen.shared.geoengine.db import get_geo_db

router = APIRouter(tags=["vehicles, transporters, lanes, drivers"])

UNKNOWN = "(no trip record)"


def _trip_rows(cur, rid: int, date_from: str | None, date_to: str | None,
               extra: str = "", params: tuple = ()) -> list[dict]:
    a, b = day_bounds(date_from, date_to)
    where, p = ["s.i_run_id=%s"], [rid]
    if a:
        where.append("s.dt_last_ping >= %s")
        p.append(a)
    if b:
        where.append("s.dt_first_ping < %s")
        p.append(b)
    if extra:
        where.append(extra)
        p.extend(params)
    cur.execute(f"""
        SELECT s.i_trip_no, COALESCE(m.s_asset_id, s.s_asset_id) AS asset, m.s_asset_type,
               m.s_trans_name, m.s_driver_name, m.s_driver_mobile, m.s_origin, m.s_destination,
               m.s_status, s.dt_first_ping, s.dt_last_ping, s.s_quality, s.i_facility_visits,
               s.i_places, s.i_facility_dwell_s, s.i_violations, s.i_inferred, s.d_distance_km,
               s.i_moving_gaps, s.s_first_site, s.i_first_site_id, s.dt_first_enter, s.dt_first_exit,
               s.s_last_site, s.i_last_site_id, s.dt_last_enter, s.dt_last_exit, s.i_transit_s,
               s.i_pings_read, m.dt_trip_start,
               sh.i_facility_visits sh_facility_visits, sh.i_facility_dwell_s sh_facility_dwell_s,
               sh.i_alerts sh_alerts, sh.i_overspeed sh_overspeed, sh.i_restricted sh_restricted,
               sh.i_inferred sh_inferred, sh.d_distance_km sh_distance_km,
               sh.i_siblings, sh.s_sibling_trips
          FROM geo_trip_summary s
          LEFT JOIN geo_trip_meta m ON m.i_trip_no = s.i_trip_no
          LEFT JOIN geo_trip_share sh ON sh.i_run_id = s.i_run_id AND sh.i_trip_no = s.i_trip_no
         WHERE {' AND '.join(where)}""", p)
    return [clean(r) for r in cur.fetchall()]


def _window(cur, rid: int, date_from, date_to):
    """The dates a list covers when the page gives none.

    The lists aggregate every trip in their range on each request (the
    figures are medians, which do not sum). On a feed of years that is
    millions of trips, so a list opened without dates covers the latest
    GEO_DEFAULT_WINDOW_DAYS of data, and says so; any range the page picks is
    honoured as it is. A feed shorter than the window is covered whole.
    """
    days = settings.scheduler.default_window_days
    if date_from or date_to or not days:
        return date_from, date_to, None
    cur.execute("SELECT MIN(d_day) a, MAX(d_day) b FROM geo_day_summary WHERE i_run_id=%s", (rid,))
    r = cur.fetchone()
    if not r or not r["b"] or (r["b"] - r["a"]).days < days:
        return date_from, date_to, None
    start = r["b"] - timedelta(days=days - 1)
    return start.isoformat(), None, {"from": start, "to": r["b"], "days": days, "defaulted": True}


def _secs(a, b):
    return (a - b).total_seconds() if a and b else None


def _share(t: dict, share_col: str, trip_col: str | None = None):
    """The trip's share of a cross-trip total. A trip re-evaluated after the
    run's summaries were built has no share yet; it counts its own figure."""
    v = t.get(share_col)
    if v is not None:
        return v
    return (t.get(trip_col) or 0) if trip_col else 0


def _once(trips: list[dict], key, value) -> list[float]:
    """One sample per physical event, keyed by vehicle and the event's stamps.

    Consignment trips on one truck repeat the same departure; a trip that
    started mid-stay sees a later entry for the same exit, so the longest
    reading -- the one that saw the entry -- is kept.
    """
    best: dict = {}
    for t in trips:
        k = key(t)
        v = value(t) if k is not None else None
        if v is None:
            continue
        k = (t["asset"] or f"trip:{t['i_trip_no']}", *k)
        if k not in best or v > best[k]:
            best[k] = v
    return list(best.values())


def _rollup(trips: list[dict]) -> dict:
    """The shared per-group figures."""
    origin_dwell = _once(trips,
                         lambda t: (t["dt_first_exit"],) if t["dt_first_exit"] and t["dt_first_enter"]
                         and t["i_places"] else None,
                         lambda t: _secs(t["dt_first_exit"], t["dt_first_enter"]))
    transit = _once(trips, lambda t: (t["dt_first_exit"], t["dt_last_enter"]) if t["i_transit_s"] else None,
                    lambda t: t["i_transit_s"])
    dest_dwell = _once(trips,
                       lambda t: (t["dt_last_enter"],) if t["dt_last_exit"] and t["dt_last_enter"] else None,
                       lambda t: _secs(t["dt_last_exit"], t["dt_last_enter"]))
    # Per consignment: each has its own gate-out stamp.
    # Positive = physically left the first place after the gate-out stamp.
    tail = [_secs(t["dt_first_exit"], t["dt_trip_start"]) for t in trips
            if t["dt_first_exit"] and t["dt_trip_start"]]
    n = len(trips)
    alerts = sum(_share(t, "sh_alerts", "i_violations") for t in trips)
    return {
        "trips": n,
        "vehicles": len({t["asset"] for t in trips if t["asset"]}),
        "transporters": len({t["s_trans_name"] for t in trips if t["s_trans_name"]}),
        "drivers": len({t["s_driver_name"] for t in trips if t["s_driver_name"]}),
        "facility_visits": sum(_share(t, "sh_facility_visits", "i_facility_visits") for t in trips),
        "facility_dwell_s": sum(_share(t, "sh_facility_dwell_s", "i_facility_dwell_s") for t in trips),
        "violations": alerts,
        "violations_per_100_trips": round(100 * alerts / n, 1) if n else None,
        "inferred": sum(_share(t, "sh_inferred", "i_inferred") for t in trips),
        "distance_km": round(sum(_share(t, "sh_distance_km", "d_distance_km") for t in trips), 1),
        "trips_sharing_gps": sum(1 for t in trips if t.get("i_siblings")),
        "good_trips": sum(1 for t in trips if t["s_quality"] == "good"),
        "broken_trips": sum(1 for t in trips if t["s_quality"] == "broken"),
        "trips_with_lane": sum(1 for t in trips if (t["i_places"] or 0) >= 2),
        "origin_dwell_p50_s": percentile(origin_dwell, 50),
        "origin_dwell_p90_s": percentile(origin_dwell, 90),
        "transit_p50_s": percentile(transit, 50),
        "dest_dwell_p50_s": percentile(dest_dwell, 50),
        "exit_after_gate_out_p50_s": percentile(tail, 50),
        "last_seen": max((t["dt_last_ping"] for t in trips if t["dt_last_ping"]), default=None),
    }


def _sort_page(items: list[dict], sort: str, order: str, page: int, page_size: int, default: str):
    """Sort on a whitelisted key -- any key the rows carry -- with missing
    values last in both directions, then page."""
    key = sort if items and sort in items[0] else default
    present = sorted((r for r in items if r.get(key) is not None), key=lambda r: r[key],
                     reverse=(order != "asc"))
    ordered = present + [r for r in items if r.get(key) is None]
    start = (page - 1) * page_size
    return paged(len(ordered), page, page_size, ordered[start:start + page_size])


def _trip_brief(t: dict) -> dict:
    """A trip row as the consignment sees it -- its own figures, not shares."""
    return {k: t[k] for k in ("i_trip_no", "asset", "s_trans_name", "s_driver_name", "s_origin",
                              "s_destination", "s_status", "dt_first_ping", "dt_last_ping",
                              "s_quality", "i_facility_visits", "i_places", "i_facility_dwell_s",
                              "i_violations", "s_first_site", "s_last_site", "i_transit_s",
                              "d_distance_km", "i_siblings", "s_sibling_trips")}


# ---------------------------------------------------------------------------
# The physical ledger, filtered to one vehicle, transporter or driver
# ---------------------------------------------------------------------------

def _match(alias: str, field: str, value, time_col: str, date_from, date_to) -> tuple[str, tuple]:
    """`field` is the ledger's attribution column; None matches rows without one."""
    where = [f"{alias}.{field} IS NULL" if value is None else f"{alias}.{field} = %s"]
    params: list = [] if value is None else [value]
    a, b = day_bounds(date_from, date_to)
    if a:
        where.append(f"{alias}.{time_col} >= %s")
        params.append(a)
    if b:
        where.append(f"{alias}.{time_col} < %s")
        params.append(b)
    return " AND ".join(where), tuple(params)


def _top_sites(cur, rid: int, field: str, value, date_from=None, date_to=None, limit: int = 15) -> list[dict]:
    where, params = _match("v", field, value, "dt_enter", date_from, date_to)
    cur.execute(f"""
        SELECT v.i_site_id, v.s_site_name, v.s_scale, v.s_category, COUNT(*) visits,
               COUNT(DISTINCT v.s_asset_id) vehicles, SUM(v.i_dwell_seconds) dwell_s,
               MAX(v.dt_enter) last_visit
          FROM geo_pvisit v
         WHERE v.i_run_id=%s AND v.b_primary=1 AND v.s_scale IN ('micro','site','campus') AND {where}
         GROUP BY v.i_site_id, v.s_site_name, v.s_scale, v.s_category
         ORDER BY visits DESC LIMIT %s""", (rid, *params, limit))
    return rows(cur)


def _violations(cur, rid: int, field: str, value, date_from=None, date_to=None, limit: int = 50) -> list[dict]:
    where, params = _match("x", field, value, "dt_event", date_from, date_to)
    cur.execute(f"""
        SELECT x.dt_event, x.s_kind, x.i_site_id, x.s_site_name, x.i_trip_no, x.i_trips, x.s_trips,
               x.s_asset_id, x.i_observed, x.i_limit, x.s_detail, x.s_driver_name, x.s_trans_name
          FROM geo_palert x
         WHERE x.i_run_id=%s AND {where}
         ORDER BY x.dt_event DESC LIMIT %s""", (rid, *params, limit))
    return rows(cur)


def _daily(cur, rid: int, field: str, value, date_from=None, date_to=None) -> list[dict]:
    where, params = _match("v", field, value, "dt_enter", date_from, date_to)
    cur.execute(f"""
        SELECT DATE(v.dt_enter) d_day, COUNT(*) visits, COUNT(DISTINCT v.i_site_id) sites,
               SUM(v.i_dwell_seconds) dwell_s
          FROM geo_pvisit v
         WHERE v.i_run_id=%s AND v.b_primary=1 AND v.s_scale IN ('micro','site','campus')
           AND v.b_entry_observed=1 AND {where}
         GROUP BY d_day ORDER BY d_day""", (rid, *params))
    return rows(cur)


# ---------------------------------------------------------------------------
# Vehicles
# ---------------------------------------------------------------------------

@router.get("/vehicles")
def list_vehicles(q: str | None = None, transporter: str | None = None,
                  date_from: str | None = Query(None, alias="from"),
                  date_to: str | None = Query(None, alias="to"),
                  sort: str = "trips", order: str = "desc",
                  page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500),
                  run: int | None = None, conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        date_from, date_to, window = _window(cur, rid, date_from, date_to)
        trips = _trip_rows(cur, rid, date_from, date_to)
        cur.execute("SELECT s_asset_id, dt_message, s_site_name, s_scale, i_speed FROM geo_live_position")
        live = {r["s_asset_id"]: r for r in cur.fetchall()}
    groups: dict[str, list] = defaultdict(list)
    for t in trips:
        if t["asset"]:
            groups[t["asset"]].append(t)
    items = []
    for asset, ts in groups.items():
        latest = max(ts, key=lambda t: t["dt_last_ping"] or t["dt_first_ping"])
        trans = Counter(t["s_trans_name"] for t in ts if t["s_trans_name"]).most_common(1)
        row = {"s_asset_id": asset, "s_asset_type": latest["s_asset_type"],
               "transporter": trans[0][0] if trans else None,
               "last_site": latest["s_last_site"] or latest["s_first_site"],
               "last_trip": latest["i_trip_no"], **_rollup(ts)}
        lp = live.get(asset)
        row["live"] = clean(lp) if lp else None
        if q and q.lower() not in f"{asset} {row['transporter'] or ''}".lower():
            continue
        if transporter and row["transporter"] != transporter:
            continue
        items.append(row)
    return {"run_id": rid, "window": window, **_sort_page(items, sort, order, page, page_size, "trips")}


@router.get("/vehicles/{asset_id}")
def vehicle_detail(asset_id: str, date_from: str | None = Query(None, alias="from"),
                   date_to: str | None = Query(None, alias="to"),
                   run: int | None = None, conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        trips = _trip_rows(cur, rid, date_from, date_to,
                           "COALESCE(m.s_asset_id, s.s_asset_id) = %s", (asset_id,))
        if not trips:
            raise HTTPException(404, f"no trips for vehicle {asset_id} in run {rid}")
        sites = _top_sites(cur, rid, "s_asset_id", asset_id, date_from, date_to, 20)
        daily = _daily(cur, rid, "s_asset_id", asset_id, date_from, date_to)
        where, params = _match("v", "s_asset_id", asset_id, "dt_enter", date_from, date_to)
        cur.execute(f"""
            SELECT v.i_trip_no, v.i_trips, v.s_trips, v.i_site_id, v.s_site_name, v.s_scale, v.dt_enter,
                   v.dt_exit, v.b_open, v.b_entry_observed, v.i_dwell_seconds, v.s_confirmed_by
              FROM geo_pvisit v
             WHERE v.i_run_id=%s AND {where} AND v.b_primary=1
               AND v.s_scale IN ('micro','site','campus')
             ORDER BY v.dt_enter DESC LIMIT 60""", (rid, *params))
        recent = rows(cur)
        violations = _violations(cur, rid, "s_asset_id", asset_id, date_from, date_to)
        cur.execute("SELECT * FROM geo_live_position WHERE s_asset_id=%s", (asset_id,))
        live = cur.fetchone()
    trips.sort(key=lambda t: t["dt_first_ping"] or t["dt_last_ping"], reverse=True)
    trans = Counter(t["s_trans_name"] for t in trips if t["s_trans_name"])
    drivers = Counter(t["s_driver_name"] for t in trips if t["s_driver_name"])
    return {"run_id": rid, "s_asset_id": asset_id, "s_asset_type": trips[0]["s_asset_type"],
            "summary": _rollup(trips),
            "transporters": [{"name": k, "trips": n} for k, n in trans.most_common()],
            "drivers": [{"name": k, "trips": n} for k, n in drivers.most_common(10)],
            "trips": [_trip_brief(t) for t in trips[:300]],
            "top_sites": sites, "daily": daily, "recent_visits": recent,
            "violations": violations, "live": clean(live) if live else None}


# ---------------------------------------------------------------------------
# Transporters
# ---------------------------------------------------------------------------

@router.get("/transporters")
def list_transporters(q: str | None = None,
                      date_from: str | None = Query(None, alias="from"),
                      date_to: str | None = Query(None, alias="to"),
                      sort: str = "trips", order: str = "desc",
                      page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
                      run: int | None = None, conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        date_from, date_to, window = _window(cur, rid, date_from, date_to)
        trips = _trip_rows(cur, rid, date_from, date_to)
    groups: dict[str, list] = defaultdict(list)
    for t in trips:
        groups[t["s_trans_name"] or UNKNOWN].append(t)
    items = [{"transporter": name, **_rollup(ts)} for name, ts in groups.items()
             if not q or q.lower() in name.lower()]
    return {"run_id": rid, "window": window, **_sort_page(items, sort, order, page, page_size, "trips")}


@router.get("/transporters/detail")
def transporter_detail(name: str, date_from: str | None = Query(None, alias="from"),
                       date_to: str | None = Query(None, alias="to"),
                       run: int | None = None, conn=Depends(get_geo_db)):
    """By query parameter, not path: carrier names contain slashes and dots."""
    value = None if name == UNKNOWN else name
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        if value is None:
            trips = _trip_rows(cur, rid, date_from, date_to, "m.s_trans_name IS NULL")
        else:
            trips = _trip_rows(cur, rid, date_from, date_to, "m.s_trans_name = %s", (value,))
        if not trips:
            raise HTTPException(404, f"no trips for transporter {name!r}")
        sites = _top_sites(cur, rid, "s_trans_name", value, date_from, date_to, 20)
        daily = _daily(cur, rid, "s_trans_name", value, date_from, date_to)
        violations = _violations(cur, rid, "s_trans_name", value, date_from, date_to)
    by_vehicle: dict[str, list] = defaultdict(list)
    by_lane: dict[tuple, list] = defaultdict(list)
    for t in trips:
        by_vehicle[t["asset"] or "?"].append(t)
        by_lane[(t["s_origin"], t["s_destination"])].append(t)
    trips.sort(key=lambda t: t["dt_first_ping"] or t["dt_last_ping"], reverse=True)
    return {
        "run_id": rid, "transporter": name, "summary": _rollup(trips),
        "vehicles": sorted(({"s_asset_id": k, **_rollup(v)} for k, v in by_vehicle.items()),
                           key=lambda r: -r["trips"]),
        "lanes": sorted(({"origin": o, "destination": d, **_rollup(v)} for (o, d), v in by_lane.items()),
                        key=lambda r: -r["trips"])[:30],
        "top_sites": sites, "daily": daily, "violations": violations,
        "trips": [_trip_brief(t) for t in trips[:300]],
    }


# ---------------------------------------------------------------------------
# Lanes
# ---------------------------------------------------------------------------

@router.get("/lanes")
def list_lanes(q: str | None = None, date_from: str | None = Query(None, alias="from"),
               date_to: str | None = Query(None, alias="to"),
               sort: str = "trips", order: str = "desc",
               page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
               run: int | None = None, conn=Depends(get_geo_db)):
    """Origin -> destination as the fleet system names them, measured by the
    places the geofences actually saw at each end."""
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        date_from, date_to, window = _window(cur, rid, date_from, date_to)
        trips = _trip_rows(cur, rid, date_from, date_to, "m.i_trip_no IS NOT NULL")
    groups: dict[tuple, list] = defaultdict(list)
    for t in trips:
        groups[(t["s_origin"] or "?", t["s_destination"] or "?")].append(t)
    items = []
    for (o, d), ts in groups.items():
        if q and q.lower() not in f"{o} {d}".lower():
            continue
        first = Counter(t["s_first_site"] for t in ts if t["s_first_site"]).most_common(1)
        last = Counter(t["s_last_site"] for t in ts if t["s_last_site"]).most_common(1)
        items.append({"origin": o, "destination": d,
                      "usual_first_place": first[0][0] if first else None,
                      "usual_last_place": last[0][0] if last else None,
                      **_rollup(ts)})
    return {"run_id": rid, "window": window, **_sort_page(items, sort, order, page, page_size, "trips")}


@router.get("/lanes/detail")
def lane_detail(origin: str, destination: str,
                date_from: str | None = Query(None, alias="from"),
                date_to: str | None = Query(None, alias="to"),
                run: int | None = None, conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        trips = _trip_rows(cur, rid, date_from, date_to,
                           "m.s_origin <=> %s AND m.s_destination <=> %s",
                           (None if origin == "?" else origin, None if destination == "?" else destination))
        if not trips:
            raise HTTPException(404, "no trips on that lane")
    by_trans: dict[str, list] = defaultdict(list)
    for t in trips:
        by_trans[t["s_trans_name"] or UNKNOWN].append(t)
    firsts = Counter(t["s_first_site"] for t in trips if t["s_first_site"])
    lasts = Counter(t["s_last_site"] for t in trips if t["s_last_site"])
    # One sample per physical departure and arrival, as in _rollup.
    dist = {
        "origin_dwell_h": sorted(round(s / 3600, 2) for s in _once(
            trips, lambda t: (t["dt_first_exit"],) if t["dt_first_exit"] and t["dt_first_enter"] else None,
            lambda t: _secs(t["dt_first_exit"], t["dt_first_enter"]))),
        "transit_h": sorted(round(s / 3600, 2) for s in _once(
            trips, lambda t: (t["dt_first_exit"], t["dt_last_enter"]) if t["i_transit_s"] else None,
            lambda t: t["i_transit_s"])),
        "dest_dwell_h": sorted(round(s / 3600, 2) for s in _once(
            trips, lambda t: (t["dt_last_enter"],) if t["dt_last_exit"] and t["dt_last_enter"] else None,
            lambda t: _secs(t["dt_last_exit"], t["dt_last_enter"]))),
    }
    trips.sort(key=lambda t: t["dt_first_ping"] or t["dt_last_ping"], reverse=True)
    return {"run_id": rid, "origin": origin, "destination": destination,
            "summary": _rollup(trips), "distributions": dist,
            "first_places": [{"name": k, "trips": n} for k, n in firsts.most_common(10)],
            "last_places": [{"name": k, "trips": n} for k, n in lasts.most_common(10)],
            "transporters": sorted(({"transporter": k, **_rollup(v)} for k, v in by_trans.items()),
                                   key=lambda r: -r["trips"]),
            "trips": [_trip_brief(t) for t in trips[:300]]}


# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------

@router.get("/drivers")
def list_drivers(q: str | None = None, date_from: str | None = Query(None, alias="from"),
                 date_to: str | None = Query(None, alias="to"),
                 sort: str = "trips", order: str = "desc",
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
                 run: int | None = None, conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        date_from, date_to, window = _window(cur, rid, date_from, date_to)
        trips = _trip_rows(cur, rid, date_from, date_to, "m.s_driver_name IS NOT NULL")
    groups: dict[str, list] = defaultdict(list)
    for t in trips:
        groups[t["s_driver_name"]].append(t)
    items = []
    for name, ts in groups.items():
        if q and q.lower() not in f"{name} {ts[0]['s_driver_mobile'] or ''}".lower():
            continue
        trans = Counter(t["s_trans_name"] for t in ts if t["s_trans_name"]).most_common(1)
        items.append({"driver": name, "mobile": ts[0]["s_driver_mobile"],
                      "transporter": trans[0][0] if trans else None,
                      "overspeed": sum(_share(t, "sh_overspeed") for t in ts),
                      "restricted": sum(_share(t, "sh_restricted") for t in ts),
                      **_rollup(ts)})
    return {"run_id": rid, "window": window, **_sort_page(items, sort, order, page, page_size, "trips")}


@router.get("/drivers/detail")
def driver_detail(name: str, run: int | None = None, conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        trips = _trip_rows(cur, rid, None, None, "m.s_driver_name = %s", (name,))
        if not trips:
            raise HTTPException(404, f"no trips for driver {name!r}")
        violations = _violations(cur, rid, "s_driver_name", name, limit=100)
        sites = _top_sites(cur, rid, "s_driver_name", name, limit=15)
    trips.sort(key=lambda t: t["dt_first_ping"] or t["dt_last_ping"], reverse=True)
    vehicles = Counter(t["asset"] for t in trips if t["asset"])
    return {"run_id": rid, "driver": name, "mobile": trips[0]["s_driver_mobile"],
            "summary": _rollup(trips),
            "vehicles": [{"s_asset_id": k, "trips": n} for k, n in vehicles.most_common()],
            "violations": violations, "top_sites": sites,
            "trips": [_trip_brief(t) for t in trips[:300]]}
