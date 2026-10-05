"""Plant congestion arithmetic (nexgen/shared/geoengine/reporting/congestion.py).

Pure logic, no database: occupancy as a step function, the usual level and the
threshold built on it, overload episodes, the scene merge, zone kinds and the
plant layout. The numbers each test uses are small enough to check by hand.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from nexgen.shared.geoengine.reporting import congestion as C

T0 = datetime(2026, 10, 5, 9, 0)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def stay(i: int, vehicle: str, a: float, b: float, **kw) -> C.Stay:
    return C.Stay(id=i, vehicle=vehicle, enter=at(a), end=at(b), **kw)


CFG = {**C.DEFAULTS, "capacity": {}}


# ---------------------------------------------------------------------------
# zone kinds
# ---------------------------------------------------------------------------

def test_zone_kind_matches_whole_words_in_rule_order():
    assert C.zone_kind("HSM GATE", "micro") == "gate"
    assert C.zone_kind("HSM GATE_LOCAL", "micro") == "gate"          # "_" is not a letter or digit
    assert C.zone_kind("SLAG PIT & GATE", "site") == "gate"
    assert C.zone_kind("WB- GATE 2", "site") == "weighbridge"         # weighbridge is the earlier rule
    assert C.zone_kind("MRP WEIGHBRIDGE", "micro") == "weighbridge"
    assert C.zone_kind("TSPDL YARD-JMD", "site") == "parking"
    assert C.zone_kind("HSM MILL PARKING", "micro") == "parking"
    assert C.zone_kind("HSM GATE IN SIDE", "site") == "area"          # past the gate, not the gate
    assert C.zone_kind("JCAPCPL LOADING", "site") == "loading"
    assert C.zone_kind("LD3 SIGNAL TO MRP.", "site") == "road"
    assert C.zone_kind("KANTADI", "site") == "area"                   # KANTA only as a whole word
    assert C.zone_kind("OXY TORCH", "micro") == "area"


def test_campus_scale_is_an_area_whatever_its_name():
    assert C.zone_kind("TISCO HSM GATE", "campus") == "area"


# ---------------------------------------------------------------------------
# occupancy
# ---------------------------------------------------------------------------

def test_occupancy_is_half_open_and_ignores_zero_length_stays():
    occ = C.Occupancy([stay(1, "A", 0, 30), stay(2, "B", 30, 60), stay(3, "C", 10, 10)])
    assert occ.at(at(-1)) == 0
    assert occ.at(at(0)) == 1
    assert occ.at(at(10)) == 1          # the single-fix pass holds no time
    assert occ.at(at(30)) == 1          # A has left as B arrives
    assert occ.at(at(60)) == 0
    assert occ.peak() == (1, at(0))


def test_peak_level_seconds_and_runs():
    occ = C.Occupancy([stay(1, "A", 0, 60), stay(2, "B", 10, 40), stay(3, "C", 20, 30)])
    assert occ.peak() == (3, at(20))
    assert occ.peak(at(30), at(60)) == (2, at(30))
    secs = occ.level_seconds()
    assert secs == {1: 1800.0, 2: 1200.0, 3: 600.0}
    assert occ.runs(2) == [(at(10), at(40), 3, at(20))]


def test_weighted_percentile_is_by_time_not_by_visit():
    # 50 minutes at 1, 5 at 2, 5 at 9: the 90th percentile of time is 2.
    assert C.weighted_percentile({1: 3000, 2: 300, 9: 300}, 90) == 2
    assert C.weighted_percentile({1: 3000, 2: 300, 9: 300}, 95) == 9
    assert C.weighted_percentile({}, 90) is None


# ---------------------------------------------------------------------------
# threshold
# ---------------------------------------------------------------------------

def _base(usual: int | None, busy_h: float = 10) -> C.Baseline:
    return C.Baseline(usual=usual, busy_s=busy_h * 3600, levels={}, thin=busy_h < CFG["min_busy_hours"],
                      stay_p50_s=None, stays_measured=0)


def test_threshold_adds_a_margin_on_large_fences_only():
    assert C.threshold("gate", _base(4), CFG) == (5, "above usual")          # 4 + max(1, ceil(0.8))
    assert C.threshold("gate", _base(5), CFG) == (6, "above usual")          # 5 + max(1, ceil(1.0))
    assert C.threshold("area", _base(77), CFG) == (93, "above usual")        # 77 + ceil(15.4)
    assert C.threshold("parking", _base(4), CFG) == (6, "minimum")           # the kind's floor wins


def test_threshold_thin_baseline_and_capacity():
    assert C.threshold("gate", _base(9, busy_h=2), CFG) == (4, "minimum (baseline thin)")
    cfg = {**CFG, "capacity": {6246: 7}}
    assert C.threshold("gate", _base(4), cfg, site_id=6246) == (7, "capacity")


# ---------------------------------------------------------------------------
# episodes
# ---------------------------------------------------------------------------

def test_episodes_merge_close_runs_drop_short_ones_and_measure_waits():
    stays = [
        stay(1, "A", 0, 120), stay(2, "B", 0, 120),
        stay(3, "C", 10, 50), stay(4, "D", 12, 52),             # 4 inside from 12 to 50
        stay(5, "E", 55, 95, enter_gap_s=900),                   # 3 between 52 and 55: a 3-minute dip
        stay(6, "F", 56, 100, open=True),                        # 4 again 56-95
        stay(7, "G", 200, 205), stay(8, "H", 200, 205), stay(9, "I", 200, 205), stay(10, "J", 200, 205),
    ]
    occ = C.Occupancy(stays)
    base = C.baseline(occ, stays, CFG)
    eps = C.episodes(1, occ, stays, base, (4, "above usual"), CFG)
    assert len(eps) == 1                                         # the 5-minute crowd at 200 is too short
    ep = eps[0]
    assert (ep.start, ep.end, ep.peak) == (at(12), at(95), 4)
    assert ep.vehicles == frozenset("ABCDEF")
    assert ep.arrivals == 3                                      # D, E, F entered during it; C before
    assert ep.arrivals == len([s for s in stays if at(12) <= s.enter < at(95)])
    assert ep.waits_measured == 2                                # D and E; F's trail ended inside
    assert ep.wait_p50_s == (40 * 60 + 40 * 60) / 2
    assert ep.uncertain == 1 and ep.open_stays == 1


# ---------------------------------------------------------------------------
# scenes and plants
# ---------------------------------------------------------------------------

class Fence:
    """A round stand-in for geoengine.model.Fence: what congestion reads of it."""

    def __init__(self, fence_id, site_id, name, lat, lon, radius_m, tolerance_m=0.0, site_type="HOME SITE"):
        self.fence_id, self.site_id, self.name = fence_id, site_id, name
        self.centroid_lat, self.centroid_lon, self.radius_m = lat, lon, radius_m
        self.area_m2 = math.pi * radius_m ** 2
        self.n_vertices, self.tolerance_m, self.site_type, self.category = 32, tolerance_m, site_type, "normal"

    def contains(self, lat, lon):
        dy = (lat - self.centroid_lat) * 111_320
        dx = (lon - self.centroid_lon) * 111_320 * math.cos(math.radians(self.centroid_lat))
        return math.hypot(dx, dy) <= self.radius_m


class Index:
    def __init__(self, fences):
        self.fences = fences

    def query_point(self, lat, lon):
        return list(self.fences)


def ep(ground_id, a, b, vehicles) -> C.Episode:
    return C.Episode(ground_id=ground_id, start=at(a), end=at(b), peak=len(vehicles), peak_at=at(a),
                     threshold=3, threshold_basis="above usual", usual=2, vehicles=frozenset(vehicles), arrivals=0,
                     wait_p50_s=None, waits_measured=0, usual_stay_p50_s=None, uncertain=0, open_stays=0)


def test_scenes_merge_the_same_place_drawn_twice_but_not_a_gate_and_its_works():
    gate_a = C.Ground(Fence(1, 101, "HSM GATE", 22.78, 86.21, 50), [], kind="gate")
    gate_b = C.Ground(Fence(2, 102, "HSM GATE", 22.78, 86.2101, 45), [], kind="gate")   # 1.2x, centroid inside
    works = C.Ground(Fence(3, 103, "WORKS", 22.78, 86.21, 1500), [], kind="area")
    grounds = {g.id: g for g in (gate_a, gate_b, works)}
    found = C.scenes(9, [ep(1, 0, 60, "ABCDE"), ep(2, 5, 70, "ABCD"), ep(3, 0, 90, "ABCDEFGH")], grounds, CFG)
    assert len(found) == 2
    gate_scene = next(s for s in found if len(s.members) == 2)
    assert gate_scene.primary.ground_id == 2                      # the smaller drawing names it
    assert {e.ground_id for e in gate_scene.members} == {1, 2}


def test_scenes_need_shared_vehicles():
    a = C.Ground(Fence(1, 101, "GATE", 22.78, 86.21, 50), [], kind="gate")
    b = C.Ground(Fence(2, 102, "GATE", 22.78, 86.21, 50.5), [], kind="gate")
    found = C.scenes(9, [ep(1, 0, 60, "ABCD"), ep(2, 0, 60, "WXYZ")], {1: a, 2: b}, CFG)
    assert len(found) == 2


def test_layout_folds_identical_copies_and_hangs_zones_on_the_outermost_plant():
    works = Fence(10, 210, "WORKS", 22.78, 86.21, 1500)
    works_copy = Fence(11, 205, "WORKS", 22.78, 86.21, 1500)                 # same polygon, another site id
    district = Fence(12, 212, "TISCO HSM GATE", 22.78, 86.21, 1200)          # campus, inside the works
    gate = Fence(13, 213, "HSM GATE", 22.781, 86.211, 40)
    elsewhere = Fence(14, 214, "CLIENT YARD", 23.5, 86.9, 100)
    fences = [works, works_copy, district, gate, elsewhere]
    grounds = C.grounds_of(fences)
    assert len(grounds) == 4
    copies = next(g for g in grounds if g.fence.name == "WORKS")
    assert copies.fence.site_id == 205 and [f.site_id for f in copies.copies] == [205, 210]
    plants = {p.ground.fence.name: p for p in C.layout(grounds, Index(fences), C.DEFAULTS["zone_kinds"])}
    assert set(plants) == {"WORKS", "CLIENT YARD"}
    zones = {z.fence.name: z.kind for z in plants["WORKS"].zones}
    assert zones == {"HSM GATE": "gate", "TISCO HSM GATE": "area"}
    assert plants["CLIENT YARD"].zones == []


# ---------------------------------------------------------------------------
# wording
# ---------------------------------------------------------------------------

def test_minutes_round_half_up_like_the_page():
    assert C.fmt_minutes(6.5 * 60) == "7 min"          # Python's round() would say 6
    assert C.fmt_minutes(125 * 60) == "2 h 05 min"


def test_headline_states_every_number_from_the_episode():
    e = C.Episode(ground_id=1, start=at(38), end=at(138), peak=8, peak_at=at(117), threshold=5,
                  threshold_basis="above usual", usual=4, vehicles=frozenset("ABCDEFGH"), arrivals=9,
                  wait_p50_s=12 * 60, waits_measured=9, usual_stay_p50_s=17 * 60, uncertain=1, open_stays=0)
    text = C.headline("gate", "HSM GATE", "TATA STEEL PLANT -JSR", e)
    assert text == ("Gate overloading at HSM GATE (TATA STEEL PLANT -JSR): 8 vehicles at once at Mon 5 Oct 10:57; "
                    "it usually holds no more than 4 (nine minutes in ten while in use). 5 or more from 09:38 to "
                    "11:18 (100 min); the 9 that arrived in that time stayed a median 12 min against 17 min usually.")
