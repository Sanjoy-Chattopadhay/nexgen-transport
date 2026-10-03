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
