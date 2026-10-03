"""Bring a run's summaries up to date for a handful of trips, not the whole run.

`summaries.build` rebuilds the physical ledger and every rollup for the whole
run -- about four minutes on 37 days. On a fleet of hundreds of vehicles with
years of history that grows without bound, and a refresh every ten or fifteen
minutes has to finish in seconds. This module redoes only what the changed
trips can have touched, and produces exactly the rows a full rebuild would.

Why that is possible
---------------------------------------------------------------------------
Everything cross-trip is local in two ways:

* **by vehicle** -- the physical ledger folds copies of one event, and copies
  are always the same vehicle's, overlapping in time (physical.py). So a trip
  can only fold with trips of its vehicle whose GPS spans overlap its own,
  directly or through a chain. Those chains ("components") are recomputed
  whole; every other vehicle's ledger is untouched.
* **by day and by fence** -- a day's summary row depends only on events near
  that day, and a fence's rollups only on that fence's physical visits. So the
  days and fences the changed rows touch, before and after, are recomputed
  from the ledger, and no others.

The changed trips' per-trip rows are replaced first (runner.reprocess); their
footprint *before* that is taken with `snapshot`, because a fence or day the
old rows touched and the new ones do not must still be recomputed.

scripts/verify_incremental.py checks the promise on a scratch copy of a real
run: a full rebuild and an incremental one after the same change give
identical tables, row for row.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from nexgen.shared.geoengine.pipeline import phases
from nexgen.shared.geoengine.pipeline import summaries as S

logger = logging.getLogger(__name__)

# Events whose copies may chain across days are read this far either side of
# the days being rebuilt (moving gaps, inferred passages).
NEAR = timedelta(days=3)
FAC = "('micro','site','campus')"


@dataclass
class Touch:
    """Where some trips' rows are: fences, days, vehicles."""
    fences: set[int] = field(default_factory=set)
    days: set[date] = field(default_factory=set)
    vehicles: set[str] = field(default_factory=set)

    def __or__(self, other: "Touch") -> "Touch":
        return Touch(self.fences | other.fences, self.days | other.days, self.vehicles | other.vehicles)


def _span_days(a: datetime | None, b: datetime | None) -> set[date]:
    if not a:
        return set()
    b = b or a
    out, d = set(), a.date()
    while d <= b.date():
        out.add(d)
        d += timedelta(days=1)
    return out


def _in(trips) -> tuple[str, list]:
    trips = sorted(trips)
    return ",".join(["%s"] * len(trips)), trips


def snapshot(conn, run_id: int, trips) -> Touch:
    """The fences, days and vehicles the per-trip ledger of `trips` touches.

    Taken before their rows are replaced and again after; the rebuild covers
    both, so nothing the old rows counted is left behind.
    """
    t = Touch()
    trips = list(trips)
    conn.commit()                      # read the ledger as it is now (see rebuild)
    for chunk in S._chunks(trips):
        marks, args = _in(chunk)
        with conn.cursor() as cur:
            cur.execute(f"""SELECT i_fence_id, dt_enter, COALESCE(dt_exit, dt_enter + INTERVAL i_dwell_seconds SECOND) e
                              FROM geo_visit WHERE i_run_id=%s AND i_trip_no IN ({marks})""", (run_id, *args))
            for r in cur.fetchall():
                t.fences.add(r["i_fence_id"])
                t.days |= _span_days(r["dt_enter"], r["e"])
            cur.execute(f"""SELECT i_fence_id, dt_event FROM geo_violation
                             WHERE i_run_id=%s AND i_trip_no IN ({marks})""", (run_id, *args))
            for r in cur.fetchall():
                t.fences.add(r["i_fence_id"])
                t.days.add(r["dt_event"].date())
            cur.execute(f"""SELECT i_fence_id, dt_start FROM geo_stop
                             WHERE i_run_id=%s AND i_trip_no IN ({marks})""", (run_id, *args))
            for r in cur.fetchall():
                if r["i_fence_id"] is not None:
                    t.fences.add(r["i_fence_id"])
                t.days.add(r["dt_start"].date())
            cur.execute(f"""SELECT i_fence_id, dt_gap_from FROM geo_inferred_visit
                             WHERE i_run_id=%s AND i_trip_no IN ({marks})""", (run_id, *args))
            for r in cur.fetchall():
                t.fences.add(r["i_fence_id"])
                t.days.add(r["dt_gap_from"].date())
            cur.execute(f"""SELECT dt_from FROM geo_gap WHERE i_run_id=%s AND s_kind='moving'
                               AND i_trip_no IN ({marks})""", (run_id, *args))
            for r in cur.fetchall():
                t.days.add(r["dt_from"].date())
            cur.execute(f"""SELECT i_trip_no, s_asset_id, dt_first_ping, dt_last_ping FROM geo_trip_summary
                             WHERE i_run_id=%s AND i_trip_no IN ({marks})""", (run_id, *args))
            for r in cur.fetchall():
                t.days |= _span_days(r["dt_first_ping"], r["dt_last_ping"])
                t.vehicles.add(r["s_asset_id"] or f"trip:{r['i_trip_no']}")
    return t


# ---------------------------------------------------------------------------
# which trips fold together
# ---------------------------------------------------------------------------

def _siblings(conn, run_id: int, trips) -> set[int]:
    """Trips that shared a physical event with `trips` in the ledger as it
    stands -- before the rebuild, so a trip that has moved to another vehicle
    still releases the events it used to share."""
    out: set[int] = set()
    for chunk in S._chunks(list(trips)):
        marks, args = _in(chunk)
        with conn.cursor() as cur:
            cur.execute(f"""SELECT s_sibling_trips FROM geo_trip_share
                             WHERE i_run_id=%s AND i_trip_no IN ({marks}) AND s_sibling_trips IS NOT NULL""",
                        (run_id, *args))
            for r in cur.fetchall():
                out |= {int(x) for x in r["s_sibling_trips"].split(",") if x.strip().isdigit()}
    return out


def components(conn, run_id: int, seed: set[int]) -> set[int]:
    """Every trip that can fold with a trip in `seed`: the vehicle's trips
    whose GPS spans overlap it, directly or through a chain."""
    heads: dict[int, dict] = {}
    for chunk in S._chunks(list(seed)):
        marks, args = _in(chunk)
        with conn.cursor() as cur:
            cur.execute(f"""SELECT i_trip_no, s_asset_id FROM geo_trip_summary
                             WHERE i_run_id=%s AND i_trip_no IN ({marks})""", (run_id, *args))
            heads.update({r["i_trip_no"]: r for r in cur.fetchall()})
    vehicles = sorted({h["s_asset_id"] for h in heads.values() if h["s_asset_id"]})
    by_vehicle: dict[str, list[dict]] = {}
    for chunk in S._chunks(vehicles, 500):
        marks, args = _in(chunk)
        with conn.cursor() as cur:
            cur.execute(f"""SELECT i_trip_no, s_asset_id, dt_first_ping, dt_last_ping FROM geo_trip_summary
                             WHERE i_run_id=%s AND s_asset_id IN ({marks})""", (run_id, *args))
            for r in cur.fetchall():
                by_vehicle.setdefault(r["s_asset_id"], []).append(r)
    out = set(seed)            # a trip without a vehicle folds with nothing but itself
    for items in by_vehicle.values():
        items.sort(key=lambda r: (r["dt_first_ping"] or datetime.min, r["i_trip_no"]))
        comp: list[int] = []
        end = None
        for r in items:
            a, b = r["dt_first_ping"], r["dt_last_ping"] or r["dt_first_ping"]
            if comp and a is not None and end is not None and a <= end:
                comp.append(r["i_trip_no"])
                end = max(end, b)
            else:
                if comp and seed.intersection(comp):
                    out.update(comp)
                comp, end = [r["i_trip_no"]], b
        if comp and seed.intersection(comp):
            out.update(comp)
    return out


# ---------------------------------------------------------------------------
# the rebuild
# ---------------------------------------------------------------------------

def _physical_footprint(conn, run_id: int, trips) -> Touch:
    """Fences and days of the physical rows the trips currently own."""
    t = Touch()
    for chunk in S._chunks(list(trips)):
        marks, args = _in(chunk)
        with conn.cursor() as cur:
            cur.execute(f"""SELECT i_fence_id, dt_enter, COALESCE(dt_exit, dt_enter + INTERVAL i_dwell_seconds SECOND) e
                              FROM geo_pvisit WHERE i_run_id=%s AND i_trip_no IN ({marks})""", (run_id, *args))
            for r in cur.fetchall():
                t.fences.add(r["i_fence_id"])
                t.days |= _span_days(r["dt_enter"], r["e"])
            cur.execute(f"SELECT i_fence_id, dt_event FROM geo_palert WHERE i_run_id=%s AND i_trip_no IN ({marks})",
                        (run_id, *args))
            for r in cur.fetchall():
                t.fences.add(r["i_fence_id"])
                t.days.add(r["dt_event"].date())
            cur.execute(f"SELECT i_fence_id, dt_start FROM geo_pstop WHERE i_run_id=%s AND i_trip_no IN ({marks})",
                        (run_id, *args))
            for r in cur.fetchall():
                if r["i_fence_id"] is not None:
                    t.fences.add(r["i_fence_id"])
                t.days.add(r["dt_start"].date())
    return t


def _delete_trips(conn, run_id: int, tables: tuple[str, ...], trips) -> None:
    for chunk in S._chunks(list(trips)):
        marks, args = _in(chunk)
        with conn.cursor() as cur:
            for table in tables:
                cur.execute(f"DELETE FROM {table} WHERE i_run_id=%s AND i_trip_no IN ({marks})", (run_id, *args))
    conn.commit()


def rebuild(conn, run_id: int, changed, before: Touch | None = None, reevaluated=None) -> dict:
    """Redo the physical ledger, trip shares, trip phases and rollups that
    `changed` trips can have affected, after their per-trip rows changed.

    `before` is `snapshot()` of the per-trip rows before they were replaced;
    `reevaluated` the trips whose per-trip rows actually changed (their
    phases are rebuilt) -- by default all of `changed`. A trip whose trip
    record alone changed (a transporter corrected upstream) is passed in
    `changed` without being re-evaluated: its events are folded again under
    the new name.
    """
    t0 = time.perf_counter()
    changed = set(changed)
    reevaluated = changed if reevaluated is None else set(reevaluated)
    if not changed:
        return {"trips": 0}
    # End whatever transaction this connection has open: under InnoDB's
    # REPEATABLE READ it would keep reading the snapshot it took before the
    # trips were re-evaluated (by other connections), and see none of it.
    conn.commit()

    # 1. Every trip that folds with a changed one, then and now.
    seed = changed | _siblings(conn, run_id, changed)
    affected = components(conn, run_id, seed)
    old = _physical_footprint(conn, run_id, affected)

    # 2. Their physical ledger and shares, from their per-trip rows.
    _delete_trips(conn, run_id, ("geo_pvisit", "geo_palert", "geo_pstop", "geo_trip_share"), affected)
    meta = S._load_meta(conn, run_id, affected)
    pvisits = S._physical_visits(conn, run_id, meta, affected)
    palerts = S._physical_alerts(conn, run_id, pvisits, meta, affected)
    pstops = S._physical_stops(conn, run_id, meta, affected)
    inferred = S._select_trips(conn, """SELECT i_trip_no, s_asset_id, i_fence_id, dt_gap_from, dt_gap_to
                                          FROM geo_inferred_visit WHERE i_run_id=%s""", run_id, affected)
    blobs = S._load_trails(conn, run_id, affected)
    S._trip_shares(conn, run_id, meta, pvisits, palerts, pstops, inferred, blobs)
    del blobs
    n_phases = phases.build(conn, run_id, sorted(reevaluated)) if reevaluated else 0

    new = Touch()
    for p in pvisits:
        new.fences.add(p["i_fence_id"])
        new.days |= _span_days(p["dt_enter"], S._end(p))
    for a in palerts:
        new.fences.add(a["i_fence_id"])
        new.days.add(a["dt_event"].date())
    for s in pstops:
        if s["i_fence_id"] is not None:
            new.fences.add(s["i_fence_id"])
        new.days.add(s["dt_start"].date())
    after = snapshot(conn, run_id, reevaluated) if reevaluated else Touch()
    touched = old | new | after | (before or Touch())

    # 3. Rollups for the fences and days touched.
    n_fences = _rebuild_fences(conn, run_id, touched.fences)
    n_days = _rebuild_days(conn, run_id, touched.days)
    out = {"changed": len(changed), "affected_trips": len(affected), "physical_visits": len(pvisits),
           "physical_alerts": len(palerts), "physical_stops": len(pstops), "trip_phases": n_phases,
           "fences": n_fences, "days": n_days, "day_list": sorted(str(d) for d in touched.days),
           "seconds": round(time.perf_counter() - t0, 2)}
    logger.info("incremental rebuild of run %s: %s", run_id, {k: v for k, v in out.items() if k != "day_list"})
    return out


def _rebuild_fences(conn, run_id: int, fences: set[int]) -> int:
    """geo_fence_stats and geo_fence_day for these fences, from the ledger."""
    if not fences:
        return 0
    fences = set(fences)
    pv: list[dict] = []
    trips_by_fence: dict[int, int] = {}
    alerts: list[dict] = []
    stops: list[dict] = []
    inferred: list[dict] = []
    for chunk in S._chunks(list(fences), 500):
        marks, args = _in(chunk)
        with conn.cursor() as cur:
            cur.execute(f"""SELECT s_asset_id, i_fence_id, i_site_id, s_site_name, s_type, s_category, s_scale,
                                   dt_enter, dt_exit, b_open, b_entry_observed, i_dwell_seconds, b_primary,
                                   s_trans_name
                              FROM geo_pvisit WHERE i_run_id=%s AND i_fence_id IN ({marks})
                             ORDER BY dt_enter, i_fence_id, id""", (run_id, *args))
            pv += list(cur.fetchall())
            cur.execute(f"""SELECT i_fence_id, COUNT(DISTINCT i_trip_no) n FROM geo_visit
                             WHERE i_run_id=%s AND i_fence_id IN ({marks}) GROUP BY i_fence_id""", (run_id, *args))
            trips_by_fence.update({r["i_fence_id"]: int(r["n"]) for r in cur.fetchall()})
            cur.execute(f"SELECT i_fence_id FROM geo_palert WHERE i_run_id=%s AND i_fence_id IN ({marks})",
                        (run_id, *args))
            alerts += list(cur.fetchall())
            cur.execute(f"SELECT i_fence_id FROM geo_pstop WHERE i_run_id=%s AND i_fence_id IN ({marks})",
                        (run_id, *args))
            stops += list(cur.fetchall())
            cur.execute(f"""SELECT i_trip_no, s_asset_id, i_fence_id, dt_gap_from, dt_gap_to FROM geo_inferred_visit
                             WHERE i_run_id=%s AND i_fence_id IN ({marks})""", (run_id, *args))
            inferred += list(cur.fetchall())
    stat_rows = S.fence_stat_rows(conn, run_id, pv, alerts, stops, inferred, trips_by_fence, only=fences)
    day_rows = S.fence_day_rows(run_id, pv)
    for chunk in S._chunks(list(fences), 500):
        marks, args = _in(chunk)
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM geo_fence_stats WHERE i_run_id=%s AND i_fence_id IN ({marks})", (run_id, *args))
            cur.execute(f"DELETE FROM geo_fence_day WHERE i_run_id=%s AND i_fence_id IN ({marks})", (run_id, *args))
    conn.commit()
    if stat_rows:
        S._insert_fence_stats(conn, stat_rows)
    if day_rows:
        S._insert_fence_days(conn, day_rows)
    return len(fences)


def _day_runs(days: set[date], longest: int = 7) -> list[tuple[date, date]]:
    """Consecutive days grouped into short windows, so a late backfill that
    touches a month is rebuilt a week at a time, not in one read."""
    out: list[tuple[date, date]] = []
    for d in sorted(days):
        if out and d == out[-1][1] + timedelta(days=1) and (d - out[-1][0]).days < longest:
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


def _rebuild_days(conn, run_id: int, days: set[date]) -> int:
    """geo_day_summary rows for these days, each from every event near it."""
    if not days:
        return 0
    with conn.cursor() as cur:
        cur.execute(f"SELECT COALESCE(MAX(i_dwell_seconds), 0) n FROM geo_pvisit WHERE i_run_id=%s AND s_scale IN {FAC}",
                    (run_id,))
        longest = timedelta(seconds=int(cur.fetchone()["n"] or 0) + 1)
    written = 0
    for first, last in _day_runs(days):
        a = datetime.combine(first, datetime.min.time())
        b = datetime.combine(last + timedelta(days=1), datetime.min.time())
        only = {first + timedelta(days=i) for i in range((last - first).days + 1)} & days
        with conn.cursor() as cur:
            cur.execute(f"""SELECT i_trip_no, s_trips, s_asset_id, i_fence_id, i_site_id, s_scale, dt_enter, dt_exit,
                                   b_open, b_entry_observed, i_dwell_seconds, b_primary
                              FROM geo_pvisit
                             WHERE i_run_id=%s AND s_scale IN {FAC} AND dt_enter < %s AND dt_enter >= %s
                               AND COALESCE(dt_exit, dt_enter + INTERVAL i_dwell_seconds SECOND) >= %s""",
                        (run_id, b, a - longest, a))
            pvisits = list(cur.fetchall())
            cur.execute("""SELECT s_kind, dt_event FROM geo_palert
                            WHERE i_run_id=%s AND dt_event >= %s AND dt_event < %s""", (run_id, a, b))
            palerts = list(cur.fetchall())
            cur.execute("""SELECT dt_start, i_fence_id, i_duration_s FROM geo_pstop
                            WHERE i_run_id=%s AND dt_start >= %s AND dt_start < %s""", (run_id, a, b))
            pstops = list(cur.fetchall())
            cur.execute("""SELECT i_trip_no, s_asset_id, dt_from, dt_to FROM geo_gap
                            WHERE i_run_id=%s AND s_kind='moving' AND dt_to >= %s AND dt_from < %s""",
                        (run_id, a - NEAR, b + NEAR))
            gaps = list(cur.fetchall())
            cur.execute("""SELECT i_trip_no, s_asset_id, i_fence_id, dt_gap_from, dt_gap_to FROM geo_inferred_visit
                            WHERE i_run_id=%s AND dt_gap_to >= %s AND dt_gap_from < %s""",
                        (run_id, a - NEAR, b + NEAR))
            inferred = list(cur.fetchall())
            cur.execute("""SELECT f.i_trip_no, f.m_data, COALESCE(t.s_asset_id, CONCAT('trip:', f.i_trip_no)) asset
                             FROM geo_trip_summary s
                             JOIN geo_fit_trail f ON f.i_run_id = s.i_run_id AND f.i_trip_no = s.i_trip_no
                             LEFT JOIN geo_trip t ON t.i_trip_no = f.i_trip_no
                            WHERE s.i_run_id=%s AND s.dt_first_ping < %s AND s.dt_last_ping >= %s""", (run_id, b, a))
            blobs = list(cur.fetchall())
        for p in pvisits:
            p["trips"] = [p["i_trip_no"]]         # facility_places needs only the owner
        rows = S.day_rows(run_id, pvisits, palerts, pstops, gaps, inferred, S._feed_by_day(blobs), only=only)
        del blobs
        with conn.cursor() as cur:
            marks, args = _in(only)
            cur.execute(f"DELETE FROM geo_day_summary WHERE i_run_id=%s AND d_day IN ({marks})", (run_id, *args))
        conn.commit()
        if rows:
            S._insert_days(conn, rows)
        written += len(rows)
    return written
