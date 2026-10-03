"""Diff two runs over the trips they share -- normally raw against fitted.

A preprocessing stage that changes the answer has to show that it changed it
for the better, and there is no ground truth to score against. What there is:
the signatures noise leaves in a ledger, which are countable in both runs.

* **Quick re-entry** -- the same trip leaves a fence and re-enters it within
  ten minutes. Occasionally real (a truck steps out to a weighbridge); mostly
  a parked truck's position hopping across the line.
* **Escape at standstill** -- a crossing confirmed by distance alone while the
  stamped fix reports zero speed. A stationary truck does not get 250 m past
  a boundary; its position estimate does.
* **Blink visits** -- closed, fully observed visits shorter than three minutes
  at facility scale.

Visits are also matched one-to-one (same trip, same fence, overlapping in
time), so the diff reports exactly which visits one run has and the other
does not, and what the removed ones looked like. That is what lets a reviewer
check a sample by eye rather than trust a percentage.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from nexgen.shared.geoengine.db import geo_session

REENTRY_S = 600
BLINK_S = 180
FACILITY = ("micro", "site", "campus")


def _visits(cur, run_id: int, trips: set[int]) -> dict:
    cur.execute("""SELECT i_trip_no, i_fence_id, s_site_name, s_scale, dt_enter, dt_exit, b_open,
                          b_entry_observed, i_dwell_seconds, s_confirmed_by, b_primary
                     FROM geo_visit WHERE i_run_id=%s""", (run_id,))
    out: dict[tuple, list] = defaultdict(list)
    for r in cur.fetchall():
        if r["i_trip_no"] in trips:
            out[(r["i_trip_no"], r["i_fence_id"])].append(r)
    for vs in out.values():
        vs.sort(key=lambda v: v["dt_enter"])
    return out


def _signatures(cur, run_id: int, trips: set[int], visits: dict) -> dict:
    by_scale: dict[str, int] = defaultdict(int)
    reentries = blinks = 0
    for vs in visits.values():
        for v in vs:
            by_scale[v["s_scale"]] += 1
            if (v["s_scale"] in FACILITY and not v["b_open"] and v["b_entry_observed"]
                    and v["i_dwell_seconds"] is not None and v["i_dwell_seconds"] < BLINK_S):
                blinks += 1
        for a, b in zip(vs, vs[1:]):
            if a["dt_exit"] and (b["dt_enter"] - a["dt_exit"]).total_seconds() < REENTRY_S:
                reentries += 1

    cur.execute("""SELECT i_trip_no, s_event, s_confirmed_by, i_speed FROM geo_event
                    WHERE i_run_id=%s""", (run_id,))
    crossings = escape = escape_still = 0
    for r in cur.fetchall():
        if r["i_trip_no"] not in trips:
            continue
        crossings += 1
        if r["s_confirmed_by"] == "escape":
            escape += 1
            if not r["i_speed"]:
                escape_still += 1

    return {
        "visits": sum(by_scale.values()),
        "visits_by_scale": dict(sorted(by_scale.items())),
        "crossings": crossings,
        "escape_crossings": escape,
        "escape_at_standstill": escape_still,
        "quick_reentries": reentries,
        "blink_visits": blinks,
    }


def _overlaps(a: dict, b: dict) -> bool:
    a_end = a["dt_exit"] or a["dt_enter"] + timedelta(seconds=a["i_dwell_seconds"] or 0)
    b_end = b["dt_exit"] or b["dt_enter"] + timedelta(seconds=b["i_dwell_seconds"] or 0)
    return a["dt_enter"] <= b_end and b["dt_enter"] <= a_end


def compare(run_a: int, run_b: int, sample: int = 25) -> dict:
    with geo_session() as conn, conn.cursor() as cur:
        cur.execute("""SELECT i_run_id, s_variant, s_scope, s_osrm, j_params FROM geo_run
                        WHERE i_run_id IN (%s, %s)""", (run_a, run_b))
        runs = {r["i_run_id"]: r for r in cur.fetchall()}
        if run_a not in runs or run_b not in runs:
            raise LookupError("both runs must exist")
        cur.execute("""SELECT a.i_trip_no FROM geo_trip_summary a
                         JOIN geo_trip_summary b ON b.i_trip_no = a.i_trip_no AND b.i_run_id = %s
                        WHERE a.i_run_id = %s""", (run_b, run_a))
        trips = {r["i_trip_no"] for r in cur.fetchall()}
        va = _visits(cur, run_a, trips)
        vb = _visits(cur, run_b, trips)
        sig_a = _signatures(cur, run_a, trips, va)
        sig_b = _signatures(cur, run_b, trips, vb)

    only_a: list[dict] = []
    only_b: list[dict] = []
    matched = 0
    shifted_enter: list[float] = []
    for key in set(va) | set(vb):
        la, lb = list(va.get(key, [])), list(vb.get(key, []))
        used_b = set()
        for a in la:
            hit = next((j for j, b in enumerate(lb) if j not in used_b and _overlaps(a, b)), None)
            if hit is None:
                only_a.append(a)
            else:
                used_b.add(hit)
                matched += 1
                shifted_enter.append(abs((lb[hit]["dt_enter"] - a["dt_enter"]).total_seconds()))
        only_b.extend(b for j, b in enumerate(lb) if j not in used_b)

    def profile(vs: list[dict]) -> dict:
        fac = [v for v in vs if v["s_scale"] in FACILITY]
        dwell = sorted(v["i_dwell_seconds"] or 0 for v in fac)
        return {
            "visits": len(vs), "facility": len(fac),
            "by_scale": dict(defaultdict(int, {s: sum(1 for v in vs if v["s_scale"] == s)
                                               for s in {v["s_scale"] for v in vs}})),
            "facility_dwell_median_s": dwell[len(dwell) // 2] if dwell else None,
            "facility_under_10min": sum(1 for d in dwell if d < 600),
        }

    def row(v: dict) -> dict:
        return {"trip": v["i_trip_no"], "fence": v["s_site_name"], "scale": v["s_scale"],
                "enter": v["dt_enter"], "exit": v["dt_exit"], "dwell_s": v["i_dwell_seconds"],
                "confirmed_by": v["s_confirmed_by"]}

    only_a.sort(key=lambda v: (v["s_scale"] not in FACILITY, v["i_trip_no"], v["dt_enter"]))
    only_b.sort(key=lambda v: (v["s_scale"] not in FACILITY, v["i_trip_no"], v["dt_enter"]))
    shifted_enter.sort()
    return {
        "run_a": {k: runs[run_a][k] for k in ("i_run_id", "s_variant", "s_scope", "s_osrm")},
        "run_b": {k: runs[run_b][k] for k in ("i_run_id", "s_variant", "s_scope", "s_osrm")},
        "common_trips": len(trips),
        "signatures": {"a": sig_a, "b": sig_b},
        "matched_visits": matched,
        "entry_shift_s": {
            "unchanged": sum(1 for s in shifted_enter if s == 0),
            "within_2min": sum(1 for s in shifted_enter if 0 < s <= 120),
            "over_2min": sum(1 for s in shifted_enter if s > 120),
        },
        "only_in_a": profile(only_a),
        "only_in_b": profile(only_b),
        "sample_only_in_a": [row(v) for v in only_a[:sample]],
        "sample_only_in_b": [row(v) for v in only_b[:sample]],
    }
