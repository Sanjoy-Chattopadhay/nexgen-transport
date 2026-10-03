"""TTA Master Reporting analytics engine.

Ports the TTA_Analysis dashboard computations (KPIs, trends, scorecards,
lanes, geo, heatmaps, correlation, distributions, box plots, fleet,
outliers, funnel) onto the smart-truck MySQL TTA schema
(tta_trips + tta_trip_metrics).

Data volume is modest, so per request we load the joined trip frame into
pandas (with a short TTL cache) and aggregate there — same trade-off as the
reference implementation, and it keeps every figure's logic in one place.
"""
import logging
import time

import numpy as np
import pandas as pd

from nexgen.shared.circlefence.gps_quality import wilson_interval
from nexgen.shared.analysis.tta_geo import (
    ORIGIN,
    STATE_CENTROIDS,
    resolve,
    state_from_pin,
)

logger = logging.getLogger(__name__)

DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

NUMERIC_METRICS = [
    "transit_hours", "planned_transit_hours", "detention_hours", "run_hours",
    "stop_hours", "plant_vivo_hours", "delivery_delta_hours", "dispatch_lead_hours",
    "distance_km", "avg_speed_kmph", "speed_violations", "gps_uptime",
    "works_detention_hours", "geofence_tail_hours", "origin_total_hours",
]
GROUP_FIELDS = ["transporter", "destination", "vehicle_category", "own_market",
                "consignor", "consignee", "ship_to_site", "device_type", "origin",
                "dest_state"]

# UI/chatbot-friendly aliases -> canonical df column. Lets the Trip Analysis
# board send by=customer / by=ship-to-site without knowing our column names.
GROUP_ALIASES = {
    "customer": "consignee",
    "ship_to": "ship_to_site", "shipto": "ship_to_site",
    "ship-to-site": "ship_to_site", "ship_to_party": "ship_to_site",
    "ship-to": "ship_to_site",
}


def resolve_group_field(by: str) -> str:
    """Map a UI/alias group name to its canonical df column (customer -> consignee)."""
    return GROUP_ALIASES.get((by or "").strip().lower(), by)

SPEED_CAP_KMPH = 110

_LOAD_SQL = """
SELECT t.i_trip_no                 AS trip_id,
       t.i_cnr_id                  AS cnr_id,
       t.s_trip_class              AS trip_class,
       t.s_trans_name              AS transporter,
       t.s_asset_id                AS vehicle_no,
       t.s_asset_type              AS vehicle_type,
       t.s_cnr_name                AS consignor,
       t.s_cne_name                AS consignee,
       t.s_org_node_name           AS origin,
       t.s_dest_node_name          AS destination,
       t.s_driver_name             AS driver_name,
       t.c_trip_status             AS trip_status,
       t.s_close_reason            AS trip_closed_reason,
       t.dt_booking                AS booking_dt,
       t.dt_trip_start             AS dept_dt,
       t.dt_trip_eta               AS eta_dt,
       t.dt_trip_ata               AS ata_dt,
       t.dt_trip_end               AS closing_dt,
       t.i_works_detention_min     AS works_detention_min,
       t.i_geofence_tail_min       AS geofence_tail_min,
       t.i_origin_total_min        AS origin_total_min,
       t.s_geofence_out_status     AS geofence_out_status,
       m.dt_ata_out                AS ata_out_dt,
       m.dt_delivery               AS delivery_date,
       m.s_delivery_status         AS delivery_status,
       m.i_delivery_delta_min      AS delivery_delta_min,
       m.i_transit_time_min        AS transit_min,
       m.i_detention_min           AS detention_min,
       m.i_moving_time_min         AS moving_min,
       m.i_stoppage_time_min       AS stoppage_min,
       m.i_plant_vivo_min          AS plant_vivo_min,
       m.d_distance_travelled_km   AS distance_km,
       m.i_speed_violation         AS speed_violations,
       m.d_uptime_pct              AS gps_uptime,
       m.s_service_provider        AS own_market,
       m.s_tag                     AS device_type,
       m.s_asset_make              AS asset_make,
       m.i_cne_pin                 AS pin_code
FROM tta_trips t
LEFT JOIN tta_trip_metrics m ON m.i_trip_no = t.i_trip_no
"""

# ----------------------------------------------------------------------
# Load + feature engineering (mirrors the TTA_Analysis ETL derivations)
# ----------------------------------------------------------------------

_CACHE: dict = {"df": None, "ts": 0.0}
_CACHE_TTL_S = 60


def invalidate_cache():
    _CACHE["df"] = None
    _CACHE["ts"] = 0.0


def _vehicle_category(vt) -> str:
    s = str(vt or "").upper().strip()
    if not s or s == "NONE":
        return "UNSPECIFIED"
    if "TRAILER" in s:
        return "TRAILER"
    if any(k in s for k in ("LCV", "PICKUP", "PICK UP", "407", "ACE", "LIGHT", "MINI")):
        return "LIGHT VEHICLE"
    if any(k in s for k in ("TRUCK", "HYVA", "TIPPER", "TANKER", "HEAVY", "LPT",
                            "10 W", "12 W", "14 W", "16 W", "6 W", "TAURUS")):
        return "HEAVY VEHICLE"
    return "SPEC/OTHER"


def _own_market(v) -> object:
    s = str(v or "").strip()
    if not s or s.lower() == "none":
        return None
    low = s.lower()
    if "own" in low:
        return "Own"
    if "market" in low:
        return "Market"
    return s.title()


# The feed only labels ownership on the zonal lane; the local lane sends nothing
# at all. Dropping those rows — which is what a NaN does to every groupby here —
# silently reshapes the split: a carrier running 164 unlabelled local trips and
# 56 labelled market ones reads as "100% market". This column keeps them in the
# picture under their own name, so an ownership mix always sums to the carrier's
# real trip count and the gap is visible rather than inferred away.
NOT_STATED = "Not stated"


def _own_market_stated(v) -> str:
    # NaN is truthy, so test for missing explicitly rather than with `or`
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return NOT_STATED
    s = str(v).strip()
    return s or NOT_STATED


# Names arrive spelled differently on the two ETL lanes — the zonal feed sends
# them upper-cased, the local feed title-cased. MySQL's default collation folds
# case, so the database counts them as one party; pandas does not, so the same
# carrier arrives as two rows and its trips are split between them.
# "UTILITY TRANSPORT COMPANY" (17 trips) and "Utility Transport Company" (19)
# were one carrier reading as two 18-trip strangers — small enough each to fall
# under the ranking bar, so it vanished from the league table entirely.
# Consignees fare worse: Tata Steel arrives four ways, so "customers served"
# over-counts.
#
# So fold on a case- and space-insensitive key and adopt the spelling that
# appears most often — the majority spelling is the one people will recognise,
# and picking it (rather than upper-casing everything) keeps the display name
# one the feed actually uses.
NAME_COLS = ["transporter", "consignee", "consignor", "destination", "origin",
             "vehicle_type", "device_type", "asset_make"]


def _canonical_names(df: pd.DataFrame, cols=NAME_COLS) -> pd.DataFrame:
    for col in cols:
        if col not in df.columns:
            continue
        vals = df[col].dropna().astype(str).str.strip()
        if vals.empty:
            continue
        key = vals.str.casefold().str.replace(r"\s+", " ", regex=True)
        # most frequent spelling per folded key; ties break alphabetically so
        # the mapping is stable between runs
        counts = (pd.DataFrame({"key": key, "name": vals})
                  .value_counts(["key", "name"]).rename("n").reset_index()
                  .sort_values(["key", "n", "name"], ascending=[True, False, True]))
        canon = counts.drop_duplicates("key").set_index("key")["name"]
        mapped = key.map(canon)
        df.loc[mapped.index, col] = mapped
    return df


def load_df(conn) -> pd.DataFrame:
    """Joined trips+metrics frame with all derived analytics columns."""
    now = time.time()
    if _CACHE["df"] is not None and now - _CACHE["ts"] < _CACHE_TTL_S:
        return _CACHE["df"]

    # the app's pymysql connection uses DictCursor, which pd.read_sql
    # mishandles — fetch dict rows and build the frame directly
    with conn.cursor() as cur:
        cur.execute(_LOAD_SQL)
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description]
    df = pd.DataFrame(rows, columns=colnames) if rows else pd.DataFrame(columns=colnames)

    date_cols = ["booking_dt", "dept_dt", "eta_dt", "ata_dt", "closing_dt",
                 "ata_out_dt", "delivery_date"]
    for c in date_cols:
        df[c] = pd.to_datetime(df[c], errors="coerce")

    # durations: provider minutes -> hours (transit 0 => force-closed, NULL it)
    df["transit_hours"] = pd.to_numeric(df["transit_min"], errors="coerce") / 60.0
    df.loc[df["transit_hours"] <= 0, "transit_hours"] = np.nan
    df["detention_hours"] = pd.to_numeric(df["detention_min"], errors="coerce") / 60.0
    df["run_hours"] = pd.to_numeric(df["moving_min"], errors="coerce") / 60.0
    df["stop_hours"] = pd.to_numeric(df["stoppage_min"], errors="coerce") / 60.0
    df["plant_vivo_hours"] = pd.to_numeric(df["plant_vivo_min"], errors="coerce") / 60.0
    df["delivery_delta_hours"] = pd.to_numeric(df["delivery_delta_min"], errors="coerce") / 60.0

    # Origin hold measured against the plant geofence rather than the gate
    # stamp. `works_detention` is what the TMS declares (plant entry -> gate
    # out); `geofence_tail` is the time still inside the 10 km fence AFTER that
    # stamp, which nobody was billing for; `origin_total` is the two together
    # and exists only for trips whose exit the GPS trail actually confirmed.
    for _src, _dst in (("works_detention_min", "works_detention_hours"),
                       ("geofence_tail_min", "geofence_tail_hours"),
                       ("origin_total_min", "origin_total_hours")):
        df[_dst] = pd.to_numeric(df[_src], errors="coerce") / 60.0
        df.loc[df[_dst] < 0, _dst] = np.nan

    # planned transit / dispatch lead
    df["planned_transit_hours"] = (df["eta_dt"] - df["dept_dt"]).dt.total_seconds() / 3600.0
    df.loc[df["planned_transit_hours"] <= 0, "planned_transit_hours"] = np.nan
    df["dispatch_lead_hours"] = (df["dept_dt"] - df["booking_dt"]).dt.total_seconds() / 3600.0
    df.loc[df["dispatch_lead_hours"] < 0, "dispatch_lead_hours"] = np.nan

    # numeric coercion
    df["distance_km"] = pd.to_numeric(df["distance_km"], errors="coerce")
    df["speed_violations"] = pd.to_numeric(df["speed_violations"], errors="coerce").fillna(0).astype(int)
    df["gps_uptime"] = pd.to_numeric(df["gps_uptime"], errors="coerce")

    # avg speed = distance / moving hours, kept only in a sane band
    with np.errstate(divide="ignore", invalid="ignore"):
        spd = df["distance_km"] / df["run_hours"]
    spd[(spd <= 1) | (spd > SPEED_CAP_KMPH)] = np.nan
    df["avg_speed_kmph"] = spd

    # on-time flag from delivery status (fallback: delivery delta sign)
    status = df["delivery_status"].astype(str).str.lower()
    df["is_on_time"] = np.where(
        status.str.contains("on time"), 1.0,
        np.where(status.str.contains("delay"), 0.0, np.nan))
    no_status = df["is_on_time"].isna() & df["delivery_delta_hours"].notna()
    df.loc[no_status, "is_on_time"] = (df.loc[no_status, "delivery_delta_hours"] <= 0).astype(float)

    # fold the spelling variants before anything groups or joins on a name
    df = _canonical_names(df)

    # categorical derivations
    df["vehicle_category"] = df["vehicle_type"].map(_vehicle_category)
    df["own_market"] = df["own_market"].map(_own_market)
    df["own_market_stated"] = df["own_market"].map(_own_market_stated)
    df["lane"] = df["origin"].fillna("?") + " → " + df["destination"].fillna("?")

    # Ship-to site = a customer's specific delivery location. The TTA feed has no
    # native ship-to-party code, so we key it on consignee + destination
    # ("<customer> @ <city>"). Requires a consignee — a site without a customer
    # isn't meaningful, so it stays null when consignee is missing.
    df["ship_to_site"] = df["consignee"].fillna("?") + " @ " + df["destination"].fillna("?")
    df.loc[df["consignee"].isna(), "ship_to_site"] = np.nan

    # calendar keys
    d = df["dept_dt"]
    df["dept_date"] = d.dt.strftime("%Y-%m-%d")
    df["dept_hour"] = d.dt.hour
    df["dept_dow"] = d.dt.dayofweek  # 0=Mon
    df["dept_month"] = d.dt.strftime("%Y-%m")
    iso = d.dt.isocalendar()
    df["dept_week"] = iso["year"].astype("string") + "-W" + iso["week"].astype("string").str.zfill(2)
    for c in ("dept_date", "dept_month", "dept_week"):
        df.loc[d.isna(), c] = np.nan

    # offline geocoding of destinations (exact city, then pin-prefix centroid)
    uniq = df[["destination", "pin_code"]].drop_duplicates()
    coords = {(r.destination, r.pin_code): resolve(r.destination, r.pin_code)
              for r in uniq.itertuples()}
    latlon = df.apply(lambda r: coords.get((r["destination"], r["pin_code"]), (None, None)), axis=1)
    df["dest_lat"] = [p[0] for p in latlon]
    df["dest_lon"] = [p[1] for p in latlon]

    # Destination STATE, from the consignee PIN rather than the node name.
    # 126 destination names spread over a country map are unreadable as bubbles;
    # the unit a planner reasons about is the state. PIN blocks are allocated
    # along state lines and, unlike a node name, a PIN cannot be spelled three
    # different ways -- see tta_geo.PIN3_STATE_RANGES.
    df["dest_state"] = df["pin_code"].map(state_from_pin)

    df = df.drop(columns=["transit_min", "detention_min", "moving_min",
                          "stoppage_min", "plant_vivo_min", "delivery_delta_min",
                          "works_detention_min", "geofence_tail_min", "origin_total_min"])

    _CACHE["df"] = df
    _CACHE["ts"] = now
    return df


# ----------------------------------------------------------------------
# Filters
# ----------------------------------------------------------------------

def apply_filters(df: pd.DataFrame, f: dict) -> pd.DataFrame:
    # Consignor scope is enforced by id (authoritative) — robust against
    # consignor-name collisions/renames, unlike the name-based `consignors`
    # filter below which serves the dashboard's own filter bar.
    if f.get("cnr_id") is not None:
        df = df[df["cnr_id"] == f["cnr_id"]]
    # Upstream classification (zonal / local). Absent = both, which is the
    # pre-existing behaviour of every one of these endpoints.
    if f.get("trip_class"):
        df = df[df["trip_class"] == f["trip_class"]]
    if f.get("date_from"):
        df = df[df["dept_dt"] >= pd.Timestamp(f["date_from"])]
    if f.get("date_to"):
        df = df[df["dept_dt"] < pd.Timestamp(f["date_to"]) + pd.Timedelta(days=1)]
    for param, col in [("transporters", "transporter"), ("destinations", "destination"),
                       ("vehicle_categories", "vehicle_category"), ("own_market", "own_market"),
                       ("consignors", "consignor"),
                       # Additional exact-match list filters (partner Trip Analysis UI).
                       ("consignees", "consignee"), ("vehicles", "vehicle_no"),
                       ("drivers", "driver_name"), ("device_types", "device_type"),
                       ("asset_makes", "asset_make")]:
        vals = f.get(param)
        if vals:
            df = df[df[col].isin(vals)]
    return df


def clean_records(df: pd.DataFrame, digits: int = 2) -> list[dict]:
    df = df.copy()
    for c in df.select_dtypes(include=["float"]).columns:
        df[c] = df[c].round(digits)
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d %H:%M")
    df = df.replace([np.inf, -np.inf], np.nan)
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


def _otd(s: pd.Series):
    s = s.dropna()
    return round(100 * float(s.mean()), 1) if len(s) else None


def _rnd(v, d=1):
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return round(float(v), d)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# KPIs
# ----------------------------------------------------------------------

def _kpi_block(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    delayed = df.loc[df["is_on_time"] == 0, "delivery_delta_hours"].dropna()
    return {
        "trips": int(len(df)),
        "transporters": int(df["transporter"].nunique()),
        "vehicles": int(df["vehicle_no"].nunique()),
        "destinations": int(df["destination"].nunique()),
        "total_km": _rnd(df["distance_km"].sum(skipna=True), 0),
        "avg_km_per_trip": _rnd(df["distance_km"].mean(skipna=True)),
        "otd_pct": _otd(df["is_on_time"]),
        "avg_transit_hours": _rnd(df["transit_hours"].mean(skipna=True)),
        "median_transit_hours": _rnd(df["transit_hours"].median(skipna=True)),
        "avg_delay_when_late_hours": _rnd(delayed.mean()) if len(delayed) else None,
        "avg_detention_hours": _rnd(df["detention_hours"].mean(skipna=True)),
        "avg_plant_vivo_hours": _rnd(df["plant_vivo_hours"].mean(skipna=True)),
        "avg_dispatch_lead_hours": _rnd(df["dispatch_lead_hours"].mean(skipna=True)),
        "speed_violations": int(df["speed_violations"].sum()),
        "avg_violations_per_trip": _rnd(df["speed_violations"].mean()),
        "avg_gps_uptime": _rnd(df["gps_uptime"].mean(skipna=True)),
        "avg_speed_kmph": _rnd(df["avg_speed_kmph"].mean(skipna=True)),
        "market_share_pct": _rnd(100 * (df["own_market"] == "Market").mean()),
    }


def kpis(conn, f: dict) -> dict:
    df = apply_filters(load_df(conn), f)
    cur = _kpi_block(df)
    prev = {}
    if not df.empty and df["dept_dt"].notna().any():
        start, end = df["dept_dt"].min(), df["dept_dt"].max()
        span = max(end - start, pd.Timedelta(days=1))
        full = apply_filters(load_df(conn), {**f, "date_from": None, "date_to": None})
        prev_df = full[(full["dept_dt"] >= start - span) & (full["dept_dt"] < start)]
        prev = _kpi_block(prev_df)
    deltas = {}
    for k, v in cur.items():
        pv = prev.get(k)
        if isinstance(v, (int, float)) and isinstance(pv, (int, float)) and pv:
            deltas[k] = round(100 * (v - pv) / abs(pv), 1)
    return {"current": cur, "previous": prev, "delta_pct": deltas}


# ----------------------------------------------------------------------
# Time series / grouping / lanes
# ----------------------------------------------------------------------

def timeseries(conn, f: dict, granularity: str = "D") -> list[dict]:
    df = apply_filters(load_df(conn), f)
    if df.empty:
        return []
    key = {"D": "dept_date", "W": "dept_week", "M": "dept_month"}.get(granularity, "dept_date")
    g = df.groupby(key).agg(
        trips=("trip_id", "count"),
        otd_pct=("is_on_time", _otd),
        avg_transit_hours=("transit_hours", "mean"),
        avg_detention_hours=("detention_hours", "mean"),
        total_km=("distance_km", "sum"),
        speed_violations=("speed_violations", "sum"),
        avg_dispatch_lead_hours=("dispatch_lead_hours", "mean"),
    ).reset_index().rename(columns={key: "period"}).sort_values("period")
    return clean_records(g)


def _add_otd_interval(g: pd.DataFrame) -> pd.DataFrame:
    """Give each rolled-up row the numbers an on-time rate needs to be judged.

    `otd_pct` alone reads the same for 3 of 4 trips and for 300 of 400. The
    Wilson interval separates them, and `judged_trips` / `late_trips` say how
    many trips the rate is actually over (trips without a delivery status are
    not judged). State, city (destination node) and every `group` cut share
    this so the three views of high-volume freight can never disagree about how
    much a rate can be trusted.

    `_otd` returns None for a row whose trips all lack a delivery status, which
    pandas stores as NaN -- and NaN is truthy -- so it is tested explicitly.
    """
    judged = g["judged_trips"].astype(int)
    on_time = [0 if pd.isna(o) else int(round(float(o) / 100 * j))
               for o, j in zip(g["otd_pct"], judged)]
    g["late_trips"] = [j - k for j, k in zip(judged, on_time)]
    ci = [wilson_interval(k, j) for k, j in zip(on_time, judged)]
    g["otd_ci_low"] = [c[0] for c in ci]
    g["otd_ci_high"] = [c[1] for c in ci]
    return g


def group_summary(conn, f: dict, by: str, min_trips: int = 1) -> list[dict]:
    df = apply_filters(load_df(conn), f)
    return _group_summary_df(df, resolve_group_field(by), min_trips)


def _group_summary_df(df: pd.DataFrame, by: str, min_trips: int = 1) -> list[dict]:
    if df.empty or by not in df.columns:
        return []
    sub = df.dropna(subset=[by])
    if sub.empty:
        return []
    g = sub.groupby(by).agg(
        trips=("trip_id", "count"),
        otd_pct=("is_on_time", _otd),
        judged_trips=("is_on_time", "count"),
        avg_transit_hours=("transit_hours", "mean"),
        median_transit_hours=("transit_hours", "median"),
        avg_planned_transit_hours=("planned_transit_hours", "mean"),
        avg_detention_hours=("detention_hours", "mean"),
        avg_distance_km=("distance_km", "mean"),
        total_km=("distance_km", "sum"),
        avg_speed_kmph=("avg_speed_kmph", "mean"),
        speed_violations=("speed_violations", "sum"),
        avg_gps_uptime=("gps_uptime", "mean"),
        vehicles=("vehicle_no", "nunique"),
        destinations=("destination", "nunique"),
    ).reset_index()
    g = g[g["trips"] >= min_trips].copy()
    g = _add_otd_interval(g)
    if by == "destination":
        # City = destination node. Its state comes from the consignee PIN (the
        # node name is spelled several ways); the modal PIN state wins where a
        # node's trips carry more than one.
        st = (sub.dropna(subset=["dest_state"]).groupby(by)["dest_state"]
              .agg(lambda x: x.mode().iloc[0]))
        g["state"] = g[by].map(st)
    g["violations_per_trip"] = (g["speed_violations"] / g["trips"]).round(1)
    g["share_pct"] = (100 * g["trips"] / len(df)).round(1)
    g["schedule_variance_hours"] = (g["avg_transit_hours"] - g["avg_planned_transit_hours"]).round(1)
    g = g.rename(columns={by: "name"})
    return clean_records(g.sort_values("trips", ascending=False))


# ----------------------------------------------------------------------
# Geo
# ----------------------------------------------------------------------

def geo_points(conn, f: dict) -> dict:
    df = apply_filters(load_df(conn), f)
    if df.empty:
        return {"origin": ORIGIN, "points": [], "unmapped": [], "mapped_pct": 0}
    mapped = df.dropna(subset=["dest_lat", "dest_lon"])
    pts = []
    if not mapped.empty:
        g = mapped.groupby(["destination", "dest_lat", "dest_lon"]).agg(
            trips=("trip_id", "count"),
            otd_pct=("is_on_time", _otd),
            judged_trips=("is_on_time", "count"),
            avg_transit_hours=("transit_hours", "mean"),
            avg_distance_km=("distance_km", "mean"),
            total_km=("distance_km", "sum"),
        ).reset_index()
        pts = clean_records(_add_otd_interval(g))
    unmapped = (df[df["dest_lat"].isna()].groupby("destination").size()
                .sort_values(ascending=False))
    return {
        "origin": ORIGIN,
        "points": pts,
        "unmapped": [{"destination": k, "trips": int(v)} for k, v in unmapped.items()],
        "mapped_pct": round(100 * len(mapped) / len(df), 1) if len(df) else 0,
    }



def geo_states(conn, f: dict) -> dict:
    """Destination volume and service rolled up to STATE.

    Why this exists next to `geo_points`: at destination grain the map is 126
    overlapping circles, most of them one or two trips, and neither "where does
    the freight go" nor "where is service bad" is legible on it. State is the
    grain those two questions are actually asked at, and it is also the grain
    they can be answered at with confidence — a state aggregates enough trips
    for an on-time rate to mean something, where a single destination often
    does not.

    Returns one row per state carrying BOTH bubble metrics (volume and on-time)
    plus the ranked table behind them, so the same call drives the map and the
    bar charts beside it and the two can never disagree.
    """
    df = apply_filters(load_df(conn), f)
    if df.empty:
        return {"origin": ORIGIN, "states": [], "unmapped": [], "mapped_pct": 0,
                "totals": {}}

    mapped = df.dropna(subset=["dest_state"])
    g = mapped.groupby("dest_state").agg(
        trips=("trip_id", "count"),
        otd_pct=("is_on_time", _otd),
        judged_trips=("is_on_time", "count"),
        avg_transit_hours=("transit_hours", "mean"),
        median_transit_hours=("transit_hours", "median"),
        avg_detention_hours=("detention_hours", "mean"),
        avg_distance_km=("distance_km", "mean"),
        total_km=("distance_km", "sum"),
        avg_speed_kmph=("avg_speed_kmph", "mean"),
        speed_violations=("speed_violations", "sum"),
        avg_gps_uptime=("gps_uptime", "mean"),
        destinations=("destination", "nunique"),
        transporters=("transporter", "nunique"),
        vehicles=("vehicle_no", "nunique"),
        consignees=("consignee", "nunique"),
    ).reset_index().rename(columns={"dest_state": "state"})

    g["share_pct"] = (100 * g["trips"] / len(mapped)).round(1)
    g["violations_per_trip"] = (g["speed_violations"] / g["trips"]).round(1)
    for c in ("avg_transit_hours", "median_transit_hours", "avg_detention_hours",
              "avg_distance_km", "avg_speed_kmph", "avg_gps_uptime"):
        g[c] = g[c].round(1)
    g["total_km"] = g["total_km"].round(0)

    # An on-time rate over 4 trips is not a finding; the interval is what the
    # map uses to grey out states it cannot speak about.
    g = _add_otd_interval(g)

    coords = g["state"].map(lambda s: STATE_CENTROIDS.get(s, (None, None)))
    g["lat"] = [c[0] for c in coords]
    g["lon"] = [c[1] for c in coords]

    unmapped = (df[df["dest_state"].isna()]
                .groupby("destination").size().sort_values(ascending=False))

    return {
        "origin": ORIGIN,
        "states": clean_records(g.sort_values("trips", ascending=False)),
        # Destinations whose PIN did not resolve to a state. Listed rather than
        # silently dropped: an unmapped destination is missing from every bubble
        # on the map, and the reader needs to know how much is missing.
        "unmapped": [{"destination": k, "trips": int(v)} for k, v in unmapped.items()],
        "mapped_pct": round(100 * len(mapped) / len(df), 1),
        "totals": {
            "states": int(g["state"].nunique()),
            "trips": int(len(df)),
            "mapped_trips": int(len(mapped)),
            "otd_pct": _otd(mapped["is_on_time"]),
            "top_state": g.sort_values("trips", ascending=False)["state"].iloc[0]
                         if len(g) else None,
            # Concentration of demand: how much of the freight the three biggest
            # states hold. High numbers make a state-level service problem a
            # network-level problem.
            "top3_share_pct": _rnd(g["share_pct"].nlargest(3).sum(), 1),
        },
    }


# ----------------------------------------------------------------------
# Heatmaps / correlation
# ----------------------------------------------------------------------

def heatmap_dow_hour(conn, f: dict) -> dict:
    df = apply_filters(load_df(conn), f)
    sub = df.dropna(subset=["dept_dow", "dept_hour"])
    if sub.empty:
        return {"rows": DOW_NAMES, "cols": list(range(24)),
                "values": [[0] * 24 for _ in range(7)]}
    pivot = pd.crosstab(sub["dept_dow"], sub["dept_hour"]).reindex(
        index=range(7), columns=range(24), fill_value=0)
    return {"rows": DOW_NAMES, "cols": list(range(24)),
            "values": pivot.fillna(0).astype(int).values.tolist()}


def heatmap_pivot(conn, f: dict, rows: str, cols: str = "dept_month",
                  metric: str = "otd_pct", top: int = 12) -> dict:
    df = apply_filters(load_df(conn), f)
    if df.empty or rows not in df.columns or cols not in df.columns:
        return {"rows": [], "cols": [], "values": []}
    sub = df.dropna(subset=[rows, cols])
    if sub.empty:
        return {"rows": [], "cols": [], "values": []}
    top_rows = sub[rows].value_counts().head(top).index.tolist()
    sub = sub[sub[rows].isin(top_rows)]
    if metric == "trips":
        pivot = pd.crosstab(sub[rows], sub[cols])
    elif metric == "otd_pct":
        pivot = sub.pivot_table(index=rows, columns=cols, values="is_on_time", aggfunc="mean") * 100
    else:
        if metric not in NUMERIC_METRICS:
            return {"rows": [], "cols": [], "values": []}
        pivot = sub.pivot_table(index=rows, columns=cols, values=metric, aggfunc="mean")
    pivot = pivot.reindex(top_rows).round(1)
    # NaN cells must serialize as JSON null. Cast to object FIRST: on a float64
    # frame `.where(..., None)` silently coerces None back to NaN, which then
    # breaks response encoding (Starlette's JSONResponse uses allow_nan=False),
    # 500-ing any metric whose pivot has gaps. Same idiom as records() (L230).
    vals = pivot.astype(object).where(pd.notna(pivot), None).values.tolist()
    return {"rows": pivot.index.astype(str).tolist(),
            "cols": pivot.columns.astype(str).tolist(), "values": vals}


def correlation(conn, f: dict) -> dict:
    df = apply_filters(load_df(conn), f)
    cols = [c for c in NUMERIC_METRICS if c in df.columns and df[c].notna().sum() > 10]
    if len(cols) < 2:
        return {"labels": [], "values": []}
    corr = df[cols].corr().round(2)
    # Cast to object before None-substitution so NaN correlations (a column with
    # zero variance yields NaN) serialize as null, not a JSON-invalid NaN.
    vals = corr.astype(object).where(pd.notna(corr), None).values.tolist()
    return {"labels": cols, "values": vals}


# ----------------------------------------------------------------------
# Distributions / box plots / outliers
# ----------------------------------------------------------------------

def distribution(conn, f: dict, metric: str, bins: int = 40) -> dict:
    df = apply_filters(load_df(conn), f)
    if metric not in NUMERIC_METRICS or metric not in df.columns:
        return {"stats": {}, "histogram": [], "ecdf": []}
    s = df[metric].dropna().astype(float)
    if not len(s):
        return {"stats": {}, "histogram": [], "ecdf": []}
    stats = {
        "count": int(len(s)), "mean": _rnd(s.mean(), 2), "median": _rnd(s.median(), 2),
        "std": _rnd(s.std(), 2), "min": _rnd(s.min(), 2), "max": _rnd(s.max(), 2),
        "p10": _rnd(s.quantile(0.10), 2), "p90": _rnd(s.quantile(0.90), 2),
        "p95": _rnd(s.quantile(0.95), 2), "skew": _rnd(s.skew(), 2) if len(s) > 2 else None,
    }
    # histogram bins
    counts, edges = np.histogram(s, bins=min(bins, max(int(len(s) ** 0.5) + 1, 5)))
    histogram = [{"bin_start": _rnd(edges[i], 2), "bin_end": _rnd(edges[i + 1], 2),
                  "mid": _rnd((edges[i] + edges[i + 1]) / 2, 2), "count": int(c)}
                 for i, c in enumerate(counts)]
    # ECDF, downsampled to <= 300 points
    sv = np.sort(s.values)
    n = len(sv)
    idx = np.unique(np.linspace(0, n - 1, min(n, 300)).astype(int))
    ecdf = [{"value": _rnd(sv[i], 2), "pct": _rnd(100 * (i + 1) / n)} for i in idx]
    return {"stats": stats, "histogram": histogram, "ecdf": ecdf}


def boxplot(conn, f: dict, group_by: str, metric: str, top: int = 10) -> list[dict]:
    df = apply_filters(load_df(conn), f)
    if group_by not in GROUP_FIELDS or metric not in NUMERIC_METRICS:
        return []
    sub = df[[group_by, metric]].dropna()
    if sub.empty:
        return []
    top_groups = sub[group_by].value_counts().head(top).index.tolist()
    out = []
    for grp in top_groups:
        v = sub.loc[sub[group_by] == grp, metric].astype(float)
        q1, med, q3 = v.quantile(0.25), v.quantile(0.5), v.quantile(0.75)
        iqr = q3 - q1
        lo = float(v[v >= q1 - 1.5 * iqr].min())
        hi = float(v[v <= q3 + 1.5 * iqr].max())
        fliers = v[(v < lo) | (v > hi)]
        out.append({
            "group": str(grp), "count": int(len(v)),
            "min": _rnd(v.min(), 2), "max": _rnd(v.max(), 2),
            "q1": _rnd(q1, 2), "median": _rnd(med, 2), "q3": _rnd(q3, 2),
            "whisker_lo": _rnd(lo, 2), "whisker_hi": _rnd(hi, 2),
            "mean": _rnd(v.mean(), 2),
            "outliers": [_rnd(x, 2) for x in fliers.sample(min(len(fliers), 25), random_state=7)],
        })
    return out


def outliers(conn, f: dict, z_threshold: float = 3.0, min_lane_trips: int = 8) -> list[dict]:
    df = apply_filters(load_df(conn), f)
    sub = df.dropna(subset=["transit_hours", "destination"]).copy()
    if sub.empty:
        return []
    grp = sub.groupby("destination")["transit_hours"]
    counts, mean, std = grp.transform("count"), grp.transform("mean"), grp.transform("std")
    sub["lane_mean_transit"] = mean.round(1)
    sub["z_score"] = ((sub["transit_hours"] - mean) / std.replace(0, np.nan)).round(2)
    flagged = sub[(counts >= min_lane_trips) & (sub["z_score"].abs() >= z_threshold)]
    cols = ["trip_id", "dept_dt", "transporter", "vehicle_no", "destination", "distance_km",
            "transit_hours", "lane_mean_transit", "z_score", "delivery_status", "driver_name"]
    flagged = flagged[cols].sort_values("z_score", key=lambda s: s.abs(), ascending=False).head(200)
    return clean_records(flagged)


# ----------------------------------------------------------------------
# Fleet / funnel / meta / records
# ----------------------------------------------------------------------

def fleet(conn, f: dict) -> dict:
    df = apply_filters(load_df(conn), f)
    out = {
        "vehicle_category": _group_summary_df(df, "vehicle_category"),
        "own_market": _group_summary_df(df, "own_market"),
        "device_type": _group_summary_df(df, "device_type"),
        "asset_make": _group_summary_df(df.dropna(subset=["asset_make"]), "asset_make"),
        "top_violating_vehicles": [],
        "low_gps_vehicles": [],
    }
    if df.empty:
        return out
    veh = df.dropna(subset=["vehicle_no"]).groupby("vehicle_no").agg(
        trips=("trip_id", "count"), violations=("speed_violations", "sum"),
        avg_gps_uptime=("gps_uptime", "mean"), total_km=("distance_km", "sum"),
        transporter=("transporter", "first"),
    ).reset_index()
    out["top_violating_vehicles"] = clean_records(
        veh.sort_values("violations", ascending=False).head(15))
    low_gps = veh[veh["avg_gps_uptime"].notna() & (veh["avg_gps_uptime"] < 80) & (veh["trips"] >= 2)]
    out["low_gps_vehicles"] = clean_records(low_gps.sort_values("avg_gps_uptime").head(15))
    return out


def funnel(conn, f: dict) -> list[dict]:
    df = apply_filters(load_df(conn), f)
    stages = [
        ("Booked", int(df["booking_dt"].notna().sum())),
        ("Departed plant", int(df["dept_dt"].notna().sum())),
        ("Arrived at destination", int(df["ata_dt"].notna().sum())),
        ("Unloaded (gate-out)", int(df["ata_out_dt"].notna().sum())),
        ("Trip closed", int(df["closing_dt"].notna().sum())),
        ("Delivery status recorded", int(df["delivery_status"].notna().sum())),
    ]
    return [{"stage": s, "count": c} for s, c in stages]


def meta(conn, f: dict | None = None) -> dict:
    # Apply the (consignor-)scoped filter before deriving option lists, so a
    # scoped caller never sees another consignor's transporters/destinations/
    # names in the filter dropdowns.
    df = apply_filters(load_df(conn), f) if f else load_df(conn)
    if df.empty:
        return {"rows": 0, "date_min": None, "date_max": None, "transporters": [],
                "destinations": [], "vehicle_categories": [], "consignors": [],
                "own_market": []}
    return {
        "rows": int(len(df)),
        "date_min": str(df["dept_dt"].min().date()) if df["dept_dt"].notna().any() else None,
        "date_max": str(df["dept_dt"].max().date()) if df["dept_dt"].notna().any() else None,
        "transporters": sorted(df["transporter"].dropna().unique().tolist()),
        "destinations": df["destination"].dropna().value_counts().index.tolist(),
        "vehicle_categories": sorted(df["vehicle_category"].dropna().unique().tolist()),
        "consignors": sorted(df["consignor"].dropna().unique().tolist()),
        "own_market": sorted(df["own_market"].dropna().unique().tolist()),
    }


RECORD_COLS = [
    "trip_id", "dept_dt", "transporter", "vehicle_no", "vehicle_category",
    "own_market", "consignor", "consignee", "origin", "destination", "driver_name",
    "eta_dt", "ata_dt", "closing_dt", "delivery_status", "transit_hours",
    "planned_transit_hours", "detention_hours", "distance_km", "avg_speed_kmph",
    "speed_violations", "gps_uptime", "trip_status", "trip_closed_reason",
]


def records(conn, f: dict, limit: int = 500) -> dict:
    df = apply_filters(load_df(conn), f)
    limit = min(max(limit, 1), 5000)
    total = int(len(df))
    sub = df.sort_values("dept_dt", ascending=False).head(limit)
    return {"total": total, "returned": int(len(sub)),
            "rows": clean_records(sub[RECORD_COLS])}
