"""KPI drill-downs: what every headline number is made of.

Every KPI tile in the application opens a dropdown that answers "which ones?"
-- the records behind the number, a page at a time, and how the number splits
by transporter, site, vehicle, day or hour. One endpoint serves them all:

    GET /api/v1/drill/{dataset}?preset=…&group=…&page=…  + the page's context

A dataset is one ledger (trips, physical visits, alerts, stops, …) with
whitelisted filters, presets, breakdowns, measures and sorts; the client never
sends SQL. The dropdown's total is computed the way its tile is, so the two
agree:

* **Direct context** (a day, a site, a date range) filters the ledger's own
  columns -- the day summary's arrivals are physical visits entered that day.
* **Trip context** (`via=trips`: a vehicle, transporter, lane or driver, or
  the trips list's filters) selects the events *owned* by those trips. Every
  physical event is owned by the lowest-numbered trip that saw it
  (geo_trip_share), which is exactly how the entity pages total them, so a
  shared stay is never counted twice across a group of trips.

Nothing is fetched until a dropdown opens, and every list is paged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from nexgen.shared.geoengine.api.v1.common import clean, day_bounds, parse_day, percentile, resolve_run, rows
from nexgen.shared.geoengine.db import get_geo_db
from nexgen.shared.geoengine.pipeline import physical as P

router = APIRouter(tags=["KPI drill-downs"])

UNKNOWN = "(no trip record)"
NULL_KEY = "__null__"
# Breakdowns over names group exactly, as the rollups count them in Python;
# the database's default collation would fold "ABC Ltd" and "ABC LTD" together.
TEXT_GROUPS = {"transporter", "vehicle", "driver", "lane", "quality", "status", "kind", "scale",
               "confirmed", "reason", "state", "district", "type", "category", "nh", "route", "spot",
               "shape", "verdict", "mode"}
FAC = "('micro','site','campus')"
GRID_DEG = 0.002


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------

@dataclass
class Ctx:
    """The page a dropdown was opened on, from the query string."""
    run: int | None = None
    day: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    via: str | None = None
    vehicle: str | None = None
    transporter: str | None = None
    driver: str | None = None
    origin: str | None = None
    destination: str | None = None
    site_id: int | None = None
    trip: int | None = None
    status: str | None = None
    quality: str | None = None
    kind: str | None = None
    state: str | None = None
    scale: str | None = None
    category: str | None = None
    type: str | None = None
    has_alerts: bool | None = None
    q: str | None = None
    min_minutes: int | None = None
    metric: str | None = None
    tmode: str | None = None
    verdict: str | None = None
    from_site: int | None = None
    to_site: int | None = None

    def window(self) -> tuple[datetime | None, datetime | None]:
        if self.day:
            d = parse_day(self.day)
            a = datetime.combine(d, datetime.min.time())
            return a, a + timedelta(days=1)
        return day_bounds(self.date_from, self.date_to)


def _ctx(request: Request) -> Ctx:
    qp = request.query_params

    def s(k):
        v = qp.get(k)
        return v if v not in (None, "") else None

    def i(k):
        v = s(k)
        if v is None:
            return None
        try:
            return int(v)
        except ValueError:
            raise HTTPException(400, f"{k} must be a number") from None

    ha = s("has_alerts")
    return Ctx(run=i("run"), day=s("day"), date_from=s("from"), date_to=s("to"), via=s("via"),
               vehicle=s("vehicle"), transporter=s("transporter"), driver=s("driver"),
               origin=s("origin"), destination=s("destination"), site_id=i("site_id"),
               trip=i("trip"), status=s("status"), quality=s("quality"), kind=s("kind"),
               state=s("state"), scale=s("scale"), category=s("category"), type=s("type"),
               has_alerts=(ha in ("1", "true", "yes")) if ha else None, q=s("q"),
               min_minutes=i("min_minutes"), metric=s("metric"), tmode=s("tmode"), verdict=s("verdict"),
               from_site=i("from_site"), to_site=i("to_site"))


_LONGEST: dict[tuple, int] = {}


def longest_stay_s(cur, rid: int, facility: bool = False) -> int:
    """The run's longest physical stay, so "overlapping a window" can use the
    time index: nothing that entered longer ago than this can still be inside.
    Cached per run and data version (a refresh can only add longer stays)."""
    from nexgen.shared.geoengine.api import cache
    key = (rid, facility, cache.current_version())
    if key not in _LONGEST:
        cur.execute("SELECT COALESCE(MAX(i_dwell_seconds), 0) n FROM geo_pvisit WHERE i_run_id=%s"
                    + (f" AND s_scale IN {FAC}" if facility else ""), (rid,))
        _LONGEST[key] = int(cur.fetchone()["n"] or 0) + 1
        if len(_LONGEST) > 32:
            _LONGEST.pop(next(iter(_LONGEST)))
    return _LONGEST[key]


def _eq(col: str, value, where: list, params: list, unknown: str | None = None) -> None:
    """col = value, where the page's "unknown" label means NULL."""
    if value is None:
        return
    if unknown is not None and value == unknown:
        where.append(f"{col} IS NULL")
    else:
        where.append(f"{col} = %s")
        params.append(value)


def trip_where(ctx: Ctx, rid: int) -> tuple[list[str], list]:
    """The trips a trip-context dropdown is about: the same selection the
    trips list and the entity pages make (aliases s, m, sh)."""
    where, params = ["s.i_run_id = %s"], [rid]
    if ctx.trip:
        where.append("s.i_trip_no = %s")
        params.append(ctx.trip)
    if ctx.vehicle:
        where.append("COALESCE(m.s_asset_id, s.s_asset_id) = %s")
        params.append(ctx.vehicle)
    _eq("m.s_trans_name", ctx.transporter, where, params, UNKNOWN)
    _eq("m.s_driver_name", ctx.driver, where, params)
    _eq("m.s_status", ctx.status, where, params)
    _eq("s.s_quality", ctx.quality, where, params)
    for col, val in (("m.s_origin", ctx.origin), ("m.s_destination", ctx.destination)):
        if val is not None:
            where.append(f"{col} <=> %s")
            params.append(None if val == "?" else val)
    if ctx.origin is not None or ctx.destination is not None:
        where.append("m.i_trip_no IS NOT NULL")
    if ctx.site_id:
        where.append("EXISTS (SELECT 1 FROM geo_visit v2 WHERE v2.i_run_id = s.i_run_id "
                     "AND v2.i_trip_no = s.i_trip_no AND v2.i_site_id = %s)")
        params.append(ctx.site_id)
    if ctx.has_alerts:
        where.append("s.i_violations > 0")
    if ctx.q:
        if ctx.q.isdigit():
            where.append("s.i_trip_no = %s")
            params.append(int(ctx.q))
        else:
            where.append("(COALESCE(m.s_asset_id, s.s_asset_id) LIKE %s OR m.s_driver_name LIKE %s "
                         "OR m.s_trans_name LIKE %s OR m.s_origin LIKE %s OR m.s_destination LIKE %s)")
            params += [f"%{ctx.q}%"] * 5
    a, b = ctx.window()
    if a:
        where.append("s.dt_last_ping >= %s")
        params.append(a)
    if b:
        where.append("s.dt_first_ping < %s")
        params.append(b)
    return where, params


TRIP_FROM = """geo_trip_summary s
    LEFT JOIN geo_trip_meta m ON m.i_trip_no = s.i_trip_no
    LEFT JOIN geo_trip_share sh ON sh.i_run_id = s.i_run_id AND sh.i_trip_no = s.i_trip_no"""


def trip_subquery(ctx: Ctx, rid: int) -> tuple[str, list]:
    where, params = trip_where(ctx, rid)
    return f"SELECT s.i_trip_no FROM {TRIP_FROM} WHERE {' AND '.join(where)}", params


# ---------------------------------------------------------------------------
# SQL datasets
# ---------------------------------------------------------------------------

@dataclass
class Dataset:
    title: str
    source: str                           # FROM clause
    run_col: str | None
    select: str
    sorts: dict[str, str]
    default_sort: str
    groups: dict[str, tuple[str, str]]    # name -> (key expr, label expr)
    measures: dict[str, str]
    presets: dict[str, str] = field(default_factory=dict)
    time_start: str | None = None         # the event's time, or the start of its interval
    time_end: str | None = None           # the end of its interval, for "overlapping the window"
    trip_col: str | None = None           # the owning trip, for via=trips
    direct: dict[str, str] = field(default_factory=dict)   # ctx field -> column
    search: tuple[str, ...] = ()
    trips_dataset: bool = False


TRIP_SELECT = """s.i_trip_no, COALESCE(m.s_asset_id, s.s_asset_id) AS s_asset_id, m.s_trans_name,
    m.s_driver_name, m.s_origin, m.s_destination, m.s_status, s.dt_first_ping, s.dt_last_ping,
    s.s_quality, s.s_quality_reason, s.i_facility_visits, s.i_places, s.i_facility_dwell_s,
    s.i_violations, s.i_transit_s, s.d_distance_km, s.i_pings_read, s.i_pings_used, s.i_pings_dropped,
    s.i_spikes, s.i_medians, s.i_snapped, s.i_stops, s.i_moving_gaps, s.i_moving_gap_s, s.i_inferred,
    s.s_first_site, s.s_last_site, sh.i_siblings, sh.s_sibling_trips,
    COALESCE(sh.i_facility_visits, s.i_facility_visits) AS share_visits,
    COALESCE(sh.i_facility_dwell_s, s.i_facility_dwell_s) AS share_dwell_s,
    COALESCE(sh.i_alerts, s.i_violations) AS share_alerts,
    COALESCE(sh.d_distance_km, s.d_distance_km) AS share_km,
    p.s_shape, p.i_loading_s, p.i_unloading_s, p.i_transit_moving_s, p.i_transit_stop_s,
    p.i_transit_halt_s, p.i_transit_silent_s, p.i_before_s, p.i_after_s, p.i_span_s, p.j_bar"""

DATASETS: dict[str, Dataset] = {
    "trips": Dataset(
        title="Trips",
        source=TRIP_FROM + " LEFT JOIN geo_trip_phase p ON p.i_run_id = s.i_run_id AND p.i_trip_no = s.i_trip_no",
        run_col=None, select=TRIP_SELECT, trips_dataset=True,
        sorts={"start": "s.dt_first_ping", "trip": "s.i_trip_no", "alerts": "share_alerts",
               "visits": "share_visits", "dwell": "share_dwell_s", "km": "share_km",
               "transit": "s.i_transit_s", "loading": "p.i_loading_s", "unloading": "p.i_unloading_s",
               "pings": "s.i_pings_read", "dropped": "s.i_pings_dropped", "spikes": "s.i_spikes",
               "medians": "s.i_medians", "snapped": "s.i_snapped", "stops": "s.i_stops",
               "moving_gaps": "s.i_moving_gaps", "inferred": "s.i_inferred"},
        default_sort="start",
        groups={
            "transporter": (f"COALESCE(m.s_trans_name, '{UNKNOWN}')", f"COALESCE(m.s_trans_name, '{UNKNOWN}')"),
            "vehicle": ("COALESCE(m.s_asset_id, s.s_asset_id)", "COALESCE(m.s_asset_id, s.s_asset_id)"),
            "driver": ("m.s_driver_name", "m.s_driver_name"),
            "lane": ("CONCAT(COALESCE(m.s_origin,'?'), ' → ', COALESCE(m.s_destination,'?'))",
                     "CONCAT(COALESCE(m.s_origin,'?'), ' → ', COALESCE(m.s_destination,'?'))"),
            "quality": ("s.s_quality", "s.s_quality"),
            "status": ("m.s_status", "m.s_status"),
            "day": ("DATE(s.dt_first_ping)", "DATE(s.dt_first_ping)"),
            "first_place": ("s.i_first_site_id", "s.s_first_site"),
            "last_place": ("s.i_last_site_id", "s.s_last_site"),
            "shape": ("p.s_shape", "p.s_shape"),
        },
        measures={"count": "COUNT(*)",
                  "visits": "SUM(COALESCE(sh.i_facility_visits, s.i_facility_visits))",
                  "dwell": "SUM(COALESCE(sh.i_facility_dwell_s, s.i_facility_dwell_s))",
                  "km": "ROUND(SUM(COALESCE(sh.d_distance_km, s.d_distance_km)), 1)",
                  "alerts": "SUM(COALESCE(sh.i_alerts, s.i_violations))",
                  "pings": "SUM(s.i_pings_read)", "used": "SUM(s.i_pings_used)",
                  "dropped": "SUM(s.i_pings_dropped)", "spikes": "SUM(s.i_spikes)",
                  "medians": "SUM(s.i_medians)", "snapped": "SUM(s.i_snapped)",
                  "stops": "SUM(s.i_stops)", "moving_gaps": "SUM(s.i_moving_gaps)",
                  "inferred": "SUM(COALESCE(sh.i_inferred, s.i_inferred))",
                  "vehicles": "COUNT(DISTINCT COALESCE(m.s_asset_id, s.s_asset_id))"},
        presets={"alerts": "COALESCE(sh.i_alerts, s.i_violations) > 0",
                 "broken": "s.s_quality = 'broken'",
                 "with_lane": "s.i_places >= 2",
                 "sharing": "sh.i_siblings > 0",
                 "moving_gaps": "s.i_moving_gaps > 0",
                 "spikes": "s.i_spikes > 0",
                 "dropped": "s.i_pings_dropped > 0",
                 "medians": "s.i_medians > 0",
                 "snapped": "s.i_snapped > 0",
                 "stops": "s.i_stops > 0",
                 "inferred": "s.i_inferred > 0",
                 "visits": "COALESCE(sh.i_facility_visits, s.i_facility_visits) > 0",
                 "loaded": "p.s_shape = 'loaded'"},
    ),
    "visits": Dataset(
        title="Stays at geofences",
        source="geo_pvisit v LEFT JOIN geo_trip_meta m ON m.i_trip_no = v.i_trip_no",
        run_col="v.i_run_id",
        select="""v.id, v.i_trip_no, v.i_trips, v.s_trips, v.s_asset_id, v.i_site_id, v.s_site_name,
                  v.s_category, v.s_scale, v.dt_enter, v.dt_exit, v.b_open, v.b_entry_observed,
                  v.i_dwell_seconds, v.b_primary, v.s_trans_name, v.s_driver_name, v.s_confirmed_by,
                  v.i_max_speed, m.s_origin, m.s_destination""",
        sorts={"enter": "v.dt_enter", "dwell": "v.i_dwell_seconds", "site": "v.s_site_name",
               "vehicle": "v.s_asset_id"},
        default_sort="enter",
        groups={"site": ("v.i_site_id", "v.s_site_name"),
                "vehicle": ("v.s_asset_id", "v.s_asset_id"),
                "transporter": (f"COALESCE(v.s_trans_name, '{UNKNOWN}')", f"COALESCE(v.s_trans_name, '{UNKNOWN}')"),
                "driver": ("v.s_driver_name", "v.s_driver_name"),
                "day": ("DATE(v.dt_enter)", "DATE(v.dt_enter)"),
                "hour": ("HOUR(v.dt_enter)", "HOUR(v.dt_enter)"),
                "weekday": ("WEEKDAY(v.dt_enter)", "DAYNAME(v.dt_enter)"),
                "scale": ("v.s_scale", "v.s_scale"),
                "confirmed": ("v.s_confirmed_by", "v.s_confirmed_by")},
        measures={"count": "COUNT(*)", "dwell": "SUM(v.i_dwell_seconds)",
                  "vehicles": "COUNT(DISTINCT v.s_asset_id)", "sites": "COUNT(DISTINCT v.i_site_id)"},
        presets={"facility": f"v.s_scale IN {FAC}",
                 "arrivals": f"v.s_scale IN {FAC} AND v.b_primary = 1 AND v.b_entry_observed = 1",
                 "primary_facility": f"v.s_scale IN {FAC} AND v.b_primary = 1",
                 "open": "v.b_open = 1",
                 "measured": "v.b_open = 0 AND v.b_entry_observed = 1 AND v.dt_exit IS NOT NULL"},
        time_start="v.dt_enter",
        time_end="COALESCE(v.dt_exit, v.dt_enter + INTERVAL v.i_dwell_seconds SECOND)",
        trip_col="v.i_trip_no",
        direct={"vehicle": "v.s_asset_id", "transporter": "v.s_trans_name", "driver": "v.s_driver_name",
                "site_id": "v.i_site_id", "scale": "v.s_scale", "category": "v.s_category"},
        search=("v.s_asset_id", "v.s_site_name", "v.s_trans_name"),
    ),
    "alerts": Dataset(
        title="Alerts",
        source="geo_palert x LEFT JOIN geo_trip_meta m ON m.i_trip_no = x.i_trip_no",
        run_col="x.i_run_id",
        select="""x.id, x.dt_event, x.s_kind, x.i_site_id, x.s_site_name, x.i_trip_no, x.i_trips, x.s_trips,
                  x.s_asset_id, x.i_observed, x.i_limit, x.s_detail, x.d_lat, x.d_long, x.s_trans_name,
                  x.s_driver_name, m.s_origin, m.s_destination""",
        sorts={"time": "x.dt_event", "excess": "(x.i_observed - x.i_limit)", "site": "x.s_site_name",
               "vehicle": "x.s_asset_id"},
        default_sort="time",
        groups={"kind": ("x.s_kind", "x.s_kind"), "site": ("x.i_site_id", "x.s_site_name"),
                "vehicle": ("x.s_asset_id", "x.s_asset_id"),
                "transporter": (f"COALESCE(x.s_trans_name, '{UNKNOWN}')", f"COALESCE(x.s_trans_name, '{UNKNOWN}')"),
                "driver": ("x.s_driver_name", "x.s_driver_name"),
                "day": ("DATE(x.dt_event)", "DATE(x.dt_event)"),
                "hour": ("HOUR(x.dt_event)", "HOUR(x.dt_event)")},
        measures={"count": "COUNT(*)", "vehicles": "COUNT(DISTINCT x.s_asset_id)"},
        presets={"overspeed": "x.s_kind = 'overspeed'", "restricted": "x.s_kind <> 'overspeed'",
                 "restricted_entry": "x.s_kind = 'restricted_entry'",
                 "high_risk_entry": "x.s_kind = 'high_risk_entry'"},
        time_start="x.dt_event", trip_col="x.i_trip_no",
        direct={"vehicle": "x.s_asset_id", "transporter": "x.s_trans_name", "driver": "x.s_driver_name",
                "site_id": "x.i_site_id", "kind": "x.s_kind"},
        search=("x.s_asset_id", "x.s_site_name", "x.s_trans_name", "x.s_driver_name"),
    ),
    "stops": Dataset(
        title="Stops",
        source="geo_pstop s LEFT JOIN geo_trip_meta m ON m.i_trip_no = s.i_trip_no",
        run_col="s.i_run_id",
        select="""s.id, s.i_trip_no, s.i_trips, s.s_trips, s.s_asset_id, s.dt_start, s.dt_end, s.i_duration_s,
                  s.d_lat, s.d_long, s.d_p90_spread_m, s.i_site_id, s.s_site_name, s.s_scale, s.s_trans_name,
                  m.s_driver_name, s.s_origin, s.s_destination""",
        sorts={"start": "s.dt_start", "duration": "s.i_duration_s", "vehicle": "s.s_asset_id"},
        default_sort="duration",
        groups={"vehicle": ("s.s_asset_id", "s.s_asset_id"),
                "transporter": (f"COALESCE(s.s_trans_name, '{UNKNOWN}')", f"COALESCE(s.s_trans_name, '{UNKNOWN}')"),
                "site": ("s.i_site_id", "COALESCE(s.s_site_name, 'outside every fence')"),
                "lane": ("CONCAT(COALESCE(s.s_origin,'?'), ' → ', COALESCE(s.s_destination,'?'))",
                         "CONCAT(COALESCE(s.s_origin,'?'), ' → ', COALESCE(s.s_destination,'?'))"),
                "day": ("DATE(s.dt_start)", "DATE(s.dt_start)"),
                "hour": ("HOUR(s.dt_start)", "HOUR(s.dt_start)"),
                "spot": (f"CONCAT(ROUND(s.d_lat / {GRID_DEG}), ':', ROUND(s.d_long / {GRID_DEG}))",
                         "CONCAT(ROUND(AVG(s.d_lat), 4), ', ', ROUND(AVG(s.d_long), 4))")},
        measures={"count": "COUNT(*)", "duration": "SUM(s.i_duration_s)",
                  "vehicles": "COUNT(DISTINCT s.s_asset_id)"},
        presets={"outside": "s.i_fence_id IS NULL", "inside": "s.i_fence_id IS NOT NULL"},
        time_start="s.dt_start", trip_col="s.i_trip_no",
        direct={"vehicle": "s.s_asset_id", "transporter": "s.s_trans_name", "site_id": "s.i_site_id"},
        search=("s.s_asset_id", "s.s_trans_name"),
    ),
    "gaps": Dataset(
        title="GPS holes",
        source="geo_gap g LEFT JOIN geo_trip_meta m ON m.i_trip_no = g.i_trip_no",
        run_col="g.i_run_id",
        select="""g.id, g.i_trip_no, g.s_asset_id, g.dt_from, g.dt_to, g.i_gap_s, g.s_kind, g.s_route,
                  g.d_straight_m, g.d_route_m, g.i_unexplained_s, g.d_from_lat, g.d_from_long,
                  m.s_trans_name, m.s_origin, m.s_destination""",
        sorts={"start": "g.dt_from", "duration": "g.i_gap_s", "moved": "g.d_straight_m"},
        default_sort="duration",
        groups={"kind": ("g.s_kind", "g.s_kind"), "vehicle": ("g.s_asset_id", "g.s_asset_id"),
                "transporter": (f"COALESCE(m.s_trans_name, '{UNKNOWN}')", f"COALESCE(m.s_trans_name, '{UNKNOWN}')"),
                "day": ("DATE(g.dt_from)", "DATE(g.dt_from)"), "hour": ("HOUR(g.dt_from)", "HOUR(g.dt_from)"),
                "route": ("g.s_route", "g.s_route")},
        measures={"count": "COUNT(*)", "duration": "SUM(g.i_gap_s)",
                  "km": "ROUND(SUM(g.d_straight_m) / 1000, 1)"},
        presets={"moving": "g.s_kind = 'moving'", "still": "g.s_kind <> 'moving'"},
        time_start="g.dt_from", trip_col="g.i_trip_no",
        direct={"vehicle": "g.s_asset_id", "trip": "g.i_trip_no"},
        search=("g.s_asset_id",),
    ),
    "feed": Dataset(
        title="GPS fixes by trip and day",
        source="geo_trip_day td LEFT JOIN geo_trip_meta m ON m.i_trip_no = td.i_trip_no",
        run_col="td.i_run_id",
        select="""td.i_trip_no, td.d_day, COALESCE(td.s_asset_id, CONCAT('trip:', td.i_trip_no)) s_asset_id,
                  td.i_pings, td.i_rejected, td.i_spikes, td.i_medians, td.i_snapped, m.s_trans_name,
                  m.s_origin, m.s_destination""",
        sorts={"pings": "td.i_pings", "rejected": "td.i_rejected", "spikes": "td.i_spikes", "day": "td.d_day"},
        default_sort="pings",
        groups={"vehicle": ("COALESCE(td.s_asset_id, CONCAT('trip:', td.i_trip_no))",
                            "COALESCE(td.s_asset_id, CONCAT('trip:', td.i_trip_no))"),
                "transporter": (f"COALESCE(m.s_trans_name, '{UNKNOWN}')", f"COALESCE(m.s_trans_name, '{UNKNOWN}')"),
                "day": ("td.d_day", "td.d_day")},
        measures={"count": "COUNT(*)", "pings": "SUM(td.i_pings)", "rejected": "SUM(td.i_rejected)",
                  "spikes": "SUM(td.i_spikes)",
                  "vehicles": "COUNT(DISTINCT COALESCE(td.s_asset_id, CONCAT('trip:', td.i_trip_no)))"},
        presets={"rejected": "td.i_rejected > 0", "spikes": "td.i_spikes > 0"},
        time_start="td.d_day", trip_col="td.i_trip_no",
        direct={"vehicle": "td.s_asset_id"},
        search=("td.s_asset_id",),
    ),
    "rejects": Dataset(
        title="Fixes refused before fitting",
        source="""geo_ping_reject r LEFT JOIN geo_trip_meta m ON m.i_trip_no = r.i_trip_no
                  LEFT JOIN geo_trip_summary s ON s.i_run_id = r.i_run_id AND s.i_trip_no = r.i_trip_no""",
        run_col="r.i_run_id",
        select="""r.i_trip_no, r.s_reason, r.i_count, COALESCE(m.s_asset_id, s.s_asset_id) s_asset_id,
                  m.s_trans_name, s.dt_first_ping, s.i_pings_read""",
        sorts={"count": "r.i_count", "trip": "r.i_trip_no"},
        default_sort="count",
        groups={"reason": ("r.s_reason", "r.s_reason"),
                "transporter": (f"COALESCE(m.s_trans_name, '{UNKNOWN}')", f"COALESCE(m.s_trans_name, '{UNKNOWN}')"),
                "vehicle": ("COALESCE(m.s_asset_id, s.s_asset_id)", "COALESCE(m.s_asset_id, s.s_asset_id)")},
        measures={"count": "SUM(r.i_count)"},
        trip_col="r.i_trip_no",
    ),
    "fences": Dataset(
        title="Geofences",
        source="geo_fence f LEFT JOIN geo_fence_stats fs ON fs.i_run_id = {rid} AND fs.i_fence_id = f.i_fence_id",
        run_col=None,
        select=f"""f.i_fence_id, f.i_site_id, f.s_site_name, f.s_type, f.s_category, f.b_active,
                   {"CASE WHEN f.d_area_sqm < 10000 THEN 'micro' WHEN f.d_area_sqm < 1000000 THEN 'site' "
                    "WHEN f.d_area_sqm < 100000000 THEN 'campus' ELSE 'regional' END"} AS s_scale,
                   ROUND(f.d_area_sqm) d_area_sqm, f.d_inradius_m, f.s_state, f.s_district, f.s_states,
                   f.d_state_offset_m, COALESCE(fs.i_visits, 0) i_visits, COALESCE(fs.i_vehicles, 0) i_vehicles,
                   COALESCE(fs.i_dwell_total_s, 0) i_dwell_total_s, fs.i_dwell_p50_s,
                   COALESCE(fs.i_violations, 0) i_violations, fs.dt_last""",
        sorts={"visits": "COALESCE(fs.i_visits, 0)", "name": "f.s_site_name", "area": "f.d_area_sqm",
               "alerts": "COALESCE(fs.i_violations, 0)", "dwell": "COALESCE(fs.i_dwell_total_s, 0)",
               "radius": "f.d_inradius_m"},
        default_sort="visits",
        groups={"state": ("f.s_state", "COALESCE(f.s_state, 'not placed')"),
                "district": ("f.s_district", "f.s_district"),
                "type": ("f.s_type", "f.s_type"), "category": ("f.s_category", "f.s_category"),
                "scale": ("CASE WHEN f.d_area_sqm < 10000 THEN 'micro' WHEN f.d_area_sqm < 1000000 THEN 'site' "
                          "WHEN f.d_area_sqm < 100000000 THEN 'campus' ELSE 'regional' END",
                          "CASE WHEN f.d_area_sqm < 10000 THEN 'micro' WHEN f.d_area_sqm < 1000000 THEN 'site' "
                          "WHEN f.d_area_sqm < 100000000 THEN 'campus' ELSE 'regional' END")},
        measures={"count": "COUNT(*)", "visits": "SUM(COALESCE(fs.i_visits, 0))",
                  "dwell": "SUM(COALESCE(fs.i_dwell_total_s, 0))",
                  "alerts": "SUM(COALESCE(fs.i_violations, 0))"},
        presets={"active": "f.b_active = 1",
                 "visited": "f.b_active = 1 AND COALESCE(fs.i_visits, 0) > 0",
                 "unvisited": "f.b_active = 1 AND COALESCE(fs.i_visits, 0) = 0",
                 "small": "f.b_active = 1 AND f.d_inradius_m < 25",
                 "cross_border": "f.s_states IS NOT NULL",
                 "offshore": "f.d_state_offset_m IS NOT NULL",
                 "placed": "f.s_state IS NOT NULL",
                 "facility_stats": "fs.s_scale IN ('micro','site','campus')",
                 "alerts": "COALESCE(fs.i_violations, 0) > 0"},
        direct={"state": "f.s_state", "category": "f.s_category", "type": "f.s_type"},
        search=("f.s_site_name",),
    ),
    "fence_days": Dataset(
        title="Geofences by day",
        source="""geo_fence_day fd
                  JOIN geo_fence_stats fs ON fs.i_run_id = fd.i_run_id AND fs.i_fence_id = fd.i_fence_id""",
        run_col="fd.i_run_id",
        select="""fd.i_fence_id, fd.i_site_id, fd.d_day, fs.s_site_name, fs.s_type, fs.s_category, fs.s_scale,
                  fd.i_entries, fd.i_exits, fd.i_vehicles, fd.i_dwell_s, fd.i_dwell_p50_s""",
        sorts={"vehicles": "fd.i_vehicles", "entries": "fd.i_entries", "dwell": "fd.i_dwell_s",
               "day": "fd.d_day", "name": "fs.s_site_name"},
        default_sort="vehicles",
        groups={"scale": ("fs.s_scale", "fs.s_scale"), "category": ("fs.s_category", "fs.s_category"),
                "type": ("fs.s_type", "fs.s_type"), "day": ("fd.d_day", "fd.d_day"),
                "site": ("fd.i_site_id", "fs.s_site_name")},
        measures={"count": "COUNT(*)", "sites": "COUNT(DISTINCT fd.i_site_id)",
                  "entries": "SUM(fd.i_entries)", "dwell": "SUM(fd.i_dwell_s)"},
        presets={"facility": "fs.s_scale IN ('micro','site','campus')",
                 "present": "fd.i_dwell_s > 0"},
        time_start="fd.d_day", direct={"site_id": "fd.i_site_id"},
        search=("fs.s_site_name",),
    ),
    "route_trips": Dataset(
        title="Journeys against their plan",
        source="geo_trip_route r LEFT JOIN geo_trip_meta m ON m.i_trip_no = r.i_trip_no",
        run_col="r.i_run_id",
        select="""r.i_trip_no, r.s_asset_id, r.s_trans_name, r.s_from_site, r.s_to_site, r.s_mode, r.s_status,
                  r.dt_transit_from, r.dt_transit_to, r.d_planned_km, r.d_actual_km, r.d_extra_km, r.d_extra_pct,
                  r.i_planned_transit_s, r.i_actual_transit_s, r.i_extra_s, r.d_offroute_km, r.i_deviations,
                  r.i_detours, r.i_offroute_stops, r.i_reroutes, r.d_max_offset_m, r.d_adherence_pct,
                  r.d_cost_plan, r.d_cost_actual, r.d_variance, r.d_variance_km, r.d_variance_time,
                  r.d_detention_h, r.d_detention_cost, r.d_margin, r.s_verdict""",
        sorts={"variance": "r.d_variance", "extra": "r.d_extra_km", "planned": "r.d_planned_km",
               "actual": "r.d_actual_km", "deviations": "r.i_deviations", "offroute": "r.d_offroute_km",
               "adherence": "r.d_adherence_pct", "start": "r.dt_transit_from", "detention": "r.d_detention_h",
               "trip": "r.i_trip_no"},
        default_sort="variance",
        groups={"verdict": ("r.s_verdict", "r.s_verdict"),
                "transporter": (f"COALESCE(r.s_trans_name, '{UNKNOWN}')", f"COALESCE(r.s_trans_name, '{UNKNOWN}')"),
                "vehicle": ("r.s_asset_id", "r.s_asset_id"),
                "lane": ("CONCAT(r.i_from_site, '>', r.i_to_site)",
                         "CONCAT(COALESCE(r.s_from_site,'?'), ' → ', COALESCE(r.s_to_site,'?'))"),
                "from_site": ("r.i_from_site", "r.s_from_site"), "to_site": ("r.i_to_site", "r.s_to_site"),
                "day": ("DATE(r.dt_transit_from)", "DATE(r.dt_transit_from)"), "mode": ("r.s_mode", "r.s_mode")},
        measures={"count": "COUNT(*)", "planned_km": "ROUND(SUM(r.d_planned_km), 1)",
                  "km": "ROUND(SUM(r.d_actual_km), 1)", "extra_km": "ROUND(SUM(r.d_extra_km), 1)",
                  "offroute_km": "ROUND(SUM(r.d_offroute_km), 1)", "deviations": "SUM(r.i_deviations)",
                  "reroutes": "SUM(r.i_reroutes)", "variance": "ROUND(SUM(r.d_variance))",
                  "inr": "ROUND(SUM(r.d_variance))", "detention_h": "ROUND(SUM(r.d_detention_h), 1)",
                  "detention_inr": "ROUND(SUM(r.d_detention_cost))"},
        presets={"ok": "r.s_status = 'ok'", "loss": "r.s_status = 'ok' AND r.s_verdict = 'loss'",
                 "saving": "r.s_status = 'ok' AND r.s_verdict = 'saving'",
                 "on_plan": "r.s_status = 'ok' AND r.s_verdict = 'on_plan'",
                 "deviated": "r.s_status = 'ok' AND r.i_deviations > 0",
                 "rerouted": "r.i_reroutes > 0", "detained": "r.d_detention_h > 0",
                 "local": "r.s_status = 'local'", "short_history": "r.s_status = 'short_history'"},
        time_start="r.dt_transit_from", trip_col="r.i_trip_no",
        direct={"vehicle": "r.s_asset_id", "transporter": "r.s_trans_name", "verdict": "r.s_verdict",
                "from_site": "r.i_from_site", "to_site": "r.i_to_site"},
        search=("r.s_asset_id", "r.s_trans_name", "r.s_from_site", "r.s_to_site"),
    ),
    "route_deviations": Dataset(
        title="Deviations from the plan",
        source="""geo_route_deviation d JOIN geo_trip_route r ON r.i_run_id = d.i_run_id AND r.i_trip_no = d.i_trip_no""",
        run_col="d.i_run_id",
        select="""d.id, d.i_trip_no, d.i_seq, d.s_kind, d.dt_leave, d.dt_back, d.d_leave_lat, d.d_leave_long,
                  d.d_actual_km, d.d_planned_km, d.d_extra_km, d.i_duration_s, d.i_stop_s, d.d_max_offset_m,
                  d.b_silent, d.b_reroute, d.d_new_route_km, r.s_asset_id, r.s_trans_name, r.s_from_site,
                  r.s_to_site""",
        sorts={"start": "d.dt_leave", "duration": "d.i_duration_s", "extra": "d.d_extra_km",
               "offset": "d.d_max_offset_m"},
        default_sort="extra",
        groups={"kind": ("d.s_kind", "d.s_kind"),
                "transporter": (f"COALESCE(r.s_trans_name, '{UNKNOWN}')", f"COALESCE(r.s_trans_name, '{UNKNOWN}')"),
                "vehicle": ("r.s_asset_id", "r.s_asset_id"),
                "lane": ("CONCAT(r.i_from_site, '>', r.i_to_site)",
                         "CONCAT(COALESCE(r.s_from_site,'?'), ' → ', COALESCE(r.s_to_site,'?'))"),
                "hour": ("HOUR(d.dt_leave)", "HOUR(d.dt_leave)"), "day": ("DATE(d.dt_leave)", "DATE(d.dt_leave)")},
        measures={"count": "COUNT(*)", "extra_km": "ROUND(SUM(d.d_extra_km), 1)",
                  "km": "ROUND(SUM(d.d_actual_km), 1)", "duration": "SUM(d.i_duration_s)"},
        presets={"detour": "d.s_kind = 'detour'", "shortcut": "d.s_kind = 'shortcut'",
                 "off_route_stop": "d.s_kind = 'off_route_stop'", "reroute": "d.b_reroute = 1",
                 "backtrack": "d.s_kind = 'backtrack'", "alternate_route": "d.s_kind = 'alternate_route'"},
        time_start="d.dt_leave", trip_col="d.i_trip_no",
        direct={"vehicle": "r.s_asset_id", "transporter": "r.s_trans_name", "kind": "d.s_kind",
                "verdict": "r.s_verdict", "from_site": "r.i_from_site", "to_site": "r.i_to_site"},
        search=("r.s_asset_id", "r.s_trans_name"),
    ),
    "tolls": Dataset(
        title="National Highway toll plazas",
        source="geo_toll_plaza t", run_col=None,
        select="""t.i_plaza_id, t.s_netc_code, t.s_name, t.s_state, t.s_district, t.s_nh, t.s_section,
                  t.s_piu, t.d_as_of""",
        sorts={"name": "t.s_name", "state": "t.s_state"}, default_sort="state",
        groups={"state": ("t.s_state", "t.s_state"), "nh": ("t.s_nh", "t.s_nh"),
                "district": ("t.s_district", "t.s_district")},
        measures={"count": "COUNT(*)"},
        direct={"state": "t.s_state"}, search=("t.s_name", "t.s_nh", "t.s_district"),
    ),
}


def _where(ds: Dataset, ctx: Ctx, rid: int, preset: str | None, longest: int = 0) -> tuple[list[str], list]:
    where: list[str] = []
    params: list = []
    if ds.trips_dataset:
        where, params = trip_where(ctx, rid)
    else:
        if ds.run_col:
            where.append(f"{ds.run_col} = %s")
            params.append(rid)
        if ctx.via == "trips" and ds.trip_col:
            sub, sp = trip_subquery(ctx, rid)
            where.append(f"{ds.trip_col} IN ({sub})")
            params += sp
        else:
            for name, col in ds.direct.items():
                val = getattr(ctx, name)
                if val is None:
                    continue
                _eq(col, val, where, params, UNKNOWN if name == "transporter" else None)
            a, b = ctx.window()
            if ds.time_start and (a or b):
                if ctx.tmode == "overlap" and ds.time_end:
                    # Covering some of the window: what the day summary means
                    # by a site being visited on a day.
                    if b:
                        where.append(f"{ds.time_start} < %s")
                        params.append(b)
                    if a:
                        where.append(f"{ds.time_end} > %s")
                        params.append(a)
                        if ds.run_col == "v.i_run_id":
                            where.append(f"{ds.time_start} >= %s")
                            params.append(a - timedelta(seconds=longest))
                    where.append(f"{ds.time_end} > {ds.time_start}")
                else:
                    if a:
                        where.append(f"{ds.time_start} >= %s")
                        params.append(a)
                    if b:
                        where.append(f"{ds.time_start} < %s")
                        params.append(b)
            if ctx.q and ds.search:
                where.append("(" + " OR ".join(f"{c} LIKE %s" for c in ds.search) + ")")
                params += [f"%{ctx.q}%"] * len(ds.search)
    if ctx.min_minutes and "i_duration_s" in ds.select:
        where.append("s.i_duration_s >= %s")
        params.append(ctx.min_minutes * 60)
    for p in (preset or "").split(","):
        p = p.strip()
        if not p:
            continue
        if p not in ds.presets:
            raise HTTPException(400, f"unknown preset {p!r}; one of {sorted(ds.presets)}")
        where.append(f"({ds.presets[p]})")
    return where or ["1=1"], params


def _key(value):
    if value is None:
        return NULL_KEY
    if isinstance(value, (datetime,)):
        return value.isoformat(sep=" ")
    return str(value)


# Distributions a dropdown can ask for: percentiles and a histogram of one column.
STATS = {("visits", "dwell"): "v.i_dwell_seconds", ("stops", "duration"): "s.i_duration_s",
         ("gaps", "duration"): "g.i_gap_s", ("route_deviations", "duration"): "d.i_duration_s"}


def _sql_drill(cur, name: str, ds: Dataset, ctx: Ctx, rid: int, preset, group, gk, measure,
               sort, order, page, page_size, stat: str | None = None) -> dict:
    longest = longest_stay_s(cur, rid) if (ctx.tmode == "overlap" and ds.time_end) else 0
    where, params = _where(ds, ctx, rid, preset, longest)
    source = ds.source.replace("{rid}", str(int(rid)))
    if measure not in ds.measures:
        raise HTTPException(400, f"unknown measure {measure!r}; one of {sorted(ds.measures)}")
    m_sql = ds.measures[measure]

    # The headline, before narrowing to one group.
    cur.execute(f"SELECT COUNT(*) n, {m_sql} v FROM {source} WHERE {' AND '.join(where)}", params)
    head = clean(cur.fetchone())
    base_where, base_params = " AND ".join(where), list(params)

    groups, groups_total = [], None
    if group:
        if group not in ds.groups:
            raise HTTPException(400, f"unknown group {group!r}; one of {sorted(ds.groups)}")
        kx, lx = ds.groups[group]
        if group in TEXT_GROUPS:
            kx = f"({kx}) COLLATE utf8mb4_bin"
        cur.execute(f"""SELECT {kx} k, ANY_VALUE({lx}) label, COUNT(*) n, {m_sql} v
                          FROM {source} WHERE {' AND '.join(where)}
                         GROUP BY k ORDER BY ABS(v) DESC, n DESC LIMIT 12""" if "AVG(" not in lx else
                    f"""SELECT {kx} k, {lx} label, COUNT(*) n, {m_sql} v
                          FROM {source} WHERE {' AND '.join(where)}
                         GROUP BY k ORDER BY ABS(v) DESC, n DESC LIMIT 12""", params)
        total_v = float(head["v"] or 0)
        for r in cur.fetchall():
            r = clean(r)
            groups.append({"key": _key(r["k"]), "label": r["label"] if r["label"] is not None else "—",
                           "n": r["n"], "value": r["v"],
                           "share": round(100 * float(r["v"] or 0) / total_v, 1) if total_v else None})
        cur.execute(f"SELECT COUNT(DISTINCT {kx}) + MAX({kx} IS NULL) n FROM {source} WHERE {' AND '.join(where)}",
                    params)
        groups_total = int(cur.fetchone()["n"] or 0)
        if gk is not None:
            if gk == NULL_KEY:
                where.append(f"{kx} IS NULL")
            else:
                where.append(f"{kx} = %s")
                params.append(gk)

    stats = None
    if stat:
        col = STATS.get((name, stat))
        if col is None:
            raise HTTPException(400, f"no {stat!r} distribution for {name}")
        # The distribution of the whole selection, before narrowing to a group.
        cur.execute(f"SELECT {col} x FROM {source} WHERE {base_where}", base_params)
        values = [float(r["x"]) for r in cur.fetchall() if r["x"] is not None]
        stats = {"n": len(values), "p10": percentile(values, 10), "p50": percentile(values, 50),
                 "p90": percentile(values, 90), "min": min(values) if values else None,
                 "max": max(values) if values else None, "histogram": _histogram(values)}

    cur.execute(f"SELECT COUNT(*) n FROM {source} WHERE {' AND '.join(where)}", params)
    total = int(cur.fetchone()["n"] or 0)
    col = ds.sorts.get(sort or "", ds.sorts[ds.default_sort])
    direction = "ASC" if (order or "").lower() == "asc" else "DESC"
    cur.execute(f"""SELECT {ds.select} FROM {source} WHERE {' AND '.join(where)}
                     ORDER BY {col} IS NULL, {col} {direction} LIMIT %s OFFSET %s""",
                (*params, page_size, (page - 1) * page_size))
    items = rows(cur)
    return {"dataset": name, "title": ds.title, "run_id": rid, "measure": measure,
            "value": head["v"], "rows_total": head["n"], "group_by": group, "groups": groups,
            "groups_total": groups_total, "total": total, "page": page, "page_size": page_size,
            "pages": max(1, -(-total // page_size)), "items": items, "stats": stats,
            "sorts": list(ds.sorts), "group_options": list(ds.groups), "presets": list(ds.presets)}


# ---------------------------------------------------------------------------
# datasets computed in Python: where the tile's number is a fold, not a sum
# ---------------------------------------------------------------------------

def _page_python(items: list[dict], sort_key, order: str, page: int, page_size: int) -> tuple[list, int]:
    present = [r for r in items if r.get(sort_key) is not None]
    missing = [r for r in items if r.get(sort_key) is None]
    present.sort(key=lambda r: r[sort_key], reverse=(order != "asc"))
    ordered = present + missing
    start = (page - 1) * page_size
    return ordered[start:start + page_size], len(ordered)


def _groups_python(items: list[dict], key, label, value, gk):
    agg: dict = {}
    for r in items:
        k = _key(key(r))
        g = agg.setdefault(k, {"key": k, "label": label(r) if label(r) is not None else "—", "n": 0, "value": 0})
        g["n"] += 1
        g["value"] += value(r) or 0
    total_v = sum(g["value"] for g in agg.values())
    out = sorted(agg.values(), key=lambda g: (-abs(g["value"]), -g["n"]))[:12]
    for g in out:
        g["share"] = round(100 * g["value"] / total_v, 1) if total_v else None
        if isinstance(g["value"], float):
            g["value"] = round(g["value"], 1)
    chosen = [r for r in items if _key(key(r)) == gk] if gk is not None else items
    return out, len(agg), chosen


def _python_response(name, title, rid, items, measure, value_of, group_fns, group, gk, sort_key,
                     order, page, page_size, extra=None):
    value = sum(value_of(r) or 0 for r in items)
    groups, groups_total, chosen = [], None, items
    if group:
        if group not in group_fns:
            raise HTTPException(400, f"unknown group {group!r}; one of {sorted(group_fns)}")
        key, label = group_fns[group]
        groups, groups_total, chosen = _groups_python(items, key, label, value_of, gk)
    page_items, total = _page_python(chosen, sort_key, order, page, page_size)
    return {"dataset": name, "title": title, "run_id": rid, "measure": measure,
            "value": round(value, 1) if isinstance(value, float) else value, "rows_total": len(items),
            "group_by": group, "groups": groups, "groups_total": groups_total, "total": total,
            "page": page, "page_size": page_size, "pages": max(1, -(-total // page_size)),
            "items": [clean(r) for r in page_items], "group_options": list(group_fns), **(extra or {})}


def _trip_set(cur, ctx: Ctx, rid: int) -> list[dict]:
    where, params = trip_where(ctx, rid)
    cur.execute(f"""SELECT s.i_trip_no, COALESCE(m.s_asset_id, s.s_asset_id) asset, s.s_asset_id raw_asset,
                           m.s_trans_name, m.s_driver_name, m.s_origin, m.s_destination, s.dt_first_ping,
                           s.dt_last_ping, s.i_places, s.s_first_site, s.i_first_site_id, s.dt_first_enter,
                           s.dt_first_exit, s.s_last_site, s.i_last_site_id, s.dt_last_enter, s.dt_last_exit,
                           s.i_transit_s, m.dt_trip_start, sh.s_sibling_trips
                      FROM {TRIP_FROM} WHERE {' AND '.join(where)}""", params)
    return list(cur.fetchall())


def _places_drill(cur, ctx: Ctx, rid: int, group, gk, sort, order, page, page_size) -> dict:
    """Time at facilities: each vehicle's facility-scale stays unioned, so a
    truck in a mill inside a works is at one place (physical.facility_places).

    For a day, clipped to the day -- the day summary's hours at facilities.
    For trips (via=trips), the places those trips own -- the entity pages'
    time at facilities, which sums geo_trip_share.
    """
    cols = """v.s_asset_id, v.i_trip_no, v.s_trips, v.i_site_id, v.s_site_name, v.s_scale, v.dt_enter,
              v.dt_exit, v.b_open, v.i_dwell_seconds, v.s_trans_name, v.s_driver_name"""
    window = None
    owned: set[int] | None = None
    if ctx.via == "trips":
        trips = _trip_set(cur, ctx, rid)
        owned = {t["i_trip_no"] for t in trips}
        vehicles = sorted({t["raw_asset"] for t in trips if t["raw_asset"]})
        no_asset = sorted(t["i_trip_no"] for t in trips if not t["raw_asset"])
        if not trips:
            pv = []
        else:
            a = min(t["dt_first_ping"] for t in trips if t["dt_first_ping"]) - timedelta(days=1)
            b = max(t["dt_last_ping"] for t in trips if t["dt_last_ping"]) + timedelta(days=1)
            pv = []
            for i in range(0, len(vehicles), 500):
                chunk = vehicles[i:i + 500]
                cur.execute(f"""SELECT {cols} FROM geo_pvisit v
                                 WHERE v.i_run_id=%s AND v.s_scale IN {FAC} AND v.s_asset_id IN ({','.join(['%s'] * len(chunk))})
                                   AND v.dt_enter < %s
                                   AND COALESCE(v.dt_exit, v.dt_enter + INTERVAL v.i_dwell_seconds SECOND) >= %s""",
                            (rid, *chunk, b, a))
                pv += list(cur.fetchall())
            if no_asset:
                cur.execute(f"""SELECT {cols} FROM geo_pvisit v WHERE v.i_run_id=%s AND v.s_scale IN {FAC}
                                   AND v.s_asset_id IS NULL AND v.i_trip_no IN ({','.join(['%s'] * len(no_asset))})""",
                            (rid, *no_asset))
                pv += list(cur.fetchall())
    else:
        a, b = ctx.window()
        if not (a and b):
            raise HTTPException(400, "facility time needs a day, a date range, or via=trips")
        window = (a, b)
        where = ["v.i_run_id=%s", f"v.s_scale IN {FAC}", "v.dt_enter < %s", "v.dt_enter >= %s",
                 "COALESCE(v.dt_exit, v.dt_enter + INTERVAL v.i_dwell_seconds SECOND) >= %s"]
        params = [rid, b, a - timedelta(seconds=longest_stay_s(cur, rid, facility=True)), a]
        for name, col in (("vehicle", "v.s_asset_id"), ("transporter", "v.s_trans_name"),
                          ("driver", "v.s_driver_name")):
            if getattr(ctx, name) is not None:
                _eq(col, getattr(ctx, name), where, params, UNKNOWN if name == "transporter" else None)
        cur.execute(f"SELECT {cols} FROM geo_pvisit v WHERE {' AND '.join(where)}", params)
        pv = list(cur.fetchall())

    # The same union as physical.facility_places, keeping each place's
    # members so it can be named by its outermost fence.
    union_rows = []
    for p in pv:
        trips = sorted(int(x) for x in (p["s_trips"] or str(p["i_trip_no"])).split(",")
                       if x.strip().isdigit()) or [p["i_trip_no"]]
        union_rows.append({"s_asset_id": p["s_asset_id"], "i_trip_no": trips[0], "trips": trips,
                           "start": p["dt_enter"], "end": p["dt_exit"] or p["dt_enter"] + timedelta(
                               seconds=p["i_dwell_seconds"] or 0), "p": p})
    rank = {"campus": 3, "site": 2, "micro": 1}
    items = []
    for cluster in P.merge_intervals(union_rows, "start", "end"):
        start = min(r["start"] for r in cluster)
        end = max(r["end"] for r in cluster)
        trips = sorted({t for r in cluster for t in r["trips"]})
        if owned is not None and trips[0] not in owned:
            continue
        outer = max((r["p"] for r in cluster),
                    key=lambda p: (rank.get(p["s_scale"], 0), p["i_dwell_seconds"] or 0))
        dwell = int((end - start).total_seconds())
        secs = dwell
        if window:
            cs, ce = max(start, window[0]), min(end, window[1])
            secs = int((ce - cs).total_seconds()) if ce > cs else 0
            if secs <= 0:
                continue
        items.append({"s_asset_id": cluster[0]["s_asset_id"], "i_trip_no": trips[0],
                      "s_trips": ",".join(str(t) for t in trips), "i_trips": len(trips),
                      "i_site_id": outer["i_site_id"], "s_site_name": outer["s_site_name"],
                      "dt_start": start, "dt_end": end,
                      "b_open": 1 if any(r["p"]["b_open"] for r in cluster) else 0,
                      "i_dwell_s": dwell, "i_seconds": secs, "s_trans_name": outer["s_trans_name"]})
    groups = {"vehicle": (lambda r: r["s_asset_id"], lambda r: r["s_asset_id"]),
              "site": (lambda r: r["i_site_id"], lambda r: r["s_site_name"]),
              "transporter": (lambda r: r["s_trans_name"] or UNKNOWN, lambda r: r["s_trans_name"] or UNKNOWN),
              "hour": (lambda r: r["dt_start"].hour, lambda r: f"{r['dt_start'].hour:02d}:00")}
    sort_key = {"start": "dt_start", "duration": "i_seconds"}.get(sort or "", "i_seconds")
    return _python_response("places", "Time at facilities", rid, items, "seconds", lambda r: r["i_seconds"],
                            groups, group, gk, sort_key, order, page, page_size)


def _moving_gaps_drill(cur, ctx: Ctx, rid: int, group, gk, sort, order, page, page_size) -> dict:
    """Stretches the truck moved while its tracker was silent, each once
    however many consignment trips carried it (physical.count_physical)."""
    a, b = ctx.window()
    where, params = ["g.i_run_id=%s", "g.s_kind='moving'"], [rid]
    if ctx.via == "trips":
        sub, sp = trip_subquery(ctx, rid)
        where.append(f"g.i_trip_no IN ({sub})")
        params += sp
        a = b = None
    else:
        if a:
            where.append("g.dt_to >= %s")
            params.append(a - timedelta(days=2))
        if b:
            where.append("g.dt_from < %s")
            params.append(b + timedelta(days=2))
        if ctx.vehicle:
            where.append("g.s_asset_id = %s")
            params.append(ctx.vehicle)
    cur.execute(f"""SELECT g.i_trip_no, g.s_asset_id, g.dt_from, g.dt_to, g.i_gap_s, g.d_straight_m, g.s_route,
                           g.d_route_m, g.d_from_lat, g.d_from_long, m.s_trans_name
                      FROM geo_gap g LEFT JOIN geo_trip_meta m ON m.i_trip_no = g.i_trip_no
                     WHERE {' AND '.join(where)}""", params)
    gaps = list(cur.fetchall())
    items = []
    for g in P.count_physical(gaps, "dt_from", "dt_to"):
        if a and g["dt_from"] < a:
            continue
        if b and g["dt_from"] >= b:
            continue
        items.append({**g, "d_straight_km": round(float(g["d_straight_m"] or 0) / 1000, 2)})
    groups = {"vehicle": (lambda r: r["s_asset_id"], lambda r: r["s_asset_id"]),
              "transporter": (lambda r: r["s_trans_name"] or UNKNOWN, lambda r: r["s_trans_name"] or UNKNOWN),
              "hour": (lambda r: r["dt_from"].hour, lambda r: f"{r['dt_from'].hour:02d}:00")}
    sort_key = {"start": "dt_from", "duration": "i_gap_s", "moved": "d_straight_m"}.get(sort or "", "i_gap_s")
    return _python_response("moving_gaps", "Moved while the GPS was silent", rid, items, "count",
                            lambda r: 1, groups, group, gk, sort_key, order, page, page_size)


LEGS = {
    "origin": "Stay at the loading place",
    "transit": "Transit, first place exit to last place arrival",
    "dest": "Stay at the destination",
    "gateout": "Left the first place after the gate-out stamp",
}


def _secs(a, b):
    return int((a - b).total_seconds()) if a and b else None


def _legs_drill(cur, ctx: Ctx, rid: int, group, gk, sort, order, page, page_size) -> dict:
    """The samples behind a median: one per physical departure or arrival,
    as the entity pages take them (entities._once), and their distribution."""
    metric = ctx.metric or "origin"
    if metric not in LEGS:
        raise HTTPException(400, f"metric is one of {sorted(LEGS)}")
    trips = _trip_set(cur, ctx, rid)
    best: dict = {}
    for t in trips:
        if metric == "origin":
            ok = t["dt_first_exit"] and t["dt_first_enter"] and t["i_places"]
            key = (t["dt_first_exit"],)
            val = _secs(t["dt_first_exit"], t["dt_first_enter"]) if ok else None
            site, sid, start, end = t["s_first_site"], t["i_first_site_id"], t["dt_first_enter"], t["dt_first_exit"]
        elif metric == "transit":
            ok = bool(t["i_transit_s"])
            key = (t["dt_first_exit"], t["dt_last_enter"])
            val = t["i_transit_s"] if ok else None
            site, sid = f"{t['s_first_site'] or '?'} → {t['s_last_site'] or '?'}", t["i_last_site_id"]
            start, end = t["dt_first_exit"], t["dt_last_enter"]
        elif metric == "dest":
            ok = t["dt_last_exit"] and t["dt_last_enter"]
            key = (t["dt_last_enter"],)
            val = _secs(t["dt_last_exit"], t["dt_last_enter"]) if ok else None
            site, sid, start, end = t["s_last_site"], t["i_last_site_id"], t["dt_last_enter"], t["dt_last_exit"]
        else:
            ok = t["dt_first_exit"] and t["dt_trip_start"]
            key = ("gateout", t["i_trip_no"])        # per consignment: each has its own stamp
            val = _secs(t["dt_first_exit"], t["dt_trip_start"]) if ok else None
            site, sid, start, end = t["s_first_site"], t["i_first_site_id"], t["dt_trip_start"], t["dt_first_exit"]
        if val is None:
            continue
        k = (t["asset"] or f"trip:{t['i_trip_no']}", *key)
        row = {"s_asset_id": t["asset"], "i_trip_no": t["i_trip_no"], "s_trans_name": t["s_trans_name"],
               "s_driver_name": t["s_driver_name"], "s_site_name": site, "i_site_id": sid,
               "lane": f"{t['s_origin'] or '?'} → {t['s_destination'] or '?'}",
               "dt_start": start, "dt_end": end, "i_seconds": val, "trips": {t["i_trip_no"]}}
        if k in best:
            best[k]["trips"].add(t["i_trip_no"])
            if val > best[k]["i_seconds"]:
                row["trips"] = best[k]["trips"]
                best[k] = row
        else:
            best[k] = row
    items = []
    for r in best.values():
        ts = sorted(r.pop("trips"))
        items.append({**r, "i_trip_no": ts[0], "s_trips": ",".join(map(str, ts)), "i_trips": len(ts)})
    values = [r["i_seconds"] for r in items]
    stats = {"n": len(values), "p10": percentile(values, 10), "p50": percentile(values, 50),
             "p90": percentile(values, 90), "min": min(values) if values else None,
             "max": max(values) if values else None, "histogram": _histogram(values)}
    groups = {"vehicle": (lambda r: r["s_asset_id"], lambda r: r["s_asset_id"]),
              "transporter": (lambda r: r["s_trans_name"] or UNKNOWN, lambda r: r["s_trans_name"] or UNKNOWN),
              "site": (lambda r: r["s_site_name"], lambda r: r["s_site_name"]),
              "lane": (lambda r: r["lane"], lambda r: r["lane"])}
    sort_key = {"start": "dt_start", "duration": "i_seconds"}.get(sort or "", "i_seconds")
    out = _python_response("legs", LEGS[metric], rid, items, "count", lambda r: 1, groups, group, gk,
                           sort_key, order, page, page_size, {"stats": stats, "metric": metric})
    return out


def _histogram(values: list[float], bins: int = 12) -> list[dict]:
    """Hour-scaled buckets up to the 95th percentile, and the rest in one."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return []
    hi = v[min(len(v) - 1, int(0.95 * (len(v) - 1)))]
    lo = min(0, v[0])
    if hi <= lo:
        return [{"from": lo, "to": hi, "n": len(v)}]
    step = (hi - lo) / bins
    # Round the bucket to a readable duration.
    for nice in (60, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800):
        if step <= nice:
            step = nice
            break
    else:
        step = math.ceil(step / 86400) * 86400
    out = []
    edge = math.floor(lo / step) * step
    while edge <= hi and len(out) < 40:
        out.append({"from": edge, "to": edge + step, "n": 0})
        edge += step
    over = {"from": edge, "to": None, "n": 0}
    for x in v:
        i = int((x - out[0]["from"]) // step)
        if 0 <= i < len(out):
            out[i]["n"] += 1
        else:
            over["n"] += 1
    if over["n"]:
        out.append(over)
    return out


# ---------------------------------------------------------------------------
# the endpoint
# ---------------------------------------------------------------------------

PYTHON = {"places": _places_drill, "moving_gaps": _moving_gaps_drill, "legs": _legs_drill}


@router.get("/drill/{dataset}")
def drill(dataset: str, request: Request,
          preset: str | None = None, group: str | None = None,
          gk: str | None = Query(None, description="restrict the records to one group's key"),
          measure: str = "count", sort: str | None = None, order: str = "desc",
          stats: str | None = Query(None, description="a distribution of the selection: dwell, duration"),
          page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=200),
          conn=Depends(get_geo_db)):
    """Records behind a KPI, with a breakdown. Context comes from the page:
    day, from, to, vehicle, transporter, driver, origin, destination, site_id,
    trip, status, quality, kind, state, category, type, q, min_minutes; and
    via=trips to count what a set of trips owns."""
    ctx = _ctx(request)
    with conn.cursor() as cur:
        rid = resolve_run(cur, ctx.run)
        if dataset in PYTHON:
            return PYTHON[dataset](cur, ctx, rid, group, gk, sort, order, page, page_size)
        ds = DATASETS.get(dataset)
        if ds is None:
            raise HTTPException(404, f"no dataset {dataset!r}; one of {sorted(DATASETS) + sorted(PYTHON)}")
        return _sql_drill(cur, dataset, ds, ctx, rid, preset, group, gk, measure, sort, order, page, page_size,
                          stats)


@router.get("/drill")
def drill_catalogue():
    """What can be drilled into, and how."""
    out = {name: {"title": ds.title, "groups": list(ds.groups), "measures": list(ds.measures),
                  "presets": list(ds.presets), "sorts": list(ds.sorts)} for name, ds in DATASETS.items()}
    out["places"] = {"title": "Time at facilities", "groups": ["vehicle", "site", "transporter", "hour"]}
    out["moving_gaps"] = {"title": "Moved while the GPS was silent", "groups": ["vehicle", "transporter", "hour"]}
    out["legs"] = {"title": "Loading, transit and destination times", "metrics": LEGS,
                   "groups": ["vehicle", "transporter", "site", "lane"]}
    return out
