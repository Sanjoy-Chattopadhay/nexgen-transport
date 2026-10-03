"""Turn a GPS trail into confirmed geofence entries and exits.

Why this is not just "first ping outside the circle"
----------------------------------------------------
GPS scatter at a standstill is tens to hundreds of metres. A truck parked on
the fence boundary produces in/out/in/out/in over consecutive pings. Taking
the first outside ping as the exit therefore reports a departure that never
happened -- and because pings arrive roughly every minute, it can be hours
early on a truck that idles at the gate.

So a state flip is only believed once it *holds*. A run of pings in the new
state is accepted as a real crossing when either:

  * it lasts at least `confirm_min` of wall time, or
  * the truck gets more than `escape_factor` x radius away from the centre --
    a truck 6 km outside a 3 km fence is not scatter, however few pings it
    managed before the tracker dropped.

The second rule matters because rule one alone loses every trip whose GPS
dies shortly after departure: we would know the truck left and still record
"never exited".

Short runs that fail both tests are absorbed into the surrounding state, and
the crossing timestamps are read off the surviving transitions.

Where the timestamp lands
-------------------------
An exit is stamped at the *last ping still inside* and an entry at the
*first ping inside*, so both stamps are instants the truck was observed at,
never interpolated. The real crossing happened somewhere in the gap to the
adjacent ping, and that gap is returned alongside as `gap_min` -- the honest
uncertainty on the stamp. A 1-minute gap is a precise answer; a 200-minute
gap is a GPS outage wearing a timestamp, and callers are expected to say so
rather than average it in silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .index import Geofence, haversine_m

# Minutes the new state must hold before a flip is believed.
DEFAULT_CONFIRM_MIN = 15.0

# A ping this many fence-radii from the centre confirms an exit on its own.
DEFAULT_ESCAPE_FACTOR = 2.0


@dataclass(frozen=True, slots=True)
class Crossing:
    event: str          # "enter" | "exit"
    ts: datetime
    gap_min: float | None   # uncertainty: wall gap to the adjacent ping
    lat: float
    lon: float


@dataclass(frozen=True, slots=True)
class Ping:
    ts: datetime
    lat: float
    lon: float


def _runs(flags: list[bool]) -> list[tuple[int, int, bool]]:
    """Collapse the flag sequence into (start, stop_exclusive, value) runs."""
    out: list[tuple[int, int, bool]] = []
    i = 0
    n = len(flags)
    while i < n:
        j = i + 1
        while j < n and flags[j] == flags[i]:
            j += 1
        out.append((i, j, flags[i]))
        i = j
    return out


def _minutes(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60.0


def crossings(
    pings: list[Ping],
    fence: Geofence,
    confirm_min: float = DEFAULT_CONFIRM_MIN,
    escape_factor: float = DEFAULT_ESCAPE_FACTOR,
) -> list[Crossing]:
    """Confirmed entries and exits of `fence` along `pings` (time-ordered)."""
    if len(pings) < 2:
        return []

    dists = [haversine_m(p.lat, p.lon, fence.lat, fence.lon) for p in pings]
    flags = [d <= fence.radius_m for d in dists]
    escape_m = fence.radius_m * escape_factor

    raw = _runs(flags)

    # --- absorb runs too brief to be believed -------------------------------
    # Walk the runs, holding a "current believed state". A run that disagrees
    # with it is only allowed to flip it if it holds long enough, or (for an
    # outside run) if the truck clearly escaped.
    merged: list[tuple[int, int, bool]] = []
    state = raw[0][2]
    cur_start = raw[0][0]
    cur_stop = raw[0][1]

    for start, stop, value in raw[1:]:
        if value == state:
            cur_stop = stop
            continue

        held_min = _minutes(pings[start].ts, pings[stop - 1].ts)
        escaped = (not value) and max(dists[start:stop]) > escape_m
        if held_min >= confirm_min or escaped:
            merged.append((cur_start, cur_stop, state))
            state, cur_start, cur_stop = value, start, stop
        else:
            # Jitter: fold it back into the run we are already in.
            cur_stop = stop
    merged.append((cur_start, cur_stop, state))

    # --- read the transitions off the surviving runs ------------------------
    out: list[Crossing] = []
    for idx in range(1, len(merged)):
        prev_start, prev_stop, prev_val = merged[idx - 1]
        start, _stop, value = merged[idx]
        if value == prev_val:
            continue
        if value:
            # entry: stamp the first ping inside
            p = pings[start]
            gap = _minutes(pings[start - 1].ts, p.ts) if start > 0 else None
            out.append(Crossing("enter", p.ts, gap, p.lat, p.lon))
        else:
            # exit: stamp the last ping still inside
            last_in = prev_stop - 1
            p = pings[last_in]
            nxt = last_in + 1
            gap = _minutes(p.ts, pings[nxt].ts) if nxt < len(pings) else None
            out.append(Crossing("exit", p.ts, gap, p.lat, p.lon))
    return out


@dataclass(frozen=True, slots=True)
class ExitResult:
    """Outcome of the origin-exit scan for one trip."""

    ts: datetime | None
    gap_min: float | None
    status: str          # ok | never_inside | never_exited | no_gps
    entered_at: datetime | None = None


def first_sustained_exit(
    pings: list[Ping],
    fence: Geofence,
    confirm_min: float = DEFAULT_CONFIRM_MIN,
    escape_factor: float = DEFAULT_ESCAPE_FACTOR,
) -> ExitResult:
    """When did the truck leave `fence` for good?

    The *first* confirmed exit, not the last, so a truck that returns to the
    origin metro late in a round trip does not overwrite its own departure.
    """
    if not pings:
        return ExitResult(None, None, "no_gps")

    events = crossings(pings, fence, confirm_min, escape_factor)
    entered = next((c.ts for c in events if c.event == "enter"), None)

    # Was the truck ever inside at all? `crossings` only reports transitions,
    # so a trail that starts inside and leaves once yields a lone "exit" and no
    # "enter" -- check the first ping directly rather than inferring it.
    ever_inside = (
        haversine_m(pings[0].lat, pings[0].lon, fence.lat, fence.lon) <= fence.radius_m
        or entered is not None
    )
    if not ever_inside:
        return ExitResult(None, None, "never_inside")

    first_exit = next((c for c in events if c.event == "exit"), None)
    if first_exit is None:
        return ExitResult(None, None, "never_exited", entered)
    return ExitResult(first_exit.ts, first_exit.gap_min, "ok", entered)
