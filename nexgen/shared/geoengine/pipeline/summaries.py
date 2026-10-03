"""Per-run rollups: what the application pages read.

Built in two steps from a run's per-trip ledger:

1. **The physical ledger** (pipeline/physical.py) -- geo_pvisit, geo_palert,
   geo_pstop. One trip per consignment means a truck's GPS is copied into
   every trip it carries, so the same stay appears once per invoice. These
   tables hold each real event once, with the trips that saw it; and
   geo_trip_share gives each event and kilometre to exactly one trip, so
   totals over a vehicle, transporter, driver or lane add up shares.

2. **The rollups**, from the physical ledger:

       geo_fence_stats   one row per fence   -- the geofence list and its header
       geo_fence_day     fence x day         -- a fence's activity over time
       geo_day_summary   one row per day     -- the day summary page

Rules that make the numbers mean something:

* **A stay is counted once**, however many consignment trips saw it; trip
  counts still count trips.
* **Dwell percentiles use only fully observed visits** -- entry seen, exit
  seen. A visit open at the end of the trail, or already in progress when it
  began, has a lower bound for a dwell, and mixing lower bounds into a median
  drags it down silently. Such visits are counted, and their observed time is
  in the totals, but they never vote on "typical dwell".
* **Day totals never double count nesting.** A truck inside a gate zone inside
  a works is one truck at one place. Facility *visits* per day count primary
  (innermost) visits; facility *time* per day is the union of each vehicle's
  facility-scale visit intervals (physical.facility_places), because the
  works visit is never primary while the truck is in the gate zone, yet the
  truck is still at the works after it leaves the gate zone. A fence's own
  page counts every visit to that fence, nested or not, because the question
  there is "who was inside this".
* **Dwell is split across midnight.** A visit from 22:00 to 04:00 puts two
  hours on one day and four on the next.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import numpy as np

from nexgen.shared.geoengine.config import settings
from nexgen.shared.geoengine.db import geo_session
from nexgen.shared.geoengine.pipeline import phases
from nexgen.shared.geoengine.pipeline import physical as P
from nexgen.shared.geoengine.prep import codec

logger = logging.getLogger(__name__)

FACILITY = ("micro", "site", "campus")
ROLLUPS = ("geo_fence_stats", "geo_fence_day", "geo_day_summary")
PHYSICAL = ("geo_pvisit", "geo_palert", "geo_pstop", "geo_trip_share")


def _pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    return int(round(float(np.percentile(values, q))))


def _day_spans(start: datetime, end: datetime):
    """(date, seconds) for each calendar day the interval covers."""
    if end <= start:
        return
    cur = start
    while cur < end:
        nxt = datetime.combine(cur.date() + timedelta(days=1), datetime.min.time())
        stop = min(nxt, end)
        yield cur.date(), (stop - cur).total_seconds()
        cur = stop


def _end(v: dict) -> datetime:
    return v["dt_exit"] or v["dt_enter"] + timedelta(seconds=v["i_dwell_seconds"] or 0)


def _measured(v: dict) -> bool:
    return not v["b_open"] and bool(v["b_entry_observed"]) and v["dt_exit"] is not None


def _trips_text(trips: list[int]) -> str:
    text = ",".join(str(t) for t in trips)
    return text if len(text) <= 512 else text[:text.rfind(",", 0, 500)] + ",..."


def build(run_id: int) -> dict:
    t0 = time.perf_counter()
    with geo_session() as conn:
        with conn.cursor() as cur:
            for table in ROLLUPS + PHYSICAL:
                cur.execute(f"DELETE FROM {table} WHERE i_run_id=%s", (run_id,))
        conn.commit()

        meta = _load_meta(conn, run_id)
        pvisits = _physical_visits(conn, run_id, meta)
        palerts = _physical_alerts(conn, run_id, pvisits, meta)
        pstops = _physical_stops(conn, run_id, meta)
        with conn.cursor() as cur:
            cur.execute("""SELECT i_trip_no, s_asset_id, i_fence_id, dt_gap_from, dt_gap_to
                             FROM geo_inferred_visit WHERE i_run_id=%s""", (run_id,))
            inferred = list(cur.fetchall())
        blobs = _load_trails(conn, run_id)
        shares = _trip_shares(conn, run_id, meta, pvisits, palerts, pstops, inferred, blobs)

        n_fences = _fence_stats(conn, run_id, pvisits, palerts, pstops, inferred)
        n_fence_days = _fence_days(conn, run_id, pvisits)
        n_days = _day_summary(conn, run_id, pvisits, palerts, pstops, inferred, blobs)
        del blobs
        n_phases = phases.build(conn, run_id)

        with conn.cursor() as cur:
            cur.execute("UPDATE geo_run SET dt_summarised=%s WHERE i_run_id=%s",
                        (datetime.now(), run_id))
        conn.commit()
    bump_version()

    out = {"run_id": run_id, "physical_visits": len(pvisits), "physical_alerts": len(palerts),
           "physical_stops": len(pstops), "trip_shares": len(shares),
           "distance_km": round(sum(x["distance_m"] or 0 for x in shares.values()) / 1000, 1),
           "distance_km_summed_per_trip": round(sum(x["own_distance_m"] or 0 for x in shares.values()) / 1000, 1),
           "fences": n_fences, "fence_days": n_fence_days,
           "days": n_days, "trip_phases": n_phases, "seconds": round(time.perf_counter() - t0, 2)}
    logger.info("summaries built: %s", out)
    return out


# ---------------------------------------------------------------------------
# 1. the physical ledger
# ---------------------------------------------------------------------------

def bump_version() -> None:
    """Tell every API process its cached answers are stale (api/cache.py)."""
    try:
        with geo_session() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO geo_state (s_key, s_value) VALUES ('data_version', '1')
                           ON DUPLICATE KEY UPDATE s_value = CAST(s_value AS UNSIGNED) + 1""")
            conn.commit()
    except Exception as exc:                        # noqa: BLE001 -- before init-db made geo_state
        logger.warning("data version not bumped: %s", exc)


def _chunks(items, n: int = 1000):
    items = list(items)
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _select_trips(conn, sql: str, run_id: int, trips, col: str = "i_trip_no") -> list[dict]:
    """`sql` over the whole run, or over some of its trips, chunked."""
    if trips is None:
        with conn.cursor() as cur:
            cur.execute(sql, (run_id,))
            return list(cur.fetchall())
    out: list[dict] = []
    for chunk in _chunks(sorted(trips)):
        with conn.cursor() as cur:
            cur.execute(f"{sql} AND {col} IN ({','.join(['%s'] * len(chunk))})", (run_id, *chunk))
            out.extend(cur.fetchall())
    return out


def _load_meta(conn, run_id: int, trips=None) -> dict[int, dict]:
    rows = _select_trips(conn, """SELECT s.i_trip_no, m.s_trans_name, m.s_driver_name, m.s_origin, m.s_destination
                                    FROM geo_trip_summary s LEFT JOIN geo_trip_meta m ON m.i_trip_no = s.i_trip_no
                                   WHERE s.i_run_id=%s""", run_id, trips, "s.i_trip_no")
    return {r["i_trip_no"]: r for r in rows}


def _physical_visits(conn, run_id: int, meta: dict, trips=None) -> list[dict]:
    visits = _select_trips(conn, """
            SELECT i_trip_no, s_asset_id, i_fence_id, i_site_id, s_site_name, s_type, s_category,
                   s_scale, dt_enter, dt_exit, b_open, b_entry_observed, i_dwell_seconds, b_primary,
                   i_enter_gap_seconds, i_exit_gap_seconds, s_confirmed_by, i_max_speed
              FROM geo_visit WHERE i_run_id = %s""", run_id, trips)
    pvisits = P.physical_visits(visits, meta)
    rows = [(run_id, p["s_asset_id"], p["i_fence_id"], p["i_site_id"], p["s_site_name"], p["s_type"],
             p["s_category"], p["s_scale"], p["dt_enter"], p["dt_exit"], p["b_open"],
             p["b_entry_observed"], p["i_dwell_seconds"], p["b_primary"], len(p["trips"]),
             p["trips"][0], _trips_text(p["trips"]), p["s_trans_name"], p["s_driver_name"],
             p["i_enter_gap_seconds"], p["i_exit_gap_seconds"], p["s_confirmed_by"], p["i_max_speed"])
            for p in pvisits]
    with conn.cursor() as cur:
        for i in range(0, len(rows), 20_000):
            cur.executemany("""
                INSERT INTO geo_pvisit
                    (i_run_id,s_asset_id,i_fence_id,i_site_id,s_site_name,s_type,s_category,s_scale,
                     dt_enter,dt_exit,b_open,b_entry_observed,i_dwell_seconds,b_primary,i_trips,
                     i_trip_no,s_trips,s_trans_name,s_driver_name,i_enter_gap_seconds,
                     i_exit_gap_seconds,s_confirmed_by,i_max_speed)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                rows[i:i + 20_000])
    conn.commit()
    logger.info("physical visits: %s from %s per-trip copies", len(pvisits), len(visits))
    return pvisits


def _physical_alerts(conn, run_id: int, pvisits: list[dict], meta: dict, trips=None) -> list[dict]:
    violations = _select_trips(conn, """SELECT i_trip_no, s_asset_id, i_fence_id, i_site_id, s_site_name, s_kind,
                                               dt_event, d_lat, d_long, i_observed, i_limit, s_detail
                                          FROM geo_violation WHERE i_run_id=%s""", run_id, trips)
    palerts = P.physical_alerts(violations, pvisits, meta)
    rows = [(run_id, a["s_asset_id"], a["i_fence_id"], a["i_site_id"], a["s_site_name"], a["s_kind"],
             a["dt_event"], a["d_lat"], a["d_long"], a["i_observed"], a["i_limit"], a["s_detail"],
             len(a["trips"]), a["trips"][0], _trips_text(a["trips"]), a["s_trans_name"], a["s_driver_name"])
            for a in palerts]
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO geo_palert
                (i_run_id,s_asset_id,i_fence_id,i_site_id,s_site_name,s_kind,dt_event,d_lat,d_long,
                 i_observed,i_limit,s_detail,i_trips,i_trip_no,s_trips,s_trans_name,s_driver_name)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
    conn.commit()
    return palerts


def _physical_stops(conn, run_id: int, meta: dict, trips=None) -> list[dict]:
    stops = _select_trips(conn, """SELECT i_trip_no, s_asset_id, dt_start, dt_end, i_duration_s, i_pings, d_lat,
                                          d_long, d_p90_spread_m, i_fence_id, i_site_id, s_site_name, s_scale
                                     FROM geo_stop WHERE i_run_id=%s""", run_id, trips)
    pstops = P.physical_stops(stops, meta)
    rows = [(run_id, s["s_asset_id"], s["dt_start"], s["dt_end"], s["i_duration_s"], s["i_pings"],
             s["d_lat"], s["d_long"], s["d_p90_spread_m"], s["i_fence_id"], s["i_site_id"],
             s["s_site_name"], s["s_scale"], len(s["trips"]), s["trips"][0], _trips_text(s["trips"]),
             s["s_trans_name"], s["s_origin"], s["s_destination"])
            for s in pstops]
    with conn.cursor() as cur:
        for i in range(0, len(rows), 20_000):
            cur.executemany("""
                INSERT INTO geo_pstop
                    (i_run_id,s_asset_id,dt_start,dt_end,i_duration_s,i_pings,d_lat,d_long,d_p90_spread_m,
                     i_fence_id,i_site_id,s_site_name,s_scale,i_trips,i_trip_no,s_trips,s_trans_name,
                     s_origin,s_destination)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows[i:i + 20_000])
    conn.commit()
    return pstops


def _load_trails(conn, run_id: int, trips=None) -> list[dict]:
    """The run's stored fitted trails, still compressed, with their vehicle."""
    return _select_trips(conn, """SELECT f.i_trip_no, f.m_data,
                                         COALESCE(t.s_asset_id, CONCAT('trip:', f.i_trip_no)) asset
                                    FROM geo_fit_trail f LEFT JOIN geo_trip t ON t.i_trip_no = f.i_trip_no
                                   WHERE f.i_run_id=%s""", run_id, trips, "f.i_trip_no")


def _trip_shares(conn, run_id: int, meta: dict, pvisits: list[dict], palerts: list[dict],
                 pstops: list[dict], inferred: list[dict], blobs: list[dict]) -> dict[int, dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT j_params FROM geo_run WHERE i_run_id=%s", (run_id,))
        row = cur.fetchone()
    params = (json.loads(row["j_params"]) if isinstance(row["j_params"], str) else row["j_params"]) if row else {}
    # The run's own gap limit, so a trip's own distance reproduces the fit's.
    max_gap = float((params or {}).get("max_gap_seconds", settings.detector.max_gap_seconds))

    reject = codec.ROLES.index("reject")
    trails = []
    for b in blobs:
        arr = codec.decode(b["m_data"])
        kept = arr[arr["role"] != reject]
        trails.append((b["i_trip_no"], b["asset"], kept["t"].astype(np.float64),
                       kept["lat"].copy(), kept["lon"].copy()))
    distance = P.exclusive_distance(trails, max_gap)
    del trails

    shares = P.trip_shares(meta.keys(), pvisits, palerts, inferred, distance, pstops)
    km = lambda m: None if m is None else round(m / 1000, 3)   # noqa: E731
    rows = [(run_id, trip, s["facility_visits"], s["facility_dwell_s"], s["alerts"], s["overspeed"],
             s["restricted"], s["inferred"], km(s["distance_m"]), km(s["own_distance_m"]), len(s["siblings"]),
             _trips_text(sorted(s["siblings"])) or None)
            for trip, s in shares.items()]
    with conn.cursor() as cur:
        for i in range(0, len(rows), 20_000):
            cur.executemany("""
                INSERT INTO geo_trip_share
                    (i_run_id,i_trip_no,i_facility_visits,i_facility_dwell_s,i_alerts,i_overspeed,
                     i_restricted,i_inferred,d_distance_km,d_own_distance_km,i_siblings,s_sibling_trips)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows[i:i + 20_000])
    conn.commit()
    return shares


# ---------------------------------------------------------------------------
# 2. rollups
# ---------------------------------------------------------------------------

def _fence_stats(conn, run_id: int, pvisits: list[dict], palerts: list[dict], pstops: list[dict],
                 inferred_rows: list[dict]) -> int:
    rows = fence_stat_rows(conn, run_id, pvisits, palerts, pstops, inferred_rows)
    _insert_fence_stats(conn, rows)
    return len(rows)


def fence_stat_rows(conn, run_id: int, pvisits: list[dict], palerts: list[dict], pstops: list[dict],
                    inferred_rows: list[dict], trips_by_fence: dict[int, int] | None = None,
                    only: set[int] | None = None) -> list[tuple]:
    """geo_fence_stats rows. `trips_by_fence`, when given, is each fence's
    count of distinct trips (the incremental rebuild reads it from geo_visit,
    which holds every trip a physical visit folded); `only` limits the rows
    to some fences."""
    by_fence: dict[int, list[dict]] = defaultdict(list)
    for v in pvisits:
        by_fence[v["i_fence_id"]].append(v)
    violations: dict[int, int] = defaultdict(int)
    for a in palerts:
        violations[a["i_fence_id"]] += 1
    stops: dict[int, int] = defaultdict(int)
    for s in pstops:
        if s["i_fence_id"] is not None:
            stops[s["i_fence_id"]] += 1

    inferred: dict[int, int] = defaultdict(int)
    for r in P.count_physical(inferred_rows, "dt_gap_from", "dt_gap_to",
                              key=lambda r: (P.vehicle_key(r), r["i_fence_id"])):
        inferred[r["i_fence_id"]] += 1

    # Fences with inferred passages or stops but no observed visit still get
    # a row, so their page is not empty of the evidence that exists.
    extra = (set(inferred) | set(stops)) - set(by_fence)
    if only is not None:
        extra &= only
    meta = {}
    if extra:
        marks = ",".join(["%s"] * len(extra))
        with conn.cursor() as cur:
            cur.execute(f"""SELECT i_fence_id, i_site_id, s_site_name, s_type, s_category, d_area_sqm
                              FROM geo_fence WHERE i_fence_id IN ({marks})""", tuple(extra))
            meta = {r["i_fence_id"]: r for r in cur.fetchall()}

    rows = []
    for fid, vs in by_fence.items():
        if only is not None and fid not in only:
            continue
        first = vs[0]
        measured = [v["i_dwell_seconds"] for v in vs if _measured(v)]
        rows.append((
            run_id, fid, first["i_site_id"], first["s_site_name"], first["s_type"],
            first["s_category"], first["s_scale"],
            len(vs), sum(1 for v in vs if v["b_primary"]),
            len({v["s_asset_id"] for v in vs if v["s_asset_id"]}),
            (trips_by_fence.get(fid, 0) if trips_by_fence is not None
             else len({t for v in vs for t in v["trips"]})),
            len({v["s_trans_name"] for v in vs if v["s_trans_name"]}),
            sum(1 for v in vs if v["b_open"]),
            sum(1 for v in vs if not v["b_entry_observed"]),
            sum(int(v["i_dwell_seconds"] or 0) for v in vs),
            _pct(measured, 50), _pct(measured, 90), max(measured) if measured else None,
            violations.get(fid, 0), inferred.get(fid, 0), stops.get(fid, 0),
            min(v["dt_enter"] for v in vs), max(_end(v) for v in vs),
        ))
    for fid in extra:
        m = meta.get(fid)
        if not m:
            continue
        area = float(m["d_area_sqm"])
        scale = ("micro" if area < 1e4 else "site" if area < 1e6 else "campus" if area < 1e8 else "regional")
        rows.append((run_id, fid, m["i_site_id"], m["s_site_name"], m["s_type"], m["s_category"],
                     scale, 0, 0, 0, 0, 0, 0, 0, 0, None, None, None,
                     violations.get(fid, 0), inferred.get(fid, 0), stops.get(fid, 0), None, None))
    return rows


def _insert_fence_stats(conn, rows: list[tuple]) -> None:
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO geo_fence_stats
                (i_run_id,i_fence_id,i_site_id,s_site_name,s_type,s_category,s_scale,i_visits,
                 i_primary_visits,i_vehicles,i_trips,i_transporters,i_open_visits,
                 i_unobserved_entries,i_dwell_total_s,i_dwell_p50_s,i_dwell_p90_s,i_dwell_max_s,
                 i_violations,i_inferred,i_stops,dt_first,dt_last)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
    conn.commit()


def _fence_days(conn, run_id: int, pvisits: list[dict]) -> int:
    rows = fence_day_rows(run_id, pvisits)
    _insert_fence_days(conn, rows)
    return len(rows)


def fence_day_rows(run_id: int, pvisits: list[dict]) -> list[tuple]:
    agg: dict[tuple[int, date], dict] = {}

    def slot(fid, day, site):
        key = (fid, day)
        if key not in agg:
            agg[key] = {"site": site, "entries": 0, "exits": 0, "vehicles": set(),
                        "dwell": 0.0, "measured": []}
        return agg[key]

    for v in pvisits:
        fid, site = v["i_fence_id"], v["i_site_id"]
        if v["b_entry_observed"]:
            s = slot(fid, v["dt_enter"].date(), site)
            s["entries"] += 1
            if _measured(v):
                s["measured"].append(v["i_dwell_seconds"])
        if v["dt_exit"] is not None:
            slot(fid, v["dt_exit"].date(), site)["exits"] += 1
        for day, secs in _day_spans(v["dt_enter"], _end(v)):
            s = slot(fid, day, site)
            s["dwell"] += secs
            if v["s_asset_id"]:
                s["vehicles"].add(v["s_asset_id"])

    return [(run_id, fid, day, s["site"], s["entries"], s["exits"], len(s["vehicles"]),
             int(s["dwell"]), _pct(s["measured"], 50))
            for (fid, day), s in agg.items()]


def _insert_fence_days(conn, rows: list[tuple]) -> None:
    with conn.cursor() as cur:
        for i in range(0, len(rows), 20_000):
            cur.executemany("""
                INSERT INTO geo_fence_day
                    (i_run_id,i_fence_id,d_day,i_site_id,i_entries,i_exits,i_vehicles,
                     i_dwell_s,i_dwell_p50_s)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows[i:i + 20_000])
    conn.commit()


def _feed_by_day(blobs: list[dict]) -> dict[date, dict]:
    """Fixes, refusals and corrected spikes per day, each physical fix once.

    Read from the stored fitted trails: two consignment trips on one truck
    carry the same fixes, so a fix is identified by (vehicle, timestamp).
    """
    if not blobs:
        return {}
    assets: dict[str, int] = {}
    keys, days, spike, reject, asset_col, trips = [], [], [], [], [], []
    spike_code = codec.FITS.index("spike")
    reject_code = codec.ROLES.index("reject")
    for b in blobs:
        arr = codec.decode(b["m_data"])
        if not len(arr):
            continue
        a = assets.setdefault(b["asset"], len(assets))
        t = arr["t"].astype(np.int64)
        # (vehicle, role, timestamp): the role matters because a refused
        # duplicate shares its timestamp with the fix it duplicated.
        keys.append((np.int64(a) << 36) | (arr["role"].astype(np.int64) << 34) | (t & ((1 << 34) - 1)))
        days.append(t // 86_400)
        spike.append(arr["fit"] == spike_code)
        reject.append(arr["role"] == reject_code)
        asset_col.append(np.full(len(arr), a, dtype=np.int64))
        trips.append(np.full(len(arr), b["i_trip_no"], dtype=np.int64))
    keys_a = np.concatenate(keys)
    _, first = np.unique(keys_a, return_index=True)
    day_a = np.concatenate(days)
    spike_a = np.concatenate(spike)
    reject_a = np.concatenate(reject)
    asset_a = np.concatenate(asset_col)
    trip_a = np.concatenate(trips)

    out: dict[date, dict] = {}
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc).date()
    ud = day_a[first]
    for d in np.unique(ud):
        sel = first[ud == d]
        all_sel = day_a == d
        out[epoch + timedelta(days=int(d))] = {
            "pings": int(len(sel)),
            "rejected": int(reject_a[sel].sum()),
            "spikes": int(spike_a[sel].sum()),
            "vehicles": int(len(np.unique(asset_a[sel]))),
            "trips": int(len(np.unique(trip_a[all_sel]))),
        }
    return out


def _day_summary(conn, run_id: int, pvisits: list[dict], palerts: list[dict], pstops: list[dict],
                 inferred: list[dict], blobs: list[dict]) -> int:
    with conn.cursor() as cur:
        cur.execute("""SELECT i_trip_no, s_asset_id, dt_from, dt_to FROM geo_gap
                        WHERE i_run_id=%s AND s_kind='moving'""", (run_id,))
        gaps = list(cur.fetchall())
    rows = day_rows(run_id, pvisits, palerts, pstops, gaps, inferred, _feed_by_day(blobs))
    _insert_days(conn, rows)
    return len(rows)


def day_rows(run_id: int, pvisits: list[dict], palerts: list[dict], pstops: list[dict], gaps: list[dict],
             inferred: list[dict], feed: dict[date, dict], only: set[date] | None = None) -> list[tuple]:
    """geo_day_summary rows. Every figure is per calendar day and its events
    are local in time, so given every event near a day, the day's row comes
    out the same from a subset as from the whole run -- which is how the
    incremental rebuild (pipeline/incremental.py) redoes only today."""
    days: dict[date, dict] = defaultdict(lambda: {
        "vehicles": 0, "trips": 0, "pings": 0, "rejected": 0, "spikes": 0,
        "entries": 0, "exits": 0, "fac_visits": 0, "sites": set(), "fac_dwell": 0.0,
        "restricted": 0, "overspeed": 0, "stops": 0, "stops_out": 0, "stop_out_s": 0,
        "moving_gaps": 0, "inferred": 0,
        "h_entries": [0] * 24, "h_exits": [0] * 24, "h_alerts": [0] * 24,
    })

    for day, f in feed.items():
        d = days[day]
        d.update({k: f[k] for k in ("pings", "rejected", "spikes", "vehicles", "trips")})

    for a in palerts:
        d = days[a["dt_event"].date()]
        if a["s_kind"] == "overspeed":
            d["overspeed"] += 1
        else:
            d["restricted"] += 1
        d["h_alerts"][a["dt_event"].hour] += 1

    for s in pstops:
        d = days[s["dt_start"].date()]
        d["stops"] += 1
        if s["i_fence_id"] is None:
            d["stops_out"] += 1
            d["stop_out_s"] += int(s["i_duration_s"] or 0)

    for g in P.count_physical(gaps, "dt_from", "dt_to"):
        days[g["dt_from"].date()]["moving_gaps"] += 1
    for r in P.count_physical(inferred, "dt_gap_from", "dt_gap_to",
                              key=lambda r: (P.vehicle_key(r), r["i_fence_id"])):
        days[r["dt_gap_from"].date()]["inferred"] += 1

    for v in pvisits:
        if v["s_scale"] not in FACILITY:
            continue
        if v["b_entry_observed"]:
            d = days[v["dt_enter"].date()]
            d["entries"] += 1
            d["h_entries"][v["dt_enter"].hour] += 1
            if v["b_primary"]:
                d["fac_visits"] += 1
        if v["dt_exit"] is not None:
            d = days[v["dt_exit"].date()]
            d["exits"] += 1
            d["h_exits"][v["dt_exit"].hour] += 1
        for day, _ in _day_spans(v["dt_enter"], _end(v)):
            days[day]["sites"].add(v["i_site_id"])
    for place in P.facility_places(pvisits):
        for day, secs in _day_spans(place["start"], place["end"]):
            days[day]["fac_dwell"] += secs

    rows = [(
        run_id, day, d["vehicles"], d["trips"], d["pings"], d["rejected"], d["spikes"],
        d["entries"], d["exits"], d["fac_visits"], len(d["sites"]), int(d["fac_dwell"]),
        d["restricted"], d["overspeed"], d["stops"], d["stops_out"], d["stop_out_s"],
        d["moving_gaps"], d["inferred"],
        json.dumps({"entries": d["h_entries"], "exits": d["h_exits"], "alerts": d["h_alerts"]}),
    ) for day, d in sorted(days.items()) if only is None or day in only]
    return rows


def _insert_days(conn, rows: list[tuple]) -> None:
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO geo_day_summary
                (i_run_id,d_day,i_vehicles,i_trips,i_pings,i_pings_rejected,i_spikes,i_entries,
                 i_exits,i_facility_visits,i_sites_visited,i_facility_dwell_s,i_restricted,
                 i_overspeed,i_stops,i_stops_outside,i_stop_outside_s,i_moving_gaps,i_inferred,
                 j_hourly)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
    conn.commit()
