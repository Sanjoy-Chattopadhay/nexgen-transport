"""A trip as a number line: loading, transit, unloading, and what filled each.

Every second of a trip's GPS span, from its first kept fix to its last, is
given to exactly one segment, so the segments add up to the whole trip and a
report can be read left to right like a ruler:

    before   ──  GPS before the first place: driving to the loading point
    loading  ──  the stay at the first place
    transit  ──  first place exit → last place arrival, itself split into
                   moving   the truck driving, GPS seen
                   stop     standing outside every facility fence (≥ 3 min),
                            or a tracker asleep while standing
                   halt     an intermediate place: a weighbridge, a yard
                   silent   the GPS was silent while the truck moved
    unloading──  the stay at the last place
    after    ──  GPS after the last place

These are the upload page's definitions (upload/trace.py, step 23) applied to
a stored trip: loading is the stay at the *first* place and unloading at the
*last*, because that is what they physically are, and transit is first exit →
last arrival, the same as geo_trip_summary.i_transit_s. A trip that saw one
place has no loading or unloading -- one place cannot say which it was -- and
a trip that saw none is all transit-like movement with no ends.

Places are nested facility visits collapsed into one (the works, not the
weighbridge inside it), exactly as the trip page draws them.

Kilometres are measured on the fitted trail with the fit's own rule -- a step
counts when its fixes are no more than the run's `max_gap_seconds` apart -- so
the ruler's last mark equals geo_trip_summary.d_distance_km. A silent stretch
longer than that carries no fitted kilometres; its straight-line length is
reported beside it as unobserved.
"""

from __future__ import annotations

import calendar
import json
import logging
from datetime import datetime, timedelta

import numpy as np

from nexgen.shared.geoengine.prep import codec

logger = logging.getLogger(__name__)

FACILITY = ("micro", "site", "campus")

PHASES = ("before", "loading", "transit", "unloading", "after", "place")


def _epoch(ts: datetime) -> int:
    return calendar.timegm(ts.timetuple())


def _from_epoch(t: float) -> datetime:
    return datetime(1970, 1, 1) + timedelta(seconds=float(t))


def _haversine_m(lat1, lon1, lat2, lon2):
    # The fit's path-length formula (prep/fit.py), so the ruler's last mark
    # reproduces the trip's recorded distance.
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (np.sin((p2 - p1) / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(np.radians(np.asarray(lon2) - np.asarray(lon1)) / 2) ** 2)
    return 2 * 6_371_008.8 * np.arcsin(np.sqrt(np.minimum(1.0, a)))


# ---------------------------------------------------------------------------
# places
# ---------------------------------------------------------------------------

def places(visits: list[dict]) -> list[dict]:
    """Nested facility visits collapsed into places, in time order.

    `visits` are geo_visit rows with the fence's area (d_area_sqm). Visits
    that overlap in time are one place, named by the outermost fence and
    located by the innermost -- pipeline/runner.places on stored rows.
    """
    fac = sorted((v for v in visits if v["s_scale"] in FACILITY), key=lambda v: v["dt_enter"])
    clusters: list[dict] = []
    for v in fac:
        end = v["dt_exit"] or v["dt_enter"] + timedelta(seconds=v["i_dwell_seconds"] or 0)
        if clusters and v["dt_enter"] <= clusters[-1]["end"]:
            c = clusters[-1]
            c["members"].append(v)
            c["end"] = max(c["end"], end)
            c["open"] = c["open"] or bool(v["b_open"])
        else:
            clusters.append({"start": v["dt_enter"], "end": end, "open": bool(v["b_open"]),
                             "members": [v]})
    out = []
    for c in clusters:
        outer = max(c["members"], key=lambda v: (v.get("d_area_sqm") or 0, -v["i_site_id"]))
        inner = min(c["members"], key=lambda v: (v.get("d_area_sqm") or 0, v["i_site_id"]))
        out.append({
            "site_id": outer["i_site_id"], "name": outer["s_site_name"], "scale": outer["s_scale"],
            "category": outer.get("s_category"),
            "innermost_site_id": inner["i_site_id"], "innermost": inner["s_site_name"],
            "enter": c["start"], "exit": None if c["open"] else c["end"],
            "last_seen": c["end"], "open": c["open"],
            "dwell_s": int((c["end"] - c["start"]).total_seconds()),
            "entry_observed": all(bool(m["b_entry_observed"]) for m in c["members"]
                                  if m["dt_enter"] == c["start"]),
            "zones": sorted({m["s_site_name"] for m in c["members"]}),
            "enter_gap_s": min((m["i_enter_gap_seconds"] for m in c["members"]
                                if m["dt_enter"] == c["start"] and m.get("i_enter_gap_seconds") is not None),
                               default=None),
        })
    return out


# ---------------------------------------------------------------------------
# the ruler: kilometres against time
# ---------------------------------------------------------------------------

class Odometer:
    """Cumulative fitted kilometres at any moment of the trip."""

    def __init__(self, t: np.ndarray, lat: np.ndarray, lon: np.ndarray, max_gap_s: float):
        self.t = np.asarray(t, dtype=np.float64)
        self.lat = np.asarray(lat, dtype=np.float64)
        self.lon = np.asarray(lon, dtype=np.float64)
        if len(self.t) > 1:
            seg = _haversine_m(self.lat[:-1], self.lon[:-1], self.lat[1:], self.lon[1:])
            seg = np.where(np.diff(self.t) <= max_gap_s, seg, 0.0)
            self.cum_m = np.concatenate([[0.0], np.cumsum(seg)])
        else:
            self.cum_m = np.zeros(len(self.t))

    @classmethod
    def from_blob(cls, blob: bytes | None, max_gap_s: float) -> "Odometer | None":
        if not blob:
            return None
        arr = codec.decode(blob)
        kept = arr[(arr["role"] != codec.ROLES.index("reject")) & ~np.isnan(arr["lat"])]
        if not len(kept):
            return None
        return cls(kept["t"], kept["lat"], kept["lon"], max_gap_s)

    @property
    def total_km(self) -> float:
        return float(self.cum_m[-1]) / 1000 if len(self.cum_m) else 0.0

    @property
    def span(self) -> tuple[datetime, datetime] | None:
        if not len(self.t):
            return None
        return _from_epoch(self.t[0]), _from_epoch(self.t[-1])

    def km_at(self, ts: datetime) -> float:
        if not len(self.t):
            return 0.0
        return float(np.interp(_epoch(ts), self.t, self.cum_m)) / 1000

    def position_at(self, ts: datetime) -> tuple[float, float] | None:
        if not len(self.t):
            return None
        e = _epoch(ts)
        return float(np.interp(e, self.t, self.lat)), float(np.interp(e, self.t, self.lon))


# ---------------------------------------------------------------------------
# segments
# ---------------------------------------------------------------------------

def _paint(timeline: list[list], start: datetime, end: datetime, kind: str, info: dict) -> None:
    """Overwrite [start, end) of a timeline of [start, end, kind, info] pieces."""
    if end <= start:
        return
    out: list[list] = []
    for a, b, k, i in timeline:
        if b <= start or a >= end:
            out.append([a, b, k, i])
            continue
        if a < start:
            out.append([a, start, k, i])
        if b > end:
            out.append([end, b, k, i])
    out.append([start, end, kind, info])
    out.sort(key=lambda p: p[0])
    timeline[:] = out


def segments(t0: datetime, t1: datetime, place_list: list[dict], stops: list[dict],
             gaps: list[dict], odo: Odometer | None = None) -> list[dict]:
    """The whole trip, [t0, t1], cut into consecutive segments.

    `stops` are geo_stop rows (i_site_id NULL = outside every facility fence);
    `gaps` are geo_gap rows. Every segment carries its phase, its kind, its
    time, and where on the kilometre ruler it starts and ends.
    """
    if t1 <= t0:
        return []
    n = len(place_list)
    # 1. Phases: places are fixed; the time between them belongs to a phase.
    pieces: list[list] = [[t0, t1, "moving", {}]]
    marks: list[tuple[datetime, datetime, str, dict]] = []
    for i, p in enumerate(place_list):
        a = max(p["enter"], t0)
        b = min(p["exit"] or p["last_seen"], t1)
        if n == 1:
            role = "place"
        elif i == 0:
            role = "loading"
        elif i == n - 1:
            role = "unloading"
        else:
            role = "halt"
        marks.append((a, b, role, {"site_id": p["site_id"], "name": p["name"],
                                   "innermost": p["innermost"], "open": p["open"],
                                   "entry_observed": p["entry_observed"], "scale": p["scale"]}))

    # 2. Inside the stretches between places: stops, then silent movement.
    for g in gaps:
        if g["s_kind"] != "moving":
            _paint(pieces, max(g["dt_from"], t0), min(g["dt_to"], t1), "stop",
                   {"asleep": True, "gap_s": g["i_gap_s"]})
    for s in stops:
        if s.get("i_site_id") is None:
            _paint(pieces, max(s["dt_start"], t0), min(s["dt_end"], t1), "stop",
                   {"lat": float(s["d_lat"]), "lon": float(s["d_long"])})
    for g in gaps:
        if g["s_kind"] == "moving":
            _paint(pieces, max(g["dt_from"], t0), min(g["dt_to"], t1), "silent",
                   {"straight_km": round(float(g["d_straight_m"] or 0) / 1000, 2),
                    "route_km": round(float(g["d_route_m"]) / 1000, 2) if g.get("d_route_m") else None})
    for a, b, role, info in marks:
        _paint(pieces, a, b, role, info)

    # 3. The phase each piece belongs to.
    out: list[dict] = []
    for a, b, kind, info in pieces:
        if b <= a:
            continue
        if kind in ("loading", "unloading", "place"):
            phase = kind
            kind = "stay"
        elif n == 0:
            phase = "transit"
        elif n == 1:
            phase = "before" if b <= place_list[0]["enter"] else "after"
        elif b <= place_list[0]["enter"]:
            phase = "before"
        elif a >= (place_list[-1]["exit"] or place_list[-1]["last_seen"]):
            phase = "after"
        else:
            phase = "transit"
        # Merge a piece into the previous one when nothing distinguishes them.
        if out and out[-1]["phase"] == phase and out[-1]["kind"] == kind == "moving":
            out[-1]["end"] = b
            continue
        out.append({"phase": phase, "kind": kind, "start": a, "end": b, **info})
    for s in out:
        s["duration_s"] = int((s["end"] - s["start"]).total_seconds())
        if odo is not None:
            s["km_from"] = round(odo.km_at(s["start"]), 2)
            s["km_to"] = round(odo.km_at(s["end"]), 2)
    return out


def summarise(segs: list[dict], place_list: list[dict], odo: Odometer | None) -> dict:
    """The phase totals of one trip: the columns of geo_trip_phase."""
    def total(phase: str, kind: str | None = None) -> int:
        return sum(s["duration_s"] for s in segs if s["phase"] == phase and (kind is None or s["kind"] == kind))

    def km(phase: str) -> float:
        return round(sum((s.get("km_to", 0) - s.get("km_from", 0)) for s in segs if s["phase"] == phase), 2)

    n = len(place_list)
    load = place_list[0] if n >= 2 else None
    unload = place_list[-1] if n >= 2 else None
    transit = [s for s in segs if s["phase"] == "transit"]
    transit_s = (int((unload["enter"] - (load["exit"] or load["last_seen"])).total_seconds())
                 if load and unload else None)
    moving_s = total("transit", "moving") if n >= 2 else None
    transit_km = km("transit") if (n >= 2 and odo is not None) else None
    return {
        "shape": "loaded" if n >= 2 else "one_place" if n == 1 else "no_place",
        "places": n,
        "span_s": sum(s["duration_s"] for s in segs),
        "loading_site_id": load["site_id"] if load else None,
        "loading_site": load["name"] if load else None,
        "loading_in": load["enter"] if load else None,
        "loading_out": (load["exit"] or load["last_seen"]) if load else None,
        "loading_s": load["dwell_s"] if load else None,
        "loading_in_seen": bool(load["entry_observed"]) if load else None,
        "unloading_site_id": unload["site_id"] if unload else None,
        "unloading_site": unload["name"] if unload else None,
        "unloading_in": unload["enter"] if unload else None,
        "unloading_out": unload["exit"] if unload else None,
        "unloading_s": unload["dwell_s"] if unload else None,
        "unloading_open": bool(unload["open"]) if unload else None,
        "transit_s": transit_s,
        "transit_moving_s": moving_s,
        "transit_stop_s": total("transit", "stop") if n >= 2 else None,
        "transit_halt_s": total("transit", "halt") if n >= 2 else None,
        "transit_silent_s": total("transit", "silent") if n >= 2 else None,
        "transit_stops": sum(1 for s in transit if s["kind"] == "stop") if n >= 2 else None,
        "transit_halts": sum(1 for s in transit if s["kind"] == "halt") if n >= 2 else None,
        "before_s": total("before"),
        "after_s": total("after"),
        "km": round(odo.total_km, 2) if odo is not None else None,
        "transit_km": transit_km,
        "transit_silent_km": round(sum(s.get("straight_km") or 0 for s in transit if s["kind"] == "silent"), 2)
        if n >= 2 else None,
        "transit_kmph": (round(transit_km / (moving_s / 3600), 1)
                         if transit_km and moving_s and moving_s >= 600 else None),
    }


# ---------------------------------------------------------------------------
# one trip, from the database
# ---------------------------------------------------------------------------

def load_trip(cur, run_id: int, trip_no: int, max_gap_s: float) -> dict | None:
    """Everything the number line of one trip needs, read from a run."""
    cur.execute("""SELECT s.i_trip_no, s.s_asset_id, s.dt_first_ping, s.dt_last_ping
                     FROM geo_trip_summary s WHERE s.i_run_id=%s AND s.i_trip_no=%s""", (run_id, trip_no))
    head = cur.fetchone()
    if not head:
        return None
    data = _load_many(cur, run_id, [trip_no])
    return build_trip(head, data["visits"].get(trip_no, []), data["stops"].get(trip_no, []),
                      data["gaps"].get(trip_no, []), data["blobs"].get(trip_no), max_gap_s)


def build_trip(head: dict, visits: list[dict], stops: list[dict], gaps: list[dict],
               blob: bytes | None, max_gap_s: float) -> dict:
    odo = Odometer.from_blob(blob, max_gap_s)
    span = odo.span if odo is not None else None
    t0 = span[0] if span else head["dt_first_ping"]
    t1 = span[1] if span else head["dt_last_ping"]
    place_list = places(visits)
    segs = segments(t0, t1, place_list, stops, gaps, odo) if t0 and t1 else []
    return {"trip_no": head["i_trip_no"], "asset": head.get("s_asset_id"), "start": t0, "end": t1,
            "places": place_list, "segments": segs, "summary": summarise(segs, place_list, odo)}


def _load_many(cur, run_id: int, trip_nos: list[int]) -> dict:
    marks = ",".join(["%s"] * len(trip_nos))
    cur.execute(f"""SELECT v.i_trip_no, v.i_site_id, v.s_site_name, v.s_category, v.s_scale, v.dt_enter,
                           v.dt_exit, v.b_open, v.b_entry_observed, v.i_dwell_seconds,
                           v.i_enter_gap_seconds, f.d_area_sqm
                      FROM geo_visit v LEFT JOIN geo_fence f ON f.i_fence_id = v.i_fence_id
                     WHERE v.i_run_id=%s AND v.i_trip_no IN ({marks})
                       AND v.s_scale IN ('micro','site','campus')""", (run_id, *trip_nos))
    visits: dict[int, list] = {}
    for r in cur.fetchall():
        visits.setdefault(r["i_trip_no"], []).append(r)
    cur.execute(f"""SELECT i_trip_no, dt_start, dt_end, i_duration_s, d_lat, d_long, i_site_id
                      FROM geo_stop WHERE i_run_id=%s AND i_trip_no IN ({marks})""", (run_id, *trip_nos))
    stops: dict[int, list] = {}
    for r in cur.fetchall():
        stops.setdefault(r["i_trip_no"], []).append(r)
    cur.execute(f"""SELECT i_trip_no, dt_from, dt_to, i_gap_s, s_kind, d_straight_m, d_route_m
                      FROM geo_gap WHERE i_run_id=%s AND i_trip_no IN ({marks})""", (run_id, *trip_nos))
    gaps: dict[int, list] = {}
    for r in cur.fetchall():
        gaps.setdefault(r["i_trip_no"], []).append(r)
    cur.execute(f"SELECT i_trip_no, m_data FROM geo_fit_trail WHERE i_run_id=%s AND i_trip_no IN ({marks})",
                (run_id, *trip_nos))
    blobs = {r["i_trip_no"]: r["m_data"] for r in cur.fetchall()}
    return {"visits": visits, "stops": stops, "gaps": gaps, "blobs": blobs}


# ---------------------------------------------------------------------------
# the stored table
# ---------------------------------------------------------------------------

_COLS = ("i_run_id", "i_trip_no", "s_asset_id", "dt_start", "dt_end", "i_span_s", "s_shape", "i_places",
         "i_loading_site_id", "s_loading_site", "dt_loading_in", "dt_loading_out", "i_loading_s",
         "b_loading_in_seen", "i_unloading_site_id", "s_unloading_site", "dt_unloading_in",
         "dt_unloading_out", "i_unloading_s", "b_unloading_open", "i_transit_s", "i_transit_moving_s",
         "i_transit_stop_s", "i_transit_halt_s", "i_transit_silent_s", "i_transit_stops",
         "i_transit_halts", "i_before_s", "i_after_s", "d_km", "d_transit_km", "d_transit_silent_km",
         "d_transit_kmph", "j_bar")

_INSERT = (f"INSERT INTO geo_trip_phase ({','.join(_COLS)}) VALUES ({','.join(['%s'] * len(_COLS))})")


def _bar(segs: list[dict]) -> str:
    """A compact copy of the segments for small number lines in lists:
    [phase, kind, offset_s, duration_s], adjacent same-kind pieces merged."""
    if not segs:
        return "[]"
    t0 = segs[0]["start"]
    out: list[list] = []
    for s in segs:
        off = int((s["start"] - t0).total_seconds())
        if out and out[-1][0] == s["phase"] and out[-1][1] == s["kind"]:
            out[-1][3] = off + s["duration_s"] - out[-1][2]
        else:
            out.append([s["phase"], s["kind"], off, s["duration_s"]])
    return json.dumps(out, separators=(",", ":"))


def _row(run_id: int, t: dict) -> tuple:
    s = t["summary"]
    return (run_id, t["trip_no"], t["asset"], t["start"], t["end"], s["span_s"], s["shape"], s["places"],
            s["loading_site_id"], (s["loading_site"] or None) and s["loading_site"][:255],
            s["loading_in"], s["loading_out"], s["loading_s"], s["loading_in_seen"],
            s["unloading_site_id"], (s["unloading_site"] or None) and s["unloading_site"][:255],
            s["unloading_in"], s["unloading_out"], s["unloading_s"], s["unloading_open"],
            s["transit_s"], s["transit_moving_s"], s["transit_stop_s"], s["transit_halt_s"],
            s["transit_silent_s"], s["transit_stops"], s["transit_halts"], s["before_s"], s["after_s"],
            s["km"], s["transit_km"], s["transit_silent_km"], s["transit_kmph"], _bar(t["segments"]))


def run_max_gap(cur, run_id: int, default: float = 1800.0) -> float:
    cur.execute("SELECT j_params FROM geo_run WHERE i_run_id=%s", (run_id,))
    row = cur.fetchone()
    params = (json.loads(row["j_params"]) if isinstance(row["j_params"], str) else row["j_params"]) if row else {}
    return float((params or {}).get("max_gap_seconds", default))


def build(conn, run_id: int, trip_nos: list[int] | None = None, chunk: int = 400) -> int:
    """(Re)build geo_trip_phase for a run, or for some of its trips."""
    conn.commit()                      # a fresh snapshot, not one from before the trips changed
    with conn.cursor() as cur:
        max_gap = run_max_gap(cur, run_id)
        if trip_nos is None:
            cur.execute("DELETE FROM geo_trip_phase WHERE i_run_id=%s", (run_id,))
            cur.execute("SELECT i_trip_no FROM geo_trip_summary WHERE i_run_id=%s ORDER BY i_trip_no", (run_id,))
            trip_nos = [r["i_trip_no"] for r in cur.fetchall()]
        conn.commit()
    written = 0
    for i in range(0, len(trip_nos), chunk):
        part = trip_nos[i:i + chunk]
        with conn.cursor() as cur:
            marks = ",".join(["%s"] * len(part))
            cur.execute(f"""SELECT i_trip_no, s_asset_id, dt_first_ping, dt_last_ping FROM geo_trip_summary
                             WHERE i_run_id=%s AND i_trip_no IN ({marks})""", (run_id, *part))
            heads = {r["i_trip_no"]: r for r in cur.fetchall()}
            data = _load_many(cur, run_id, part)
            rows = []
            for trip in part:
                head = heads.get(trip)
                if not head:
                    continue
                t = build_trip(head, data["visits"].get(trip, []), data["stops"].get(trip, []),
                               data["gaps"].get(trip, []), data["blobs"].get(trip), max_gap)
                rows.append(_row(run_id, t))
            cur.execute(f"DELETE FROM geo_trip_phase WHERE i_run_id=%s AND i_trip_no IN ({marks})",
                        (run_id, *part))
            cur.executemany(_INSERT, rows)
        conn.commit()
        written += len(rows)
    logger.info("trip phases: %s trips in run %s", written, run_id)
    return written
