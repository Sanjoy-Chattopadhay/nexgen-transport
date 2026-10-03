"""Transporter (carrier) analytics — the drill-down behind /transporters.

The TTA analytics engine already loads a fully derived trip frame
(`tta_dashboard.load_df`: transit / detention / OTD / GPS uptime / violations /
own-market / vehicle category / calendar keys), so this module aggregates that
same frame rather than re-deriving anything in SQL. Two levels:

    list_transporters()   -> one scored row per carrier + fleet benchmark
    overview_analytics()  -> cross-carrier comparison (trends, spread, mix,
                             movers, concentration, lane dependency)
    transporter_detail()  -> one carrier's full profile (trend, lanes, fleet,
                             drivers, mix, spread, rhythm, risk, insights)

Everything honours the shared dashboard filters (date window + consignor scope)
built by `backend/app/api/tta_dashboard.dashboard_filters`.
"""
import logging

import numpy as np
import pandas as pd
from fastapi import HTTPException

from nexgen.services.analytics.lib import tta_dashboard as tta
from nexgen.shared.circlefence.gps_quality import wilson_interval
from nexgen.services.analytics.lib.tta_dashboard import (
    DOW_NAMES,
    _otd,
    _rnd,
    apply_filters,
    clean_records,
    load_df,
)

logger = logging.getLogger(__name__)

# Composite scorecard: (column, lower_is_better, weight).
# Ranks are percentile ranks across carriers, so the score is robust to the
# wildly different units (hours vs % vs counts) and to outliers.
SCORE_WEIGHTS = [
    ("otd_pct", False, 0.40),
    ("schedule_variance_hours", True, 0.15),
    ("avg_detention_hours", True, 0.15),
    ("violations_per_trip", True, 0.15),
    ("avg_gps_uptime", False, 0.15),
]

GRADES = [(80, "A"), (65, "B"), (50, "C"), (35, "D")]

# A carrier needs this many trips before its score is treated as meaningful.
DEFAULT_MIN_TRIPS = 3


def _grade(score) -> str:
    # unqualified carriers carry NaN, not None — pd.isna covers both
    if score is None or pd.isna(score):
        return "—"
    for cutoff, letter in GRADES:
        if score >= cutoff:
            return letter
    return "E"


def _dt_str(v):
    return None if pd.isna(v) else pd.Timestamp(v).strftime("%Y-%m-%d")


# ----------------------------------------------------------------------
# Scorecard (all carriers)
# ----------------------------------------------------------------------

def _scorecard(df: pd.DataFrame, min_trips: int = DEFAULT_MIN_TRIPS) -> pd.DataFrame:
    """One row per transporter with KPIs, composite score, grade and rank."""
    sub = df.dropna(subset=["transporter"])
    if sub.empty:
        return pd.DataFrame()

    g = sub.groupby("transporter").agg(
        trips=("trip_id", "count"),
        otd_pct=("is_on_time", _otd),
        avg_transit_hours=("transit_hours", "mean"),
        median_transit_hours=("transit_hours", "median"),
        avg_planned_transit_hours=("planned_transit_hours", "mean"),
        avg_detention_hours=("detention_hours", "mean"),
        avg_plant_vivo_hours=("plant_vivo_hours", "mean"),
        avg_dispatch_lead_hours=("dispatch_lead_hours", "mean"),
        avg_distance_km=("distance_km", "mean"),
        total_km=("distance_km", "sum"),
        avg_speed_kmph=("avg_speed_kmph", "mean"),
        speed_violations=("speed_violations", "sum"),
        avg_gps_uptime=("gps_uptime", "mean"),
        vehicles=("vehicle_no", "nunique"),
        drivers=("driver_name", "nunique"),
        destinations=("destination", "nunique"),
        consignors=("consignor", "nunique"),
        first_trip=("dept_dt", "min"),
        last_trip=("dept_dt", "max"),
        active_days=("dept_date", "nunique"),
    ).reset_index().rename(columns={"transporter": "name"})

    # average slip on the trips that actually ran late
    late = sub[sub["is_on_time"] == 0].groupby("transporter")["delivery_delta_hours"].mean()
    g["avg_delay_when_late_hours"] = g["name"].map(late)

    # market-fleet reliance: share of this carrier's trips run on market trucks
    market = sub.assign(_m=(sub["own_market"] == "Market").astype(float)) \
                .groupby("transporter")["_m"].mean() * 100
    g["market_pct"] = g["name"].map(market)

    # Origin hold. Prefer the geofence-confirmed total where the GPS trail
    # proved the exit; fall back to the declared works detention otherwise, so
    # the column is populated for every carrier and the profile says which.
    for col, src in (("avg_works_detention_hours", "works_detention_hours"),
                     ("avg_geofence_tail_hours", "geofence_tail_hours"),
                     ("avg_origin_total_hours", "origin_total_hours")):
        g[col] = g["name"].map(sub.groupby("transporter")[src].mean()).round(1)
    g["origin_total_trips"] = g["name"].map(
        sub.dropna(subset=["origin_total_hours"]).groupby("transporter").size()).fillna(0).astype(int)
    g["grade_detention_hours"] = g["avg_origin_total_hours"].fillna(g["avg_works_detention_hours"])

    g["customers"] = g["name"].map(sub.groupby("transporter")["consignee"].nunique())
    g["states"] = (g["name"].map(sub.groupby("transporter")["dest_state"].nunique())
                   if "dest_state" in sub.columns else 0)
    g["lanes"] = g["name"].map(
        sub.dropna(subset=["destination"]).groupby("transporter")
           .apply(lambda d: d.groupby(["origin", "destination"]).ngroups, include_groups=False))

    # Own vs market as plain counts, stated without inference.
    g["own_trips"] = g["name"].map(
        sub.assign(_o=(sub["own_market"] == "Own")).groupby("transporter")["_o"].sum()).fillna(0).astype(int)
    g["market_trips"] = g["name"].map(
        sub.assign(_m=(sub["own_market"] == "Market")).groupby("transporter")["_m"].sum()).fillna(0).astype(int)
    g["fleet_unknown_trips"] = (g["trips"] - g["own_trips"] - g["market_trips"]).clip(lower=0)

    # The provider's speed-violation field counts over-limit PINGS, not events:
    # a trip with 15,149 pings carries 1,745 of them, and it scales with tracker
    # density rather than with driving. Kept for continuity, renamed in the API
    # docs, and deliberately NOT an input to the grade.
    g["provider_violation_pings_per_trip"] = (g["speed_violations"] / g["trips"]).round(1)
    g["violations_per_trip"] = g["provider_violation_pings_per_trip"]
    g["share_pct"] = (100 * g["trips"] / len(sub)).round(1)
    g["schedule_variance_hours"] = (g["avg_transit_hours"] - g["avg_planned_transit_hours"]).round(1)
    # Distance from the carrier's OWN quote, in either direction — being wildly
    # early is a planning failure too, so the grade uses the magnitude.
    g["abs_schedule_variance_hours"] = g["schedule_variance_hours"].abs()
    g["trips_per_vehicle"] = (g["trips"] / g["vehicles"].replace(0, np.nan)).round(1)
    g["market_pct"] = g["market_pct"].round(1)
    for c in ("avg_transit_hours", "median_transit_hours", "avg_planned_transit_hours",
              "avg_detention_hours", "avg_plant_vivo_hours", "avg_dispatch_lead_hours",
              "avg_distance_km", "avg_speed_kmph", "avg_gps_uptime",
              "avg_delay_when_late_hours"):
        g[c] = g[c].round(1)
    g["total_km"] = g["total_km"].round(0)

    # ---- composite score over the qualifying carriers only -------------
    qual = g["trips"] >= min_trips
    g["score"] = np.nan
    if qual.sum():
        q = g.loc[qual]
        num = pd.Series(0.0, index=q.index)
        den = pd.Series(0.0, index=q.index)
        for col, lower_better, w in SCORE_WEIGHTS:
            vals = q[col]
            if vals.notna().sum() < 2:
                continue  # nothing to rank against — leave the axis out
            pct = vals.rank(pct=True, ascending=not lower_better)
            pct = pct.fillna(0.5)  # a carrier missing the metric sits mid-pack
            num += pct * w
            den += w
        score = (100 * num / den.replace(0, np.nan)).round(1)
        g.loc[qual, "score"] = score
    g["grade"] = g["score"].map(_grade)
    g["qualified"] = qual

    g = g.sort_values(["score", "trips"], ascending=[False, False])
    g["rank"] = np.where(g["score"].notna(), g["score"].rank(ascending=False, method="min"), np.nan)

    for c in ("first_trip", "last_trip"):
        g[c] = g[c].map(_dt_str)
    return g.sort_values("trips", ascending=False)


def _fleet_benchmark(score_df: pd.DataFrame, df: pd.DataFrame) -> dict:
    """Median-carrier reference line every profile is compared against."""
    if score_df.empty:
        return {}
    q = score_df[score_df["qualified"]]
    ref = q if not q.empty else score_df
    med = lambda c: _rnd(ref[c].median(skipna=True), 1)  # noqa: E731
    return {
        "transporters": int(len(score_df)),
        "qualified": int(len(q)),
        "trips": int(len(df)),
        "otd_pct": _otd(df["is_on_time"]),
        "median_otd_pct": med("otd_pct"),
        "median_transit_hours": med("avg_transit_hours"),
        "median_schedule_variance_hours": med("schedule_variance_hours"),
        "median_detention_hours": med("avg_detention_hours"),
        "median_dispatch_lead_hours": med("avg_dispatch_lead_hours"),
        "median_violations_per_trip": med("violations_per_trip"),
        "median_gps_uptime": med("avg_gps_uptime"),
        "median_speed_kmph": med("avg_speed_kmph"),
        "total_km": _rnd(df["distance_km"].sum(skipna=True), 0),
        # concentration: how much freight the biggest carriers hold
        "top3_share_pct": _rnd(score_df["share_pct"].nlargest(3).sum(), 1),
        "hhi": _rnd(((score_df["share_pct"] / 100) ** 2).sum() * 10000, 0),
    }


def _grading_inputs(conn, f: dict) -> dict:
    """Safety and tracking numbers for the grade, keyed by carrier name.

    Both come from the modules that own those definitions rather than being
    recomputed here — the speed rollups for episodes, the geofence module for
    tracking quality — so a grade can never disagree with the page that shows
    the same figure. Either can be unavailable (rollups not built yet); the
    grade then drops that component and says so instead of scoring a zero.
    """
    out: dict[str, dict] = {}
    try:
        from nexgen.services.analytics.lib.speed_safety import carrier_safety
        for r in carrier_safety(conn, f).get("rows", []):
            e = out.setdefault(r["transporter"], {})
            e["episodes_per_100_trips"] = r.get("episodes_per_100_trips")
            # Road-only rate is what the grade uses — see the component's `why`.
            gps = r.get("gps_trips") or 0
            e["road_episodes_per_100_trips"] = (
                round(100 * (r.get("road_episodes") or 0) / gps, 1) if gps else None)
    except Exception as exc:
        logger.warning("Safety input unavailable for grading: %s", exc)
    try:
        from nexgen.shared.circlefence import gps_quality as gq
        for r in gq.transporter_scorecard(trip_class=None, min_trips=1, conn=conn):
            out.setdefault(r["transporter"], {})["gps_ok_pct"] = r.get("gps_ok_pct")
    except Exception as exc:
        logger.warning("GPS input unavailable for grading: %s", exc)
    return out


def _apply_grades(rows: list[dict], inputs: dict) -> None:
    """Attach the absolute grade to each scorecard row, in place."""
    for r in rows:
        r.update(inputs.get(r["name"], {}))
        g = grade_carrier(r)
        r["grade"] = g["grade"]
        r["grade_score"] = g["grade_score"]
        r["rated"] = g["rated"]


def list_transporters(conn, f: dict, search: str = "",
                      min_trips: int = DEFAULT_MIN_TRIPS) -> dict:
    """Scored league table of every carrier in the filtered window."""
    df = apply_filters(load_df(conn), f)
    score_df = _scorecard(df, min_trips)
    if score_df.empty:
        return {"data": [], "total": 0, "fleet": {}, "min_trips": min_trips}

    fleet = _fleet_benchmark(score_df, df)
    if search:
        score_df = score_df[score_df["name"].str.contains(search, case=False, na=False)]
    rows = clean_records(score_df.drop(columns=["qualified"]))
    _apply_grades(rows, _grading_inputs(conn, f))
    return {"data": rows, "total": len(rows), "fleet": fleet, "min_trips": min_trips,
            "grading": {"components": GRADE_COMPONENTS,
                        "bands": [{"at": a, "grade": g, "meaning": m} for a, g, m in GRADE_BANDS],
                        "min_trips": GRADE_MIN_TRIPS}}


# ----------------------------------------------------------------------
# Cross-carrier comparison (the analytics behind the overview page)
# ----------------------------------------------------------------------

PERIOD_KEYS = {"D": "dept_date", "W": "dept_week", "M": "dept_month"}


def _period_records(pivot: pd.DataFrame, names: list[str],
                    fill_zero: bool = False) -> list[dict]:
    """Wide pivot (period index x carrier columns) -> recharts-ready records."""
    if pivot is None or pivot.empty:
        return []
    pivot = pivot.reindex(columns=names).sort_index().round(1)
    out = []
    for idx, row in pivot.iterrows():
        rec: dict = {"period": str(idx)}
        for c in names:
            v = row.get(c)
            rec[c] = (0 if fill_zero else None) if pd.isna(v) else float(v)
        out.append(rec)
    return out


# ----------------------------------------------------------------------
# One carrier's profile
# ----------------------------------------------------------------------

def _mix(sub: pd.DataFrame, col: str) -> list[dict]:
    """Trip split across a categorical dimension, with OTD per slice."""
    s = sub.dropna(subset=[col])
    if s.empty:
        return []
    g = s.groupby(col).agg(
        trips=("trip_id", "count"),
        otd_pct=("is_on_time", _otd),
    ).reset_index().rename(columns={col: "name"})
    return clean_records(g.sort_values("trips", ascending=False))


def _monthly(sub: pd.DataFrame) -> list[dict]:
    s = sub.dropna(subset=["dept_month"])
    if s.empty:
        return []
    g = s.groupby("dept_month").agg(
        trips=("trip_id", "count"),
        otd_pct=("is_on_time", _otd),
        avg_transit_hours=("transit_hours", "mean"),
        avg_detention_hours=("detention_hours", "mean"),
        total_km=("distance_km", "sum"),
        speed_violations=("speed_violations", "sum"),
    ).reset_index().rename(columns={"dept_month": "month"}).sort_values("month")
    return clean_records(g)


def _lanes(sub: pd.DataFrame, limit: int = 12) -> list[dict]:
    s = sub.dropna(subset=["destination"])
    if s.empty:
        return []
    g = s.groupby(["origin", "destination"]).agg(
        trips=("trip_id", "count"),
        otd_pct=("is_on_time", _otd),
        avg_transit_hours=("transit_hours", "mean"),
        avg_planned_transit_hours=("planned_transit_hours", "mean"),
        avg_distance_km=("distance_km", "mean"),
        avg_detention_hours=("detention_hours", "mean"),
    ).reset_index()
    g["schedule_variance_hours"] = (g["avg_transit_hours"] - g["avg_planned_transit_hours"]).round(1)
    g["lane"] = g["origin"].fillna("?") + " → " + g["destination"].fillna("?")
    for c in ("avg_transit_hours", "avg_planned_transit_hours",
              "avg_distance_km", "avg_detention_hours"):
        g[c] = g[c].round(1)
    return clean_records(g.sort_values("trips", ascending=False).head(limit))


def _spread(sub: pd.DataFrame, metric: str = "transit_hours") -> dict:
    """Histogram + quartile stats for one metric — how predictable the carrier is."""
    s = sub[metric].dropna().astype(float)
    if len(s) < 2:
        return {"stats": {}, "histogram": []}
    q1, med, q3 = s.quantile(0.25), s.quantile(0.5), s.quantile(0.75)
    iqr = q3 - q1
    lo = float(s[s >= q1 - 1.5 * iqr].min())
    hi = float(s[s <= q3 + 1.5 * iqr].max())
    counts, edges = np.histogram(s, bins=min(30, max(int(len(s) ** 0.5) + 1, 5)))
    return {
        "stats": {
            "count": int(len(s)), "mean": _rnd(s.mean(), 2), "median": _rnd(med, 2),
            "std": _rnd(s.std(), 2), "cv_pct": _rnd(100 * s.std() / s.mean(), 1) if s.mean() else None,
            "q1": _rnd(q1, 2), "q3": _rnd(q3, 2), "iqr": _rnd(iqr, 2),
            "whisker_lo": _rnd(lo, 2), "whisker_hi": _rnd(hi, 2),
            "min": _rnd(s.min(), 2), "max": _rnd(s.max(), 2),
            "p90": _rnd(s.quantile(0.90), 2),
        },
        "histogram": [{"bin_start": _rnd(edges[i], 2), "bin_end": _rnd(edges[i + 1], 2),
                       "mid": _rnd((edges[i] + edges[i + 1]) / 2, 2), "count": int(c)}
                      for i, c in enumerate(counts)],
    }


RISK_COLS = ["trip_id", "dept_dt", "vehicle_no", "driver_name", "origin", "destination",
             "transit_hours", "planned_transit_hours", "delivery_delta_hours",
             "detention_hours", "distance_km", "delivery_status", "speed_violations"]

RECENT_COLS = ["trip_id", "dept_dt", "eta_dt", "ata_dt", "vehicle_no", "vehicle_category",
               "driver_name", "origin", "destination", "consignor", "own_market",
               "transit_hours", "detention_hours", "distance_km", "avg_speed_kmph",
               "speed_violations", "gps_uptime", "delivery_status", "trip_status",
               "is_on_time"]


def _insights(row: dict, fleet: dict, spread: dict) -> list[dict]:
    """Plain-English strengths / watch-outs vs the median carrier.

    Every bullet is a comparison a planner can act on, so the profile reads as
    an assessment rather than a wall of numbers.
    """
    out: list[dict] = []

    def cmp(label, val, ref, lower_better, unit="", tol=0.05):
        if val is None or ref in (None, 0):
            return
        gap = val - ref
        rel = abs(gap) / abs(ref)
        if rel < tol:
            out.append({"tone": "info", "text": f"{label} is in line with the fleet median ({val}{unit} vs {ref}{unit})."})
            return
        better = gap < 0 if lower_better else gap > 0
        arrow = "below" if gap < 0 else "above"
        out.append({
            "tone": "good" if better else "bad",
            "text": f"{label} {round(val, 1)}{unit} — {abs(round(gap, 1))}{unit} {arrow} the fleet median "
                    f"({ref}{unit}), {'better' if better else 'worse'} than the typical carrier.",
        })

    cmp("On-time delivery", row.get("otd_pct"), fleet.get("median_otd_pct"), False, "%")
    cmp("Average transit", row.get("avg_transit_hours"), fleet.get("median_transit_hours"), True, " h")
    cmp("Detention at plant", row.get("avg_detention_hours"), fleet.get("median_detention_hours"), True, " h")
    cmp("Speed alerts per trip", row.get("violations_per_trip"), fleet.get("median_violations_per_trip"), True)
    cmp("GPS uptime", row.get("avg_gps_uptime"), fleet.get("median_gps_uptime"), False, "%")

    sv = row.get("schedule_variance_hours")
    if sv is not None:
        out.append({
            "tone": "bad" if sv > 1 else "good" if sv < -1 else "info",
            "text": (f"Runs {abs(round(sv, 1))} h {'behind' if sv > 0 else 'ahead of'} its own promised ETA on average."
                     if abs(sv) >= 0.1 else "Delivers almost exactly to its promised ETA on average."),
        })

    cv = (spread.get("stats") or {}).get("cv_pct")
    if cv is not None:
        out.append({
            "tone": "good" if cv < 25 else "bad" if cv > 50 else "info",
            "text": f"Transit-time variability {cv}% of the mean — "
                    f"{'tight and plannable' if cv < 25 else 'erratic, forces buffer stock downstream' if cv > 50 else 'moderate spread'}.",
        })

    share = row.get("share_pct")
    if share is not None and share >= 25:
        out.append({"tone": "info",
                    "text": f"Carries {share}% of all filtered freight — a concentration risk if service slips."})

    mkt = row.get("market_pct")
    if mkt is not None and mkt >= 50:
        out.append({"tone": "info",
                    "text": f"{round(mkt)}% of its trips run on market (hired) trucks — less control over quality."})

    tpv = row.get("trips_per_vehicle")
    if tpv is not None and tpv >= 1:
        out.append({"tone": "info",
                    "text": f"{row.get('vehicles')} vehicles doing {round(tpv, 1)} trips each over the window."})
    return out


# ----------------------------------------------------------------------
# Head-to-head comparison of hand-picked carriers
# ----------------------------------------------------------------------

# Four is what the UI can render side by side and read at a glance; it also
# matches the trip comparison on the same page.
MAX_COMPARE = 4


def _box(v: pd.Series, group: str) -> dict | None:
    """Box-and-whisker stats for one carrier.

    Same quartile / 1.5×IQR whisker maths as `tta_dashboard.boxplot`, so a box
    drawn here is directly readable against the ones on /transporters — but for
    hand-picked carriers rather than the top N by volume.
    """
    v = v.dropna().astype(float)
    if len(v) < 2:
        return None
    q1, med, q3 = v.quantile(0.25), v.quantile(0.5), v.quantile(0.75)
    iqr = q3 - q1
    lo = float(v[v >= q1 - 1.5 * iqr].min())
    hi = float(v[v <= q3 + 1.5 * iqr].max())
    fliers = v[(v < lo) | (v > hi)]
    return {
        "group": group, "count": int(len(v)),
        "min": _rnd(v.min(), 2), "max": _rnd(v.max(), 2),
        "q1": _rnd(q1, 2), "median": _rnd(med, 2), "q3": _rnd(q3, 2),
        "whisker_lo": _rnd(lo, 2), "whisker_hi": _rnd(hi, 2),
        "mean": _rnd(v.mean(), 2),
        "outliers": [_rnd(x, 2) for x in fliers.sample(min(len(fliers), 25), random_state=7)],
    }


def _shared_lanes(df: pd.DataFrame, names: list[str], limit: int = 20) -> list[dict]:
    """Lanes that more than one of the picked carriers actually runs.

    This is the only fair head-to-head. Comparing two carriers on overall
    average transit mostly measures who holds the longer routes — same lane,
    different carrier takes the route out of the comparison entirely. `cells` is
    positional against `names` (None where that carrier never ran the lane) so
    the UI can render it as a column-per-carrier table without lookups.
    """
    sub = df[df["transporter"].isin(names)].dropna(subset=["destination"]).copy()
    if sub.empty:
        return []
    sub["lane"] = sub["origin"].fillna("?") + " → " + sub["destination"].fillna("?")

    g = sub.groupby(["lane", "transporter"]).agg(
        trips=("trip_id", "count"),
        otd_pct=("is_on_time", _otd),
        avg_transit_hours=("transit_hours", "mean"),
        avg_detention_hours=("detention_hours", "mean"),
        avg_distance_km=("distance_km", "mean"),
    ).reset_index()

    out = []
    for lane, part in g.groupby("lane"):
        if part["transporter"].nunique() < 2:
            continue  # nothing to compare against on this lane
        by = {r["transporter"]: r for r in part.to_dict("records")}
        out.append({
            "lane": str(lane),
            "carriers": int(part["transporter"].nunique()),
            "trips": int(part["trips"].sum()),
            "avg_distance_km": _rnd(part["avg_distance_km"].mean(), 1),
            "cells": [
                None if n not in by else {
                    "trips": int(by[n]["trips"]),
                    "otd_pct": _rnd(by[n]["otd_pct"], 1),
                    "avg_transit_hours": _rnd(by[n]["avg_transit_hours"], 1),
                    "avg_detention_hours": _rnd(by[n]["avg_detention_hours"], 1),
                }
                for n in names
            ],
        })
    # most-contested lanes first: every carrier present, then by volume
    out.sort(key=lambda r: (-r["carriers"], -r["trips"]))
    return out[:limit]


def compare_transporters(conn, names: list[str], f: dict,
                         min_trips: int = DEFAULT_MIN_TRIPS,
                         granularity: str = "M") -> dict:
    """Benchmark 2–4 hand-picked carriers against each other and the fleet.

    Deliberately one endpoint rather than N calls to `transporter_detail`: that
    would re-run `load_df` (a full read + re-derive of the trip frame) once per
    carrier. Here the frame is loaded once and sliced per name, so comparing
    four carriers costs about what profiling one does.
    """
    names = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))[:MAX_COMPARE]
    if len(names) < 2:
        raise HTTPException(400, "Pick at least 2 transporters to compare")

    df = apply_filters(load_df(conn), f)
    if df.empty:
        raise HTTPException(404, "No trips in the selected window")

    # Carriers with no trips in the window are reported back rather than
    # silently dropped — an empty column is a finding, not a glitch.
    missing = [n for n in names if df[df["transporter"] == n].empty]
    if len(missing) == len(names):
        raise HTTPException(404, "None of the selected transporters ran in the selected window")

    score_df = _scorecard(df, min_trips)
    fleet = _fleet_benchmark(score_df, df)
    rows = ({r["name"]: r for r in clean_records(score_df.drop(columns=["qualified"]))}
            if not score_df.empty else {})

    carriers = []
    for n in names:
        sub = df[df["transporter"] == n]
        row = rows.get(n, {})
        transit_spread = _spread(sub, "transit_hours")
        carriers.append({
            "name": n,
            "trips": int(len(sub)),
            "kpis": row,
            "insights": _insights(row, fleet, transit_spread) if row else [],
            "monthly": _monthly(sub),
            "lanes": _lanes(sub),
            "transit_spread": transit_spread,
            "detention_spread": _spread(sub, "detention_hours"),
            "delivery": _mix(sub, "delivery_status"),
            "own_market": _mix(sub, "own_market"),
            "vehicle_category": _mix(sub, "vehicle_category"),
        })

    # Trends over the same period grid, one column per picked carrier.
    key = PERIOD_KEYS.get(granularity, "dept_month")
    picked = df[df["transporter"].isin(names)].dropna(subset=[key])
    trend_trips: list[dict] = []
    trend_otd: list[dict] = []
    trend_transit: list[dict] = []
    if not picked.empty:
        trend_trips = _period_records(
            pd.crosstab(picked[key], picked["transporter"]), names, fill_zero=True)
        trend_otd = _period_records(
            picked.pivot_table(index=key, columns="transporter", values="is_on_time",
                               aggfunc="mean") * 100, names)
        trend_transit = _period_records(
            picked.pivot_table(index=key, columns="transporter", values="transit_hours",
                               aggfunc="mean"), names)

    def boxes(metric: str) -> list[dict]:
        made = (_box(df.loc[df["transporter"] == n, metric], n) for n in names)
        return [b for b in made if b]

    return {
        "names": names,
        "missing": missing,
        "granularity": granularity,
        "min_trips": min_trips,
        "fleet": fleet,
        "carriers": carriers,
        "box_transit": boxes("transit_hours"),
        "box_detention": boxes("detention_hours"),
        "trend_trips": trend_trips,
        "trend_otd": trend_otd,
        "trend_transit": trend_transit,
        "shared_lanes": _shared_lanes(df, names),
    }


# ----------------------------------------------------------------------
# One carrier's profile — the "everything about this transporter" page
# ----------------------------------------------------------------------

def _vehicles(sub: pd.DataFrame, limit: int = 40) -> list[dict]:
    s = sub.dropna(subset=["vehicle_no"])
    if s.empty:
        return []
    g = s.groupby("vehicle_no").agg(
        trips=("trip_id", "count"),
        otd_pct=("is_on_time", _otd),
        total_km=("distance_km", "sum"),
        avg_transit_hours=("transit_hours", "mean"),
        avg_speed_kmph=("avg_speed_kmph", "mean"),
        speed_violations=("speed_violations", "sum"),
        avg_gps_uptime=("gps_uptime", "mean"),
        vehicle_type=("vehicle_type", "first"),
        vehicle_category=("vehicle_category", "first"),
        own_market=("own_market", "first"),
        last_trip=("dept_dt", "max"),
    ).reset_index()
    g["violations_per_trip"] = (g["speed_violations"] / g["trips"]).round(1)
    for c in ("total_km", "avg_speed_kmph", "avg_gps_uptime", "avg_transit_hours"):
        g[c] = g[c].round(1)
    g["last_trip"] = g["last_trip"].map(_dt_str)
    return clean_records(g.sort_values("trips", ascending=False).head(limit))


def _drivers(sub: pd.DataFrame, limit: int = 40) -> list[dict]:
    s = sub.dropna(subset=["driver_name"])
    s = s[s["driver_name"].astype(str).str.strip() != ""]
    if s.empty:
        return []
    g = s.groupby("driver_name").agg(
        trips=("trip_id", "count"),
        otd_pct=("is_on_time", _otd),
        avg_transit_hours=("transit_hours", "mean"),
        avg_speed_kmph=("avg_speed_kmph", "mean"),
        speed_violations=("speed_violations", "sum"),
        total_km=("distance_km", "sum"),
        vehicles=("vehicle_no", "nunique"),
        destinations=("destination", "nunique"),
        last_trip=("dept_dt", "max"),
    ).reset_index().rename(columns={"driver_name": "name"})
    g["violations_per_trip"] = (g["speed_violations"] / g["trips"]).round(1)
    for c in ("avg_transit_hours", "avg_speed_kmph", "total_km"):
        g[c] = g[c].round(1)
    g["last_trip"] = g["last_trip"].map(_dt_str)
    return clean_records(g.sort_values("trips", ascending=False).head(limit))


def _rhythm(sub: pd.DataFrame) -> dict:
    """Weekday x hour departure heatmap — when this carrier actually loads."""
    s = sub.dropna(subset=["dept_dow", "dept_hour"])
    if s.empty:
        return {"rows": DOW_NAMES, "cols": list(range(24)),
                "values": [[0] * 24 for _ in range(7)]}
    pivot = pd.crosstab(s["dept_dow"], s["dept_hour"]).reindex(
        index=range(7), columns=range(24), fill_value=0)
    return {"rows": DOW_NAMES, "cols": list(range(24)),
            "values": pivot.fillna(0).astype(int).values.tolist()}


def _risk_trips(sub: pd.DataFrame, limit: int = 15) -> list[dict]:
    """Worst delivery slips — the evidence to bring to a carrier review."""
    s = sub.dropna(subset=["delivery_delta_hours"])
    s = s[s["delivery_delta_hours"] > 0]
    if s.empty:
        return []
    return clean_records(
        s[RISK_COLS].sort_values("delivery_delta_hours", ascending=False).head(limit))


def _states(sub: pd.DataFrame) -> list[dict]:
    """Where this carrier delivers, by state."""
    if "dest_state" not in sub.columns:
        return []
    s = sub.dropna(subset=["dest_state"])
    if s.empty:
        return []
    g = s.groupby("dest_state").agg(
        trips=("trip_id", "count"),
        otd_pct=("is_on_time", _otd),
        avg_transit_hours=("transit_hours", "mean"),
        total_km=("distance_km", "sum"),
    ).reset_index().rename(columns={"dest_state": "state"})
    for c in ("avg_transit_hours", "total_km"):
        g[c] = g[c].round(1)
    return clean_records(g.sort_values("trips", ascending=False))


def _bio(sub: pd.DataFrame, df: pd.DataFrame, name: str) -> dict:
    """Identity card: who this carrier is, at a glance.

    Everything here is a count or a date rather than a rating — this is the
    "who am I looking at" block that a service assessment is read against, so it
    deliberately contains no judgement.
    """
    dept = sub["dept_dt"].dropna()
    span_days = (dept.max() - dept.min()).days + 1 if len(dept) else 0
    active_days = int(sub["dept_date"].nunique())
    makes = sub["asset_make"].dropna().value_counts()
    devices = sub["device_type"].dropna().value_counts()
    return {
        "name": name,
        "trips": int(len(sub)),
        "share_pct": _rnd(100 * len(sub) / len(df), 1) if len(df) else None,
        "first_trip": _dt_str(dept.min()) if len(dept) else None,
        "last_trip": _dt_str(dept.max()) if len(dept) else None,
        "window_days": span_days,
        "active_days": active_days,
        # Days with a departure out of days in the relationship. A carrier used
        # twice a month is a different kind of partner from one used daily, and
        # every rate on the page should be read knowing which this is.
        "activity_pct": _rnd(100 * active_days / span_days, 1) if span_days else None,
        "trips_per_active_day": _rnd(len(sub) / active_days, 1) if active_days else None,
        "vehicles": int(sub["vehicle_no"].nunique()),
        "drivers": int(sub["driver_name"].dropna().nunique()),
        "destinations": int(sub["destination"].nunique()),
        "lanes": int(sub.dropna(subset=["destination"])
                     .groupby(["origin", "destination"]).ngroups),
        "states": int(sub["dest_state"].dropna().nunique()) if "dest_state" in sub else 0,
        "consignors": int(sub["consignor"].nunique()),
        "consignees": int(sub["consignee"].dropna().nunique()),
        "total_km": _rnd(sub["distance_km"].sum(skipna=True), 0),
        "own_pct": _rnd(100 * (sub["own_market"] == "Own").mean(), 1),
        "market_pct": _rnd(100 * (sub["own_market"] == "Market").mean(), 1),
        "top_make": makes.index[0] if len(makes) else None,
        "makes": int(len(makes)),
        "top_device": devices.index[0] if len(devices) else None,
        "vehicle_categories": sorted(sub["vehicle_category"].dropna().unique().tolist()),
    }


def _gps_quality(conn, name: str) -> dict:
    """This carrier's row from the GPS coverage scorecard.

    Reuses the geofence module's scorecard rather than recomputing coverage
    here: that module owns the definition of a "silent" or "gappy" trip and
    ranks on a confidence interval, and a second definition living on the
    transporter page would eventually contradict it.
    """
    try:
        from nexgen.shared.circlefence import gps_quality as gq
        rows = gq.transporter_scorecard(trip_class=None, min_trips=1, conn=conn)
    except Exception as exc:  # the rollup is optional; a profile must still render
        logger.warning("GPS quality unavailable for %s: %s", name, exc)
        return {"available": False, "reason": str(exc)}
    mine = next((r for r in rows if r.get("transporter") == name), None)
    if not mine:
        return {"available": False, "reason": "no GPS-scored trips for this carrier"}
    ranked = sorted((r for r in rows if r.get("gps_ok_pct") is not None),
                    key=lambda r: r["gps_ok_pct"], reverse=True)
    fleet_ok = [r["gps_ok_pct"] for r in ranked]
    return {
        "available": True,
        **mine,
        "rank": next((i + 1 for i, r in enumerate(ranked)
                      if r.get("transporter") == name), None),
        "carriers_ranked": len(ranked),
        "fleet_median_ok_pct": _rnd(float(np.median(fleet_ok)), 1) if fleet_ok else None,
    }


def transporter_detail(conn, name: str, f: dict,
                       min_trips: int = DEFAULT_MIN_TRIPS) -> dict:
    """Everything known about one carrier, benchmarked against the rest.

    Assembled as one response rather than a dozen endpoints because every block
    is a slice of the same filtered frame: fetching them separately would re-run
    `load_df` once per block, and could return blocks from different filter
    states if the user moved the date picker mid-render.
    """
    df = apply_filters(load_df(conn), f)
    if df.empty:
        raise HTTPException(404, "No trips in the selected window")

    sub = df[df["transporter"] == name]
    if sub.empty:
        raise HTTPException(404, f"Transporter '{name}' has no trips in the selected window")

    score_df = _scorecard(df, min_trips)
    fleet = _fleet_benchmark(score_df, df)
    me = score_df[score_df["name"] == name]
    row = clean_records(me.drop(columns=["qualified"]))[0] if not me.empty else {}

    spread = _spread(sub, "transit_hours")
    row.update(_grading_inputs(conn, f).get(name, {}))
    grade = grade_carrier(row) if row else {}
    if grade:
        row["grade"] = grade["grade"]
        row["grade_score"] = grade["grade_score"]
    return {
        "transporter": name,
        "bio": _bio(sub, df, name),
        "kpis": row,
        "fleet": fleet,
        "grade": grade,
        "fleet_shape": _fleet_shape(sub),
        "origin_detention": _origin_detention(sub, df),
        "insights": _insights(row, fleet, spread) if row else [],
        "gps_quality": _gps_quality(conn, name),
        "monthly": _monthly(sub),
        "lanes": _lanes(sub, limit=25),
        "states": _states(sub),
        "vehicles": _vehicles(sub),
        "drivers": _drivers(sub),
        "delivery": _mix(sub, "delivery_status"),
        "own_market": _mix(sub, "own_market"),
        "vehicle_category": _mix(sub, "vehicle_category"),
        "consignor_mix": _mix(sub, "consignor"),
        "device_type": _mix(sub, "device_type"),
        "transit_spread": spread,
        "detention_spread": _spread(sub, "detention_hours"),
        "rhythm": _rhythm(sub),
        "risk_trips": _risk_trips(sub),
        "recent_trips": clean_records(
            sub[RECENT_COLS].sort_values("dept_dt", ascending=False).head(25)),
    }


# ----------------------------------------------------------------------
# Reliability matrix — volume against reliability
# ----------------------------------------------------------------------

# Where the quadrant lines sit. Volume splits at the median carrier so the
# picture stays readable whatever the fleet's size distribution; reliability
# splits at the service target, which is an absolute promise to the customer and
# must not move just because every carrier happens to be missing it.
OTD_TARGET = 95.0


def _reliability_score(g: pd.DataFrame) -> pd.Series:
    """Reliability as more than a single month's on-time rate.

    Three things a planner means by "reliable", weighted:

      * hits the date (OTD, 60%)
      * keeps its own promise (variance against its quoted ETA, 20%)
      * is predictable (transit-time spread, 20%) — a carrier that is always
        two hours late is easier to plan around than one that is on time on
        average by being wildly early and wildly late.

    Each axis is scored 0-100 on its own fixed scale rather than
    percentile-ranked across the fleet, so a carrier's position does not move
    when an unrelated carrier joins or leaves the filter.
    """
    otd = g["otd_pct"].fillna(0).clip(0, 100)
    # +/- 12 h against plan spans the full 0-100 band: past half a day, the
    # difference between "late" and "very late" stops changing the decision.
    adherence = (100 * (1 - g["schedule_variance_hours"].abs().clip(0, 12) / 12)).fillna(50)
    consistency = 100 - g["transit_cv_pct"].fillna(50).clip(0, 100)
    return (0.60 * otd + 0.20 * adherence + 0.20 * consistency).round(1)


def _quadrant(volume_high: bool, reliable: bool) -> str:
    if volume_high and reliable:
        return "core"          # protect: much of the freight, and it lands
    if volume_high and not reliable:
        return "critical"      # fix, or move the volume — the expensive corner
    if not volume_high and reliable:
        return "grow"          # earned more freight than it is getting
    return "review"            # little volume, poor service


QUADRANT_LABEL = {
    "core": "Core partner",
    "critical": "Critical risk",
    "grow": "Grow",
    "review": "Review / exit",
}

QUADRANT_ACTION = {
    "core": "Carries serious volume and delivers. Protect the relationship and "
            "settle rates before someone else does.",
    "critical": "The expensive corner: a lot of freight riding on a carrier that "
                "misses dates. Fix with a service plan, or move volume to the Grow list.",
    "grow": "Delivers, but is barely used. The cheapest service improvement "
            "available is to hand this carrier some of the Critical list's freight.",
    "review": "Little volume and poor service — nothing gained by keeping it on "
              "the panel, and little lost by putting it on notice.",
}


def reliability_matrix(conn, f: dict, min_trips: int = DEFAULT_MIN_TRIPS,
                       otd_target: float = OTD_TARGET) -> dict:
    """Volume against reliability, one point per carrier, split into quadrants.

    The question this answers is not "who is best" — the league table already
    ranks that — but "where is the freight sitting relative to the service".
    A poor carrier with 4 trips is a rounding error; the same carrier holding
    30% of the freight is the biggest service risk on the panel, and a ranked
    list shows the two identically.
    """
    df = apply_filters(load_df(conn), f)
    empty = {"carriers": [], "axes": {}, "quadrants": {}, "min_trips": min_trips}
    if df.empty:
        return empty

    score_df = _scorecard(df, min_trips)
    if score_df.empty:
        return empty

    g = score_df.copy()

    # Transit-time spread per carrier, for the consistency axis.
    cv = (df.dropna(subset=["transporter", "transit_hours"])
            .groupby("transporter")["transit_hours"].agg(["mean", "std"]))
    cv["cv"] = (100 * cv["std"] / cv["mean"].replace(0, np.nan)).round(1)
    g["transit_cv_pct"] = g["name"].map(cv["cv"])

    # On-time rate as an interval, not a point. Two carriers at 80% are not the
    # same finding when one has 5 judged trips and the other 500, and this
    # matrix is read as a decision about where to move freight.
    ontime = df.dropna(subset=["transporter", "is_on_time"]).groupby("transporter")["is_on_time"]
    judged, hits = ontime.count(), ontime.sum()
    ci = {n: wilson_interval(int(hits.get(n, 0)), int(judged.get(n, 0))) for n in g["name"]}
    g["otd_judged_trips"] = g["name"].map(judged).fillna(0).astype(int)
    g["otd_ci_low"] = g["name"].map(lambda n: ci[n][0])
    g["otd_ci_high"] = g["name"].map(lambda n: ci[n][1])

    g["reliability_score"] = _reliability_score(g)

    qual = g[g["trips"] >= min_trips]
    median_trips = float((qual if not qual.empty else g)["trips"].median())

    g["volume_high"] = g["trips"] >= median_trips
    g["reliable"] = g["otd_pct"].fillna(0) >= otd_target
    g["quadrant"] = [_quadrant(bool(v), bool(r))
                     for v, r in zip(g["volume_high"], g["reliable"])]
    g["quadrant_label"] = g["quadrant"].map(QUADRANT_LABEL)
    g["qualified"] = g["trips"] >= min_trips

    cols = ["name", "trips", "share_pct", "otd_pct", "otd_ci_low", "otd_ci_high",
            "otd_judged_trips", "reliability_score", "transit_cv_pct",
            "schedule_variance_hours", "avg_transit_hours", "avg_detention_hours",
            "violations_per_trip", "avg_gps_uptime", "total_km", "vehicles",
            "destinations", "market_pct", "score", "grade", "quadrant",
            "quadrant_label", "qualified"]
    rows = clean_records(g[cols].sort_values("trips", ascending=False))

    summary = {}
    for key, label in QUADRANT_LABEL.items():
        part = g[g["quadrant"] == key]
        summary[key] = {
            "label": label,
            "action": QUADRANT_ACTION[key],
            "carriers": int(len(part)),
            "trips": int(part["trips"].sum()),
            "share_pct": _rnd(100 * part["trips"].sum() / len(df), 1) if len(df) else None,
            "median_otd_pct": _rnd(part["otd_pct"].median(skipna=True), 1),
        }

    return {
        "carriers": rows,
        "axes": {
            "volume_split": median_trips,
            "volume_split_label": f"median carrier ({median_trips:.0f} trips)",
            "otd_target": otd_target,
            "max_trips": int(g["trips"].max()),
        },
        "quadrants": summary,
        "min_trips": min_trips,
        "fleet": _fleet_benchmark(score_df, df),
    }


# ----------------------------------------------------------------------
# Grading — absolute, published, auditable
# ----------------------------------------------------------------------
#
# The composite `score` above is a PERCENTILE rank: it answers "where does this
# carrier sit against the others in this filter". That is useful for sorting a
# league table and useless as a grade, because it moves when an unrelated
# carrier joins or leaves the window — a carrier can drop from A to C without
# changing anything it does. It is kept for ranking; the grade below replaces it
# for judgement.
#
# A grade here is measured against FIXED published bands. Every component says
# what it measures, what the cut-offs are and what this carrier scored, so a
# carrier shown a C can be told exactly which band it fell in and what would
# move it. That is the whole point: a grade you cannot explain is a grade you
# cannot defend in a contract review.
#
# Bands are (threshold, points) evaluated best-first; `lower_is_better` metrics
# pass when the value is <= the threshold.

GRADE_COMPONENTS = [
    {
        "key": "otd_pct", "label": "Delivers on the promised date",
        "weight": 40, "unit": "%", "lower_is_better": False,
        "bands": [(95, 100), (90, 80), (85, 60), (75, 40), (60, 20)],
        "why": "The customer-facing promise. Weighted heaviest because every "
               "other measure is a means to this one.",
    },
    {
        "key": "abs_schedule_variance_hours", "label": "Keeps its own quoted ETA",
        "weight": 10, "unit": " h", "lower_is_better": True,
        "bands": [(12, 100), (24, 80), (36, 60), (60, 30)],
        "why": "Distance from the ETA the carrier itself quoted, in either "
               "direction — being wildly early is a planning failure too, "
               "because it means the quote was padded. Weighted lightly and "
               "banded wide on purpose: on this corpus EVERY carrier sits "
               "18-53 h off its quote, which is a systemic quoting problem "
               "rather than something individual carriers are doing wrong.",
    },
    {
        "key": "grade_detention_hours", "label": "Clears the origin quickly",
        "weight": 15, "unit": " h", "lower_is_better": True,
        "bands": [(6, 100), (12, 80), (24, 50), (36, 25)],
        "why": "True hold at origin where the GPS trail confirms it, falling "
               "back to the declared works detention. Part of it is your "
               "loading bay, which is why it is not weighted like on-time.",
    },
    {
        "key": "road_episodes_per_100_trips", "label": "Drives within the speed limit",
        "weight": 20, "unit": " per 100 trips", "lower_is_better": True,
        "bands": [(0, 100), (25, 80), (75, 60), (175, 30)],
        "why": "Overspeed episodes ON THE ROAD per 100 GPS-producing trips, at "
               "the 60 km/h default. Episodes, not pings: a 12-minute overspeed "
               "is one event, and the provider's own violation field is a ping "
               "count that rises with tracker density, so it is not used here. "
               "In-plant episodes are excluded from the grade: the plant fence "
               "is a 10 km circle containing public road, so judging a carrier "
               "on a 20 km/h yard limit inside it would penalise ordinary "
               "driving near the works.",
    },
    {
        "key": "gps_ok_pct", "label": "Can be tracked at all",
        "weight": 15, "unit": "%", "lower_is_better": False,
        "bands": [(95, 100), (85, 80), (70, 55), (50, 25)],
        "why": "Share of trips tracked cleanly. A carrier that cannot be "
               "measured cannot be shown to be good, so this is graded rather "
               "than assumed.",
    },
]

# Absolute cut-offs on the weighted total. Published so a carrier can be told
# what would move it up a band.
GRADE_BANDS = [
    (80, "A", "Meets the standard on every axis that matters."),
    (65, "B", "Sound, with one axis worth a conversation."),
    (50, "C", "Below standard — needs a service plan, not a warning."),
    (0,  "D", "Failing on multiple axes. Move volume or exit."),
]

# Below this many trips a grade would be noise dressed as a verdict.
GRADE_MIN_TRIPS = 10


def _band_points(value, bands, lower_is_better: bool):
    """Points for a value against its published bands, or None if unmeasured."""
    if value is None or pd.isna(value):
        return None
    v = float(value)
    for threshold, points in bands:
        if (v <= threshold) if lower_is_better else (v >= threshold):
            return points
    return 0


def grade_carrier(row: dict) -> dict:
    """Grade one carrier against the published bands, showing the working.

    Components with no data are dropped and the weights renormalised over what
    remains, so a carrier is never punished for a metric nobody could measure —
    but `measured_weight` reports how much of the grade was actually evidenced,
    and a grade resting on half the axes says so.
    """
    trips = row.get("trips") or 0
    components, earned, total_weight = [], 0.0, 0.0

    for spec in GRADE_COMPONENTS:
        value = row.get(spec["key"])
        points = _band_points(value, spec["bands"], spec["lower_is_better"])
        components.append({
            "key": spec["key"], "label": spec["label"], "weight": spec["weight"],
            "value": _rnd(value, 1) if value is not None and not pd.isna(value) else None,
            "unit": spec["unit"], "points": points, "why": spec["why"],
            "bands": [{"at": t, "points": p} for t, p in spec["bands"]],
            "lower_is_better": spec["lower_is_better"],
        })
        if points is not None:
            earned += points * spec["weight"]
            total_weight += spec["weight"]

    if trips < GRADE_MIN_TRIPS or total_weight == 0:
        return {
            "grade": "—", "grade_score": None, "rated": False,
            "reason": (f"Fewer than {GRADE_MIN_TRIPS} trips in this window — too "
                       f"little evidence to grade." if trips < GRADE_MIN_TRIPS
                       else "None of the graded metrics could be measured."),
            "components": components,
            "measured_weight": round(total_weight),
            "bands": [{"at": a, "grade": g, "meaning": m} for a, g, m in GRADE_BANDS],
        }

    score = earned / total_weight
    letter, meaning = next((g, m) for a, g, m in GRADE_BANDS if score >= a)
    return {
        "grade": letter, "grade_score": round(score, 1), "rated": True,
        "meaning": meaning,
        "reason": None,
        "components": components,
        "measured_weight": round(total_weight),
        "bands": [{"at": a, "grade": g, "meaning": m} for a, g, m in GRADE_BANDS],
    }


# ----------------------------------------------------------------------
# Fleet shape
# ----------------------------------------------------------------------

def _fleet_shape(sub: pd.DataFrame) -> dict:
    """What the carrier's fleet actually looks like, not its average.

    The profile used to say "118 vehicles doing 2.8 trips each". Arithmetically
    right, and it describes a fleet that does not exist: on this corpus the
    biggest carrier's median vehicle runs ONE trip and 69% of its trucks appear
    exactly once, while a handful do a dozen. The mean sat between two
    populations and represented neither.
    """
    per_vehicle = sub.dropna(subset=["vehicle_no"]).groupby("vehicle_no").size()
    n = len(per_vehicle)
    if not n:
        return {"vehicles": 0}
    trips = int(per_vehicle.sum())
    ordered = per_vehicle.sort_values(ascending=False)
    top10 = int(ordered.head(10).sum())
    once = int((per_vehicle == 1).sum())
    return {
        "vehicles": n,
        "trips": trips,
        "median_trips_per_vehicle": float(per_vehicle.median()),
        "max_trips_per_vehicle": int(per_vehicle.max()),
        # The two numbers that tell a hired fleet from a dedicated one.
        "single_trip_vehicles": once,
        "single_trip_pct": _rnd(100 * once / n, 0),
        "top10_share_pct": _rnd(100 * top10 / trips, 0) if trips else None,
        "repeat_vehicles": int((per_vehicle > 1).sum()),
    }


def _origin_detention(sub: pd.DataFrame, df: pd.DataFrame) -> dict:
    """How long this carrier's trucks are held at origin, and how much of it
    the declared figure misses.

    Three windows, all per the geofence module: the DECLARED works detention
    (plant entry to the gate-out stamp), the TAIL still inside the 10 km plant
    fence after that stamp, and the TRUE total. The tail exists because the
    gate-out stamp fires before the truck has physically cleared the works, so
    the declared figure is structurally optimistic — this block quantifies by
    how much rather than asserting it.

    The true total is only available for trips whose exit the GPS trail
    confirmed, so its trip count is reported alongside: a 20-hour average over
    six trips is not the same claim as one over two hundred.
    """
    def stat(col):
        v = sub[col].dropna()
        fleet_v = df[col].dropna()
        return {
            "trips": int(len(v)),
            "mean_hours": _rnd(v.mean(), 1) if len(v) else None,
            "median_hours": _rnd(v.median(), 1) if len(v) else None,
            "p90_hours": _rnd(v.quantile(0.90), 1) if len(v) else None,
            "fleet_median_hours": _rnd(fleet_v.median(), 1) if len(fleet_v) else None,
        }

    declared = stat("works_detention_hours")
    tail = stat("geofence_tail_hours")
    true_total = stat("origin_total_hours")
    understated = None
    if true_total["median_hours"] and declared["median_hours"] is not None:
        gap = true_total["median_hours"] - declared["median_hours"]
        if true_total["median_hours"]:
            understated = _rnd(100 * gap / true_total["median_hours"], 0)
    return {
        "declared": declared, "fence_tail": tail, "true_total": true_total,
        "declared_understates_by_pct": understated,
        "measured_trips": true_total["trips"],
        "total_trips": int(len(sub)),
    }


# ----------------------------------------------------------------------
# Best carrier per lane
# ----------------------------------------------------------------------

# A carrier needs this many trips ON THAT LANE before it can win it. A single
# on-time trip is not evidence that a carrier is the best choice for a route.
LANE_MIN_TRIPS = 5


def best_by_lane(conn, f: dict, min_trips: int = LANE_MIN_TRIPS,
                 limit: int = 60) -> dict:
    """Who to give each lane to, judged only on that lane.

    Comparing carriers on their overall averages mostly measures who holds the
    longer routes. This compares them only against carriers who ran the SAME
    origin-destination pair, which is the only comparison that isolates the
    carrier from the route.

    Ranking inside a lane is on-time first, then the carrier's own schedule
    adherence, then transit — with a Wilson lower bound on the on-time rate so a
    carrier cannot win a lane on three lucky trips.
    """
    df = apply_filters(load_df(conn), f)
    if df.empty:
        return {"lanes": [], "min_trips": min_trips, "commodity": _COMMODITY_NOTE}

    sub = df.dropna(subset=["destination", "transporter"]).copy()
    if sub.empty:
        return {"lanes": [], "min_trips": min_trips, "commodity": _COMMODITY_NOTE}
    sub["lane"] = sub["origin"].fillna("?") + " → " + sub["destination"].fillna("?")

    g = sub.groupby(["lane", "transporter"]).agg(
        trips=("trip_id", "count"),
        otd_pct=("is_on_time", _otd),
        judged=("is_on_time", "count"),
        on_time=("is_on_time", "sum"),
        avg_transit_hours=("transit_hours", "mean"),
        avg_planned_hours=("planned_transit_hours", "mean"),
        avg_detention_hours=("detention_hours", "mean"),
        avg_distance_km=("distance_km", "mean"),
    ).reset_index()
    g["schedule_variance_hours"] = (g["avg_transit_hours"] - g["avg_planned_hours"]).round(1)
    for c in ("avg_transit_hours", "avg_detention_hours", "avg_distance_km"):
        g[c] = g[c].round(1)

    ci = [wilson_interval(int(h or 0), int(j or 0)) for h, j in zip(g["on_time"], g["judged"])]
    g["otd_ci_low"] = [c[0] for c in ci]
    g["otd_ci_high"] = [c[1] for c in ci]

    eligible = g[g["trips"] >= min_trips]
    lanes = []
    for lane, part in eligible.groupby("lane"):
        if len(part) < 2:
            continue  # nothing to choose between
        ranked = part.sort_values(
            ["otd_ci_low", "otd_pct", "avg_transit_hours"],
            ascending=[False, False, True])
        best = ranked.iloc[0]
        worst = ranked.iloc[-1]
        lanes.append({
            "lane": str(lane),
            "carriers": int(len(part)),
            "trips": int(part["trips"].sum()),
            "avg_distance_km": _rnd(part["avg_distance_km"].mean(), 1),
            "best": clean_records(ranked.head(1).drop(columns=["lane"]))[0],
            "runner_up": (clean_records(ranked.iloc[1:2].drop(columns=["lane"]))[0]
                          if len(ranked) > 1 else None),
            "worst": clean_records(ranked.tail(1).drop(columns=["lane"]))[0],
            # What switching the lane to the best carrier would be worth, in
            # on-time points. Stated as a gap, not a promise: the worst carrier
            # may be running the harder loads on that lane.
            "otd_gap_pts": _rnd((best["otd_pct"] or 0) - (worst["otd_pct"] or 0), 1),
            "all": clean_records(ranked.drop(columns=["lane"])),
        })

    lanes.sort(key=lambda r: (-(r["otd_gap_pts"] or 0), -r["trips"]))
    return {
        "lanes": lanes[:limit],
        "total_lanes": len(lanes),
        "min_trips": min_trips,
        "commodity": _COMMODITY_NOTE,
    }


# Asked for, and not possible from this feed. Returned with the lane report so
# the answer is visible in the product rather than only in a conversation.
_COMMODITY_NOTE = {
    "available": False,
    "reason": "No commodity dimension exists in the eTrans feed. "
              "tta_trip_metrics.s_material_desc is empty on all 2,988 trips, "
              "c_trip_type is NULL throughout, and s_trip_type_desc holds only "
              "INVOICE TRIP / LOCAL TRIP — a trip class, not a material.",
    "needed": "Either the material/commodity field populated upstream in the "
              "TTA report, or an invoice-line feed keyed on s_invoice, which "
              "the trips table already carries.",
}
