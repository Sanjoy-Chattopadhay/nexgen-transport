"""GPS pre-filter: decide which fixes are allowed to vote.

Nothing here knows about fences. The job is to turn a raw device feed into a
clean, monotonic, physically plausible trail, and to say exactly what it threw
away and why -- a trail that lost 40% of its fixes must not produce a report
that looks as confident as one that lost none.

The four things actually wrong with this feed
---------------------------------------------
Measured over the 5.1M fixes in the corpus:

1. **Standstill scatter.** 75% of fixes are at zero speed. A stationary
   consumer-grade receiver still reports motion -- typically tens of metres of
   wander, worse under gantries and beside sheds, which is precisely where
   trucks park inside a works. This is the dominant error and it is *not*
   removed here: it is a boundary problem, not an outlier problem, and it is
   handled by hysteresis in state.py. Trying to smooth it away here would blunt
   real crossings too.

2. **Outliers / teleports.** Isolated fixes hundreds of km off the trail, from
   a bad almanac or a cell-tower fallback fix. These *are* removed here,
   because one of them dropped in the middle of a fence produces a phantom
   visit that no amount of downstream logic can distinguish from a real one.

3. **Duplicates and out-of-order arrivals.** The unique key on the source
   table stops exact duplicates, but re-sent packets still arrive with the
   same second, and a device that reconnects can replay a burst out of order.

4. **Outages.** 8,076 gaps over an hour. A gap is not an error and is never
   dropped; it is recorded, so the detector can refuse to treat a six-hour
   hole as six hours of evidence.

Why the teleport gate is not just "speed > 150 km/h, drop it"
-------------------------------------------------------------
Naive gating measures each fix against its predecessor. When the *predecessor*
is the outlier, that test rejects the entire remainder of the trail: every
subsequent good fix looks like a 300 km jump away from the bad anchor. The
rule here checks a fix against the last *accepted* fix and, before rejecting
it, looks one fix ahead. If the successor agrees with the suspect fix and not
with the anchor, the anchor was wrong and the trail has genuinely moved, so
the suspect is kept and the anchor is re-established. A lone spike fails that
test -- nothing agrees with it -- and is dropped.

This is the standard single-outlier rejection used in trajectory cleaning; see
Zheng, Y. "Trajectory Data Mining: An Overview" (ACM TIST 6(3), 2015), sec.
3.1, which describes exactly this speed/heuristic filter and its failure mode
against consecutive outliers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from nexgen.shared.geoengine.geometry import haversine_m

# Plausible extent for a GPS fix. Deliberately tighter on the west than the
# master loader's bound: a *fence* may legitimately sit offshore (the client's
# ARABIAN SEA sanity fences do), but a *truck* reported there has a broken
# receiver. Dropping the fix and detecting the fence are both correct, and
# they need different bounds.
LAT_MIN, LAT_MAX = 5.0, 38.5
LON_MIN, LON_MAX = 66.0, 98.5


@dataclass
class CleanTrail:
    """A filtered trail plus an account of what was removed."""

    ts: list[datetime] = field(default_factory=list)
    lat: list[float] = field(default_factory=list)
    lon: list[float] = field(default_factory=list)
    speed: list[int | None] = field(default_factory=list)
    # Source row id per kept fix, when the rows carried one. Lets the fit
    # stage write its result beside the raw ping instead of over it.
    ids: list[int | None] = field(default_factory=list)
    # (id, ts, lat, lon, reason) per refused fix that had an id -- the
    # per-ping form of `dropped`, so a map can show what was thrown away.
    rejected: list[tuple] = field(default_factory=list)

    read: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    max_gap_seconds: int = 0
    breaks: int = 0
    resequenced: int = 0

    def _drop(self, reason: str, rec: tuple | None = None) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1
        if rec is not None and rec[4] is not None:
            self.rejected.append((rec[4], rec[0], rec[1], rec[2], reason))

    def _keep(self, rec: tuple) -> None:
        self.ts.append(rec[0]); self.lat.append(rec[1]); self.lon.append(rec[2])
        self.speed.append(rec[3]); self.ids.append(rec[4])

    @property
    def used(self) -> int:
        return len(self.ts)

    @property
    def total_dropped(self) -> int:
        return sum(self.dropped.values())

    @property
    def quality(self) -> str:
        """A one-word verdict, so a report never has to infer trustworthiness.

        `broken` means the trail has a hole long enough that a truck could
        have entered and left a fence entirely inside it -- any "never visited"
        conclusion from such a trail is unsupported, not negative.
        """
        if self.used == 0:
            return "no_gps"
        if self.used < 5:
            return "sparse"
        if self.max_gap_seconds >= 3600:
            return "broken"
        if self.read and self.total_dropped / self.read > 0.10:
            return "noisy"
        if self.max_gap_seconds >= 900:
            return "sparse"
        return "good"


def prefilter_enabled() -> bool:
    """services.yaml geofence.settings.prefilter.enabled (default on).

    NexGen decision (2026-10-03): filtering is optional -- the system must be
    able to accept and process everything. Off, `clean` keeps every fix that
    has a time and a position: no out-of-range gate, no duplicate removal
    beyond the exact same reading, no teleport gate. Fixes without a time or
    a position are still set aside, because nothing can place them.
    """
    from nexgen.shared.geoengine.config import settings
    return bool(getattr(settings, "prefilter_enabled", True))


def clean(
    rows,
    max_plausible_kmph: float = 150.0,
    max_gap_seconds: float = 1800.0,
    prefilter: bool | None = None,
) -> CleanTrail:
    """Filter a raw ping sequence.

    `rows` is any iterable of mappings with `dt_message`, `d_lat`, `d_long`
    and optionally `i_speed` -- i.e. rows straight off the source table.
    `prefilter=False` (or the setting) passes every placeable fix through.
    """
    if prefilter is None:
        prefilter = prefilter_enabled()
    if not prefilter:
        return _pass_through(rows, max_gap_seconds)
    out = CleanTrail()

    # -- 1. structural validity, and sort ------------------------------------
    # Records are (ts, lat, lon, speed, id); id is None for rows without one.
    staged: list[tuple] = []
    for r in rows:
        out.read += 1
        rid = r.get("id")
        ts = r.get("dt_message")
        if ts is None:
            out._drop("null_fix", (ts, None, None, None, rid))
            continue
        try:
            lat = float(r["d_lat"])
            lon = float(r["d_long"])
        except (KeyError, TypeError, ValueError):
            out._drop("null_fix", (ts, None, None, None, rid))
            continue
        if lat == 0.0 and lon == 0.0:
            out._drop("null_fix", (ts, lat, lon, None, rid))
            continue
        if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
            out._drop("out_of_range", (ts, lat, lon, None, rid))
            continue
        spd = r.get("i_speed")
        staged.append((ts, lat, lon, int(spd) if spd is not None else None, rid))

    if not staged:
        return out

    # A stable sort keeps the arrival order of same-second fixes, so "keep the
    # first" below is deterministic rather than dependent on cursor order.
    # Re-ordering is repaired, not penalised -- the fix is good, it merely
    # arrived late -- but it is counted, because a device that replays bursts
    # out of order is a device worth knowing about.
    out_of_order = sum(1 for a, b in zip(staged, staged[1:]) if b[0] < a[0])
    out.resequenced = out_of_order
    ordered = sorted(staged, key=lambda x: x[0])

    # -- 2. de-duplicate on timestamp ----------------------------------------
    deduped: list[tuple] = []
    for rec in ordered:
        if deduped and rec[0] == deduped[-1][0]:
            out._drop("duplicate", rec)
            continue
        deduped.append(rec)

    # -- 3. teleport gate ----------------------------------------------------
    n = len(deduped)
    i = 0
    anchor: tuple | None = None

    while i < n:
        rec = deduped[i]
        ts, lat, lon = rec[0], rec[1], rec[2]

        if anchor is None:
            anchor = rec
            out._keep(rec)
            i += 1
            continue

        dt_s = (ts - anchor[0]).total_seconds()
        if dt_s <= 0:
            out._drop("out_of_order", rec)
            i += 1
            continue

        # Across a long outage the truck could plausibly be anywhere, so the
        # speed test carries no information and is not applied. The gap is
        # recorded instead -- that is the honest handling.
        if dt_s > max_gap_seconds:
            out.breaks += 1
            out.max_gap_seconds = max(out.max_gap_seconds, int(dt_s))
            anchor = rec
            out._keep(rec)
            i += 1
            continue

        implied = haversine_m(anchor[1], anchor[2], lat, lon) / dt_s * 3.6
        if implied <= max_plausible_kmph:
            out.max_gap_seconds = max(out.max_gap_seconds, int(dt_s))
            anchor = rec
            out._keep(rec)
            i += 1
            continue

        # Suspect. Look one fix ahead before rejecting it.
        keep = False
        if i + 1 < n:
            nts, nlat, nlon = deduped[i + 1][0], deduped[i + 1][1], deduped[i + 1][2]
            fwd_s = (nts - ts).total_seconds()
            anc_s = (nts - anchor[0]).total_seconds()
            if fwd_s > 0 and anc_s > 0:
                fwd = haversine_m(lat, lon, nlat, nlon) / fwd_s * 3.6
                from_anchor = haversine_m(anchor[1], anchor[2], nlat, nlon) / anc_s * 3.6
                # The successor sits with the suspect, not with the anchor:
                # the trail really did move and the anchor is the stale one.
                keep = fwd <= max_plausible_kmph and from_anchor > max_plausible_kmph

        if keep:
            out.max_gap_seconds = max(out.max_gap_seconds, int(dt_s))
            anchor = rec
            out._keep(rec)
        else:
            out._drop("teleport", rec)
        i += 1

    # Recompute the largest surviving gap over the kept fixes: dropping an
    # outlier can merge two ordinary intervals into one long one, and the
    # quality verdict must reflect the trail actually used.
    for k in range(1, len(out.ts)):
        gap = int((out.ts[k] - out.ts[k - 1]).total_seconds())
        if gap > out.max_gap_seconds:
            out.max_gap_seconds = gap

    return out


def _pass_through(rows, max_gap_seconds: float) -> CleanTrail:
    """The pre-filter switched off: keep every fix that has a time and a
    position, in time order. Only an exact repeat (same second, same
    position) is counted once, because it is the same reading twice."""
    out = CleanTrail()
    staged: list[tuple] = []
    for r in rows:
        out.read += 1
        rid = r.get("id")
        ts = r.get("dt_message")
        if ts is None:
            out._drop("null_fix", (ts, None, None, None, rid))
            continue
        try:
            lat = float(r["d_lat"])
            lon = float(r["d_long"])
        except (KeyError, TypeError, ValueError):
            out._drop("null_fix", (ts, None, None, None, rid))
            continue
        spd = r.get("i_speed")
        staged.append((ts, lat, lon, int(spd) if spd is not None else None, rid))
    out.resequenced = sum(1 for a, b in zip(staged, staged[1:]) if b[0] < a[0])
    prev = None
    for rec in sorted(staged, key=lambda x: x[0]):
        if prev is not None and rec[0] == prev[0] and rec[1] == prev[1] and rec[2] == prev[2]:
            out._drop("duplicate", rec)
            continue
        if prev is not None:
            gap = int((rec[0] - prev[0]).total_seconds())
            out.max_gap_seconds = max(out.max_gap_seconds, gap)
            if gap > max_gap_seconds:
                out.breaks += 1
        out._keep(rec)
        prev = rec
    return out

