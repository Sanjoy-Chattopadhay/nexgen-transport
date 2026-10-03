"""The fleet processor keeps the source's cumulative distance only where it differs.

A trip's i_cdist is derived as the running sum of its per-fix distances, which
is what the source sends -- until its counter restarts mid-trip (8 of 7,257
imported trips). _keep_source_cdist stores the source's figure for exactly the
fixes where the two disagree, and clears an earlier exception when a re-sent
block agrees again.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from nexgen.services.fleet.processor import _keep_source_cdist

T0 = datetime(2026, 7, 29, 15, 47)


def at(i: int) -> datetime:
    return T0 + timedelta(minutes=i)


class FakeCursor:
    """Answers the two reads (exceptions held, the trip's copy); records the writes."""

    def __init__(self, dists, held=()):
        self.copy = [{"dt_message": at(i), "i_seq": 0, "i_dist": d} for i, d in enumerate(dists)]
        self.held = [{"dt_fix": at(i), "i_seq": 0} for i in held]
        self.last = ""
        self.writes: dict[str, list] = {}

    def execute(self, sql, params=None):
        self.last = sql

    def fetchall(self):
        if "FROM trip_fix_override" in self.last:
            return self.held
        assert "v1_tta_trip_gps" in self.last and "ORDER BY dt_message, i_seq" in self.last
        return self.copy

    def executemany(self, sql, rows):
        self.writes[sql.split()[0]] = list(rows)


def placed(cdists):
    return [(at(i), 0, c) for i, c in enumerate(cdists)]


def test_agreeing_source_writes_nothing():
    cur = FakeCursor([0, 100, 250, 50])
    assert _keep_source_cdist(cur, 1, 42, placed([0, 100, 350, 400])) == 0
    assert cur.writes == {}


def test_counter_restart_is_kept_from_the_restart_on():
    # the source's counter restarts at the third fix: 350 -> 30, then +50
    cur = FakeCursor([0, 100, 250, 50])
    assert _keep_source_cdist(cur, 1, 42, placed([0, 100, 30, 80])) == 2
    assert [(r[2], r[4]) for r in cur.writes["INSERT"]] == [(at(2), 30), (at(3), 80)]
    assert "UPDATE" not in cur.writes


def test_a_corrected_resend_clears_only_held_exceptions():
    cur = FakeCursor([0, 100, 250, 50], held=[2, 3])
    assert _keep_source_cdist(cur, 1, 42, placed([0, 100, 350, 400])) == 0
    assert [r[2] for r in cur.writes["UPDATE"]] == [at(2), at(3)]


def test_no_source_figure_means_derived():
    cur = FakeCursor([0, 100])
    assert _keep_source_cdist(cur, 1, 42, placed([None, None])) == 0
    assert cur.writes == {}
