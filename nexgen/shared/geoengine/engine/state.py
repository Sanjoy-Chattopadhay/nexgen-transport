"""The crossing detector: signed distance in, confirmed crossings out.

This is the part that decides what is real. Everything upstream is plumbing
and everything downstream is bookkeeping.

The problem
-----------
A GPS receiver standing still reports a cloud of positions tens of metres
across. Park a truck on a fence line -- which is what a truck at a weighbridge
or a gate is doing, by definition -- and consecutive fixes fall alternately
inside and outside. Reading each fix as a verdict produces in/out/in/out: a
"visit count" in the hundreds for one parking event, a "dwell time" that is
noise, and an alert stream nobody will keep paying attention to. On this
corpus 75% of fixes are at zero speed, so this is the normal case, not an edge
case.

The mechanism: a band and a hold
--------------------------------
Two independent ideas, and both are needed.

**Hysteresis.** Instead of one boundary there are two, `hysteresis_m` either
side of the real one. Crossing into the band changes nothing -- the previous
state persists. Only clearing the far edge is even *considered* a change. A
truck jittering across the line therefore stays in whatever state it was in,
because the jitter never clears the far edge. This is the Schmitt trigger, the
standard answer to a noisy signal crossing a threshold, and it is what stops
flapping at the boundary.

**Dwell confirmation.** Hysteresis alone still flips on a single fix that
lands well past the band -- one bad multipath reflection 100 m into a yard. So
a flip must also *hold*: the new state has to persist for `confirm_seconds`
before it is believed. Short runs are absorbed back into the state around
them.

Why there is an escape rule as well
-----------------------------------
Dwell confirmation alone quietly loses two real cases:

* the truck genuinely leaves and its tracker dies two minutes later -- the run
  never reaches `confirm_seconds`, so the departure is never recorded and the
  truck is reported as still inside, indefinitely;
* a fast transit of a small fence -- through a 200 m gate zone at 40 km/h is
  18 seconds, and no dwell threshold useful against jitter is that short.

So a flip is *also* confirmed, however brief, if the trail gets more than
`escape_m` past the band. A fix 250 m clear of the boundary is not receiver
scatter at any grade of hardware; it is displacement. Distance substitutes for
time when time is unavailable.

Why outages cannot confirm anything
-----------------------------------
Held time accumulates only over intervals shorter than `max_gap_seconds`. A
six-hour hole in the trail would otherwise satisfy any dwell threshold on its
own, and the detector would "confirm" a state transition using an interval
during which it observed nothing at all. Gaps are recorded as uncertainty, not
consumed as evidence.

Where the timestamp lands
-------------------------
Always on a fix that was actually observed -- entry at the first fix of the
confirmed run, exit at the last fix before it -- never interpolated. The true
crossing lies somewhere in the gap to the neighbouring fix, and that gap is
returned as `gap_seconds`. A crossing stamped with a 45-second gap is precise;
one with a 4-hour gap is a guess wearing a timestamp, and the report layer is
expected to say so rather than average the two together.

Tuning
------
Defaults are derived in docs/ALGORITHMS.md from this corpus: `hysteresis_m=25`
(roughly 3x the observed standstill scatter), `confirm_seconds=90` (about 1.5
sampling intervals at the observed ~60 s cadence, so two independent fixes
must agree), `escape_m=250`. They are recorded on every run row, so any report
can be reproduced with the settings that produced it.

References
----------
* Schmitt, O. H. "A thermionic trigger", J. Sci. Instrum. 15(24), 1938 --
  the hysteresis comparator.
* Zheng, Y. "Trajectory Data Mining: An Overview", ACM TIST 6(3), 2015 --
  stay-point / dwell detection over noisy trajectories.
* Ranacher, P. et al. "Why GPS makes distances bigger than they are",
  IJGIS 30(2), 2016 -- why positional noise biases naive trajectory measures,
  and why smoothing before thresholding is not a free improvement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

INSIDE = "inside"
OUTSIDE = "outside"


@dataclass(frozen=True, slots=True)
class Crossing:
    event: str                # enter | exit
    index: int                # index into the trail of the stamped fix
    ts: datetime
    gap_seconds: int | None   # uncertainty: wall gap to the adjacent fix
    confirmed_by: str         # dwell | escape


@dataclass(frozen=True, slots=True)
class OpenCandidate:
    """A state change that was in progress when the trail ran out.

    Reported rather than resolved. It is the difference between "the truck did
    not leave" and "we stopped being able to see it while it might have been
    leaving", and a report that conflates those two is lying by omission.
    """

    to_state: str
    index: int
    ts: datetime
    held_seconds: float
    best_distance_m: float


@dataclass(frozen=True, slots=True)
class DetectionResult:
    initial_state: str
    crossings: list[Crossing]
    open_candidate: OpenCandidate | None
    inside_mask: np.ndarray   # confirmed state per fix, after debouncing


def _opposite(s: str) -> str:
    return OUTSIDE if s == INSIDE else INSIDE


class FenceTracker:
    """The detector as an incremental state machine: one fix in, at most one
    crossing out.

    This class *is* the algorithm. `detect()` below is a loop over it, and the
    live detector is the same loop driven by arriving pings instead of an
    array. That is deliberate and load-bearing: a batch pipeline and a live
    pipeline that each implement the rules separately will drift, and the
    first anyone hears of it is a dashboard disagreeing with a report about
    the same truck on the same day. Here they cannot drift, because there is
    only one implementation.

    State carried between fixes is small and picklable -- the confirmed state,
    when it started, and the progress of any run that is trying to overturn
    it -- so a live detector can persist it and resume after a restart.
    """

    __slots__ = ("band", "confirm_seconds", "escape_m", "max_gap_seconds",
                 "state", "since", "seen", "prev_ts",
                 "_run_ts", "_run_held", "_run_escape", "_run_target",
                 "_prev_pending_ts")

    def __init__(self, hysteresis_m: float = 25.0, confirm_seconds: float = 90.0,
                 escape_m: float = 250.0, max_gap_seconds: float = 1800.0,
                 state: str | None = None, since: datetime | None = None):
        self.band = abs(hysteresis_m)
        self.confirm_seconds = confirm_seconds
        self.escape_m = escape_m
        self.max_gap_seconds = max_gap_seconds

        self.state = state          # None until the first unambiguous fix
        self.since = since
        self.seen = 0
        self.prev_ts: datetime | None = None

        self._run_ts: datetime | None = None    # first fix of the candidate run
        self._run_held = 0.0
        self._run_escape = 0.0
        self._run_target = ""
        # The fix immediately before the run began -- where an exit is stamped.
        self._prev_pending_ts: datetime | None = None

    # -- introspection used by the live layer -------------------------------

    @property
    def inside(self) -> bool:
        return self.state == INSIDE

    @property
    def pending(self) -> str:
        """The state a run is currently trying to establish, if any."""
        return self._run_target if self._run_ts is not None else ""

    def step(self, ts: datetime, dist: float) -> Crossing | None:
        """Feed one fix. Returns a crossing if this fix confirmed one.

        `dist` is signed distance to the effective boundary in metres,
        negative inside.
        """
        self.seen += 1
        clear_in = dist <= -self.band
        clear_out = dist >= self.band
        prev_ts, self.prev_ts = self.prev_ts, ts

        # The first *unambiguous* fix sets the initial state. A trail that
        # begins with the truck sitting on the boundary must not have its
        # starting state decided by which way the noise fell.
        if self.state is None:
            if clear_in:
                self.state, self.since = INSIDE, ts
            elif clear_out:
                self.state, self.since = OUTSIDE, ts
            return None

        confirms_state = clear_in if self.state == INSIDE else clear_out
        contradicts = clear_in if self.state == OUTSIDE else clear_out

        if self._run_ts is not None:
            if confirms_state:
                # The truck came back. Whatever the run was, it was jitter.
                self._reset_run()
                return None
            # A continuing opposite reading and an ambiguous one both extend
            # the run: the ambiguous fix does not contradict it, and dropping
            # the run on every band reading would defeat the hysteresis.
            if prev_ts is not None:
                gap = (ts - prev_ts).total_seconds()
                if 0 < gap <= self.max_gap_seconds:
                    self._run_held += gap
            beyond = (dist - self.band) if self._run_target == OUTSIDE else (-dist - self.band)
            self._run_escape = max(self._run_escape, beyond)

        elif contradicts:
            self._run_ts = ts
            self._run_target = _opposite(self.state)
            self._run_held = 0.0
            beyond = (dist - self.band) if self._run_target == OUTSIDE else (-dist - self.band)
            self._run_escape = max(0.0, beyond)
            self._prev_pending_ts = prev_ts

        if self._run_ts is None:
            return None

        by = None
        if self._run_escape >= self.escape_m:
            by = "escape"
        elif self._run_held >= self.confirm_seconds:
            by = "dwell"
        if by is None:
            return None

        if self._run_target == INSIDE:
            stamp = self._run_ts
            gap = (int((self._run_ts - self._prev_pending_ts).total_seconds())
                   if self._prev_pending_ts else None)
            crossing = Crossing("enter", -1, stamp, gap, by)
        else:
            # An exit is stamped at the last fix still inside, which is the one
            # before the run started.
            stamp = self._prev_pending_ts or self._run_ts
            gap = (int((self._run_ts - stamp).total_seconds())
                   if self._prev_pending_ts else None)
            crossing = Crossing("exit", -1, stamp, gap, by)

        self.state = self._run_target
        self.since = stamp
        self._reset_run()
        return crossing

    def _reset_run(self) -> None:
        self._run_ts = None
        self._run_held = 0.0
        self._run_escape = 0.0
        self._run_target = ""
        self._prev_pending_ts = None


def detect(
    ts: list[datetime],
    dist: np.ndarray,
    hysteresis_m: float = 25.0,
    confirm_seconds: float = 90.0,
    escape_m: float = 250.0,
    max_gap_seconds: float = 1800.0,
) -> DetectionResult:
    """Confirmed crossings along one trail against one fence.

    A loop over `FenceTracker`, plus the two things a batch caller needs and a
    live one cannot have: the index each crossing landed on, and the confirmed
    inside/outside state back-filled across the whole trail.

    `dist` is signed distance to the effective boundary in metres, negative
    inside. Same length as `ts`.
    """
    n = len(ts)
    if n == 0:
        return DetectionResult(OUTSIDE, [], None, np.zeros(0, dtype=bool))

    tracker = FenceTracker(hysteresis_m, confirm_seconds, escape_m, max_gap_seconds)
    crossings: list[Crossing] = []
    inside_mask = np.zeros(n, dtype=bool)
    at: dict[datetime, int] = {}
    run_start_idx = -1
    # Captured the moment the tracker first commits to a state. Reading
    # `tracker.state` after the loop would give the *final* state, which is a
    # different question and silently wrong for any trail that crosses.
    initial: str | None = None

    for i in range(n):
        if ts[i] not in at:
            at[ts[i]] = i
        was_pending = tracker.pending
        state_before = tracker.state

        crossing = tracker.step(ts[i], float(dist[i]))
        if initial is None and tracker.state is not None:
            initial = tracker.state

        # Track where the current run began, so a confirmation can be
        # back-filled over it.
        if tracker.pending and not was_pending:
            run_start_idx = i
        elif not tracker.pending and not crossing:
            run_start_idx = -1

        inside_mask[i] = (state_before == INSIDE) if state_before else False

        if crossing:
            idx = at.get(crossing.ts, max(run_start_idx, 0))
            crossings.append(Crossing(crossing.event, idx, crossing.ts,
                                      crossing.gap_seconds, crossing.confirmed_by))
            # The truck was in the new state for the whole run; we merely took
            # until now to believe it.
            start = run_start_idx if run_start_idx >= 0 else i
            inside_mask[start:i + 1] = (tracker.state == INSIDE)
            run_start_idx = -1

    # Every fix was inside the hysteresis band, so nothing was unambiguous.
    # Fall back to the raw sign of the first fix.
    if initial is None:
        initial = INSIDE if float(dist[0]) <= 0 else OUTSIDE
    if not crossings:
        inside_mask[:] = (initial == INSIDE)
    else:
        # Everything before the first crossing was the initial state.
        first = crossings[0]
        head = first.index if first.event == "enter" else first.index + 1
        inside_mask[:max(head, 0)] = (initial == INSIDE)

    open_candidate = None
    if tracker.pending:
        open_candidate = OpenCandidate(
            to_state=tracker.pending,
            index=max(run_start_idx, 0),
            ts=ts[max(run_start_idx, 0)],
            held_seconds=tracker._run_held,
            best_distance_m=tracker._run_escape,
        )

    return DetectionResult(initial, crossings, open_candidate, inside_mask)


def pair_visits(result: DetectionResult, ts: list[datetime]):
    """Fold crossings into (enter, exit) visits.

    A trail that begins with the truck already inside yields an exit with no
    matching entry; that is a real visit whose start predates the trail, and
    it is emitted with `dt_enter` at the first fix and `entry_observed=False`
    rather than being dropped for being untidy. Symmetrically, a visit still
    open at the end of the trail is emitted with `open=True`.
    """
    visits: list[dict] = []
    n = len(ts)
    if n == 0:
        return visits

    open_enter: tuple[int, int | None] | None = None
    entry_observed = True

    if result.initial_state == INSIDE:
        open_enter = (0, None)
        entry_observed = False

    for c in result.crossings:
        if c.event == "enter":
            if open_enter is None:
                open_enter = (c.index, c.gap_seconds)
                entry_observed = True
        else:
            if open_enter is None:
                # An exit with no entry on a trail that started outside means
                # the truck entered and left inside a single gap. Recorded as
                # a zero-length visit at the exit fix so the visit count stays
                # truthful, flagged by the gap on the row.
                open_enter = (c.index, None)
                entry_observed = False
            start, egap = open_enter
            visits.append({
                "start": start, "end": c.index,
                "enter_gap": egap, "exit_gap": c.gap_seconds,
                "entry_observed": entry_observed, "open": False,
                "confirmed_by": c.confirmed_by,
            })
            open_enter = None
            entry_observed = True

    if open_enter is not None:
        start, egap = open_enter
        visits.append({
            "start": start, "end": n - 1,
            "enter_gap": egap, "exit_gap": None,
            "entry_observed": entry_observed, "open": True,
            "confirmed_by": "trail_end",
        })
    return visits
