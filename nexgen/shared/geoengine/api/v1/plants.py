"""Plants & congestion: who was inside a plant at any moment, and when it overloaded.

    GET /api/v1/geo/plants                     plants with traffic, busiest first
    GET /api/v1/geo/plants/scenes              what the scan found: overload scenes, worst first
    GET /api/v1/geo/plants/{site_id}           one plant: zones, timeline by zone kind, its scenes
    GET /api/v1/geo/plants/{site_id}/inside    who was inside at a moment, where, moving or standing
    GET /api/v1/geo/plants/proof/{dataset}     how a figure was counted, with its records (and CSV)

The arithmetic is reporting/congestion.py. This module loads the published
run's physical ledger into it once per data version (about a second for the
18,755 facility stays of run 1) and answers every request from that model, so
the plant list, a plant's page, the scene feed and every proof are counted
from the same stays at the same moment. The responses themselves are cached
per data version by the service's middleware like every other geo page.

Every figure here has a proof dataset that recounts it from its records:
`inside` (vehicles inside a fence at a moment), `peak` (the busiest moment of a
window), `usual` (the usual level and the threshold built on it), `episode`
(the vehicles in an overload), `waits` (how long the trucks that arrived during
it stayed), `stays` (a fence's usual stay), `scenes` (the feed's count),
`plants` (which fences are plants) and `tracked` (the vehicles that could be seen).
They answer in the shape core/proof/ProofPanel reads, so a plant page's tiles
open the same proof panel as the fleet pages.
"""

from __future__ import annotations

import calendar
import csv
import io
import math
import threading
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from nexgen.shared.geoengine import store
from nexgen.shared.geoengine.api.v1.common import resolve_run
from nexgen.shared.geoengine.db import get_geo_db
from nexgen.shared.geoengine.prep import codec
from nexgen.shared.geoengine.reporting import congestion as C

router = APIRouter(tags=["Plants & congestion"])

FAC = "('micro','site','campus')"
MAX_CSV_ROWS = 100_000
KIND_LABEL = {"plant": "Elsewhere in the plant", "gate": "Gates", "weighbridge": "Weighbridges",
              "parking": "Parking & yards", "loading": "Loading points", "road": "Internal roads",
              "area": "Other areas"}


# ---------------------------------------------------------------------------
# the model: one run's facility stays, folded into plants, once per version
# ---------------------------------------------------------------------------

@dataclass
class Model:
    rid: int
    version: str
    cfg: dict
    built_at: datetime
    grounds: dict[int, C.Ground]                       # by canonical fence id
    by_site: dict[int, C.Ground]                       # every copy's site id -> its ground
    plants: list[C.Plant]
    plant_of: dict[int, C.Plant]                       # ground id -> the plant it is (in)
    stays: dict[int, list[C.Stay]]                     # ground id -> its stays
    by_vehicle: dict[int, dict[str, list[C.Stay]]]     # ground id -> vehicle -> stays
    occ: dict[int, C.Occupancy]
    base: dict[int, C.Baseline]
    thr: dict[int, tuple[int, str]]
    episodes: dict[int, list[C.Episode]]
    scenes: list[C.Scene]
    tracked: C.Occupancy                               # vehicles on trips with GPS, over time
    tracked_spans: dict[str, list[list[datetime]]]     # vehicle -> its trips' GPS spans, overlaps merged
    unindexed: int = 0                                 # visited fences no longer in the active master
    zero_length: dict[int, int] = field(default_factory=dict)


_MODELS: dict[tuple, Model] = {}
_BUILD = threading.Lock()


def get_model(cur, rid: int) -> Model:
    """The run's model for this data version and these settings. A capacity
    saved from the Admin page rebuilds it at once; responses the service has
    already cached for this data version follow at the next refresh pass."""
    from nexgen.shared.geoengine.api import cache
    cfg = C.settings()
    key = (rid, cache.current_version(), tuple(sorted(cfg["capacity"].items())))
    hit = _MODELS.get(key)
    if hit is not None:
        return hit
    with _BUILD:
        hit = _MODELS.get(key)
        if hit is None:
            hit = _build(cur, rid, key[1], cfg)
            _MODELS.clear()                  # one version at a time: older ones are never asked for again
            _MODELS[key] = hit
    return hit


def _stay(r: dict) -> C.Stay:
    end = r["dt_exit"] or r["dt_enter"] + timedelta(seconds=int(r["i_dwell_seconds"] or 0))
    return C.Stay(id=int(r["id"]), vehicle=r["s_asset_id"] or "?", enter=r["dt_enter"], end=end,
                  open=bool(r["b_open"]), entry_observed=bool(r["b_entry_observed"]),
                  enter_gap_s=int(r["i_enter_gap_seconds"] or 0), exit_gap_s=int(r["i_exit_gap_seconds"] or 0),
                  trip_no=r["i_trip_no"], trips=r["s_trips"], transporter=r["s_trans_name"],
                  driver=r["s_driver_name"])


def _build(cur, rid: int, version: str, cfg: dict) -> Model:
    cur.execute(f"""SELECT id, i_fence_id, s_asset_id, dt_enter, dt_exit, b_open, b_entry_observed, i_dwell_seconds,
                           i_enter_gap_seconds, i_exit_gap_seconds, i_trip_no, s_trips, s_trans_name, s_driver_name
                      FROM geo_pvisit WHERE i_run_id=%s AND s_scale IN {FAC}""", (rid,))
    by_fence: dict[int, list[C.Stay]] = defaultdict(list)
    for r in cur.fetchall():
        by_fence[int(r["i_fence_id"])].append(_stay(r))
    idx = store.get_index()
    fences, unindexed = [], 0
    for fid in by_fence:
        f = idx.by_id(fid)
        if f is None:
            unindexed += 1
        else:
            fences.append(f)
    grounds = C.grounds_of(fences)
    plants = C.layout(grounds, idx, cfg["zone_kinds"])
    gmap = {g.id: g for g in grounds}
    by_site = {f.site_id: g for g in grounds for f in g.copies}
    plant_of: dict[int, C.Plant] = {}
    for p in plants:
        plant_of[p.id] = p
        for z in p.zones:
            plant_of[z.id] = p

    stays, by_vehicle, occ, base, thr, eps, zero = {}, {}, {}, {}, {}, {}, {}
    for g in grounds:
        s = sorted(by_fence[g.id], key=lambda x: x.enter)
        stays[g.id] = s
        bv: dict[str, list[C.Stay]] = defaultdict(list)
        for x in s:
            bv[x.vehicle].append(x)
        by_vehicle[g.id] = dict(bv)
        occ[g.id] = C.Occupancy(s)
        base[g.id] = C.baseline(occ[g.id], s, cfg)
        thr[g.id] = C.threshold(g.kind, base[g.id], cfg, g.fence.site_id)
        eps[g.id] = C.episodes(g.id, occ[g.id], s, base[g.id], thr[g.id], cfg)
        zero[g.id] = sum(1 for x in s if x.end <= x.enter)
    scenes: list[C.Scene] = []
    for p in plants:
        mine = [e for g in [p.ground, *p.zones] for e in eps[g.id]]
        scenes += C.scenes(p.id, mine, gmap, cfg)

    cur.execute("""SELECT s_asset_id, dt_first_ping, dt_last_ping FROM geo_trip_summary
                    WHERE i_run_id=%s AND dt_first_ping IS NOT NULL AND dt_last_ping IS NOT NULL""", (rid,))
    spans: dict[str, list[list[datetime]]] = defaultdict(list)
    for r in sorted(cur.fetchall(), key=lambda r: r["dt_first_ping"]):
        v = spans[r["s_asset_id"] or "?"]
        if v and r["dt_first_ping"] <= v[-1][1]:
            v[-1][1] = max(v[-1][1], r["dt_last_ping"])
        else:
            v.append([r["dt_first_ping"], r["dt_last_ping"]])
    tracked = C.Occupancy([C.Stay(id=0, vehicle=k, enter=a, end=b) for k, iv in spans.items() for a, b in iv])

    return Model(rid=rid, version=version, cfg=cfg, built_at=datetime.now(), grounds=gmap, by_site=by_site,
                 plants=plants, plant_of=plant_of, stays=stays, by_vehicle=by_vehicle, occ=occ, base=base, thr=thr,
                 episodes=eps, scenes=scenes, tracked=tracked, tracked_spans=dict(spans), unindexed=unindexed,
                 zero_length=zero)


# ---------------------------------------------------------------------------
# shapes
# ---------------------------------------------------------------------------

def _iso(t: datetime | None) -> str | None:
    return t.isoformat(sep=" ") if t else None


def _ring(f) -> list[list[float]]:
    return [[round(float(a), 7), round(float(b), 7)] for a, b in zip(f.ring_lat, f.ring_lon)]


def ground_brief(m: Model, g: C.Ground) -> dict:
    f = g.fence
    return {"site_id": f.site_id, "fence_id": g.id, "name": f.name, "kind": g.kind, "scale": g.scale,
            "type": f.site_type, "category": f.category, "area_m2": round(f.area_m2),
            "copies": [c.site_id for c in g.copies],
            "centroid": [round(f.centroid_lat, 7), round(f.centroid_lon, 7)]}


def ground_stats(m: Model, g: C.Ground, a: datetime | None = None, b: datetime | None = None) -> dict:
    occ, base = m.occ[g.id], m.base[g.id]
    peak, peak_at = occ.peak(a, b)
    thr, basis = m.thr[g.id]
    stays = m.stays[g.id]
    return {"stays": len(stays), "vehicles": len(m.by_vehicle[g.id]), "usual": base.usual, "threshold": thr,
            "threshold_basis": basis, "baseline_thin": base.thin, "busy_hours": round(base.busy_s / 3600, 1),
            "stay_p50_s": base.stay_p50_s, "stays_measured": base.stays_measured, "peak": peak,
            "peak_at": _iso(peak_at), "first": _iso(occ.first), "last": _iso(occ.last)}


def scene_id(ep: C.Episode) -> str:
    return f"{ep.ground_id}-{ep.start:%Y%m%d%H%M}"


def scene_json(m: Model, sc: C.Scene) -> dict:
    ep = sc.primary
    g = m.grounds[ep.ground_id]
    p = m.plant_of[sc.plant_id]
    return {
        "id": scene_id(ep), "plant": {"site_id": p.ground.fence.site_id, "name": p.ground.fence.name},
        "zone": ground_brief(m, g), "kind": g.kind, "severity": C.severity(ep), "score": C.score(ep),
        "start": _iso(ep.start), "end": _iso(ep.end), "minutes": ep.minutes, "peak": ep.peak,
        "peak_at": _iso(ep.peak_at), "usual": ep.usual, "threshold": ep.threshold,
        "threshold_basis": ep.threshold_basis, "baseline_thin": m.base[g.id].thin,
        "vehicles": len(ep.vehicles), "arrivals": ep.arrivals, "wait_p50_s": ep.wait_p50_s,
        "waits_measured": ep.waits_measured, "usual_stay_p50_s": ep.usual_stay_p50_s,
        "uncertain": ep.uncertain, "open_stays": ep.open_stays, "tracked_at_peak": m.tracked.at(ep.peak_at),
        "headline": C.headline(g.kind, g.fence.name, p.ground.fence.name, ep),
        "members": [{"site_id": m.grounds[e.ground_id].fence.site_id, "name": m.grounds[e.ground_id].fence.name,
                     "kind": m.grounds[e.ground_id].kind, "scale": m.grounds[e.ground_id].scale,
                     "peak": e.peak, "peak_at": _iso(e.peak_at), "start": _iso(e.start), "end": _iso(e.end),
                     "usual": e.usual, "threshold": e.threshold} for e in sc.members],
    }


def _ground(m: Model, site_id: int) -> C.Ground:
    g = m.by_site.get(int(site_id))
    if g is None:
        raise HTTPException(404, f"site {site_id} is not a facility fence with stays in run {m.rid}")
    return g


def _plant(m: Model, site_id: int) -> tuple[C.Plant, C.Ground]:
    g = _ground(m, site_id)
    return m.plant_of[g.id], g


def parse_time(value: str | None, name: str, end_of_day: bool = False) -> datetime | None:
    """A moment from the query string: 2026-10-05, 2026-10-05T10:57 or 2026-10-05 10:57:30.
    A bare date is its midnight, or the next midnight as the end of a range."""
    if not value:
        return None
    v = value.strip().replace("T", " ")
    try:
        if len(v) == 10:
            d = date.fromisoformat(v)
            return datetime.combine(d + timedelta(days=1) if end_of_day else d, datetime.min.time())
        return datetime.fromisoformat(v)
    except ValueError:
        raise HTTPException(400, f"bad {name} {value!r}; use YYYY-MM-DD or YYYY-MM-DDTHH:MM") from None


def _window(m: Model, g: C.Ground, d_from: str | None, d_to: str | None) -> tuple[datetime, datetime]:
    """The window a plant page shows: what it asks for, else the last `window_days` of the plant's data."""
    occ = m.occ[g.id]
    a, b = parse_time(d_from, "from"), parse_time(d_to, "to", end_of_day=True)
    last = occ.last or datetime.now()
    first = occ.first or last - timedelta(days=1)
    b = b or last
    a = a or max(first, b - timedelta(days=float(m.cfg["window_days"])))
    if b <= a:
        raise HTTPException(400, "the window ends before it starts")
    return a, b


# ---------------------------------------------------------------------------
# the plant list and the scene feed
# ---------------------------------------------------------------------------

@router.get("/plants")
def plants(q: str | None = None, run: int | None = None, conn=Depends(get_geo_db)):
    """Every plant with tracked traffic: its zones, busiest moment, usual level and scenes."""
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        m = get_model(cur, rid)
    by_plant: dict[int, list[C.Scene]] = defaultdict(list)
    for sc in m.scenes:
        by_plant[sc.plant_id].append(sc)
    out = []
    for p in m.plants:
        f = p.ground.fence
        if q and q.lower() not in f.name.lower() and not any(q.lower() in z.fence.name.lower() for z in p.zones):
            continue
        kinds: dict[str, int] = defaultdict(int)
        for z in p.zones:
            kinds[z.kind] += 1
        mine = by_plant.get(p.id, [])
        worst = max(mine, key=lambda s: C.score(s.primary), default=None)
        out.append({**ground_brief(m, p.ground), **ground_stats(m, p.ground), "zones": len(p.zones),
                    "zone_kinds": dict(kinds), "scenes": len(mine),
                    "scenes_high": sum(1 for s in mine if C.severity(s.primary) == "high"),
                    "last_scene": _iso(max((s.primary.start for s in mine), default=None)),
                    "worst": scene_json(m, worst) if worst else None})
    out.sort(key=lambda r: (-r["scenes_high"], -r["scenes"], -r["stays"]))
    kinds_found: dict[str, int] = defaultdict(int)
    for sc in m.scenes:
        kinds_found[m.grounds[sc.primary.ground_id].kind] += 1
    return {"run_id": rid, "computed_at": _iso(m.built_at), "plants": out,
            "totals": {"plants": len(out), "scenes": sum(r["scenes"] for r in out),
                       "scenes_high": sum(r["scenes_high"] for r in out),
                       "zones": sum(r["zones"] for r in out), "scenes_by_kind": dict(kinds_found)},
            "settings": _settings_view(m.cfg), "unindexed_fences": m.unindexed}


def _settings_view(cfg: dict) -> dict:
    return {k: cfg[k] for k in ("min_minutes", "merge_gap_minutes", "usual_percentile", "min_busy_hours", "margin",
                                "uncertain_gap_s", "scene_overlap", "same_place_area_ratio", "min_vehicles")}


def _filter_scenes(m: Model, site_id: int | None, kind: str | None, severity: str | None,
                   a: datetime | None, b: datetime | None) -> list[C.Scene]:
    plant_id = None
    if site_id is not None:
        plant_id = m.plant_of[_ground(m, site_id).id].id
    out = []
    for sc in m.scenes:
        ep = sc.primary
        if plant_id is not None and sc.plant_id != plant_id:
            continue
        if kind and m.grounds[ep.ground_id].kind != kind:
            continue
        if severity and C.severity(ep) != severity:
            continue
        if a and sc.end <= a:
            continue
        if b and sc.start >= b:
            continue
        out.append(sc)
    return out


@router.get("/plants/scenes")
def plant_scenes(site_id: int | None = None, kind: str | None = None, severity: str | None = None,
                 date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                 sort: str = "score", page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
                 run: int | None = None, conn=Depends(get_geo_db)):
    """What the scan found: every overload scene, worst first (or newest first with sort=time)."""
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        m = get_model(cur, rid)
    a, b = parse_time(date_from, "from"), parse_time(date_to, "to", end_of_day=True)
    found = _filter_scenes(m, site_id, kind, severity, a, b)
    if sort == "time":
        found.sort(key=lambda s: s.primary.start, reverse=True)
    else:
        found.sort(key=lambda s: (-C.score(s.primary), s.primary.start))
    total = len(found)
    items = [scene_json(m, s) for s in found[(page - 1) * page_size: page * page_size]]
    kinds: dict[str, int] = defaultdict(int)
    for s in found:
        kinds[m.grounds[s.primary.ground_id].kind] += 1
    return {"run_id": rid, "computed_at": _iso(m.built_at), "total": total, "page": page, "page_size": page_size,
            "pages": max(1, -(-total // page_size)), "items": items, "by_kind": dict(kinds),
            "high": sum(1 for s in found if C.severity(s.primary) == "high"),
            "days": sorted({s.primary.start.date().isoformat() for s in found})}


# ---------------------------------------------------------------------------
# one plant
# ---------------------------------------------------------------------------

def _zone_spans(m: Model, plant: C.Plant) -> dict[str, list[tuple[datetime, datetime, float, str, C.Ground]]]:
    """Every vehicle's stays in the plant's zones, for 'which zone was it in'."""
    out: dict[str, list] = defaultdict(list)
    for z in plant.zones:
        for v, ss in m.by_vehicle[z.id].items():
            for s in ss:
                if s.end > s.enter:
                    out[v].append((s.enter, s.end, z.area, z.kind, z))
    return out


def _innermost(spans: list, t: datetime):
    best = None
    for a, b, area, _kind, z in spans:
        if a <= t < b and (best is None or area < best[2]):
            best = (a, b, area, _kind, z)
    return best


@router.get("/plants/{site_id:int}")
def plant_detail(site_id: int, date_from: str | None = Query(None, alias="from"),
                 date_to: str | None = Query(None, alias="to"), run: int | None = None,
                 conn=Depends(get_geo_db)):
    """One plant: its zones, how full it was over the window by zone kind, and its scenes."""
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        m = get_model(cur, rid)
    plant, asked = _plant(m, site_id)
    g = plant.ground
    a, b = _window(m, g, date_from, date_to)
    res = C.resolution_minutes(b - a)
    step = timedelta(minutes=res)
    t0 = a.replace(second=0, microsecond=0) - timedelta(minutes=a.minute % res if res < 60 else a.minute)
    samples: list[datetime] = []
    t = t0
    while t < b:
        samples.append(t)
        t += step

    # Who was where at each sample: the plant's own stays, each vehicle in its innermost zone.
    spans = _zone_spans(m, plant)
    rows = [{"t": _iso(s), "total": 0, "max": 0, "tracked": m.tracked.at(s), **{k: 0 for k in KIND_LABEL}}
            for s in samples]
    for st in m.stays[g.id]:
        if st.end <= st.enter or st.end <= t0 or st.enter >= b:
            continue
        i = bisect_left(samples, st.enter)
        while i < len(samples) and samples[i] < st.end:
            hit = _innermost(spans.get(st.vehicle, []), samples[i])
            rows[i][hit[3] if hit else "plant"] += 1
            rows[i]["total"] += 1
            i += 1
    occ = m.occ[g.id]
    for i, s in enumerate(samples):
        rows[i]["max"] = occ.peak(s, s + step)[0]

    zones = []
    for z in plant.zones:
        n_sc = sum(1 for sc in m.scenes if sc.plant_id == plant.id and sc.primary.ground_id == z.id
                   and sc.end > a and sc.start < b)
        zones.append({**ground_brief(m, z), **ground_stats(m, z, a, b), "scenes": n_sc, "ring": _ring(z.fence)})
    scenes = [scene_json(m, s) for s in _filter_scenes(m, plant.ground.fence.site_id, None, None, a, b)]
    scenes.sort(key=lambda s: s["start"])
    peak, peak_at = occ.peak(a, b)
    return {"run_id": rid, "computed_at": _iso(m.built_at), "plant": {**ground_brief(m, g), **ground_stats(m, g),
            "ring": _ring(g.fence)},
            "focus": ground_brief(m, asked) if asked is not g else None,
            "window": {"from": _iso(a), "to": _iso(b), "resolution_minutes": res, "peak": peak,
                       "peak_at": _iso(peak_at), "usual": m.base[g.id].usual, "threshold": m.thr[g.id][0]},
            "kinds": KIND_LABEL, "timeline": rows, "zones": zones, "scenes": scenes,
            "settings": _settings_view(m.cfg)}


def _positions(cur, rid: int, wanted: dict[str, list[int]], at: datetime) -> dict[str, dict]:
    """Each vehicle's fitted position at `at`: its last fitted fix at or before it,
    from whichever of its trips carries the most recent one."""
    trips = sorted({t for ts in wanted.values() for t in ts})
    if not trips:
        return {}
    blobs: dict[int, np.ndarray] = {}
    for i in range(0, len(trips), 500):
        part = trips[i:i + 500]
        cur.execute(f"SELECT i_trip_no, m_data FROM geo_fit_trail WHERE i_run_id=%s AND i_trip_no IN "
                    f"({','.join(['%s'] * len(part))})", (rid, *part))
        for r in cur.fetchall():
            arr = codec.decode(r["m_data"])
            ok = (arr["reject"] == 0) & ~np.isnan(arr["lat"]) & ~np.isnan(arr["lon"])
            blobs[int(r["i_trip_no"])] = arr[ok]
    epoch = calendar.timegm(at.timetuple())
    out: dict[str, dict] = {}
    for vehicle, ts in wanted.items():
        best = None
        for trip in ts:
            arr = blobs.get(trip)
            if arr is None or not len(arr):
                continue
            j = int(np.searchsorted(arr["t"], epoch, side="right")) - 1
            if j < 0:
                continue
            if best is None or arr["t"][j] > best[1]["t"]:
                nxt = arr[j + 1] if j + 1 < len(arr) else None
                best = (trip, arr[j], nxt)
        if best is None:
            continue
        trip, p, nxt = best
        fix_t = datetime.fromtimestamp(int(p["t"]), tz=timezone.utc).replace(tzinfo=None)
        out[vehicle] = {"lat": round(float(p["lat"]), 7), "lon": round(float(p["lon"]), 7), "at": _iso(fix_t),
                        "age_s": int(epoch - int(p["t"])), "role": codec.ROLES[int(p["role"])] or None,
                        "trip": trip,
                        "next_s": int(int(nxt["t"]) - epoch) if nxt is not None else None}
    return out


def _trips_of(s: C.Stay) -> list[int]:
    out = []
    for part in (s.trips or "").replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    if s.trip_no and int(s.trip_no) not in out:
        out.append(int(s.trip_no))
    return out


@router.get("/plants/{site_id:int}/inside")
def plant_inside(site_id: int, at: str | None = None, run: int | None = None, conn=Depends(get_geo_db)):
    """Who was inside the plant at a moment: each vehicle's zone, how long it had been
    there, where its fitted trail put it and whether it was moving."""
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        m = get_model(cur, rid)
        plant, _asked = _plant(m, site_id)
        g = plant.ground
        t = parse_time(at, "at")
        if t is None:
            a, b = _window(m, g, None, None)
            t = m.occ[g.id].peak(a, b)[1] or b
        inside = [s for s in m.stays[g.id] if s.inside(t)]
        positions = _positions(cur, rid, {s.vehicle: _trips_of(s) for s in inside}, t)
    spans = _zone_spans(m, plant)
    gap = int(m.cfg["uncertain_gap_s"])
    rows, by_kind = [], defaultdict(int)
    for s in inside:
        hit = _innermost(spans.get(s.vehicle, []), t)
        kind = hit[3] if hit else "plant"
        by_kind[kind] += 1
        z = hit[4] if hit else None
        rows.append({
            "vehicle": s.vehicle, "trip": s.trip_no, "trips": s.trips, "transporter": s.transporter,
            "driver": s.driver, "entered": _iso(s.enter), "entry_observed": s.entry_observed,
            "inside_s": int((t - s.enter).total_seconds()), "left": None if s.open else _iso(s.end),
            "open": s.open, "uncertain": s.uncertain(gap), "enter_gap_s": s.enter_gap_s, "exit_gap_s": s.exit_gap_s,
            "kind": kind, "zone": ground_brief(m, z) if z else None,
            "zone_entered": _iso(hit[0]) if hit else None,
            "zone_inside_s": int((t - hit[0]).total_seconds()) if hit else None,
            "position": positions.get(s.vehicle)})
    rows.sort(key=lambda r: -r["inside_s"])
    zone_counts = [{"site_id": z.fence.site_id, "name": z.fence.name, "kind": z.kind, "scale": z.scale,
                    "inside": m.occ[z.id].at(t), "threshold": m.thr[z.id][0], "usual": m.base[z.id].usual}
                   for z in plant.zones if m.occ[z.id].at(t) > 0]
    zone_counts.sort(key=lambda r: -r["inside"])
    return {"run_id": rid, "at": _iso(t), "plant": ground_brief(m, g), "count": len(rows),
            "usual": m.base[g.id].usual, "threshold": m.thr[g.id][0], "tracked": m.tracked.at(t),
            "by_kind": dict(by_kind), "kinds": KIND_LABEL, "vehicles": rows, "zones": zone_counts,
            "positioned": sum(1 for r in rows if r["position"])}


# ---------------------------------------------------------------------------
# proof
# ---------------------------------------------------------------------------

STAY_COLUMNS = [
    {"key": "vehicle", "label": "Vehicle"},
    {"key": "entered", "label": "Entered", "kind": "datetime"},
    {"key": "entry_seen", "label": "Entry seen", "kind": "bool"},
    {"key": "left", "label": "Left (or last fix)", "kind": "datetime"},
    {"key": "still_inside", "label": "Trail ended inside", "kind": "bool"},
    {"key": "stay_min", "label": "Stay (min)", "kind": "number", "digits": 1},
    {"key": "enter_gap_s", "label": "GPS gap at entry (s)", "kind": "number"},
    {"key": "exit_gap_s", "label": "GPS gap at exit (s)", "kind": "number"},
    {"key": "transporter", "label": "Transporter"},
    {"key": "trip", "label": "Trip", "link": "/geo/trips/{trip}"},
]


def _stay_row(s: C.Stay, **extra) -> dict:
    return {"vehicle": s.vehicle, "entered": _iso(s.enter), "entry_seen": int(s.entry_observed),
            "left": _iso(s.end), "still_inside": int(s.open), "stay_min": round(s.seconds / 60, 1),
            "enter_gap_s": s.enter_gap_s, "exit_gap_s": s.exit_gap_s, "transporter": s.transporter or "",
            "trip": s.trip_no, **extra}


def _where_lines(m: Model, g: C.Ground) -> list[str]:
    f = g.fence
    copies = [c.site_id for c in g.copies]
    p = m.plant_of[g.id]
    lines = [f"Physical stays (geo_pvisit) of run {m.rid} at {f.name}, site {f.site_id} "
             f"({g.scale}, {round(f.area_m2):,} m²): one row per vehicle per stay, however many "
             f"consignments it carried."]
    if len(copies) > 1:
        lines.append(f"Sites {', '.join(map(str, copies))} are drawn on exactly this polygon with the same "
                     f"tolerance, so their stays are identical; they are counted once, from site {copies[0]}.")
    if p.ground is not g:
        article = "An" if g.kind[0] in "aeiou" else "A"
        lines.append(f"{article} {g.kind} zone of the plant {p.ground.fence.name}. Never added to the plant's or "
                     f"another zone's count: each fence is counted on its own.")
    return lines


def _tracked_line(m: Model, t: datetime) -> str:
    return (f"Only vehicles on trips with GPS can be seen: {m.tracked.at(t)} vehicles were being tracked "
            f"fleet-wide at {t:%d-%m-%Y %H:%M}. A truck without GPS inside the plant is not in this number.")


def _proof_inside(m: Model, g: C.Ground, p: dict) -> dict:
    t = parse_time(p.get("at"), "at")
    if t is None:
        raise HTTPException(400, "at is required")
    stays = m.stays[g.id]
    rows = [s for s in stays if s.inside(t)]
    hour = timedelta(hours=1)
    plant = m.plant_of[g.id]
    columns, where = STAY_COLUMNS, {}
    if plant.ground is g:
        # A plant's records say where in it each vehicle was: its innermost zone,
        # which is how the page splits the count by kind.
        spans = _zone_spans(m, plant)
        for s in rows:
            hit = _innermost(spans.get(s.vehicle, []), t)
            where[s.id] = {"zone": hit[4].fence.name if hit else "(no zone: elsewhere in the plant)",
                           "zone_kind": hit[3] if hit else "plant"}
        columns = [STAY_COLUMNS[0], {"key": "zone", "label": "Where at that moment"},
                   {"key": "zone_kind", "label": "Kind"}, *STAY_COLUMNS[1:]]
    return {
        "title": f"Vehicles inside {g.fence.name}", "format": "int", "value": len({s.vehicle for s in rows}),
        "method": [*_where_lines(m, g),
                   f"Inside at {t:%d-%m-%Y %H:%M:%S} means entered at or before it and not yet left: "
                   f"entered ≤ {t:%H:%M:%S} < left. A stay whose trail ended inside runs to its last fix there.",
                   _tracked_line(m, t),
                   "No transporter, vehicle, consignor or trip filter applies: every tracked vehicle counts."],
        "formula": f"{len(rows)} stays cover {t:%d-%m-%Y %H:%M:%S} = {len({s.vehicle for s in rows})} vehicles",
        "excluded": [
            {"label": "Left in the hour before (no longer inside)",
             "count": sum(1 for s in stays if t - hour <= s.end <= t and s.end > s.enter)},
            {"label": "Arrived in the hour after (not inside yet)",
             "count": sum(1 for s in stays if t < s.enter <= t + hour)},
            {"label": "Single-fix passes in that hour (no time inside)",
             "count": sum(1 for s in stays if s.end <= s.enter and abs((s.enter - t).total_seconds()) <= 3600)},
        ],
        "columns": columns, "rows": [_stay_row(s, **where.get(s.id, {})) for s in rows]}


def _proof_peak(m: Model, g: C.Ground, p: dict) -> dict:
    a, b = parse_time(p.get("from"), "from"), parse_time(p.get("to"), "to", end_of_day=True)
    occ = m.occ[g.id]
    peak, at = occ.peak(a, b)
    rows = [s for s in m.stays[g.id] if at and s.inside(at)]
    window = (f"between {a:%d-%m-%Y %H:%M} and {b:%d-%m-%Y %H:%M}" if a and b else
              "over the whole run" if not (a or b) else f"from {a:%d-%m-%Y %H:%M}" if a else f"up to {b:%d-%m-%Y %H:%M}")
    return {
        "title": f"Busiest moment at {g.fence.name}", "format": "int", "value": len(rows),
        "method": [*_where_lines(m, g),
                   f"Every entry adds one and every exit takes one away, in time order; the count is "
                   f"read {window} and the highest value is the peak.",
                   f"The records are the stays inside at the first moment the peak was reached "
                   f"({at:%d-%m-%Y %H:%M:%S})." if at else "No vehicle was inside in this window.",
                   *([_tracked_line(m, at)] if at else [])],
        "formula": (f"max(count {window}) = {peak}, first at {at:%d-%m-%Y %H:%M:%S}; "
                    f"{len(rows)} stays cover that moment") if at else "no stays in the window → 0",
        "excluded": [{"label": "Single-fix passes (no time inside, never counted)",
                      "count": m.zero_length.get(g.id, 0)}],
        "columns": STAY_COLUMNS, "rows": [_stay_row(s) for s in rows]}


def _threshold_line(m: Model, g: C.Ground) -> str:
    base, (thr, basis) = m.base[g.id], m.thr[g.id]
    floor = int(m.cfg["min_vehicles"].get(g.kind, m.cfg["min_vehicles"]["area"]))
    if basis == "capacity":
        return (f"Threshold: the capacity set for site {g.fence.site_id} in the client's settings "
                f"(plant_capacity) = {thr}.")
    if base.thin or base.usual is None:
        return (f"Threshold: only {base.busy_s / 3600:.1f} h of busy time (under the "
                f"{m.cfg['min_busy_hours']} h a usual level needs), so the minimum for a {g.kind} applies = {thr}.")
    over = base.usual + max(1, math.ceil(base.usual * float(m.cfg["margin"]) - 1e-9))
    return (f"Threshold: max(minimum for a {g.kind} = {floor}, usual {base.usual} + max(1, ceil({base.usual} × "
            f"{m.cfg['margin']})) = {over}) = {thr}. An overload is {thr} or more for at least "
            f"{m.cfg['min_minutes']} min; stretches under {m.cfg['merge_gap_minutes']} min apart are one.")


def _proof_usual(m: Model, g: C.Ground, p: dict) -> dict:
    base = m.base[g.id]
    total = sum(base.levels.values())
    rows, acc = [], 0.0
    for level in sorted(base.levels):
        acc += base.levels[level]
        rows.append({"level": level, "minutes": round(base.levels[level] / 60, 1),
                     "share": round(100 * base.levels[level] / total, 2) if total else 0,
                     "cumulative": round(100 * acc / total, 2) if total else 0})
    occ = m.occ[g.id]
    empty = occ.level_seconds().get(0, 0.0)
    q = m.cfg["usual_percentile"]
    return {
        "title": f"Usual level at {g.fence.name}", "format": "int", "value": base.usual,
        "method": [*_where_lines(m, g),
                   "Busy time is the time the fence held at least one vehicle; the time spent at each count is "
                   "measured from the entries and exits (weighted by time, not by visit).",
                   f"The usual level is the smallest count at or below which {q}% of the busy time was spent.",
                   _threshold_line(m, g)],
        "formula": (f"busy {total / 3600:.1f} h; cumulative share first reaches {q}% at {base.usual} vehicles"
                    if base.usual is not None else "no busy time → no usual level"),
        "excluded": [{"label": "Minutes with nobody inside (between the first entry and the last exit)",
                      "count": int(round(empty / 60))}],
        "columns": [{"key": "level", "label": "Vehicles inside", "kind": "number"},
                    {"key": "minutes", "label": "Minutes at this count", "kind": "number", "digits": 1},
                    {"key": "share", "label": "Share of busy time %", "kind": "number", "digits": 2},
                    {"key": "cumulative", "label": "Cumulative %", "kind": "number", "digits": 2}],
        "rows": rows}


def _episode_window(p: dict) -> tuple[datetime, datetime]:
    a, b = parse_time(p.get("from"), "from"), parse_time(p.get("to"), "to")
    if a is None or b is None:
        raise HTTPException(400, "from and to (the episode's start and end) are required")
    return a, b


def _proof_episode(m: Model, g: C.Ground, p: dict) -> dict:
    a, b = _episode_window(p)
    rows = [s for s in m.stays[g.id] if s.overlaps(a, b) and s.end > s.enter]
    gap = int(m.cfg["uncertain_gap_s"])
    vehicles = {s.vehicle for s in rows}
    return {
        "title": f"Vehicles in the overload at {g.fence.name}", "format": "int", "value": len(vehicles),
        "method": [*_where_lines(m, g), _threshold_line(m, g),
                   f"Counted: every vehicle inside at some point between {a:%d-%m-%Y %H:%M} and {b:%H:%M} "
                   f"(entered before the end and left after the start).",
                   f"Stays whose entry or exit lies in a GPS gap over {gap // 60} min are kept and marked: their "
                   f"times are known only to within that gap."],
        "formula": f"{len(rows)} stays overlap {a:%H:%M}–{b:%H:%M} = {len(vehicles)} vehicles",
        "excluded": [{"label": f"Entry or exit inside a GPS gap over {gap // 60} min (counted, uncertain)",
                      "count": sum(1 for s in rows if s.uncertain(gap))},
                     {"label": "Trail ended inside (counted; the stay is a lower bound)",
                      "count": sum(1 for s in rows if s.open)}],
        "columns": STAY_COLUMNS, "rows": [_stay_row(s) for s in rows]}


def _nth(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _median_line(values: list[float]) -> str:
    n = len(values)
    if not n:
        return "no fully observed stays → no median"
    v = sorted(values)
    if n == 1:
        return f"one stay = {v[0]:.1f} min"
    if n % 2:
        return f"median of {n} stays = the {_nth(n // 2 + 1)} shortest = {v[n // 2]:.1f} min"
    return (f"median of {n} stays = mean of the {_nth(n // 2)} and {_nth(n // 2 + 1)} shortest = "
            f"({v[n // 2 - 1]:.1f} + {v[n // 2]:.1f}) / 2 = {(v[n // 2 - 1] + v[n // 2]) / 2:.1f} min")


def _proof_waits(m: Model, g: C.Ground, p: dict) -> dict:
    a, b = _episode_window(p)
    arrived = [s for s in m.stays[g.id] if s.entry_observed and a <= s.enter < b]
    counted = [s for s in arrived if s.measured and s.seconds > 0]
    mins = [s.seconds / 60 for s in counted]
    return {
        "title": f"Stay of trucks arriving during the overload at {g.fence.name}", "format": "min",
        "value": round(float(np.median(mins)), 1) if mins else None,
        "method": [*_where_lines(m, g),
                   f"Arrivals: stays whose entry was seen between {a:%d-%m-%Y %H:%M} and {b:%H:%M}.",
                   "Counted: those whose exit was also seen, so the stay's length is known, not a lower bound.",
                   "Compared on the page with the fence's usual stay: the median of every fully observed stay "
                   "there in the run."],
        "formula": _median_line(mins),
        "excluded": [{"label": "Arrived but the trail ended inside (stay unknown)",
                      "count": sum(1 for s in arrived if s.open)},
                     {"label": "Single-fix passes (no time inside)", "count": sum(1 for s in arrived if s.seconds <= 0)},
                     {"label": "Already inside when its trail began (entry not seen; not an arrival)",
                      "count": sum(1 for s in m.stays[g.id] if not s.entry_observed and a <= s.enter < b)}],
        "columns": [*STAY_COLUMNS[:6], {"key": "counted", "label": "Counted", "kind": "bool"}, *STAY_COLUMNS[6:]],
        "rows": [_stay_row(s, counted=int(s in counted)) for s in arrived]}


def _proof_stays(m: Model, g: C.Ground, p: dict) -> dict:
    stays = m.stays[g.id]
    counted = [s for s in stays if s.measured and s.seconds > 0]
    mins = [s.seconds / 60 for s in counted]
    return {
        "title": f"Usual stay at {g.fence.name}", "format": "min",
        "value": round(float(np.median(mins)), 1) if mins else None,
        "method": [*_where_lines(m, g),
                   "Counted: every stay in the run whose entry and exit were both seen, so its length is known."],
        "formula": _median_line(mins),
        "excluded": [{"label": "Trail ended inside (stay is a lower bound)", "count": sum(1 for s in stays if s.open)},
                     {"label": "Already inside when the trail began (entry not seen)",
                      "count": sum(1 for s in stays if not s.entry_observed and not s.open)},
                     {"label": "Single-fix passes (no time inside)", "count": sum(1 for s in stays if s.seconds <= 0)}],
        "columns": STAY_COLUMNS, "rows": [_stay_row(s) for s in counted]}


SCENE_COLUMNS = [
    {"key": "start", "label": "From", "kind": "datetime"},
    {"key": "end", "label": "To", "kind": "datetime"},
    {"key": "plant", "label": "Plant", "link": "/geo/plants/{plant_site_id}"},
    {"key": "zone", "label": "Where"},
    {"key": "kind", "label": "Kind"},
    {"key": "peak", "label": "Peak", "kind": "number"},
    {"key": "usual", "label": "Usual", "kind": "number"},
    {"key": "threshold", "label": "Threshold", "kind": "number"},
    {"key": "minutes", "label": "Minutes", "kind": "number"},
    {"key": "severity", "label": "Severity"},
    {"key": "fences", "label": "Fences in the scene", "kind": "number"},
]


def _proof_scenes(m: Model, p: dict) -> dict:
    site = p.get("site_id")
    a, b = parse_time(p.get("from"), "from"), parse_time(p.get("to"), "to", end_of_day=True)
    found = _filter_scenes(m, int(site) if site else None, p.get("kind") or None, p.get("severity") or None, a, b)
    all_eps = sum(len(e) for e in m.episodes.values())
    rows = []
    for sc in found:
        ep = sc.primary
        g = m.grounds[ep.ground_id]
        pl = m.plant_of[sc.plant_id].ground.fence
        rows.append({"start": _iso(ep.start), "end": _iso(ep.end), "plant": pl.name, "plant_site_id": pl.site_id,
                     "zone": g.fence.name, "kind": g.kind, "peak": ep.peak, "usual": ep.usual,
                     "threshold": ep.threshold, "minutes": ep.minutes, "severity": C.severity(ep),
                     "fences": len(sc.members)})
    filters = [f"plant of site {site}" if site else "every plant",
               f"kind {p['kind']}" if p.get("kind") else "every kind",
               f"severity {p['severity']}" if p.get("severity") else "every severity",
               f"overlapping {a:%d-%m-%Y} to {b - timedelta(days=1):%d-%m-%Y}" if a and b else "the whole run"]
    return {
        "title": "Overload scenes", "format": "int", "value": len(rows),
        "method": [f"Every facility fence with stays in run {m.rid} is scanned: its usual level, its threshold, "
                   f"and the stretches at or above the threshold for at least {m.cfg['min_minutes']} min.",
                   f"Overloads at two drawings of one place (one inside the other, at most "
                   f"{m.cfg['same_place_area_ratio']}× its size), overlapping in time and sharing at least "
                   f"{int(100 * float(m.cfg['scene_overlap']))}% of the smaller crowd, are one scene.",
                   "Filters: " + "; ".join(filters) + "."],
        "formula": f"{all_eps} overloads in the run → {len(m.scenes)} scenes → {len(rows)} match the filters",
        "excluded": [{"label": "Overloads folded into another fence's scene (same place, same crowd)",
                      "count": sum(len(s.members) - 1 for s in found)}],
        "columns": SCENE_COLUMNS, "rows": rows}


def _proof_tracked(m: Model, p: dict) -> dict:
    t = parse_time(p.get("at"), "at")
    if t is None:
        raise HTTPException(400, "at is required")
    rows = []
    for v, spans in m.tracked_spans.items():
        for a, b in spans:
            if a <= t < b:
                rows.append({"vehicle": v, "from": _iso(a), "to": _iso(b)})
    return {
        "title": "Vehicles being tracked", "format": "int", "value": len(rows),
        "method": [f"Every trip of run {m.rid} with GPS runs from its first fix to its last (geo_trip_summary); "
                   "a vehicle's trips are merged where they overlap, so a truck carrying three consignments is "
                   "one vehicle.",
                   f"Tracked at {t:%d-%m-%Y %H:%M:%S}: first fix at or before it and last fix after it. A GPS gap "
                   "inside a trip still counts as tracked; the plant figures mark those stays separately."],
        "formula": f"{len(rows)} vehicles have a trip spanning {t:%d-%m-%Y %H:%M:%S}",
        "excluded": [{"label": "Vehicles whose GPS had ended by then",
                      "count": sum(1 for sp in m.tracked_spans.values() if sp and sp[-1][1] <= t)},
                     {"label": "Vehicles whose GPS had not started yet",
                      "count": sum(1 for sp in m.tracked_spans.values() if sp and sp[0][0] > t)}],
        "columns": [{"key": "vehicle", "label": "Vehicle", "link": "/geo/vehicles/{vehicle}"},
                    {"key": "from", "label": "GPS from", "kind": "datetime"},
                    {"key": "to", "label": "GPS to", "kind": "datetime"}],
        "rows": rows}


def _proof_plants(m: Model, p: dict) -> dict:
    rows = []
    for pl in m.plants:
        f, g = pl.ground.fence, pl.ground
        peak, at = m.occ[g.id].peak()
        rows.append({"plant": f.name, "site_id": f.site_id, "type": f.site_type or "", "scale": g.scale,
                     "zones": len(pl.zones), "stays": len(m.stays[g.id]), "vehicles": len(m.by_vehicle[g.id]),
                     "peak": peak, "peak_at": _iso(at), "usual": m.base[g.id].usual})
    zones = sum(len(pl.zones) for pl in m.plants)
    return {
        "title": "Plants with tracked traffic", "format": "int", "value": len(rows),
        "method": [f"Every facility fence (micro, site or campus scale: under 100 km²) with physical stays in run "
                   f"{m.rid}. Regional catchments (100 km² and over) are not facilities and are left out.",
                   "A plant is one whose centroid lies inside no larger visited facility fence; every visited "
                   "facility fence inside a plant is one of its zones. Nesting is decided by the engine's own "
                   "containment test, the one the visits were detected with.",
                   "Fences drawn on exactly the same polygon with the same tolerance are one ground, counted once."],
        "formula": (f"{len(m.grounds)} facility grounds with stays → {len(rows)} outermost (plants) + "
                    f"{zones} inside one (zones)"),
        "excluded": [{"label": "Identical copies folded into one ground",
                      "count": sum(len(g.copies) - 1 for g in m.grounds.values())},
                     {"label": "Visited fences no longer in the active master (cannot be placed)",
                      "count": m.unindexed}],
        "columns": [{"key": "plant", "label": "Plant", "link": "/geo/plants/{site_id}"},
                    {"key": "type", "label": "Type"}, {"key": "scale", "label": "Scale"},
                    {"key": "zones", "label": "Zones", "kind": "number"},
                    {"key": "stays", "label": "Stays", "kind": "number"},
                    {"key": "vehicles", "label": "Vehicles", "kind": "number"},
                    {"key": "peak", "label": "Busiest", "kind": "number"},
                    {"key": "peak_at", "label": "Busiest at", "kind": "datetime"},
                    {"key": "usual", "label": "Usual level", "kind": "number"}],
        "rows": rows}


PROOFS = {"inside": _proof_inside, "peak": _proof_peak, "usual": _proof_usual, "episode": _proof_episode,
          "waits": _proof_waits, "stays": _proof_stays}


@router.get("/plants/proof/{dataset}")
def plant_proof(dataset: str, request: Request, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500),
                sort: str | None = None, order: str = "desc", format: str | None = None, run: int | None = None,
                conn=Depends(get_geo_db)):
    """How a plant figure was counted: method, arithmetic, what was left out, and the records."""
    params = dict(request.query_params)
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        m = get_model(cur, rid)
    if dataset == "scenes":
        ans = _proof_scenes(m, params)
    elif dataset == "plants":
        ans = _proof_plants(m, params)
    elif dataset == "tracked":
        ans = _proof_tracked(m, params)
    elif dataset in PROOFS:
        if not params.get("site_id"):
            raise HTTPException(400, "site_id is required")
        try:
            g = _ground(m, int(params["site_id"]))
        except ValueError:
            raise HTTPException(400, "site_id must be a number") from None
        ans = PROOFS[dataset](m, g, params)
    else:
        raise HTTPException(404, f"no dataset {dataset!r}; one of {sorted([*PROOFS, 'scenes', 'plants', 'tracked'])}")
    rows = ans.pop("rows")
    keys = {c["key"] for c in ans["columns"]}
    if sort in keys:
        present = [r for r in rows if r.get(sort) not in (None, "")]
        missing = [r for r in rows if r.get(sort) in (None, "")]
        present.sort(key=lambda r: r[sort], reverse=(order != "asc"))
        rows = present + missing
    if format == "csv":
        return _csv(dataset, ans["columns"], rows[:MAX_CSV_ROWS])
    start = (page - 1) * page_size
    return {"dataset": dataset, **ans, "rows": rows[start:start + page_size], "total": len(rows), "page": page,
            "page_size": page_size, "computed_at": _iso(m.built_at), "run_id": rid}


def _csv(name: str, columns: list[dict], rows: list[dict]) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c["label"] for c in columns])
    for r in rows:
        w.writerow(["" if r.get(c["key"]) is None else r.get(c["key"]) for c in columns])
    data = buf.getvalue().encode("utf-8-sig")
    return StreamingResponse(iter([data]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
