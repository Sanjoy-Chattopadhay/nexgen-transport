"""The physical ledger: one row per real event, however many trips saw it.

Why this exists
---------------------------------------------------------------------------
The fleet system opens one trip per consignment. A truck carrying three
invoices is three trips, and each trip carries its own copy of the truck's
GPS. On this corpus 482,908 fixes (9.4%) are extra copies of a fix another
trip carries, 1,827 of 4,814 trips share their truck's fixes, and run 6's
216,474 detected visits fold into 181,427 physical ones -- vehicle OD19N9988
has trips 778162 and 778163 with identical fixes, and 778442 starting two
hours into the same stay.

Per trip that is correct: each consignment really was at the plant. Summed
across trips it is not: a fence's visit count, a day's hours at facilities, a
transporter's alerts all count the one physical stay once per invoice.

So the per-trip ledger (geo_visit, geo_violation, geo_stop) stays as it is,
and this module folds it into a physical one:

    geo_pvisit       a vehicle inside a fence, once, with the trips that saw it
    geo_palert       a breach, once per physical visit and kind
    geo_pstop        a standstill, once
    geo_trip_share   each trip's share: the events and kilometres it owns

Everything aggregated across trips -- fence pages, the day summary, alerts,
stops -- reads the first three. Totals over a group of trips (a vehicle, a
transporter, a driver, a lane) sum the shares. Trip pages read the per-trip
tables, where a consignment's own view is the right one.

How copies are recognised
---------------------------------------------------------------------------
Copies come from the same fixes over different windows, so they overlap in
time for the same vehicle and fence; two genuinely separate visits never do.
Copies are therefore merged by interval overlap, not matched on exact
timestamps: a trip that starts mid-stay records the stay from its first fix,
with the entry unobserved, and that partial copy must still fold into the
full one.

Overlap means different things for different events (measured on run 6):

    visits, stops  contain their boundary fixes, so copies may merely touch
                   (132 visit pairs from different trips share only a fix);
                   one trip never has two touching visits to one fence
    gaps, inferred lie between fixes, so a shared endpoint separates two
                   holes -- 3,508 pairs of one trip's moving gaps touch --
                   and only a real overlap from another trip is a copy

merge_copies encodes that; merge_intervals is a plain union, used only to
measure time.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

import numpy as np

FACILITY = ("micro", "site", "campus")


def _end(v: dict) -> datetime:
    return v["dt_exit"] or v["dt_enter"] + timedelta(seconds=v["i_dwell_seconds"] or 0)


def _majority(values) -> str | None:
    c = Counter(v for v in values if v)
    return c.most_common(1)[0][0] if c else None


# ---------------------------------------------------------------------------
# visits
# ---------------------------------------------------------------------------

def merge_visits(copies: list[dict], meta: dict[int, dict] | None = None) -> list[dict]:
    """Fold visit rows for ONE (vehicle, fence) into physical visits.

    Rows carry the geo_visit columns. The merged visit:
      * enters at the earliest copy, observed if that copy observed it;
      * is closed if any copy saw the exit, at the latest observed exit --
        a copy still "open" past it was cut off by its trip's trail ending,
        not by the truck staying;
      * lists every trip that saw it.
    """
    meta = meta or {}
    rows = sorted(copies, key=lambda v: (v["dt_enter"], _end(v)))
    groups: list[dict] = []
    for v in rows:
        end = _end(v)
        if groups and v["dt_enter"] <= groups[-1]["end"]:
            g = groups[-1]
            g["copies"].append(v)
            g["end"] = max(g["end"], end)
        else:
            groups.append({"copies": [v], "end": end})

    out: list[dict] = []
    for g in groups:
        cs = g["copies"]
        enter = min(c["dt_enter"] for c in cs)
        first = [c for c in cs if c["dt_enter"] == enter]
        closed = [c for c in cs if c["dt_exit"] is not None]
        if closed:
            exit_ = max(c["dt_exit"] for c in closed)
            closer = next(c for c in closed if c["dt_exit"] == exit_)
            end = exit_
        else:
            exit_, closer, end = None, None, g["end"]
        trips = sorted({c["i_trip_no"] for c in cs})
        speeds = [c["i_max_speed"] for c in cs if c.get("i_max_speed") is not None]
        base = first[0]
        out.append({
            "s_asset_id": base["s_asset_id"], "i_fence_id": base["i_fence_id"],
            "i_site_id": base["i_site_id"], "s_site_name": base["s_site_name"],
            "s_type": base.get("s_type"), "s_category": base.get("s_category"),
            "s_scale": base.get("s_scale"),
            "dt_enter": enter, "dt_exit": exit_, "b_open": 0 if closed else 1,
            "b_entry_observed": 1 if any(c["b_entry_observed"] for c in first) else 0,
            "i_dwell_seconds": int((end - enter).total_seconds()),
            "b_primary": 1 if any(c["b_primary"] for c in cs) else 0,
            "trips": trips,
            "s_trans_name": _majority(meta.get(t, {}).get("s_trans_name") for t in trips),
            "s_driver_name": _majority(meta.get(t, {}).get("s_driver_name") for t in trips),
            "i_enter_gap_seconds": base.get("i_enter_gap_seconds"),
            "i_exit_gap_seconds": closer.get("i_exit_gap_seconds") if closer else None,
            "s_confirmed_by": (closer or base).get("s_confirmed_by"),
            "i_max_speed": max(speeds) if speeds else None,
        })
    return out


def physical_visits(visits: list[dict], meta: dict[int, dict] | None = None) -> list[dict]:
    by_key: dict[tuple, list] = defaultdict(list)
    for v in visits:
        # A visit without a vehicle cannot be matched to another trip's copy.
        key = (v["s_asset_id"] or f"trip:{v['i_trip_no']}", v["i_fence_id"])
        by_key[key].append(v)
    out: list[dict] = []
    for copies in by_key.values():
        out.extend(merge_visits(copies, meta))
    out.sort(key=lambda p: (p["dt_enter"], p["i_fence_id"]))
    return out


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------

def physical_alerts(violations: list[dict], pvisits: list[dict],
                    meta: dict[int, dict] | None = None) -> list[dict]:
    """One breach per physical visit and kind.

    Restricted entries keep the earliest stamp (the entry); overspeed keeps
    the worst fix, as the per-trip rule does.
    """
    meta = meta or {}
    spans: dict[tuple, list] = defaultdict(list)
    for i, p in enumerate(pvisits):
        asset = p["s_asset_id"] or f"trip:{p['trips'][0]}"
        spans[(asset, p["i_fence_id"])].append((p["dt_enter"], _end(p), i))

    chosen: dict[tuple, dict] = {}
    trips: dict[tuple, set] = defaultdict(set)
    for v in violations:
        asset = v["s_asset_id"] or f"trip:{v['i_trip_no']}"
        owner = next((i for a, b, i in spans.get((asset, v["i_fence_id"]), ())
                      if a <= v["dt_event"] <= b), None)
        key = (asset, v["i_fence_id"], v["s_kind"], owner if owner is not None else v["dt_event"])
        trips[key].add(v["i_trip_no"])
        cur = chosen.get(key)
        if cur is None:
            chosen[key] = v
        elif v["s_kind"] == "overspeed":
            if (v["i_observed"] or 0) > (cur["i_observed"] or 0):
                chosen[key] = v
        elif v["dt_event"] < cur["dt_event"]:
            chosen[key] = v

    out = []
    for key, v in chosen.items():
        ts = sorted(trips[key])
        out.append({**v, "trips": ts,
                    "s_trans_name": _majority(meta.get(t, {}).get("s_trans_name") for t in ts),
                    "s_driver_name": _majority(meta.get(t, {}).get("s_driver_name") for t in ts)})
    out.sort(key=lambda a: a["dt_event"])
    return out


# ---------------------------------------------------------------------------
# stops, gaps, inferred passages
# ---------------------------------------------------------------------------

def vehicle_key(r: dict):
    """A row's vehicle, or its trip when the feed gave no vehicle id."""
    return r["s_asset_id"] or f"trip:{r['i_trip_no']}"


def merge_copies(rows: list[dict], start: str, end: str, key=vehicle_key,
                 touching: bool = False) -> list[list[dict]]:
    """Cluster rows that are copies of one event: same key(row), different
    trips, overlapping in time. Identical intervals merge whatever their trips.

    Rows of one trip are never linked to each other directly. A trip's own
    ledger holds no copies, and consecutive events in it can share an endpoint:
    a hole in the trail runs from one fix to the next, so the next hole starts
    at the fix where this one ended. Chaining those would count a sparse
    journey as one gap -- on run 6, 3,508 pairs of one trip's moving gaps touch
    this way, against 62 genuine copies from other trips.

    `touching` is whether copies can merely touch. It can for events that
    contain their boundary fixes (a stop): a trip ending at the fix where the
    next trip begins splits one stop into two that share it. It cannot for
    events that lie *between* fixes (a gap, a passage inferred across one),
    where a shared endpoint is an observation separating two holes.
    """
    by: dict = defaultdict(list)
    for r in rows:
        by[key(r)].append(r)
    clusters: list[list[dict]] = []
    for items in by.values():
        items.sort(key=lambda r: (r[start], r[end]))
        parent = list(range(len(items)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        active: list[int] = []
        for i, r in enumerate(items):
            active = [j for j in active if items[j][end] > r[start]
                      or (touching and items[j][end] == r[start])
                      or (items[j][start], items[j][end]) == (r[start], r[end])]
            for j in active:
                a = items[j]
                if (a[start], a[end]) == (r[start], r[end]) or (
                        a["i_trip_no"] != r["i_trip_no"]
                        and (r[start] < a[end] or (touching and r[start] == a[end]))):
                    parent[find(i)] = find(j)
            active.append(i)
        groups: dict[int, list[dict]] = defaultdict(list)
        for i, r in enumerate(items):
            groups[find(i)].append(r)
        clusters.extend(groups.values())
    return clusters


def merge_intervals(rows: list[dict], start: str, end: str, key=vehicle_key) -> list[list[dict]]:
    """Cluster rows whose [start, end] overlap or touch within the same key(row):
    a plain union, for measuring time (see facility_places). To fold copies of
    an event, use merge_copies."""
    by: dict = defaultdict(list)
    for r in rows:
        by[key(r)].append(r)
    clusters: list[list[dict]] = []
    for items in by.values():
        items.sort(key=lambda r: (r[start], r[end]))
        cur: list[dict] = []
        cur_end = None
        for r in items:
            if cur and r[start] <= cur_end:
                cur.append(r)
                cur_end = max(cur_end, r[end])
            else:
                if cur:
                    clusters.append(cur)
                cur, cur_end = [r], r[end]
        if cur:
            clusters.append(cur)
    return clusters


def physical_stops(stops: list[dict], meta: dict[int, dict] | None = None) -> list[dict]:
    meta = meta or {}
    out = []
    for cluster in merge_copies(stops, "dt_start", "dt_end", touching=True):
        rep = max(cluster, key=lambda s: s["i_duration_s"])
        start = min(s["dt_start"] for s in cluster)
        end = max(s["dt_end"] for s in cluster)
        ts = sorted({s["i_trip_no"] for s in cluster})
        m = meta.get(rep["i_trip_no"], {})
        out.append({**rep, "dt_start": start, "dt_end": end,
                    "i_duration_s": int((end - start).total_seconds()), "trips": ts,
                    "s_trans_name": _majority(meta.get(t, {}).get("s_trans_name") for t in ts),
                    "s_origin": m.get("s_origin"), "s_destination": m.get("s_destination")})
    out.sort(key=lambda s: s["dt_start"])
    return out


def count_physical(rows: list[dict], start: str, end: str, key=vehicle_key) -> list[dict]:
    """One representative row per event (moving gaps, inferred passages): copies
    from other trips folded, a trip's own consecutive holes kept apart."""
    return [c[0] for c in merge_copies(rows, start, end, key)]


# ---------------------------------------------------------------------------
# time at facilities
# ---------------------------------------------------------------------------

def facility_places(pvisits: list[dict]) -> list[dict]:
    """A vehicle's time at facilities, with nesting and copies collapsed.

    A truck inside a mill inside a works is at one place, and it stays at
    that place while it is in the works but outside the mill. Summing only
    the innermost ("primary") visits loses that time -- the works visit is
    never primary, because the mill overlaps it -- so time at facilities is
    the union of the vehicle's facility-scale visit intervals. This is the
    physical form of the per-trip `places()` in pipeline/runner.py.
    """
    rows = [{"s_asset_id": p["s_asset_id"], "i_trip_no": p["trips"][0],
             "start": p["dt_enter"], "end": _end(p), "p": p}
            for p in pvisits if p["s_scale"] in FACILITY]
    out = []
    for cluster in merge_intervals(rows, "start", "end"):
        start = min(r["start"] for r in cluster)
        end = max(r["end"] for r in cluster)
        out.append({"s_asset_id": cluster[0]["s_asset_id"], "start": start, "end": end,
                    "dwell_s": int((end - start).total_seconds()),
                    "open": any(r["p"]["b_open"] for r in cluster),
                    "trips": sorted({t for r in cluster for t in r["p"]["trips"]})})
    out.sort(key=lambda c: c["start"])
    return out


# ---------------------------------------------------------------------------
# distance
# ---------------------------------------------------------------------------

def _haversine_m(lat1, lon1, lat2, lon2):
    # Same formula as the fit's path length (prep/fit.py), so a trip's own
    # distance here reproduces geo_trip_summary.d_distance_km.
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (np.sin((p2 - p1) / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(np.radians(np.asarray(lon2) - np.asarray(lon1)) / 2) ** 2)
    return 2 * 6_371_008.8 * np.arcsin(np.sqrt(np.minimum(1.0, a)))


def exclusive_distance(trails: list[tuple], max_gap_s: float) -> dict[int, tuple[float, float]]:
    """Path length per trip, whole and exclusive of other trips' copies.

    `trails` holds (trip_no, vehicle, t, lat, lon): the kept fixes of one
    trip in time order, t in epoch seconds. A trip's own length sums the
    steps between consecutive fixes no more than `max_gap_s` apart, exactly as
    the fit measures it. Its exclusive length drops the steps that fall inside
    the time span of a lower-numbered trip on the same vehicle -- that trip
    already carries the same fixes -- so exclusive lengths add up across any
    set of trips without driving a shared kilometre twice.

    Returns {trip_no: (own_m, exclusive_m)}.
    """
    by_vehicle: dict = defaultdict(list)
    for trail in trails:
        by_vehicle[trail[1]].append(trail)
    out: dict[int, tuple[float, float]] = {}
    for items in by_vehicle.values():
        items.sort(key=lambda x: x[0])
        spans: list[list[float]] = []            # merged time spans of trips already seen
        for trip_no, _, t, lat, lon in items:
            t = np.asarray(t, dtype=np.float64)
            if len(t) < 2:
                out[trip_no] = (0.0, 0.0)
            else:
                seg = _haversine_m(lat[:-1], lon[:-1], lat[1:], lon[1:])
                step = np.diff(t) <= max_gap_s
                own = float(seg[step].sum())
                if spans:
                    starts = np.array([s[0] for s in spans])
                    ends = np.array([s[1] for s in spans])
                    mid = (t[:-1] + t[1:]) / 2.0
                    i = np.searchsorted(starts, mid, side="right") - 1
                    covered = (i >= 0) & (mid <= ends[np.maximum(i, 0)])
                    out[trip_no] = (own, float(seg[step & ~covered].sum()))
                else:
                    out[trip_no] = (own, own)
            if len(t):
                spans.append([float(t[0]), float(t[-1])])
                spans.sort()
                merged = [spans[0]]
                for a, b in spans[1:]:
                    if a <= merged[-1][1]:
                        merged[-1][1] = max(merged[-1][1], b)
                    else:
                        merged.append([a, b])
                spans = merged
    return out


# ---------------------------------------------------------------------------
# trip shares
# ---------------------------------------------------------------------------

def trip_shares(trip_nos, pvisits: list[dict], palerts: list[dict], inferred: list[dict],
                distance: dict[int, tuple[float, float]], pstops: list[dict] | None = None) -> dict[int, dict]:
    """Each trip's share of the physical ledger.

    Every physical event belongs to exactly one trip -- the lowest-numbered
    trip that saw it -- so summing shares over any group of trips (a
    vehicle's, a transporter's, a lane's) counts each real event once, while
    the group's trip count still counts consignments. `siblings` names the
    other trips that shared this trip's fixes, which is why summing the
    per-trip figures would have counted twice.
    """
    # Distance stays None for a trip with no stored trail: unknown, not zero.
    shares = {t: {"facility_visits": 0, "facility_dwell_s": 0, "alerts": 0, "overspeed": 0,
                  "restricted": 0, "inferred": 0, "distance_m": None, "own_distance_m": None,
                  "siblings": set()}
              for t in trip_nos}

    def share(trip):
        return shares.setdefault(trip, {"facility_visits": 0, "facility_dwell_s": 0, "alerts": 0,
                                        "overspeed": 0, "restricted": 0, "inferred": 0,
                                        "distance_m": None, "own_distance_m": None, "siblings": set()})

    def siblings(trips):
        if len(trips) > 1:
            for t in trips:
                share(t)["siblings"].update(x for x in trips if x != t)

    for p in pvisits:
        siblings(p["trips"])
        if p["b_primary"] and p["s_scale"] in FACILITY:
            share(p["trips"][0])["facility_visits"] += 1
    for place in facility_places(pvisits):
        share(place["trips"][0])["facility_dwell_s"] += place["dwell_s"]
    for a in palerts:
        s = share(a["trips"][0])
        s["alerts"] += 1
        s["overspeed" if a["s_kind"] == "overspeed" else "restricted"] += 1
    for cluster in merge_copies(inferred, "dt_gap_from", "dt_gap_to",
                                key=lambda r: (vehicle_key(r), r["i_fence_id"])):
        share(min(r["i_trip_no"] for r in cluster))["inferred"] += 1
    for s in pstops or ():
        siblings(s["trips"])
    for trip, (own, excl) in distance.items():
        s = share(trip)
        s["own_distance_m"], s["distance_m"] = own, excl
    return shares
