"""Proof: every figure opens to how it was calculated and the records behind it.

A number on a page is only as good as the reader's ability to check it. Each
dataset here reproduces one figure from the records that make it up -- the
same table, the same filters, the same exclusions as the figure itself -- and
answers with:

    value     the figure, recounted from those records on the server
    method    what is counted, in plain words, with every filter that applied
              (and every one that did not)
    formula   the arithmetic, with this window's own numbers in it
    excluded  records in scope that the figure deliberately leaves out, and why
    rows      the records themselves, a page at a time (or all, as CSV)

The page puts `value` beside the number on the tile; they must agree. Datasets
are a whitelist (DATASETS), never interpolated SQL, and every one must count
exactly the way its tile does -- that is the whole point.

Geo-Fencing's pages have the same thing as /api/v1/geo/drill/{dataset}.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from nexgen.shared.common.consignor import ConsignorScope, base_clause, consignor_scope
from nexgen.shared.common.sql import and_where, date_frags
from nexgen.shared.common.trip_class import TripClassScope, resolve_trip_class
from nexgen.shared.legacy_db import get_db

router = APIRouter(prefix="/proof", tags=["Proof"])

MAX_CSV_ROWS = 100_000


@dataclass
class Ctx:
    conn: object
    scope: ConsignorScope
    date_from: str
    date_to: str
    page: int
    page_size: int
    sort: str | None
    order: str
    trip_class: str | None
    extra: dict = field(default_factory=dict)

    @property
    def tclass(self) -> TripClassScope:
        return resolve_trip_class(self.trip_class)

    # -- the window and scope every trips-table figure uses -------------------
    def trips_where(self, alias: str = "t") -> tuple[str, list]:
        return and_where(base_clause(self.scope, "cnr_id", alias),
                         *date_frags(f"{alias}.trip_start", self.date_from, self.date_to))

    def window_text(self) -> str:
        f, t = _day(self.date_from), _day(self.date_to)
        if f and t:
            return f"departing (trip_start) from {f} to {t}, both days whole"
        if f:
            return f"departing (trip_start) on or after {f}"
        if t:
            return f"departing (trip_start) up to and including {t}"
        return "departing at any time (no date window)"

    def scope_lines(self, honours_trip_class: bool) -> list[str]:
        lines = [f"Consignor: {self.scope.name or self.scope.id}" if self.scope.active else "Consignor: all"]
        if self.trip_class:
            lines.append(f"Zonal / local filter ({self.trip_class}): "
                         + ("applied." if honours_trip_class else "NOT applied to this figure -- it counts every class."))
        return lines


def _day(s: str) -> str | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        return s


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    return v


def _rows(cur) -> list[dict]:
    return [{k: _plain(v) for k, v in r.items()} for r in cur.fetchall()]


def _fmt(n, digits: int = 0) -> str:
    if n is None:
        return "—"
    return f"{n:,.{digits}f}"


DATASETS: dict[str, Callable[[Ctx], dict]] = {}


def dataset(name: str):
    def wrap(fn):
        DATASETS[name] = fn
        return fn
    return wrap


def _page(ctx: Ctx, sql: str, params: list, sortable: dict[str, str], default_sort: str) -> tuple[list[dict], int]:
    """Count, then one sorted page of `sql` (a SELECT without ORDER/LIMIT)."""
    with ctx.conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM ({sql}) q", params)
        total = int(cur.fetchone()["n"])
        col = sortable.get(ctx.sort or "", sortable[default_sort])
        direction = "ASC" if ctx.order == "asc" else "DESC"
        if ctx.extra.get("csv"):
            cur.execute(f"{sql} ORDER BY {col} {direction} LIMIT {MAX_CSV_ROWS}", params)
        else:
            cur.execute(f"{sql} ORDER BY {col} {direction} LIMIT %s OFFSET %s",
                        params + [ctx.page_size, (ctx.page - 1) * ctx.page_size])
        return _rows(cur), total


# ---------------------------------------------------------------------------
# Dashboard: the six fleet tiles (api/dashboard.py get_fleet_summary)
# ---------------------------------------------------------------------------

_TRIP_SELECT = """
    SELECT t.dispatch_entry_no AS trip_no, t.trip_start, o.name AS origin, dst.name AS destination,
           dr.name AS driver, v.asset_id AS vehicle, t.trip_km, t.avg_speed_kmph, t.eta_met,
           t.eta_data_status, t.trip_duration_minutes
      FROM trips t
      LEFT JOIN locations o   ON o.id = t.origin_id
      LEFT JOIN locations dst ON dst.id = t.destination_id
      LEFT JOIN drivers dr    ON dr.id = t.driver_id
      LEFT JOIN vehicles v    ON v.id = t.vehicle_id
"""
_TRIP_SORT = {"trip_start": "trip_start", "trip_no": "trip_no", "trip_km": "trip_km",
              "avg_speed_kmph": "avg_speed_kmph", "driver": "driver", "vehicle": "vehicle"}
_TRIP_COLUMNS = [
    {"key": "trip_no", "label": "Trip", "link": "/trips/{trip_no}"},
    {"key": "trip_start", "label": "Departed", "kind": "datetime"},
    {"key": "origin", "label": "From"},
    {"key": "destination", "label": "To"},
    {"key": "driver", "label": "Driver"},
    {"key": "vehicle", "label": "Vehicle"},
    {"key": "trip_km", "label": "Km", "kind": "number", "digits": 1},
    {"key": "avg_speed_kmph", "label": "Avg km/h", "kind": "number", "digits": 1},
    {"key": "eta_met", "label": "ETA met", "kind": "bool"},
]


def _count(ctx: Ctx, sql: str, params: list) -> int:
    with ctx.conn.cursor() as cur:
        cur.execute(sql, params)
        return int(list(cur.fetchone().values())[0] or 0)


@dataset("dashboard.trips")
def _dash_trips(ctx: Ctx) -> dict:
    where, params = ctx.trips_where()
    rows, total = _page(ctx, f"{_TRIP_SELECT} {where}", params, _TRIP_SORT, "trip_start")
    return {
        "title": "Total trips", "format": "int", "value": total,
        "method": [f"Every trip {ctx.window_text()}.", "Each trip counted once.",
                   *ctx.scope_lines(False)],
        "formula": f"COUNT(trips) = {_fmt(total)}",
        "excluded": [],
        "columns": _TRIP_COLUMNS, "rows": rows, "total": total,
    }


def _distinct(ctx: Ctx, key: str, label: str, name_sql: str, link: str) -> dict:
    where, params = ctx.trips_where()
    inner = f"""
        SELECT t.{key} AS id, {name_sql} AS name, COUNT(*) AS trips, ROUND(SUM(t.trip_km), 1) AS trip_km
          FROM trips t
          LEFT JOIN drivers dr ON dr.id = t.driver_id
          LEFT JOIN vehicles v ON v.id = t.vehicle_id
          {where} {'AND' if where else 'WHERE'} t.{key} IS NOT NULL
         GROUP BY t.{key}, name"""
    rows, total = _page(ctx, inner, params, {"trips": "trips", "name": "name", "trip_km": "trip_km"}, "trips")
    trips_all = _count(ctx, f"SELECT COUNT(*) FROM trips t {where}", params)
    missing = _count(ctx, f"SELECT COUNT(*) FROM trips t {where} {'AND' if where else 'WHERE'} t.{key} IS NULL", params)
    return {
        "format": "int", "value": total,
        "method": [f"The different {label}s that ran at least one trip {ctx.window_text()}.",
                   f"A {label} who ran several trips is counted once.",
                   *ctx.scope_lines(False)],
        "formula": f"COUNT(DISTINCT {key}) over {_fmt(trips_all)} trips = {_fmt(total)}",
        "excluded": ([{"label": f"Trips with no {label} recorded (no {label} to count)", "count": missing}]
                     if missing else []),
        "columns": [{"key": "name", "label": label.capitalize(), "link": link},
                    {"key": "trips", "label": "Trips", "kind": "number"},
                    {"key": "trip_km", "label": "Km", "kind": "number", "digits": 1}],
        "rows": rows, "total": total,
    }


@dataset("dashboard.drivers")
def _dash_drivers(ctx: Ctx) -> dict:
    out = _distinct(ctx, "driver_id", "driver", "dr.name", "/drivers/{id}")
    out["title"] = "Active drivers"
    return out


@dataset("dashboard.vehicles")
def _dash_vehicles(ctx: Ctx) -> dict:
    out = _distinct(ctx, "vehicle_id", "vehicle", "v.asset_id", "/vehicles/{id}")
    out["title"] = "Vehicles"
    return out


def _measured(ctx: Ctx, col: str) -> tuple[str, list, int, int]:
    where, params = ctx.trips_where()
    joiner = "AND" if where else "WHERE"
    have = f"{where} {joiner} t.{col} IS NOT NULL"
    n_have = _count(ctx, f"SELECT COUNT(*) FROM trips t {have}", params)
    n_missing = _count(ctx, f"SELECT COUNT(*) FROM trips t {where} {joiner} t.{col} IS NULL", params)
    return have, params, n_have, n_missing


@dataset("dashboard.distance")
def _dash_distance(ctx: Ctx) -> dict:
    have, params, n, missing = _measured(ctx, "trip_km")
    with ctx.conn.cursor() as cur:
        cur.execute(f"SELECT ROUND(SUM(t.trip_km), 2) AS s FROM trips t {have}", params)
        s = cur.fetchone()["s"]
    # SQL's SUM over no values is NULL, which is what the tile shows ("—").
    total_km = float(s) if s is not None else None
    rows, total = _page(ctx, f"{_TRIP_SELECT} {have}", params, _TRIP_SORT, "trip_km")
    return {
        "title": "Total distance", "format": "km", "value": total_km,
        "method": [f"The distance of every trip {ctx.window_text()}, added up.",
                   "Each trip's distance (trip_km) is the figure the source reported for it.",
                   *ctx.scope_lines(False)],
        "formula": (f"SUM(trip_km) over {_fmt(n)} trips = {_fmt(total_km, 2)} km"
                    if n else "no trip in the window has a distance"),
        "excluded": ([{"label": "Trips with no distance reported (not added)", "count": missing}] if missing else []),
        "columns": _TRIP_COLUMNS, "rows": rows, "total": total,
    }


@dataset("dashboard.speed")
def _dash_speed(ctx: Ctx) -> dict:
    have, params, n, missing = _measured(ctx, "avg_speed_kmph")
    with ctx.conn.cursor() as cur:
        cur.execute(f"SELECT SUM(t.avg_speed_kmph) AS s, ROUND(AVG(t.avg_speed_kmph), 2) AS a "
                    f"FROM trips t {have}", params)
        r = cur.fetchone()
    s, avg = float(r["s"] or 0), (float(r["a"]) if r["a"] is not None else None)
    rows, total = _page(ctx, f"{_TRIP_SELECT} {have}", params, _TRIP_SORT, "avg_speed_kmph")
    return {
        "title": "Average speed", "format": "kmh", "value": avg,
        "method": [f"The mean of each trip's own average speed, over every trip {ctx.window_text()}.",
                   "Every trip weighs the same, however long it was: this is the typical trip's speed, "
                   "not total distance divided by total time.",
                   *ctx.scope_lines(False)],
        "formula": (f"{_fmt(s, 1)} km/h summed over {_fmt(n)} trips ÷ {_fmt(n)} = {_fmt(avg, 2)} km/h"
                    if n else "no trip in the window has an average speed"),
        "excluded": ([{"label": "Trips with no average speed (not averaged)", "count": missing}] if missing else []),
        "columns": _TRIP_COLUMNS, "rows": rows, "total": total,
    }


@dataset("dashboard.eta")
def _dash_eta(ctx: Ctx) -> dict:
    have, params, judged, missing = _measured(ctx, "eta_met")
    met = _count(ctx, f"SELECT COUNT(*) FROM trips t {have} AND t.eta_met = 1", params)
    rate = round(met / judged * 100, 2) if judged else None
    rows, total = _page(ctx, f"{_TRIP_SELECT} {have}", params, _TRIP_SORT, "trip_start")
    where, wparams = ctx.trips_where()
    joiner = "AND" if where else "WHERE"
    with ctx.conn.cursor() as cur:
        cur.execute(f"SELECT IFNULL(t.eta_data_status, '(none)') AS s, COUNT(*) AS n FROM trips t "
                    f"{where} {joiner} t.eta_met IS NULL GROUP BY s ORDER BY n DESC", wparams)
        why = cur.fetchall()
    return {
        "title": "ETA success rate", "format": "pct", "value": rate,
        "method": [f"Of the trips {ctx.window_text()} that have an ETA verdict, the share that met it.",
                   "A trip meets its ETA when it arrived by the ETA it was given (eta_met = 1).",
                   *ctx.scope_lines(False)],
        "formula": (f"{_fmt(met)} met ÷ {_fmt(judged)} judged × 100 = {_fmt(rate, 2)}%"
                    if judged else "no trip in the window has an ETA verdict"),
        "excluded": [{"label": f"Trips with no ETA verdict — {r['s']}", "count": int(r["n"])} for r in why],
        "columns": _TRIP_COLUMNS, "rows": rows, "total": total,
    }


# ---------------------------------------------------------------------------
# Trips page: the eight headline tiles (api/tta.py list_tta_trips)
# ---------------------------------------------------------------------------

_TTA_SELECT = """
    SELECT t.i_trip_no AS trip_no, t.s_trip_class AS trip_class, t.c_trip_status AS status,
           t.s_asset_id AS vehicle, t.s_org_node_name AS origin, t.s_dest_node_name AS destination,
           t.dt_trip_start AS trip_start, m.i_transit_time_min AS transit_min, m.i_detention_min AS detention_min,
           m.i_delivery_delta_min AS delivery_delta_min, m.d_distance_travelled_km AS distance_km,
           t.i_gps_ping_count AS gps_pings
"""
_TTA_FROM = "FROM tta_trips t LEFT JOIN tta_trip_metrics m ON m.i_trip_no = t.i_trip_no"
_TTA_SORT = {"trip_start": "trip_start", "trip_no": "trip_no", "status": "status", "transit_min": "transit_min",
             "detention_min": "detention_min", "delivery_delta_min": "delivery_delta_min",
             "distance_km": "distance_km", "gps_pings": "gps_pings", "vehicle": "vehicle"}
_TTA_COLUMNS = [
    {"key": "trip_no", "label": "Trip", "link": "/trips/{trip_no}"},
    {"key": "trip_class", "label": "Class"},
    {"key": "status", "label": "Status"},
    {"key": "vehicle", "label": "Vehicle"},
    {"key": "origin", "label": "From"},
    {"key": "destination", "label": "To"},
    {"key": "trip_start", "label": "Started", "kind": "datetime"},
    {"key": "transit_min", "label": "Transit min", "kind": "number"},
    {"key": "detention_min", "label": "Detention min", "kind": "number"},
    {"key": "delivery_delta_min", "label": "Late by min", "kind": "number"},
    {"key": "distance_km", "label": "Km", "kind": "number", "digits": 1},
    {"key": "gps_pings", "label": "GPS pings", "kind": "number"},
]


def _tta(ctx: Ctx) -> tuple[str, list, list[str]]:
    """The Trips page's own filter (api/tta.py trips_list_where), and what it said."""
    from nexgen.services.analytics.api.tta import trips_list_where
    search, status = ctx.extra.get("search", ""), ctx.extra.get("status", "")
    where, params = trips_list_where(ctx.scope, ctx.tclass, search, status)
    lines = ["Every trip on record (the Trips page has no date window)."]
    lines += ctx.scope_lines(True)
    if search:
        lines.append(f"Search: trips whose number, vehicle, consignor, origin, destination or driver "
                     f"contains “{search}”.")
    if status:
        lines.append(f"Status: exactly “{status}”.")
    return where, params, lines


def _and(where: str, cond: str) -> str:
    return f"{where} AND {cond}" if where else f"WHERE {cond}"


def _scalar(ctx: Ctx, sql: str, params: list):
    with ctx.conn.cursor() as cur:
        cur.execute(sql, params)
        v = list(cur.fetchone().values())[0]
    return float(v) if isinstance(v, Decimal) else v


def _tta_count(ctx: Ctx, title: str, cond: str | None, how: str, shown: str | None = None) -> dict:
    where, params, lines = _tta(ctx)
    w = _and(where, cond) if cond else where
    rows, total = _page(ctx, f"{_TTA_SELECT} {_TTA_FROM} {w}", params, _TTA_SORT, "trip_start")
    everything = int(_scalar(ctx, f"SELECT COUNT(*) {_TTA_FROM} {where}", params) or 0)
    label = shown or (cond or "")
    return {"title": title, "format": "int", "value": total, "method": [how, *lines],
            "formula": (f"COUNT(trips{' where ' + label if label else ''}) = {_fmt(total)}"
                        + (f" of {_fmt(everything)} trips" if cond else "")),
            "excluded": ([{"label": "Trips in scope that do not meet the condition", "count": everything - total}]
                         if cond and everything - total else []),
            "columns": _TTA_COLUMNS, "rows": rows, "total": total}


@dataset("trips.total")
def _trips_total(ctx: Ctx) -> dict:
    return _tta_count(ctx, "Total trips", None, "Each trip counted once.")


@dataset("trips.closed")
def _trips_closed(ctx: Ctx) -> dict:
    return _tta_count(ctx, "Closed trips", "LOWER(t.c_trip_status) LIKE '%%close%%'",
                      "A trip is closed when its status contains “close” (any case).",
                      "status contains 'close'")


@dataset("trips.active")
def _trips_active(ctx: Ctx) -> dict:
    return _tta_count(ctx, "Active trips", "LOWER(COALESCE(t.c_trip_status, '')) NOT LIKE '%%close%%'",
                      "Every trip that is not closed, including trips with no status yet. "
                      "Closed + active = all trips.",
                      "status does not contain 'close'")


def _tta_mean(ctx: Ctx, title: str, col: str, sort_key: str, how: str) -> dict:
    where, params, lines = _tta(ctx)
    have = _and(where, f"m.{col} IS NOT NULL")
    with ctx.conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n, SUM(m.{col}) AS s, ROUND(AVG(m.{col}), 0) AS a {_TTA_FROM} {have}", params)
        r = cur.fetchone()
    n, total_sum = int(r["n"] or 0), (float(r["s"]) if r["s"] is not None else None)
    value = float(r["a"]) if r["a"] is not None else None
    missing = int(_scalar(ctx, f"SELECT COUNT(*) {_TTA_FROM} {_and(where, f'm.{col} IS NULL')}", params) or 0)
    rows, total = _page(ctx, f"{_TTA_SELECT} {_TTA_FROM} {have}", params, _TTA_SORT, sort_key)
    return {"title": title, "format": "min", "value": value, "method": [how, *lines],
            "formula": (f"{_fmt(total_sum)} min summed over {_fmt(n)} trips ÷ {_fmt(n)} = "
                        f"{_fmt(total_sum / n, 2)} min, rounded to {_fmt(value)} min" if n else
                        "no trip in scope has this figure"),
            "excluded": ([{"label": "Trips without this figure from the source (not averaged)", "count": missing}]
                         if missing else []),
            "columns": _TTA_COLUMNS, "rows": rows, "total": total}


@dataset("trips.transit")
def _trips_transit(ctx: Ctx) -> dict:
    return _tta_mean(ctx, "Average transit", "i_transit_time_min", "transit_min",
                     "The mean of each trip's transit time, as the source reports it (minutes).")


@dataset("trips.detention")
def _trips_detention(ctx: Ctx) -> dict:
    return _tta_mean(ctx, "Average detention", "i_detention_min", "detention_min",
                     "The mean of each trip's detention, as the source reports it (minutes).")


@dataset("trips.ontime")
def _trips_ontime(ctx: Ctx) -> dict:
    where, params, lines = _tta(ctx)
    have = _and(where, "m.i_delivery_delta_min IS NOT NULL")
    judged = int(_scalar(ctx, f"SELECT COUNT(*) {_TTA_FROM} {have}", params) or 0)
    on_time = int(_scalar(ctx, f"SELECT COUNT(*) {_TTA_FROM} {_and(have, 'm.i_delivery_delta_min <= 0')}",
                          params) or 0)
    value = _scalar(ctx, f"SELECT ROUND(100 * SUM(m.i_delivery_delta_min <= 0) / "
                         f"NULLIF(SUM(m.i_delivery_delta_min IS NOT NULL), 0), 1) {_TTA_FROM} {where}", params)
    missing = int(_scalar(ctx, f"SELECT COUNT(*) {_TTA_FROM} {_and(where, 'm.i_delivery_delta_min IS NULL')}",
                          params) or 0)
    rows, total = _page(ctx, f"{_TTA_SELECT} {_TTA_FROM} {have}", params, _TTA_SORT, "delivery_delta_min")
    return {"title": "On-time %", "format": "pct", "value": value,
            "method": ["Of the trips with a delivery time, the share delivered on time: late by zero minutes "
                       "or less (i_delivery_delta_min ≤ 0, from the source).", *lines],
            "formula": (f"{_fmt(on_time)} on time ÷ {_fmt(judged)} with a delivery time × 100 = "
                        f"{_fmt(value, 1)}%" if judged else "no trip in scope has a delivery time"),
            "excluded": ([{"label": "Trips with no delivery time yet (not judged)", "count": missing}]
                         if missing else []),
            "columns": _TTA_COLUMNS, "rows": rows, "total": total}


@dataset("trips.distance")
def _trips_distance(ctx: Ctx) -> dict:
    where, params, lines = _tta(ctx)
    have = _and(where, "m.d_distance_travelled_km IS NOT NULL")
    value = _scalar(ctx, f"SELECT ROUND(SUM(m.d_distance_travelled_km), 1) {_TTA_FROM} {where}", params)
    n = int(_scalar(ctx, f"SELECT COUNT(*) {_TTA_FROM} {have}", params) or 0)
    missing = int(_scalar(ctx, f"SELECT COUNT(*) {_TTA_FROM} {_and(where, 'm.d_distance_travelled_km IS NULL')}",
                          params) or 0)
    rows, total = _page(ctx, f"{_TTA_SELECT} {_TTA_FROM} {have}", params, _TTA_SORT, "distance_km")
    return {"title": "Total distance", "format": "km", "value": value,
            "method": ["Each trip's distance travelled, as the source reports it, added up.", *lines],
            "formula": (f"SUM(distance) over {_fmt(n)} trips = {_fmt(value, 1)} km" if n
                        else "no trip in scope reports a distance"),
            "excluded": ([{"label": "Trips with no distance from the source (not added)", "count": missing}]
                         if missing else []),
            "columns": _TTA_COLUMNS, "rows": rows, "total": total}


@dataset("trips.gps")
def _trips_gps(ctx: Ctx) -> dict:
    where, params, lines = _tta(ctx)
    everything = int(_scalar(ctx, f"SELECT COUNT(*) {_TTA_FROM} {where}", params) or 0)
    with_gps = int(_scalar(ctx, f"SELECT COUNT(*) {_TTA_FROM} {_and(where, 't.i_gps_ping_count > 0')}",
                           params) or 0)
    value = _scalar(ctx, f"SELECT ROUND(100 * SUM(t.i_gps_ping_count > 0) / NULLIF(COUNT(*), 0), 0) "
                         f"{_TTA_FROM} {where}", params)
    if ctx.sort is None:
        ctx.order = "asc"           # the trips without GPS first: they are the ones to look at
    rows, total = _page(ctx, f"{_TTA_SELECT} {_TTA_FROM} {where}", params, _TTA_SORT, "gps_pings")
    return {"title": "GPS coverage", "format": "pct", "value": value,
            "method": ["The share of trips with at least one GPS fix stored.", *lines],
            "formula": (f"{_fmt(with_gps)} trips with GPS ÷ {_fmt(everything)} trips × 100 = {_fmt(value)}%"
                        if everything else "no trips in scope"),
            "excluded": ([{"label": "Trips with no GPS fix (counted in the total, not as covered)",
                           "count": everything - with_gps}] if everything - with_gps else []),
            "columns": _TTA_COLUMNS, "rows": rows, "total": total}


# ---------------------------------------------------------------------------
# One trip's page (api/tta.py get_tta_trip): the source's figures, and its GPS
# ---------------------------------------------------------------------------

# tile -> (stored column, the source record's field names, unit, label)
_SOURCE_FIELDS = {
    "distance": ("d_distance_travelled_km", ("distance_travelled",), "km", "Distance"),
    "transit": ("i_transit_time_min", ("transit_time",), "min", "Transit time"),
    "moving": ("i_moving_time_min", ("total_moving_time",), "min", "Moving time"),
    "stoppage": ("i_stoppage_time_min", ("total_stoppage_time",), "min", "Stoppage"),
    "violations": ("i_speed_violation", ("speed_voilation", "speed_violation"), "int", "Speed violations"),
}


def _trip_no(ctx: Ctx) -> int:
    try:
        trip_no = int(ctx.extra.get("trip_no", ""))
    except ValueError:
        raise HTTPException(400, "trip_no is required")
    # Out-of-scope trips are indistinguishable from missing ones, as on the trip page.
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT i_cnr_id FROM tta_trips WHERE i_trip_no = %s", (trip_no,))
        row = cur.fetchone()
    if not row or not ctx.scope.allows(row["i_cnr_id"]):
        raise HTTPException(404, f"Trip {trip_no} not found")
    return trip_no


def _duration_steps(text) -> str | None:
    """The minutes arithmetic for a duration text, exactly as parse_duration_min does it."""
    from nexgen.shared.feed.tta import _DUR_RE, _DUR_WORDS_RE, clean
    v = clean(text)
    if v is None:
        return None
    m = _DUR_RE.search(str(v))
    if m:
        d, h, mi = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
        return f"{d} d × 1440 + {h} h × 60 + {mi} min = {d * 1440 + h * 60 + mi} min"
    w = _DUR_WORDS_RE.search(str(v))
    if not w or not any(w.groups()):
        return None
    d, h, mi, sec = (int(g or 0) for g in w.groups())
    return (f"{d} d × 1440 + {h} h × 60 + {mi} min + ⌊{sec} s ÷ 60⌋ = "
            f"{d * 1440 + h * 60 + mi + sec // 60} min (seconds are dropped, never rounded up)")


@dataset("trip.source")
def _trip_source(ctx: Ctx) -> dict:
    import json
    trip_no = _trip_no(ctx)
    key = ctx.extra.get("field", "")
    if key not in _SOURCE_FIELDS:
        raise HTTPException(400, f"field must be one of {sorted(_SOURCE_FIELDS)}")
    col, names, unit, label = _SOURCE_FIELDS[key]
    with ctx.conn.cursor() as cur:
        cur.execute(f"SELECT m.{col} AS v, m.raw_json, m.dt_created, t.s_trip_class "
                    f"FROM tta_trips t LEFT JOIN tta_trip_metrics m ON m.i_trip_no = t.i_trip_no "
                    f"WHERE t.i_trip_no = %s", (trip_no,))
        r = cur.fetchone() or {}
    raw = r.get("raw_json")
    record = (json.loads(raw) if isinstance(raw, str) else raw) or {}
    used = next((n for n in names if n in record), None)
    value = _plain(r.get("v"))
    sent = record.get(used) if used else None
    lines = [f"Reported by the source (eTrans {r.get('s_trip_class') or ''} TTA report) for trip {trip_no}: "
             f"NexGen does not calculate this figure, it keeps what was sent.",
             (f"Source field “{used}” = “{sent}”, stored {str(r.get('dt_created') or '')[:16]}."
              if used else f"The source record has no {' / '.join(names)} field for this trip, so the tile is blank.")]
    if unit == "min":
        steps = _duration_steps(sent) if used else None
        formula = steps or ("as reported" if used else "nothing reported")
    elif unit == "km":
        formula = f"“{sent}” read as a number = {_fmt(value, 2)} km" if used else "nothing reported"
    else:
        formula = f"“{sent}” read as a whole number = {_fmt(value)}" if used else "nothing reported"

    # An independent look from the trip's own GPS, where one means the same thing.
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n, SUM(IFNULL(i_dist, 0)) AS m, MIN(dt_message) AS a, MAX(dt_message) AS b "
                    "FROM tta_trip_gps WHERE i_trip_no = %s", (trip_no,))
        g = cur.fetchone()
    if g and g["n"]:
        if key == "distance":
            lines.append(f"Independent check: the trip's {_fmt(g['n'])} GPS fixes add up to "
                         f"{_fmt(float(g['m'] or 0) / 1000, 1)} km (the per-fix distances the source sent).")
        elif key == "transit":
            span = (g["b"] - g["a"]).total_seconds() / 60 if g["a"] and g["b"] else None
            lines.append(f"For comparison: the trip's GPS runs from {str(g['a'])[:16]} to {str(g['b'])[:16]}, "
                         f"{_fmt(span)} min (booking to arrival, a wider window than transit).")
    rows = [{"field": k, "value": None if v is None else str(v), "used": "this figure" if k == used else ""}
            for k, v in sorted(record.items(), key=lambda kv: (kv[0] != used, kv[0]))]
    return {"title": label, "format": "km" if unit == "km" else ("min" if unit == "min" else "int"),
            "value": value, "method": lines, "formula": formula, "excluded": [],
            "columns": [{"key": "field", "label": "Source field"}, {"key": "value", "label": "As sent"},
                        {"key": "used", "label": "Used for"}],
            "rows": rows, "total": len(rows)}


@dataset("trip.gps")
def _trip_gps(ctx: Ctx) -> dict:
    trip_no = _trip_no(ctx)
    sql = ("SELECT dt_message, d_lat, d_long, COALESCE(i_status_speed_kmph, i_speed) AS speed_kmph, s_status, "
           "is_moving, s_wpnt1, i_dist, i_cdist FROM tta_trip_gps_cdist WHERE i_trip_no = %s")
    if ctx.sort is None:
        ctx.order = "asc"
    rows, total = _page(ctx, sql, [trip_no], {"dt_message": "dt_message", "speed_kmph": "speed_kmph",
                                               "i_dist": "i_dist"}, "dt_message")
    with ctx.conn.cursor() as cur:
        cur.execute("SELECT SUM(is_moving = 1) AS mv, SUM(is_moving = 0) AS st FROM tta_trip_gps WHERE i_trip_no = %s",
                    (trip_no,))
        mv = cur.fetchone()
    return {"title": "GPS pings", "format": "int", "value": total,
            "method": [f"Every GPS fix of trip {trip_no}: its truck's fixes inside the trip's window "
                       f"(booking to arrival), one stored copy per physical fix.",
                       f"Moving fixes {_fmt(int(mv['mv'] or 0))}, stopped {_fmt(int(mv['st'] or 0))} "
                       f"(the source's own moving flag)."],
            "formula": f"COUNT(fixes of trip {trip_no}) = {_fmt(total)}",
            "excluded": [],
            "columns": [{"key": "dt_message", "label": "Time", "kind": "datetime"},
                        {"key": "d_lat", "label": "Lat", "kind": "number", "digits": 5},
                        {"key": "d_long", "label": "Lon", "kind": "number", "digits": 5},
                        {"key": "speed_kmph", "label": "km/h", "kind": "number"},
                        {"key": "s_status", "label": "Status"},
                        {"key": "is_moving", "label": "Moving", "kind": "bool"},
                        {"key": "s_wpnt1", "label": "Near"},
                        {"key": "i_dist", "label": "Step m", "kind": "number"},
                        {"key": "i_cdist", "label": "Cumulative m", "kind": "number"}],
            "rows": rows, "total": total}


# ---------------------------------------------------------------------------
# Analytics > Overview: the KPI block (lib/tta_dashboard.py kpis / _kpi_block)
# ---------------------------------------------------------------------------
# Computed in pandas over the analytics filter bar's frame. The proof builds
# that same frame with the same two functions (dashboard_filters, then
# apply_filters(load_df())) and reads the figure from the same _kpi_block, so
# the tile and its recount are one computation; only the records and the
# arithmetic are added.

_OV_MULTI = ("transporters", "destinations", "vehicle_categories", "own_market", "consignors", "consignees",
             "vehicles", "drivers", "device_types", "asset_makes")


def _overview_frame(ctx: Ctx):
    from nexgen.services.analytics.api.tta_dashboard import dashboard_filters
    from nexgen.services.analytics.lib import tta_dashboard as svc
    f = dashboard_filters(date_from=ctx.date_from, date_to=ctx.date_to, scope=ctx.scope, tclass=ctx.tclass,
                          **{k: ctx.extra.get(k, "") for k in _OV_MULTI})
    df = svc.apply_filters(svc.load_df(ctx.conn), f)
    fr, to = _day(ctx.date_from), _day(ctx.date_to)
    lines = [f"Trips dispatched from {fr} to {to}, both days whole." if fr and to else
             "Trips dispatched at any time (no date window)."]
    lines += ctx.scope_lines(True)
    for k in _OV_MULTI:
        v = f.get(k)
        if v:
            lines.append(f"{k.replace('_', ' ').capitalize()}: {', '.join(map(str, v))}.")
    lines.append("Names spelled several ways (case, spacing) are folded into one before anything is counted.")
    return df, svc, lines


_OV_COLS = [
    {"key": "trip_id", "label": "Trip", "link": "/trips/{trip_id}"},
    {"key": "dept_dt", "label": "Dispatched", "kind": "datetime"},
    {"key": "transporter", "label": "Transporter"},
    {"key": "vehicle_no", "label": "Vehicle"},
    {"key": "destination", "label": "To"},
    {"key": "delivery_status", "label": "Delivery status"},
    {"key": "is_on_time", "label": "On time", "kind": "bool"},
    {"key": "transit_hours", "label": "Transit h", "kind": "number", "digits": 1},
    {"key": "detention_hours", "label": "Detention h", "kind": "number", "digits": 1},
    {"key": "distance_km", "label": "Km", "kind": "number", "digits": 1},
    {"key": "speed_violations", "label": "Speed alerts", "kind": "number"},
]


def _frame_page(ctx: Ctx, df, sort_default: str, cols=None) -> tuple[list[dict], int]:
    from nexgen.services.analytics.lib.tta_dashboard import clean_records
    cols = cols or [c["key"] for c in _OV_COLS]
    key = ctx.sort if ctx.sort in df.columns else sort_default
    out = df.sort_values(key, ascending=(ctx.order == "asc"), na_position="last") if key in df.columns else df
    if not ctx.extra.get("csv"):
        out = out.iloc[(ctx.page - 1) * ctx.page_size: ctx.page * ctx.page_size]
    return clean_records(out[[c for c in cols if c in out.columns]]), int(len(df))


def _ov_metric(ctx: Ctx, key: str) -> dict:
    df, svc, lines = _overview_frame(ctx)
    block = svc._kpi_block(df) if len(df) else {}
    n = int(len(df))
    if key == "trips":
        rows, total = _frame_page(ctx, df, "dept_dt")
        return {"title": "Total trips", "format": "int", "value": block.get("trips", 0),
                "method": ["Each trip counted once.", *lines], "formula": f"COUNT(trips) = {_fmt(n)}",
                "excluded": [], "columns": _OV_COLS, "rows": rows, "total": total}
    if key == "otd":
        judged = df[df["is_on_time"].notna()]
        on = int((judged["is_on_time"] == 1).sum())
        rows, total = _frame_page(ctx, judged, "dept_dt")
        return {"title": "On-time delivery", "format": "pct", "value": block.get("otd_pct"),
                "method": ["Of the trips that can be judged, the share delivered on time.",
                           "On time when the delivery status says “on time”, late when it says "
                           "“delay”; with no status, on time when delivered no later than due "
                           "(delivery delta ≤ 0).", *lines],
                "formula": (f"{_fmt(on)} on time ÷ {_fmt(len(judged))} judged × 100 = "
                            f"{_fmt(block.get('otd_pct'), 1)}%" if len(judged) else "no trip can be judged yet"),
                "excluded": ([{"label": "Trips with neither a delivery status nor a delivery time (not judged)",
                               "count": n - len(judged)}] if n - len(judged) else []),
                "columns": _OV_COLS, "rows": rows, "total": total}
    if key in ("transit", "detention"):
        col = f"{key}_hours"
        have = df[df[col].notna()]
        rows, total = _frame_page(ctx, have, col)
        mean = float(have[col].mean()) if len(have) else None
        value = block.get(f"avg_{key}_hours")
        excluded = []
        if n - len(have):
            label = ("Trips with no transit time, or a transit of 0 (a force-closed trip), not averaged"
                     if key == "transit" else "Trips with no detention from the source (not averaged)")
            excluded.append({"label": label, "count": n - len(have)})
        return {"title": f"Average {key}", "format": "hours", "value": value,
                "method": [f"The mean of each trip's {key} in hours (the source's minutes ÷ 60).", *lines],
                "formula": (f"{_fmt(float(have[col].sum()), 2)} h summed over {_fmt(len(have))} trips ÷ "
                            f"{_fmt(len(have))} = {_fmt(mean, 3)} h, rounded to {_fmt(value, 1)} h"
                            if len(have) else "no trip in the window has this figure"),
                "excluded": excluded, "columns": _OV_COLS, "rows": rows, "total": total}
    if key == "km":
        have = df[df["distance_km"].notna()]
        rows, total = _frame_page(ctx, have, "distance_km")
        return {"title": "Total distance", "format": "km", "value": block.get("total_km"),
                "method": ["Each trip's distance travelled, as the source reports it, added up "
                           "and rounded to whole km.", *lines],
                "formula": (f"SUM(distance) over {_fmt(len(have))} trips = {_fmt(float(have['distance_km'].sum()), 2)} km"
                            f" → {_fmt(block.get('total_km'))} km" if len(have) else "no trip reports a distance"),
                "excluded": ([{"label": "Trips with no distance from the source (not added)", "count": n - len(have)}]
                             if n - len(have) else []),
                "columns": _OV_COLS, "rows": rows, "total": total}
    if key == "violations":
        reporting = df[df["violations_reported"] == 1]
        rows, total = _frame_page(ctx, reporting, "speed_violations")
        sv, k = int(reporting["speed_violations"].sum()), int(len(reporting))
        return {"title": "Speed alerts per trip", "format": "num", "value": block.get("avg_violations_per_trip"),
                "method": ["Speed violations the source reports, divided by the trips that report a count.",
                           "A trip whose report has no violation field is left out, not counted as 0: "
                           "the local report carries no such field.", *lines],
                "formula": (f"{_fmt(sv)} violations ÷ {_fmt(k)} reporting trips = {_fmt(sv / k, 3)}, "
                            f"rounded to {_fmt(block.get('avg_violations_per_trip'), 1)}" if k
                            else "no trip in the window reports a violation count"),
                "excluded": ([{"label": "Trips whose source reports no violation count (not averaged)", "count": n - k}]
                             if n - k else []),
                "columns": _OV_COLS, "rows": rows, "total": total}
    # distinct transporters / vehicles: one row per name
    col, label = ("transporter", "transporter") if key == "transporters" else ("vehicle_no", "vehicle")
    named = df[df[col].notna()]
    g = (named.groupby(col).agg(trips=("trip_id", "count"), km=("distance_km", "sum")).reset_index()
         .rename(columns={col: "name"}))
    if ctx.sort not in ("trips", "km", "name"):
        ctx.sort = "trips"
    rows, total = _frame_page(ctx, g, "trips", ["name", "trips", "km"])
    return {"title": f"Active {label}s" if key == "transporters" else "Unique vehicles", "format": "int",
            "value": block.get(key, 0),
            "method": [f"The different {label}s that ran at least one trip in the window, each counted once.", *lines],
            "formula": f"COUNT(DISTINCT {label}) over {_fmt(n)} trips = {_fmt(block.get(key, 0))}",
            "excluded": ([{"label": f"Trips with no {label} named (nothing to count)", "count": n - len(named)}]
                         if n - len(named) else []),
            "columns": [{"key": "name", "label": label.capitalize()},
                        {"key": "trips", "label": "Trips", "kind": "number"},
                        {"key": "km", "label": "Km", "kind": "number", "digits": 1}],
            "rows": rows, "total": total}


for _k in ("trips", "otd", "transit", "detention", "km", "violations", "transporters", "vehicles"):
    DATASETS[f"overview.{_k}"] = (lambda key: (lambda ctx: _ov_metric(ctx, key)))(_k)


# ---------------------------------------------------------------------------
# Vehicle / driver / route pages: their precomputed summaries
# (lib/data_migration.py refresh_summaries_incremental)
# ---------------------------------------------------------------------------
# The tiles read vehicle_summary / driver_summary / route_summary, which the
# kpi worker rebuilds whenever one of the entity's trips changes. The proof
# recomputes the figure from the trips now, with the summaries' own rule
# (_INCR_BASE_WHERE, imported so the two cannot drift), and lists those trips
# and the ones the rule leaves out. A tile that disagrees is a stale summary.

_ENTITY = {
    # kind: (trip-key condition, params from the request, summary table and key, label)
    "vehicle": ("t.vehicle_id = %s", ("id",), "vehicle_summary", "vehicle_id = %s", "this vehicle"),
    "driver": ("t.driver_id = %s", ("id",), "driver_summary", "driver_id = %s", "this driver"),
    "route": ("lo.name = %s AND ld.name = %s", ("origin", "destination"), "route_summary",
              "origin = %s AND destination = %s", "this route"),
}

_SUMMARY_METRICS = {
    # metric: (title, format, SQL over trips t, summary column, what it is)
    "trips": ("Total trips", "int", "COUNT(*)", None, "Each counted trip once."),
    "drivers": ("Drivers used", "int", "COUNT(DISTINCT t.driver_id)", "drivers_used",
                "The different drivers on those trips, each counted once."),
    "speed": ("Average speed", "kmh", "ROUND(AVG(t.avg_speed_kmph), 2)", "avg_speed_kmph",
              "The mean of each trip's own average speed (every trip weighs the same)."),
    "eta": ("ETA rate", "pct", "ROUND(SUM(CASE WHEN t.eta_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(t.eta_met), 0) * 100, 2)",
            "eta_success_rate", "Of those trips with an ETA verdict, the share that met it."),
    "delay": ("Average delay", "min", "ROUND(AVG(t.eta_delay_minutes), 2)", "avg_eta_delay_min",
              "The mean of each trip's delay against its ETA, in minutes (early trips count as negative)."),
    "duration": ("Average duration", "min", "ROUND(AVG(t.trip_duration_minutes), 2)", "avg_duration_min",
                 "The mean of each trip's duration, in minutes."),
    "distance": ("Average distance", "km", "ROUND(AVG(t.trip_km), 2)", "avg_distance_km",
                 "The mean of each trip's distance (trip_km)."),
}

_ENT_JOIN = """
      FROM trips t
      LEFT JOIN locations lo  ON lo.id = t.origin_id
      LEFT JOIN locations ld  ON ld.id = t.destination_id
      LEFT JOIN drivers dr    ON dr.id = t.driver_id
      LEFT JOIN vehicles v    ON v.id = t.vehicle_id
"""
_ENT_SELECT = """
    SELECT t.dispatch_entry_no AS trip_no, t.trip_start, lo.name AS origin, ld.name AS destination,
           dr.name AS driver, v.asset_id AS vehicle, t.trip_duration_minutes AS duration_min, t.trip_km,
           t.avg_speed_kmph, t.eta_met, t.eta_delay_minutes AS delay_min, t.eta_data_status
"""
_ENT_COLUMNS = [
    {"key": "trip_no", "label": "Trip", "link": "/trips/{trip_no}"},
    {"key": "trip_start", "label": "Departed", "kind": "datetime"},
    {"key": "origin", "label": "From"},
    {"key": "destination", "label": "To"},
    {"key": "driver", "label": "Driver"},
    {"key": "vehicle", "label": "Vehicle"},
    {"key": "duration_min", "label": "Duration min", "kind": "number"},
    {"key": "trip_km", "label": "Km", "kind": "number", "digits": 1},
    {"key": "avg_speed_kmph", "label": "Avg km/h", "kind": "number", "digits": 1},
    {"key": "eta_met", "label": "ETA met", "kind": "bool"},
    {"key": "delay_min", "label": "Delay min", "kind": "number"},
]
_ENT_SORT = {"trip_start": "trip_start", "trip_no": "trip_no", "duration_min": "duration_min",
             "trip_km": "trip_km", "avg_speed_kmph": "avg_speed_kmph", "delay_min": "delay_min"}


def _summary_metric(ctx: Ctx, kind: str, metric: str) -> dict:
    from nexgen.services.analytics.lib.data_migration import _INCR_BASE_WHERE as RULE
    key_sql, key_params, table, table_key, who = _ENTITY[kind]
    try:
        kp = [int(ctx.extra[k]) if k == "id" else ctx.extra[k] for k in key_params]
    except (KeyError, ValueError):
        raise HTTPException(400, f"{' and '.join(key_params)} required")
    title, fmt, expr, col, what = _SUMMARY_METRICS[metric]
    scope_sql, scope_params = (" AND t.cnr_id = %s", [ctx.scope.id]) if ctx.scope.active else ("", [])
    entity = f"WHERE {key_sql}{scope_sql}"
    counted = f"{entity} AND {RULE}"
    params = kp + scope_params
    value = _scalar(ctx, f"SELECT {expr} {_ENT_JOIN} {counted}", params)
    n = int(_scalar(ctx, f"SELECT COUNT(*) {_ENT_JOIN} {counted}", params) or 0)
    everything = int(_scalar(ctx, f"SELECT COUNT(*) {_ENT_JOIN} {entity}", params) or 0)
    with ctx.conn.cursor() as cur:
        cur.execute(f"SELECT {col or 'total_trips' if kind != 'route' else col or 'trip_count'} AS v "
                    f"FROM {table} WHERE {table_key} AND cnr_id = %s", kp + [ctx.scope.summary_id])
        stored_row = cur.fetchone()
        cur.execute(f"""SELECT CASE WHEN IFNULL(t.eta_data_status, '') <> 'available'
                                    THEN CONCAT('ETA data ', IFNULL(t.eta_data_status, 'missing'))
                                    ELSE 'no positive duration' END AS why, COUNT(*) AS n
                          {_ENT_JOIN} {entity} AND NOT ({RULE}) GROUP BY why ORDER BY n DESC""", params)
        why = cur.fetchall()
    stored = _plain(stored_row["v"]) if stored_row else None
    rows, total = _page(ctx, f"{_ENT_SELECT} {_ENT_JOIN} {counted}", params, _ENT_SORT, "trip_start")
    extra = []
    if metric == "eta":
        met = int(_scalar(ctx, f"SELECT COUNT(*) {_ENT_JOIN} {counted} AND t.eta_met = 1", params) or 0)
        judged = int(_scalar(ctx, f"SELECT COUNT(t.eta_met) {_ENT_JOIN} {counted}", params) or 0)
        formula = (f"{_fmt(met)} met ÷ {_fmt(judged)} judged × 100 = {_fmt(value, 2)}%"
                   if judged else "no counted trip has an ETA verdict")
    elif metric in ("trips", "drivers"):
        formula = f"{expr.replace('t.', '')} over {_fmt(n)} counted trips = {_fmt(value)}"
    else:
        formula = (f"{expr.replace('t.', '').replace('ROUND(', '').replace(', 2)', '')} over {_fmt(n)} counted trips "
                   f"= {_fmt(value, 2)}" if n else "no counted trips")
    return {"title": title, "format": fmt, "value": value,
            "method": [f"{what}",
                       f"Counted: the trips of {who} whose ETA data is available and whose duration is above "
                       f"zero — the rule the page's summary is built with. {_fmt(n)} of its "
                       f"{_fmt(everything)} trips qualify.",
                       *ctx.scope_lines(False),
                       ("The tile reads the precomputed summary"
                        + (f" (stored value: {stored})." if stored is not None else ", which has no row yet.")
                        + " It is rebuilt whenever one of these trips changes; this dropdown recounts from the "
                          "trips now."), *extra],
            "formula": formula,
            "excluded": [{"label": f"Trips of {who} left out — {w['why']}", "count": int(w["n"])} for w in why],
            "differs_note": "The tile reads the stored summary; if it differs from the recount, the summary is "
                            "stale and is rebuilt on the next change to these trips.",
            "columns": _ENT_COLUMNS, "rows": rows, "total": total}


for _kind, _metrics in (("vehicle", ("trips", "drivers", "speed", "eta")),
                        ("driver", ("trips", "eta", "speed", "delay")),
                        ("route", ("trips", "duration", "eta", "distance"))):
    for _m in _metrics:
        DATASETS[f"{_kind}.{_m}"] = (lambda k, m: (lambda ctx: _summary_metric(ctx, k, m)))(_kind, _m)


# ---------------------------------------------------------------------------
# Detention & delivery proof page (Smart-Truck's circle geofencing:
# shared/circlefence/detention.py and gps_quality.py)
# ---------------------------------------------------------------------------
# These endpoints are fleet-wide (no consignor filter) and default to the
# zonal class, so the proofs do the same. Each calls the very function the
# tile's endpoint calls and lists the per-trip inputs it judged.

def _circle_class(ctx: Ctx) -> str | None:
    v = ctx.trip_class if ctx.trip_class is not None else "zonal"
    return None if v in ("", "all") else v


def _class_line(tc: str | None) -> str:
    return (f"Trip class: {tc} (this page's own toggle)." if tc else "Trip class: all.") + \
        " This page is fleet-wide: the consignor filter does not apply to it."


def _median_note(values: list, pick) -> str:
    n = len(values)
    if not n:
        return "no values"
    i = min(n - 1, int(n * 0.5))
    return (f"the {n} values sorted ascending; position ⌊{n} × 0.5⌋ = {i} (counting from 0) "
            f"is {pick}" + (" — for an even count the upper of the two middle values, not their average"
                            if n % 2 == 0 else ""))


_DET_COLS = [
    {"key": "trip_no", "label": "Trip", "link": "/trips/{trip_no}"},
    {"key": "transporter", "label": "Transporter"},
    {"key": "destination", "label": "To"},
    {"key": "declared_h", "label": "Declared h", "kind": "number", "digits": 2},
    {"key": "tail_h", "label": "Hidden tail h", "kind": "number", "digits": 2},
    {"key": "total_h", "label": "True total h", "kind": "number", "digits": 2},
    {"key": "gap_min", "label": "Exit gap min", "kind": "number"},
    {"key": "rank", "label": "Rank", "kind": "number"},
]


def _list_page(ctx: Ctx, rows: list[dict], default: str) -> tuple[list[dict], int]:
    key = ctx.sort or default
    rows = sorted(rows, key=lambda r: (r.get(key) is None, r.get(key)), reverse=(ctx.order == "desc"))
    if ctx.extra.get("csv"):
        return rows, len(rows)
    return rows[(ctx.page - 1) * ctx.page_size: ctx.page * ctx.page_size], len(rows)


def _detention(ctx: Ctx, metric: str) -> dict:
    from nexgen.shared.circlefence import detention as det
    tc = _circle_class(ctx)
    rows = det._fetch(ctx.conn, tc, None)
    wide = [r for r in rows if (r["i_geofence_out_gap_min"] or 0) > det.MAX_TRUSTED_GAP_MIN]
    trusted = [r for r in rows if (r["i_geofence_out_gap_min"] or 0) <= det.MAX_TRUSTED_GAP_MIN]
    negative = [r for r in trusted if (r["tail_min"] or 0) < 0]
    clean = [r for r in trusted if (r["tail_min"] or 0) >= 0]
    field = {"declared": "declared_min", "tail": "tail_min", "total": "total_min", "tail4h": "tail_min"}[metric]
    have = [r for r in clean if r[field] is not None]
    values = [r[field] / 60.0 for r in have]
    order = sorted(range(len(have)), key=lambda i: values[i])
    rank = {i: k for k, i in enumerate(order)}
    out_rows = [{"trip_no": r["i_trip_no"], "transporter": r["s_trans_name"], "destination": r["s_dest_node_name"],
                 "declared_h": None if r["declared_min"] is None else round(r["declared_min"] / 60.0, 2),
                 "tail_h": None if r["tail_min"] is None else round(r["tail_min"] / 60.0, 2),
                 "total_h": None if r["total_min"] is None else round(r["total_min"] / 60.0, 2),
                 "gap_min": r["i_geofence_out_gap_min"], "rank": rank[i]} for i, r in enumerate(have)]
    excluded = []
    if wide:
        excluded.append({"label": f"Exit time uncertain by more than {det.MAX_TRUSTED_GAP_MIN} min (GPS gap at the "
                                  "crossing)", "count": len(wide)})
    if negative:
        excluded.append({"label": "Cleared the fence before its own gate-out stamp (a late stamp, not detention)",
                         "count": len(negative)})
    lines = ["Trips with a confirmed exit from the origin geofence and a booking time.",
             "Declared = booking → trip start (the gate-out stamp); hidden tail = trip start → the "
             "GPS-confirmed geofence exit; true total = booking → exit.", _class_line(tc)]
    if metric == "tail4h":
        over = [r for r in out_rows if r["tail_h"] is not None and r["tail_h"] > 4]
        page, total = _list_page(ctx, over, "tail_h")
        return {"title": "Trips with 4h+ hidden tail", "format": "int", "value": sum(1 for v in values if v > 4),
                "method": ["The trips whose hidden tail (trip start → GPS exit) is longer than 4 hours.", *lines],
                "formula": f"COUNT(hidden tail > 4 h) over {_fmt(len(values))} trips = {_fmt(len(over))}",
                "excluded": excluded, "columns": _DET_COLS, "rows": page, "total": total}
    value = det._pct(values, 50)
    title = {"declared": "Declared detention (median)", "tail": "Hidden tail after gate-out (median)",
             "total": "True total (median)"}[metric]
    key = {"declared": "declared_h", "tail": "tail_h", "total": "total_h"}[metric]
    if ctx.sort is None:
        ctx.sort, ctx.order = "rank", "asc"
    page, total = _list_page(ctx, out_rows, "rank")
    return {"title": title, "format": "hours", "value": value,
            "method": ["The median, not the mean: detention is heavily right-skewed and a few multi-day holds "
                       "would otherwise set the typical figure.", *lines],
            "formula": f"median = {_median_note(values, f'{_fmt(value, 2)} h' if value is not None else '')}",
            "excluded": excluded, "columns": _DET_COLS, "rows": page, "total": total}


for _m in ("declared", "tail", "total", "tail4h"):
    DATASETS[f"detention.{_m}"] = (lambda m: (lambda ctx: _detention(ctx, m)))(_m)


_GPS_COLS = [
    {"key": "trip_no", "label": "Trip", "link": "/trips/{trip_no}"},
    {"key": "transporter", "label": "Transporter"},
    {"key": "verdict", "label": "Verdict"},
    {"key": "pings", "label": "Pings", "kind": "number"},
    {"key": "trip_start", "label": "Trip start", "kind": "datetime"},
    {"key": "first_ping", "label": "First ping", "kind": "datetime"},
    {"key": "lag_min", "label": "Lag min", "kind": "number"},
    {"key": "uptime_pct", "label": "Uptime %", "kind": "number", "digits": 1},
]


def _gps(ctx: Ctx, metric: str) -> dict:
    from nexgen.shared.circlefence import gps_quality as gq
    tc = _circle_class(ctx)
    raw = gq._coverage_rows(ctx.conn, tc)
    rows = []
    for r in raw:
        lag = ((r["first_ping"] - r["dt_trip_start"]).total_seconds() / 60.0
               if r["dt_trip_start"] and r["first_ping"] else None)
        rows.append({"trip_no": r["i_trip_no"], "transporter": r["s_trans_name"], "verdict": gq._classify(r),
                     "pings": r["i_gps_ping_count"] or 0, "trip_start": _plain(r["dt_trip_start"]),
                     "first_ping": _plain(r["first_ping"]), "lag_min": None if lag is None else round(lag),
                     "uptime_pct": _plain(r["d_uptime_pct"])})
    n = len(rows)
    rules = ["Each trip gets one verdict, the first that applies: silent (no ping at all) → died at origin "
             "(tracker stopped inside the origin fence) → late start (first ping more than "
             f"{gq.ON_TIME_LAG_MIN} min after trip start) → gappy (uptime under 80%) → healthy.",
             _class_line(tc)]
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("ok", "silent", "died_at_origin")}
    if metric in ("ok", "silent", "died"):
        verdict = {"ok": "ok", "silent": "silent", "died": "died_at_origin"}[metric]
        sel = [r for r in rows if r["verdict"] == verdict]
        page, total = _list_page(ctx, sel, "trip_start")
        if metric == "ok":
            value = gq._rate(counts["ok"], n)
            return {"title": "GPS healthy", "format": "pct", "value": value, "method": rules,
                    "formula": (f"{_fmt(counts['ok'])} healthy ÷ {_fmt(n)} trips × 100 = {_fmt(value, 1)}%"
                                if n else "no trips"),
                    "excluded": [], "columns": _GPS_COLS, "rows": page, "total": total}
        title = "Silent (no data received)" if metric == "silent" else "Died at origin"
        return {"title": title, "format": "int", "value": len(sel), "method": rules,
                "formula": f"COUNT(verdict = {verdict}) over {_fmt(n)} trips = {_fmt(len(sel))}",
                "excluded": [], "columns": _GPS_COLS, "rows": page, "total": total}
    if metric == "ontime":
        timed = [r for r in rows if r["lag_min"] is not None]
        on = sum(1 for r in rows if r["trip_start"] and r["first_ping"] and
                 (datetime.fromisoformat(r["first_ping"]) - datetime.fromisoformat(r["trip_start"])).total_seconds()
                 / 60.0 <= gq.ON_TIME_LAG_MIN)
        value = gq._rate(on, len(timed))
        page, total = _list_page(ctx, timed, "lag_min")
        return {"title": "GPS on time", "format": "pct", "value": value,
                "method": [f"Of the trips with both a trip start and a first ping, the share whose first ping came "
                           f"no more than {gq.ON_TIME_LAG_MIN} min after the trip started.", _class_line(tc)],
                "formula": (f"{_fmt(on)} on time ÷ {_fmt(len(timed))} trips × 100 = {_fmt(value, 1)}%"
                            if timed else "no trip has both times"),
                "excluded": ([{"label": "Trips without a trip start or without any ping (cannot be timed)",
                               "count": n - len(timed)}] if n - len(timed) else []),
                "columns": _GPS_COLS, "rows": page, "total": total}
    # median pings per trip
    vals = sorted(r["pings"] for r in rows)
    value = vals[n // 2] if n else None
    if ctx.sort is None:
        ctx.sort, ctx.order = "pings", "asc"
    page, total = _list_page(ctx, rows, "pings")
    return {"title": "Median pings / trip", "format": "int", "value": value,
            "method": ["The middle of the trips' GPS ping counts (a trip with no ping counts as 0).", _class_line(tc)],
            "formula": (f"the {n} counts sorted ascending; position ⌊{n} ÷ 2⌋ = {n // 2} (counting from 0) "
                        f"is {_fmt(value)}" if n else "no trips"),
            "excluded": [], "columns": _GPS_COLS, "rows": page, "total": total}


for _m in ("ok", "silent", "died", "ontime", "pings"):
    DATASETS[f"gpsperf.{_m}"] = (lambda m: (lambda ctx: _gps(ctx, m)))(_m)


def _coverage(ctx: Ctx, metric: str) -> dict:
    from nexgen.shared.circlefence import gps_quality as gq
    tc = _circle_class(ctx)
    if metric in ("short", "checked"):
        lanes = gq.route_end_gap(tc, 5, ctx.conn)
        rows = [{"destination": l["destination"], "trips": l["trips"], "median_gap_km": l["median_end_gap_km"],
                 "fence": l["fence_source"], "conclusive": 1 if l["gap_is_conclusive"] else 0,
                 "counted": 1 if (l["gap_is_conclusive"] and l["median_end_gap_km"] > 25) else 0} for l in lanes]
        cols = [{"key": "destination", "label": "Destination"}, {"key": "trips", "label": "Trips", "kind": "number"},
                {"key": "median_gap_km", "label": "Median end gap km", "kind": "number", "digits": 1},
                {"key": "fence", "label": "Fence from"}, {"key": "conclusive", "label": "Conclusive", "kind": "bool"},
                {"key": "counted", "label": "Stops short", "kind": "bool"}]
        how = ["A lane is one destination with at least 5 trips; its end gap is how far from the destination's "
               "fence each trip's last GPS fix was, and the median of those.",
               "Conclusive unless the fence is a gazetteer town centre and the median is 100 km or less "
               "(then the delivery point is not precisely known).", _class_line(tc)]
        if metric == "short":
            sel = [r for r in rows if r["counted"]]
            page, total = _list_page(ctx, sel, "median_gap_km")
            return {"title": "Lanes stopping short", "format": "int", "value": len(sel),
                    "method": ["Lanes whose trails conclusively end more than 25 km short of the destination.", *how],
                    "formula": f"COUNT(conclusive AND median end gap > 25 km) over {_fmt(len(rows))} lanes = {_fmt(len(sel))}",
                    "excluded": [], "columns": cols, "rows": page, "total": total}
        page, total = _list_page(ctx, rows, "median_gap_km")
        return {"title": "Lanes checked", "format": "int", "value": len(rows), "method": how,
                "formula": f"COUNT(lanes with ≥ 5 trips and a destination fence) = {_fmt(len(rows))}",
                "excluded": [], "columns": cols, "rows": page, "total": total}
    u = gq.ungeofenced_destinations(tc, ctx.conn)
    rows = [{"destination": f["destination"], "lane_trips": f["trips_on_lane"],
             "affected": f["trips_ran_full_route_no_geofence_close"], "median_km": f["median_lane_distance_km"],
             "close_reasons": ", ".join(f["close_reasons"])} for f in u["findings"]]
    cols = [{"key": "destination", "label": "Destination"}, {"key": "lane_trips", "label": "Trips on lane", "kind": "number"},
            {"key": "affected", "label": "Ran full route, closed without a geofence", "kind": "number"},
            {"key": "median_km", "label": "Lane median km", "kind": "number", "digits": 1},
            {"key": "close_reasons", "label": "Close reasons"}]
    how = [f"A destination qualifies when no upstream geofence is recorded on its lane, it has at least "
           f"{gq.MIN_LANE_TRIPS} trips with a distance, and trips ran at least {int(gq.DISTANCE_OK_FRACTION * 100)}% "
           "of the lane's median distance with GPS yet closed for a reason other than a geofence hit.", _class_line(tc)]
    page, total = _list_page(ctx, rows, "affected")
    if metric == "need":
        return {"title": "Destinations needing a geofence", "format": "int", "value": u["destinations_needing_a_geofence"],
                "method": how, "formula": f"COUNT(qualifying destinations) = {_fmt(len(rows))}",
                "excluded": [], "columns": cols, "rows": page, "total": total}
    return {"title": "Trips affected", "format": "int", "value": u["trips_affected"], "method": how,
            "formula": (" + ".join(_fmt(r["affected"]) for r in rows[:12]) + (" + …" if len(rows) > 12 else "")
                        + f" = {_fmt(u['trips_affected'])}") if rows else "no destination qualifies",
            "excluded": [], "columns": cols, "rows": page, "total": total}


for _m in ("short", "checked", "need", "affected"):
    DATASETS[f"coverage.{_m}"] = (lambda m: (lambda ctx: _coverage(ctx, m)))(_m)


# ---------------------------------------------------------------------------
# Detention page > Trip on the map: one trip's three windows
# (shared/circlefence/track.py trip_track)
# ---------------------------------------------------------------------------

@dataset("tripproof.hold")
def _trip_hold(ctx: Ctx) -> dict:
    from nexgen.shared.circlefence.track import _hours
    trip_no = _trip_no(ctx)
    metric = ctx.extra.get("metric", "declared")
    with ctx.conn.cursor() as cur:
        cur.execute("""SELECT dt_booking, dt_trip_start, dt_geofence_out, i_geofence_out_gap_min,
                              s_geofence_out_status, s_org_node_name
                         FROM tta_trips WHERE i_trip_no = %s""", (trip_no,))
        t = cur.fetchone()
        cur.execute("""SELECT s_fence_key, s_role, s_event, dt_event, i_gap_min FROM tta_trip_geofence_events
                        WHERE i_trip_no = %s ORDER BY dt_event""", (trip_no,))
        events = cur.fetchall()
    b, st, out = t["dt_booking"], t["dt_trip_start"], t["dt_geofence_out"]
    declared, hidden, total = _hours(b, st), _hours(st, out), _hours(b, out)
    under = None if not total or not declared or total == 0 else round((total - declared) / total * 100, 1)
    rows = [{"what": "Booking (plant entry)", "time": _plain(b), "source": "TMS record (dt_booking)"},
            {"what": "Trip start (gate-out stamp)", "time": _plain(st), "source": "TMS record (dt_trip_start)"},
            {"what": "Exit from the origin geofence", "time": _plain(out),
             "source": (f"GPS: the last fix inside the {t['s_org_node_name'] or 'origin'} fence; next fix "
                        f"{t['i_geofence_out_gap_min']} min later" if out else
                        f"not confirmed ({t['s_geofence_out_status'] or 'not computed yet'})")}]
    rows += [{"what": f"Fence {e['s_event']} ({e['s_role']}: {e['s_fence_key']})", "time": _plain(e["dt_event"]),
              "source": f"GPS crossing, gap {e['i_gap_min']} min"} for e in events]

    def hrs(a, z, v):
        return f"{str(z)[:16]} − {str(a)[:16]} = {_fmt(v, 2)} h" if v is not None else "a time is missing"

    pick = {
        "declared": ("Declared by the TMS", declared, hrs(b, st, declared),
                     "Booking to the gate-out stamp: the detention the TMS itself reports."),
        "hidden": ("Hidden after gate-out", hidden, hrs(st, out, hidden),
                   "Gate-out stamp to the moment the GPS shows the truck leaving the origin fence."),
        "total": ("True total hold", total, hrs(b, out, total), "Booking to the GPS-confirmed exit."),
        "understated": ("Understated by", under,
                        (f"({_fmt(total, 2)} − {_fmt(declared, 2)}) ÷ {_fmt(total, 2)} × 100 = {_fmt(under, 1)}%"
                         if under is not None else "needs both the declared and the true total"),
                        "How much of the true hold the declared figure leaves out."),
    }
    if metric not in pick:
        raise HTTPException(400, f"metric must be one of {sorted(pick)}")
    title, value, formula, what = pick[metric]
    return {"title": title, "format": "pct" if metric == "understated" else "hours", "value": value,
            "method": [what, f"Trip {trip_no}. Hours are rounded to two decimals."],
            "formula": formula, "excluded": [],
            "columns": [{"key": "what", "label": "Event"}, {"key": "time", "label": "When", "kind": "datetime"},
                        {"key": "source", "label": "From"}],
            "rows": rows, "total": len(rows)}


# ---------------------------------------------------------------------------
# the endpoint
# ---------------------------------------------------------------------------

@router.get("")
def list_datasets():
    return {"datasets": sorted(DATASETS)}


@router.get("/{name}")
def proof(name: str, request: Request,
          date_from: str = "", date_to: str = "",
          page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
          sort: str | None = None, order: str = Query("desc", pattern="^(asc|desc)$"),
          format: str = Query("json", pattern="^(json|csv)$"),
          trip_class: str | None = None,
          scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    fn = DATASETS.get(name)
    if fn is None:
        raise HTTPException(404, f"no proof dataset {name!r}")
    extra = {k: v for k, v in request.query_params.items()
             if k not in {"date_from", "date_to", "page", "page_size", "sort", "order", "format", "trip_class"}}
    ctx = Ctx(conn, scope, date_from, date_to, page, page_size, sort, order, trip_class or None, extra)
    if format == "csv":
        ctx.extra["csv"] = True
        out = fn(ctx)
        buf = io.StringIO()
        cols = out["columns"]
        w = csv.writer(buf)
        w.writerow([c["label"] for c in cols])
        for r in out["rows"]:
            w.writerow([r.get(c["key"]) for c in cols])
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    out = fn(ctx)
    out.update({"dataset": name, "page": page, "page_size": page_size,
                "computed_at": datetime.now().isoformat(sep=" ", timespec="seconds")})
    return out
