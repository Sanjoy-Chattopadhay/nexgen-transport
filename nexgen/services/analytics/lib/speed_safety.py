"""Speed and safety analytics — the query side of the rollups.

Reads the two tables built by `speed_profile.py` and answers:

    speed_overview()    how fast do we run inside a plant vs out on the road,
                        judged against a limit the caller chooses
    safety_daily()      violations per day, with the fleet's exposure alongside
    running_pattern()   what a fleet is doing across the 24 hours of a day
    carrier_safety()    the same, one row per transporter
    events()            the violation register itself, for drill-down

Three rules hold everywhere in this module.

**The limit is an input, never a constant.** Every count of "violations" is
computed against `road_limit` / `plant_limit` as passed in. The defaults are the
business's (60 on the road, 20 in a plant), but nothing here bakes them in, and
every response echoes the limits it used, because a violation count without its
limit is not a number anyone can act on.

**Each figure comes from the source where it is exact.** Time and distance over
the limit come from the histogram, which resolves 1 km/h and can therefore be
summed above any threshold exactly. Episode counts, peaks and locations come
from the event register. Never the other way round: an episode's duration was
measured at the detection floor, so reading it as "time above 60" would
overstate it. See migrations/schema_speed_safety.sql.

**Filtering goes through the dashboard frame, not through SQL.** The trip set is
resolved by `tta_dashboard.load_df` + `apply_filters` — the same code path as
every other analytics screen — and only then are the rollups queried for those
trip ids. Reimplementing the filters in SQL here would let this screen and the
one next to it disagree about what "August, market fleet" means.
"""
import hashlib
import threading
import time

import numpy as np
import pandas as pd

from nexgen.services.analytics.lib import speed_profile as sp
from nexgen.services.analytics.lib.tta_dashboard import apply_filters, load_df

# Below this the truck is stopped, not "driving slowly". Averaging the parked
# hours into a mean speed drags every carrier towards zero and hides the
# difference between them.
MOVING_FLOOR_KMPH = 1

# Fatigue risk clusters in these hours, so the share of driving done inside them
# is reported as a safety number rather than a utilisation one.
NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 6

ZONE_LABEL = {"plant": "Inside plant", "road": "Outside plant"}


def _rnd(v, d=1):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else round(f, d)


def _nulls(df: pd.DataFrame) -> list[dict]:
    """Records with NaN/NaT rendered as JSON null (Starlette rejects NaN)."""
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


def validate_limit(zone: str, value: int | None) -> int:
    """Snap a requested limit to one this module can answer exactly.

    Anything off the list falls back to the documented default rather than
    being answered approximately under the caller's label — a response saying
    "3 violations over 63 km/h" when 63 was never evaluated is worse than one
    that says it used 60.
    """
    allowed = sp.limits_for(zone)
    if value is None:
        return sp.default_limit(zone)
    try:
        v = int(value)
    except (TypeError, ValueError):
        return sp.default_limit(zone)
    return v if v in allowed else sp.default_limit(zone)


# ---------------------------------------------------------------------------
# Trip scope
# ---------------------------------------------------------------------------

def _scope(conn, f: dict) -> pd.DataFrame:
    """The filtered trip frame — the single definition of "in scope"."""
    return apply_filters(load_df(conn), f)


def _ids(df: pd.DataFrame) -> list[int]:
    return [int(x) for x in df["trip_id"].dropna().unique()]


def _in_clause(ids: list[int]) -> str:
    """Inline the trip ids.

    Safe: these are ints coming out of an int column, re-cast here rather than
    trusted. Inlined rather than parameterised because the list runs to a few
    thousand entries and pymysql would build the same string anyway.
    """
    return "(" + ",".join(str(int(i)) for i in ids) + ")"


# ---------------------------------------------------------------------------
# Histogram
# ---------------------------------------------------------------------------

# Aggregation happens in SQL, not in pandas.
#
# The histogram is ~450k rows on this corpus and a screen needs at most a few
# hundred of them; pulling the lot per request measured 17-25 s a call. Every
# helper below therefore GROUP BYs inside MySQL against the rollup's own index
# and returns only what will be drawn. The trip-id list still comes from the
# pandas filter frame, so the filter semantics are unchanged.

# The rollups only change when speed_profile.refresh_all() runs -- every few
# hours, on the waypoint cadence -- so a read of them is safe to reuse for far
# longer than a live query would be.
#
# It is worth having because the Speed page fires five of these at once and they
# all scan the same ~450k rows: measured individually at 2-4 s each, they
# contend to ~9 s of wall clock together. With the cache, everything after the
# first paint (switching a limit, opening a carrier, coming back to the tab)
# reads from memory.
#
# Keyed on the exact trip set and the limits, so a different filter is a
# different entry and can never be served another filter's answer.
_ROLLUP_TTL_S = 300
_ROLLUP_MAX_ENTRIES = 64
_rollup_cache: dict[str, tuple[float, object]] = {}
_rollup_lock = threading.Lock()
# One lock per cache key, so a cold key is computed once and the other callers
# wait for it -- see _cached.
_key_locks: dict[str, threading.Lock] = {}


def _scope_key(ids: list[int], *parts) -> str:
    """Stable key for a trip set. Hashed because the id list runs to thousands."""
    digest = hashlib.blake2b(
        ",".join(str(i) for i in sorted(ids)).encode(), digest_size=16).hexdigest()
    return "|".join((digest, *(str(p) for p in parts)))


def _fresh(key: str):
    """(hit, value) for a key that is present and inside its TTL."""
    with _rollup_lock:
        hit = _rollup_cache.get(key)
        if hit and time.time() - hit[0] < _ROLLUP_TTL_S:
            return True, hit[1]
    return False, None


def _cached(key: str, produce):
    """Read-through cache with single-flight.

    Single-flight matters more here than the caching does. The Speed page fires
    five requests at once and they share these keys, so without it all five miss
    the cold cache simultaneously and each runs the same multi-second query --
    measured 25 s for a first paint that takes 3.8 s once one caller computes
    and the rest wait on it.

    The lock is per key, never global: a slow histogram must not block an
    unrelated carrier query, only the callers that want the same answer.
    """
    hit, value = _fresh(key)
    if hit:
        return value

    with _rollup_lock:
        lock = _key_locks.setdefault(key, threading.Lock())

    with lock:
        # Someone may have filled it while this caller waited for the lock.
        hit, value = _fresh(key)
        if hit:
            return value
        value = produce()
        with _rollup_lock:
            _rollup_cache[key] = (time.time(), value)
            if len(_rollup_cache) > _ROLLUP_MAX_ENTRIES:
                for stale in sorted(_rollup_cache, key=lambda k: _rollup_cache[k][0])[:16]:
                    _rollup_cache.pop(stale, None)
                    _key_locks.pop(stale, None)
    return value


def invalidate_cache() -> None:
    """Drop every cached rollup read. Called after a rebuild."""
    with _rollup_lock:
        _rollup_cache.clear()
        _key_locks.clear()


def _scalar(conn, sql: str, args: tuple = ()) -> dict:
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone() or {}


def _frame(conn, sql: str, args: tuple = ()) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


def _zone_histogram(conn, ids: list[int]) -> pd.DataFrame:
    """Speed histogram per zone, summed over trips and hours.

    Rolled up from the same cached per-trip frame the carrier report uses
    rather than issuing its own GROUP BY. The two used to scan the rollup
    separately; the Speed page loads both, so one scan and a 40 ms pandas
    groupby beats two scans.
    """
    cols = ["s_zone", "i_kmph", "i_pings", "d_minutes", "i_dist_m"]
    df = _trip_speed_frame(conn, ids)
    if df.empty:
        return pd.DataFrame(columns=cols)
    return df.groupby(["s_zone", "i_kmph"], as_index=False).agg(
        i_pings=("i_pings", "sum"), d_minutes=("d_minutes", "sum"),
        i_dist_m=("i_dist_m", "sum"))


_TRIP_SPEED_COLS = ["i_trip_no", "s_zone", "i_kmph", "i_pings", "d_minutes", "i_dist_m"]


def _trip_speed_frame(conn, ids: list[int]) -> pd.DataFrame:
    """Per-trip speed histogram: one row per (trip, zone, km/h).

    ~96k rows on this corpus against the rollup's 448k, and crucially it carries
    NO threshold — so one cached fetch answers every limit the user can pick,
    and the per-carrier report never touches SQL again when they move the
    slider.
    """
    if not ids:
        return pd.DataFrame(columns=_TRIP_SPEED_COLS)

    def _q() -> pd.DataFrame:
        df = _frame(conn, f"""
            SELECT i_trip_no, s_zone, i_kmph,
                   SUM(i_pings) i_pings, SUM(d_minutes) d_minutes, SUM(i_dist_m) i_dist_m
              FROM gps_speed_profile
             WHERE i_trip_no IN {_in_clause(ids)}
             GROUP BY i_trip_no, s_zone, i_kmph""")
        if df.empty:
            return pd.DataFrame(columns=_TRIP_SPEED_COLS)
        for c in ("i_trip_no", "i_kmph", "i_pings", "i_dist_m"):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
        df["d_minutes"] = pd.to_numeric(df["d_minutes"], errors="coerce").fillna(0.0)
        return df

    return _cached(_scope_key(ids, "tripspeed"), _q)


def _gps_trips(conn, ids: list[int]) -> int:
    """Trips that produced at least one usable ping — off the cached frame."""
    df = _trip_speed_frame(conn, ids)
    return 0 if df.empty else int(df["i_trip_no"].nunique())


def _percentile_kmph(hist: pd.DataFrame, q: float) -> float | None:
    """Time-weighted percentile speed over the MOVING part of the histogram.

    Time-weighted, not ping-weighted: a tracker that pings twice a minute while
    stationary and once a minute on the move would otherwise pull every
    percentile downwards purely as an artefact of its cadence.
    """
    m = hist[hist["i_kmph"] >= MOVING_FLOOR_KMPH].sort_values("i_kmph")
    if m.empty or m["d_minutes"].sum() <= 0:
        return None
    cum = m["d_minutes"].cumsum() / m["d_minutes"].sum()
    hit = m.loc[cum >= q, "i_kmph"]
    return float(hit.iloc[0]) if len(hit) else float(m["i_kmph"].iloc[-1])


_EMPTY_ZONE = {
    "pings": 0, "minutes": 0.0, "dist_km": 0.0, "moving_minutes": 0.0,
    "avg_moving_kmph": None, "p50_kmph": None, "p85_kmph": None, "p95_kmph": None,
    "max_kmph": None, "over_minutes": 0.0, "over_dist_km": 0.0, "over_pings": 0,
    "over_pct_of_moving_time": None, "histogram": [],
}


def _zone_block(hist: pd.DataFrame, limit: int) -> dict:
    """One zone's speed picture, judged against `limit`. All figures exact."""
    if hist.empty:
        return {"limit_kmph": limit, **_EMPTY_ZONE}

    moving = hist[hist["i_kmph"] >= MOVING_FLOOR_KMPH]
    moving_min = float(moving["d_minutes"].sum())
    over = hist[hist["i_kmph"] > limit]
    over_min = float(over["d_minutes"].sum())
    wsum = float((moving["i_kmph"] * moving["d_minutes"]).sum())

    return {
        "limit_kmph": limit,
        "pings": int(hist["i_pings"].sum()),
        "minutes": _rnd(hist["d_minutes"].sum(), 1),
        "dist_km": _rnd(hist["i_dist_m"].sum() / 1000.0, 1),
        "moving_minutes": _rnd(moving_min, 1),
        "avg_moving_kmph": _rnd(wsum / moving_min, 1) if moving_min else None,
        "p50_kmph": _percentile_kmph(hist, 0.50),
        "p85_kmph": _percentile_kmph(hist, 0.85),
        "p95_kmph": _percentile_kmph(hist, 0.95),
        "max_kmph": int(hist["i_kmph"].max()),
        "over_minutes": _rnd(over_min, 1),
        "over_dist_km": _rnd(over["i_dist_m"].sum() / 1000.0, 1),
        "over_pings": int(over["i_pings"].sum()),
        # Share of DRIVING time spent over the limit — the honest denominator.
        # Against total time it would shrink towards zero for any carrier whose
        # trucks wait a lot, which would reward detention.
        "over_pct_of_moving_time": _rnd(100 * over_min / moving_min, 1) if moving_min else None,
        "histogram": [
            {"kmph": int(r.i_kmph), "pings": int(r.i_pings),
             "minutes": _rnd(r.d_minutes, 1), "over": bool(r.i_kmph > limit)}
            for r in hist.sort_values("i_kmph").itertuples()
        ],
    }


def _collapse(hist: pd.DataFrame, zone: str) -> pd.DataFrame:
    """One zone's slice of the already-aggregated histogram."""
    cols = ["i_kmph", "i_pings", "d_minutes", "i_dist_m"]
    if hist.empty:
        return pd.DataFrame(columns=cols)
    return hist[hist["s_zone"] == zone][cols]


# ---------------------------------------------------------------------------
# Episode register
# ---------------------------------------------------------------------------

def _events_rows(conn, ids: list[int], road_limit: int, plant_limit: int) -> pd.DataFrame:
    """Episodes whose peak breaches the chosen limit, joined to trip identity.

    The peak filter is what makes a register built at the detection floor answer
    a higher limit: an episode peaking at 63 contains driving above 60, one
    peaking at 52 does not. Durations on these rows remain floor-measured — see
    the module docstring for which figure may be read from where.
    """
    if not ids:
        return pd.DataFrame()
    return _cached(_scope_key(ids, "events", road_limit, plant_limit),
                   lambda: _events_rows_uncached(conn, ids, road_limit, plant_limit))


def _events_rows_uncached(conn, ids: list[int], road_limit: int,
                          plant_limit: int) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT e.i_trip_no, e.s_zone, e.i_floor_kmph, e.dt_start, e.dt_end,
                   e.i_peak_kmph, e.d_avg_kmph, e.d_minutes, e.i_dist_m, e.i_pings,
                   e.d_lat, e.d_long,
                   t.s_trans_name, t.s_asset_id, t.s_driver_name,
                   t.s_org_node_name, t.s_dest_node_name
              FROM gps_speed_events e
              JOIN tta_trips t ON t.i_trip_no = e.i_trip_no
             WHERE e.i_trip_no IN {_in_clause(ids)}
               AND ((e.s_zone = 'road'  AND e.i_peak_kmph > %s)
                 OR (e.s_zone = 'plant' AND e.i_peak_kmph > %s))""",
            (road_limit, plant_limit))
        rows = cur.fetchall()
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["dt_start"] = pd.to_datetime(df["dt_start"], errors="coerce")
    df["dt_end"] = pd.to_datetime(df["dt_end"], errors="coerce")
    for c in ("d_minutes", "d_avg_kmph", "d_lat", "d_long"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("i_peak_kmph", "i_dist_m", "i_pings", "i_floor_kmph"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
    df["date"] = df["dt_start"].dt.strftime("%Y-%m-%d")
    return df


# A trip that runs longer than this is treated as a data error rather than a
# three-week haul: an unclosed trip with a stale arrival stamp would otherwise
# be counted as "on the road" for every day in between and quietly inflate the
# exposure of every day it touches.
MAX_TRIP_DAYS = 14


def _daily_exposure(trips: pd.DataFrame) -> pd.DataFrame:
    """Trips and vehicles ON THE ROAD on each calendar day.

    Why not simply count departures. A violation belongs to the day it happened,
    but the trip that produced it may have left three days earlier -- so dividing
    violations by that day's DEPARTURES asks a question the two numbers do not
    share. On this corpus that is not a rounding error: the ETL fetches discrete
    windows, so there are days with a thousand violations and no departure at
    all, and the resulting rate is either infinite or absurd.

    Counting the trips actually running that day makes the denominator the same
    population as the numerator. A trip runs from its departure to its arrival
    (falling back to closure, then to a single day when neither is recorded --
    an unfinished trip is only credited to the day it left, which understates
    exposure rather than inventing it).
    """
    cols = ["date", "active_trips", "active_vehicles", "departures"]
    if trips.empty or trips["dept_dt"].isna().all():
        return pd.DataFrame(columns=cols)

    t = trips.dropna(subset=["dept_dt"]).copy()
    end = t["ata_dt"]
    if "closing_dt" in t.columns:
        end = end.fillna(t["closing_dt"])
    end = end.fillna(t["dept_dt"])
    # A negative or absurd span is a bad stamp, not a long trip.
    end = end.where(end >= t["dept_dt"], t["dept_dt"])
    end = end.combine(t["dept_dt"] + pd.Timedelta(days=MAX_TRIP_DAYS), min)

    rows = []
    for trip_id, veh, a, b in zip(t["trip_id"], t["vehicle_no"],
                                  t["dept_dt"].dt.normalize(), end.dt.normalize()):
        for d in pd.date_range(a, b, freq="D"):
            rows.append((d.strftime("%Y-%m-%d"), trip_id, veh))
    if not rows:
        return pd.DataFrame(columns=cols)

    span = pd.DataFrame(rows, columns=["date", "trip_id", "vehicle_no"])
    out = span.groupby("date").agg(
        active_trips=("trip_id", "nunique"),
        active_vehicles=("vehicle_no", "nunique"),
    ).reset_index()
    dep = (t.dropna(subset=["dept_date"]).groupby("dept_date")["trip_id"].count()
            .rename("departures").reset_index().rename(columns={"dept_date": "date"}))
    return out.merge(dep, on="date", how="outer").fillna({"departures": 0})


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def speed_overview(conn, f: dict, road_limit: int | None = None,
                   plant_limit: int | None = None) -> dict:
    """In-plant vs on-road speed, judged against caller-chosen limits."""
    road_limit = validate_limit("road", road_limit)
    plant_limit = validate_limit("plant", plant_limit)

    trips = _scope(conn, f)
    ids = _ids(trips)
    hist = _zone_histogram(conn, ids)
    ev = _events_rows(conn, ids, road_limit, plant_limit)

    # What "inside plant" actually covers, reported rather than assumed. The
    # fences are 10 km circles round published plant coordinates because that is
    # the radius the business specified, so this zone is "within 10 km of the
    # works" -- an industrial belt with public road in it, not the far side of
    # the gate. A works speed limit read against it will therefore flag ordinary
    # road driving near the plant, and a screen that does not say so invites the
    # wrong conversation with a carrier.
    plant_fences = sp.plant_fences(conn)
    fences = {
        "count": len(plant_fences),
        "names": sorted({f.name for f in plant_fences}),
        "radius_km": _rnd(max((f.radius_m for f in plant_fences), default=0) / 1000.0, 1),
    }

    zones = {}
    for zone, limit in (("plant", plant_limit), ("road", road_limit)):
        block = _zone_block(_collapse(hist, zone), limit)
        block["label"] = ZONE_LABEL[zone]
        block["detection_floor_kmph"] = sp.EVENT_FLOORS[zone]
        block["fences"] = fences if zone == "plant" else None
        zone_ev = ev[ev["s_zone"] == zone] if not ev.empty else pd.DataFrame()
        block["episodes"] = int(len(zone_ev))
        block["episode_trips"] = int(zone_ev["i_trip_no"].nunique()) if len(zone_ev) else 0
        block["episode_vehicles"] = int(zone_ev["s_asset_id"].nunique()) if len(zone_ev) else 0
        block["worst_peak_kmph"] = int(zone_ev["i_peak_kmph"].max()) if len(zone_ev) else None
        zones[zone] = block

    # Trips that produced GPS at all — the denominator every rate below uses.
    # A trip with no ping cannot be judged on speed, and counting it as clean
    # would reward a dead tracker.
    measured = _gps_trips(conn, ids)
    # Days on which something could have been observed: a day with a departure,
    # or a day a violation landed on. Departure dates alone under-count badly
    # here -- the ETL fetches discrete windows, so 2.5k trips leave on 20 dates
    # but run across 30 -- and dividing by 20 would inflate "violations per day"
    # by half against the daily chart on the same screen.
    exposure = _daily_exposure(trips)
    active_days = set(exposure["date"]) if not exposure.empty else set()
    if not ev.empty:
        active_days |= set(ev["date"].dropna())
    days = len(active_days)
    observed_max = max((z["max_kmph"] or 0) for z in zones.values()) or None

    return {
        "limits": {
            "road": road_limit, "plant": plant_limit,
            "road_options": list(sp.ROAD_LIMITS), "plant_options": list(sp.PLANT_LIMITS),
            "road_default": sp.DEFAULT_ROAD_LIMIT, "plant_default": sp.DEFAULT_PLANT_LIMIT,
        },
        "coverage": {
            "trips_in_filter": int(len(trips)),
            "trips_with_gps": measured,
            "gps_coverage_pct": _rnd(100 * measured / len(trips), 1) if len(trips) else None,
            "days": days,
            # The fastest ping anywhere in scope. A limit above this yields zero
            # violations by construction, and a screen that does not say so
            # reads as "this fleet is safe" when it means "you set the bar
            # higher than any truck can physically go".
            "observed_max_kmph": observed_max,
        },
        "zones": zones,
        "totals": {
            "episodes": int(len(ev)),
            "episodes_per_day": _rnd(len(ev) / days, 1) if days else None,
            "episodes_per_100_trips": _rnd(100 * len(ev) / measured, 1) if measured else None,
            "over_minutes": _rnd(sum(z["over_minutes"] or 0 for z in zones.values()), 1),
            "over_dist_km": _rnd(sum(z["over_dist_km"] or 0 for z in zones.values()), 1),
        },
        "build": sp.build_status(conn),
    }


def safety_daily(conn, f: dict, road_limit: int | None = None,
                 plant_limit: int | None = None) -> dict:
    """One row per calendar day: violations, and the exposure behind them.

    A raw daily count is not comparable across days — 12 violations on a
    400-trip day is a better day than 6 on a 50-trip day. Every row therefore
    carries the day's trip and vehicle counts and a normalised rate, so the
    trend line reads as safety rather than as dispatch volume.

    Dated by when the violation happened (the episode's own start), not by when
    its trip departed: a trip that leaves on Monday and speeds on Wednesday is a
    Wednesday event, and rolling it back to Monday would smear every multi-day
    lane across the chart.
    """
    road_limit = validate_limit("road", road_limit)
    plant_limit = validate_limit("plant", plant_limit)

    trips = _scope(conn, f)
    ids = _ids(trips)
    ev = _events_rows(conn, ids, road_limit, plant_limit)

    base = _daily_exposure(trips)

    if ev.empty:
        daily = base.assign(episodes=0, plant_episodes=0, road_episodes=0,
                            episode_minutes=0.0, episode_dist_km=0.0,
                            offending_vehicles=0, offending_trips=0,
                            worst_peak_kmph=None)
    else:
        agg = ev.groupby("date").agg(
            episodes=("i_trip_no", "size"),
            episode_minutes=("d_minutes", "sum"),
            episode_dist_m=("i_dist_m", "sum"),
            offending_vehicles=("s_asset_id", "nunique"),
            offending_trips=("i_trip_no", "nunique"),
            worst_peak_kmph=("i_peak_kmph", "max"),
        ).reset_index()
        by_zone = ev.pivot_table(index="date", columns="s_zone", values="i_trip_no",
                                 aggfunc="size").reset_index()
        for z in ("plant", "road"):
            if z not in by_zone.columns:
                by_zone[z] = 0
        by_zone = by_zone.rename(columns={"plant": "plant_episodes", "road": "road_episodes"})
        # Outer merge: a day carrying violations from trips that left before the
        # window is real and must not vanish from a safety trend.
        daily = base.merge(agg, on="date", how="outer").merge(
            by_zone[["date", "plant_episodes", "road_episodes"]], on="date", how="left")
        daily["episode_dist_km"] = daily["episode_dist_m"].fillna(0) / 1000.0
        daily = daily.drop(columns=["episode_dist_m"])

    for c in ("active_trips", "active_vehicles", "departures", "episodes",
              "plant_episodes", "road_episodes", "offending_vehicles", "offending_trips"):
        if c in daily.columns:
            daily[c] = pd.to_numeric(daily[c], errors="coerce").fillna(0).astype(int)
    for c in ("episode_minutes", "episode_dist_km"):
        daily[c] = pd.to_numeric(daily.get(c), errors="coerce").fillna(0.0).round(1)

    # Rated against the trips actually on the road that day, so numerator and
    # denominator describe the same population.
    daily["episodes_per_100_trips"] = np.where(
        daily["active_trips"] > 0,
        (100 * daily["episodes"] / daily["active_trips"]).round(1), np.nan)
    daily["episodes_per_vehicle"] = np.where(
        daily["active_vehicles"] > 0,
        (daily["episodes"] / daily["active_vehicles"]).round(2), np.nan)
    daily = daily.sort_values("date").reset_index(drop=True)

    n_days = len(daily)
    return {
        "limits": {"road": road_limit, "plant": plant_limit,
                   "detection_floors": sp.EVENT_FLOORS},
        "series": _nulls(daily),
        "summary": {
            "days": n_days,
            "episodes": int(daily["episodes"].sum()) if n_days else 0,
            "avg_per_day": _rnd(daily["episodes"].mean(), 1) if n_days else None,
            "worst_day": (str(daily.loc[daily["episodes"].idxmax(), "date"])
                          if n_days and daily["episodes"].max() > 0 else None),
            "worst_day_episodes": int(daily["episodes"].max()) if n_days else 0,
            "clean_days": int((daily["episodes"] == 0).sum()) if n_days else 0,
            "episode_minutes": _rnd(daily["episode_minutes"].sum(), 1) if n_days else 0,
            "episode_dist_km": _rnd(daily["episode_dist_km"].sum(), 1) if n_days else 0,
            "offending_vehicles": int(ev["s_asset_id"].nunique()) if not ev.empty else 0,
        },
        "worst_offenders": _offenders(ev),
    }


def _offenders(ev: pd.DataFrame, top: int = 15) -> list[dict]:
    """Vehicles ranked by violation episodes, worst first."""
    if ev.empty:
        return []
    g = ev.groupby(["s_asset_id", "s_trans_name"]).agg(
        episodes=("i_trip_no", "size"),
        trips=("i_trip_no", "nunique"),
        episode_minutes=("d_minutes", "sum"),
        peak_kmph=("i_peak_kmph", "max"),
        plant_episodes=("s_zone", lambda s: int((s == "plant").sum())),
    ).reset_index().rename(columns={"s_asset_id": "vehicle_no",
                                    "s_trans_name": "transporter"})
    g["episode_minutes"] = g["episode_minutes"].round(1)
    g = g.sort_values(["episodes", "peak_kmph"], ascending=False).head(top)
    return _nulls(g)


def events(conn, f: dict, road_limit: int | None = None, plant_limit: int | None = None,
           zone: str | None = None, transporter: str | None = None,
           limit_rows: int = 300) -> dict:
    """The violation register itself — worst peak first, for drill-down."""
    road_limit = validate_limit("road", road_limit)
    plant_limit = validate_limit("plant", plant_limit)
    ev = _events_rows(conn, _ids(_scope(conn, f)), road_limit, plant_limit)
    meta = {"limits": {"road": road_limit, "plant": plant_limit},
            "detection_floors": sp.EVENT_FLOORS}
    if ev.empty:
        return {**meta, "total": 0, "returned": 0, "rows": []}
    if zone in ("plant", "road"):
        ev = ev[ev["s_zone"] == zone]
    if transporter:
        ev = ev[ev["s_trans_name"] == transporter]
    total = len(ev)
    ev = ev.sort_values(["i_peak_kmph", "d_minutes"], ascending=False).head(
        max(1, min(limit_rows, 2000))).copy()
    ev["dist_km"] = (ev["i_dist_m"] / 1000.0).round(1)
    ev["start"] = ev["dt_start"].dt.strftime("%Y-%m-%d %H:%M")
    ev["end"] = ev["dt_end"].dt.strftime("%Y-%m-%d %H:%M")
    out = ev.rename(columns={
        "i_trip_no": "trip_id", "s_zone": "zone", "i_floor_kmph": "detected_above_kmph",
        "i_peak_kmph": "peak_kmph", "d_avg_kmph": "avg_kmph",
        # Named for what it is: the span of continuous running above the
        # DETECTION FLOOR that contains this excursion, not time above the
        # selected limit. Exact over-limit time is on /overview.
        "d_minutes": "episode_minutes",
        "i_pings": "pings", "s_trans_name": "transporter", "s_asset_id": "vehicle_no",
        "s_driver_name": "driver_name", "s_org_node_name": "origin",
        "s_dest_node_name": "destination", "d_lat": "lat", "d_long": "lon",
    })[["trip_id", "transporter", "vehicle_no", "driver_name", "origin", "destination",
        "zone", "detected_above_kmph", "peak_kmph", "avg_kmph", "episode_minutes",
        "dist_km", "pings", "start", "end", "lat", "lon", "date"]]
    return {**meta, "total": total, "returned": int(len(out)), "rows": _nulls(out)}


# ---------------------------------------------------------------------------
# 24-hour running pattern
# ---------------------------------------------------------------------------

def running_pattern(conn, f: dict, transporter: str | None = None) -> dict:
    """What the fleet is doing at each hour of the clock.

    Not "how many trips departed at 03:00" — that is the departure rhythm, a
    different question answered on the Heatmaps page. This is what the trucks
    already on the road are DOING at 03:00: moving, parked, or inside a plant.
    Moving and stopped partition the hour, so the chart reads as a utilisation
    budget rather than as three unrelated lines.
    """
    trips = _scope(conn, f)
    if transporter:
        trips = trips[trips["transporter"] == transporter]
    ids = _ids(trips)
    if not ids:
        return {"transporter": transporter, "trips": 0, "hours": [], "summary": {}}

    # Moving vs stopped is read off the speed itself: in this corpus is_moving
    # is 0 on exactly the pings whose speed is 0, so the histogram carries the
    # same information without a second column to keep in step.
    #
    # No JOIN to tta_trips here, deliberately. The only thing the join provided
    # was COUNT(DISTINCT s_asset_id) per hour, and paying for it inside the
    # aggregate took this query from 1.4 s to 5.8 s. The distinct (hour, trip)
    # pairs cost 0.8 s on their own, and the trip -> vehicle mapping is already
    # in the filtered frame -- so the count is finished in pandas, off the same
    # rows the rest of the page is filtered by.
    g = _cached(_scope_key(ids, "hours"), lambda: _frame(conn, f"""
        SELECT i_hour,
               SUM(CASE WHEN i_kmph >= {MOVING_FLOOR_KMPH} THEN d_minutes ELSE 0 END) moving_min,
               SUM(CASE WHEN i_kmph >= {MOVING_FLOOR_KMPH} THEN 0 ELSE d_minutes END) stopped_min,
               SUM(CASE WHEN s_zone = 'plant' THEN d_minutes ELSE 0 END) plant_min,
               SUM(CASE WHEN i_kmph >= {MOVING_FLOOR_KMPH}
                        THEN i_kmph * d_minutes ELSE 0 END) kmh_min,
               SUM(i_dist_m) dist_m, MAX(i_kmph) max_kmph, SUM(i_pings) pings,
               COUNT(DISTINCT i_trip_no) trips
          FROM gps_speed_profile
         WHERE i_trip_no IN {_in_clause(ids)}
         GROUP BY i_hour"""))
    if g.empty:
        return {"transporter": transporter, "trips": len(ids), "hours": [], "summary": {}}
    for c in ("moving_min", "stopped_min", "plant_min", "kmh_min", "dist_m"):
        g[c] = pd.to_numeric(g[c], errors="coerce").fillna(0.0)
    g = g.set_index(pd.to_numeric(g["i_hour"], errors="coerce").astype(int))

    pairs = _cached(_scope_key(ids, "hourtrips"), lambda: _frame(
        conn, f"""SELECT DISTINCT i_hour, i_trip_no
                    FROM gps_speed_profile
                   WHERE i_trip_no IN {_in_clause(ids)}"""))
    if pairs.empty:
        veh = pd.Series(dtype="int64")
    else:
        pairs["vehicle_no"] = pd.to_numeric(pairs["i_trip_no"], errors="coerce").map(
            trips.set_index("trip_id")["vehicle_no"])
        veh = pairs.groupby(pd.to_numeric(pairs["i_hour"], errors="coerce").astype(int)
                            )["vehicle_no"].nunique()

    hours = []
    for h in range(24):
        r = g.loc[h] if h in g.index else None
        mv = float(r["moving_min"]) if r is not None else 0.0
        st = float(r["stopped_min"]) if r is not None else 0.0
        pl = float(r["plant_min"]) if r is not None else 0.0
        total = mv + st
        hours.append({
            "hour": h,
            "label": f"{h:02d}:00",
            "moving_hours": _rnd(mv / 60, 1),
            "stopped_hours": _rnd(st / 60, 1),
            # In-plant time is a slice of the same wall clock as moving/stopped,
            # not a fourth bucket — a truck creeping through the works is both
            # moving and in-plant. Kept separate so it is never double-counted.
            "in_plant_hours": _rnd(pl / 60, 1),
            "running_pct": _rnd(100 * mv / total, 1) if total else None,
            "in_plant_pct": _rnd(100 * pl / total, 1) if total else None,
            "dist_km": _rnd(float(r["dist_m"]) / 1000.0, 1) if r is not None else 0.0,
            # Time-weighted mean speed while actually moving in this hour.
            "avg_kmph": _rnd(float(r["kmh_min"]) / mv, 1) if r is not None and mv else None,
            "max_kmph": int(r["max_kmph"]) if r is not None else None,
            "vehicles": int(veh.get(h, 0)),
            "trips": int(r["trips"]) if r is not None else 0,
        })

    live = [h for h in hours if h["running_pct"] is not None]
    peak = max(live, key=lambda h: h["running_pct"]) if live else None
    quiet = min(live, key=lambda h: h["running_pct"]) if live else None
    night_mv = sum(h["moving_hours"] or 0 for h in hours
                   if h["hour"] >= NIGHT_START_HOUR or h["hour"] < NIGHT_END_HOUR)
    all_mv = sum(h["moving_hours"] or 0 for h in hours)

    return {
        "transporter": transporter,
        "trips": len(ids),
        "hours": hours,
        "summary": {
            "peak_hour": peak["label"] if peak else None,
            "peak_running_pct": peak["running_pct"] if peak else None,
            "quiet_hour": quiet["label"] if quiet else None,
            "quiet_running_pct": quiet["running_pct"] if quiet else None,
            "avg_running_pct": _rnd(np.mean([h["running_pct"] for h in live]), 1) if live else None,
            "total_moving_hours": _rnd(all_mv, 0),
            "total_stopped_hours": _rnd(sum(h["stopped_hours"] or 0 for h in hours), 0),
            "total_in_plant_hours": _rnd(sum(h["in_plant_hours"] or 0 for h in hours), 0),
            "night_share_pct": _rnd(100 * night_mv / all_mv, 1) if all_mv else None,
            "night_moving_hours": _rnd(night_mv, 0),
            "night_window": f"{NIGHT_START_HOUR:02d}:00-{NIGHT_END_HOUR:02d}:00",
        },
    }


# ---------------------------------------------------------------------------
# Per-carrier safety
# ---------------------------------------------------------------------------

def carrier_safety(conn, f: dict, road_limit: int | None = None,
                   plant_limit: int | None = None, min_trips: int = 1,
                   only: str | None = None) -> dict:
    """One safety row per transporter, or for a single carrier when `only` is set.

    Rates are per 100 trips **with GPS**, not per 100 trips. A carrier whose
    trackers are dark cannot be shown as the safest fleet on the page simply
    because nothing was recorded against it — its GPS coverage is reported
    alongside so a thin denominator is visible rather than flattering.
    """
    road_limit = validate_limit("road", road_limit)
    plant_limit = validate_limit("plant", plant_limit)

    trips = _scope(conn, f)
    if only:
        trips = trips[trips["transporter"] == only]
    ids = _ids(trips)
    ev = _events_rows(conn, ids, road_limit, plant_limit)

    exposure = (trips.dropna(subset=["transporter"]).groupby("transporter")
                .agg(trips=("trip_id", "count"), vehicles=("vehicle_no", "nunique"),
                     days=("dept_date", "nunique")).reset_index())
    if exposure.empty:
        return {"limits": {"road": road_limit, "plant": plant_limit},
                "min_trips": min_trips, "rows": []}

    # Per-carrier speed aggregates, grouped in SQL against the rollup index.
    # The carrier name comes from the JOIN rather than from a pandas map, so a
    # 450k-row histogram never crosses the wire to answer a 40-row table.
    # The SQL here is deliberately LIMIT-INDEPENDENT: it fetches the per-trip
    # speed histogram and every threshold is then applied in pandas.
    #
    # That is the whole point of this screen -- the user moves the limit and
    # expects the page to follow. With the limits baked into the SQL, every
    # move invalidated the cache and cost a fresh ~450k-row scan (measured 12 s
    # across the page). Limit-free, the scan is cached once per filter and a
    # limit change is a boolean mask over 96k rows: milliseconds.
    #
    # Grouping by trip rather than by transporter also avoids a join to
    # tta_trips (1.2 s), and keeps carrier attribution identical to every other
    # screen because it reuses the filtered frame's own mapping.
    per_trip = _trip_speed_frame(conn, ids)

    agg_cols = ["gps_trips", "max_kmph", "moving_minutes", "kmh_minutes",
                "plant_minutes", "over_minutes", "over_dist_m"]
    if per_trip.empty:
        prof = pd.DataFrame(columns=["transporter", *agg_cols])
    else:
        d = per_trip.assign(
            transporter=per_trip["i_trip_no"].map(trips.set_index("trip_id")["transporter"]))
        d = d.dropna(subset=["transporter"])
        moving = d["i_kmph"] >= MOVING_FLOOR_KMPH
        over = (((d["s_zone"] == "road") & (d["i_kmph"] > road_limit))
                | ((d["s_zone"] == "plant") & (d["i_kmph"] > plant_limit)))
        d = d.assign(
            moving_minutes=np.where(moving, d["d_minutes"], 0.0),
            kmh_minutes=np.where(moving, d["i_kmph"] * d["d_minutes"], 0.0),
            plant_minutes=np.where(d["s_zone"] == "plant", d["d_minutes"], 0.0),
            over_minutes=np.where(over, d["d_minutes"], 0.0),
            over_dist_m=np.where(over, d["i_dist_m"], 0.0),
        )
        prof = d.groupby("transporter").agg(
            gps_trips=("i_trip_no", "nunique"),
            max_kmph=("i_kmph", "max"),
            moving_minutes=("moving_minutes", "sum"),
            kmh_minutes=("kmh_minutes", "sum"),
            plant_minutes=("plant_minutes", "sum"),
            over_minutes=("over_minutes", "sum"),
            over_dist_m=("over_dist_m", "sum"),
        ).reset_index()
    for c in agg_cols:
        prof[c] = pd.to_numeric(prof[c], errors="coerce").fillna(0.0)
    prof["avg_moving_kmph"] = (prof["kmh_minutes"]
                               / prof["moving_minutes"].replace(0, np.nan)).round(1)

    if not ev.empty:
        e = ev.groupby("s_trans_name").agg(
            episodes=("i_trip_no", "size"),
            road_episodes=("s_zone", lambda s: int((s == "road").sum())),
            plant_episodes=("s_zone", lambda s: int((s == "plant").sum())),
            peak_kmph=("i_peak_kmph", "max"),
            offending_vehicles=("s_asset_id", "nunique"),
            offending_trips=("i_trip_no", "nunique"),
        ).reset_index().rename(columns={"s_trans_name": "transporter"})
    else:
        e = pd.DataFrame(columns=["transporter", "episodes", "road_episodes",
                                  "plant_episodes", "peak_kmph",
                                  "offending_vehicles", "offending_trips"])

    out = (exposure.merge(e, on="transporter", how="left")
                   .merge(prof.drop(columns=["kmh_minutes"]), on="transporter", how="left"))

    for c in ("episodes", "road_episodes", "plant_episodes", "offending_vehicles",
              "offending_trips", "gps_trips"):
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0).astype(int)
    out["over_minutes"] = pd.to_numeric(out["over_minutes"], errors="coerce").fillna(0).round(1)
    out["over_dist_km"] = (pd.to_numeric(out["over_dist_m"], errors="coerce")
                           .fillna(0) / 1000.0).round(1)
    out["moving_hours"] = (pd.to_numeric(out["moving_minutes"], errors="coerce")
                           .fillna(0) / 60.0).round(1)
    out["plant_hours"] = (pd.to_numeric(out["plant_minutes"], errors="coerce")
                          .fillna(0) / 60.0).round(1)
    mv = pd.to_numeric(out["moving_minutes"], errors="coerce").replace(0, np.nan)
    out["over_pct_of_moving_time"] = (100 * out["over_minutes"] / mv).round(1)
    out = out.drop(columns=["over_dist_m", "moving_minutes", "plant_minutes"])

    den = out["gps_trips"].replace(0, np.nan)
    out["episodes_per_100_trips"] = (100 * out["episodes"] / den).round(1)
    out["episodes_per_day"] = (out["episodes"] / out["days"].replace(0, np.nan)).round(2)
    out["gps_coverage_pct"] = (100 * out["gps_trips"] / out["trips"].replace(0, np.nan)).round(1)
    out = out[out["trips"] >= max(1, min_trips)]
    out = out.sort_values(["episodes_per_100_trips", "episodes"], ascending=False)

    return {
        "limits": {"road": road_limit, "plant": plant_limit,
                   "detection_floors": sp.EVENT_FLOORS},
        "min_trips": min_trips,
        "rows": _nulls(out),
    }
