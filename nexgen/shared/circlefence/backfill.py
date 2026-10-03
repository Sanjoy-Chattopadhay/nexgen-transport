"""Compute `dt_geofence_out` and the crossing ledger across the trip corpus.

The two-pass shape, and why
---------------------------
A trip's trail touches one or two facilities out of the hundred-odd on file.
Running the crossing detector for every (trip, fence) pair would mean ~100
full passes over each trail -- the exact cost the spatial index exists to
avoid. So each trail is walked *once* through `GeofenceIndex.locate`, which
is a dict lookup per ping and rejects the highway outright, and only the
handful of fences that actually came back are then handed to the detector.

That is the whole point of the index: it turns "which of a hundred fences
might this trip care about?" into a cheap question, so the expensive
jitter-aware detection runs two or three times per trip instead of a hundred.

Idempotence
-----------
A trip's ledger rows are deleted and rewritten inside the same transaction
that updates its stamp, so a re-run replaces rather than duplicates, and an
interrupted run leaves no half-written trip behind.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from nexgen.shared.legacy_db import get_connection
from .detection import Ping, crossings, first_sustained_exit
from .index import GeofenceIndex, haversine_m
from .store import build_index, ensure_schema

logger = logging.getLogger(__name__)

# Trips are fetched in blocks so a full-corpus run holds one block of pings in
# memory, not 3.5M rows.
TRIP_BLOCK = 200


def _fetch_trips(cur, trip_class: str | None, only_missing: bool, limit: int | None):
    where = ["1=1"]
    params: list = []
    if trip_class:
        where.append("s_trip_class = %s")
        params.append(trip_class)
    if only_missing:
        where.append("s_geofence_out_status IS NULL")
    sql = f"""SELECT i_trip_no, s_org_node_name, s_dest_node_name, s_trip_class,
                     dt_booking, dt_trip_start
                FROM tta_trips
               WHERE {' AND '.join(where)}
               ORDER BY i_trip_no"""
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    cur.execute(sql, params)
    return cur.fetchall()


def _fetch_pings(cur, trip_nos: list[int]) -> dict[int, list[Ping]]:
    """All pings for a block of trips, grouped by trip and time-ordered."""
    if not trip_nos:
        return {}
    placeholders = ",".join(["%s"] * len(trip_nos))
    cur.execute(
        f"""SELECT i_trip_no, dt_message, d_lat, d_long
              FROM tta_trip_gps
             WHERE i_trip_no IN ({placeholders})
             ORDER BY i_trip_no, dt_message""",
        trip_nos,
    )
    out: dict[int, list[Ping]] = {}
    for r in cur.fetchall():
        out.setdefault(r["i_trip_no"], []).append(
            Ping(r["dt_message"], float(r["d_lat"]), float(r["d_long"]))
        )
    return out


def _analyse_trip(trip: dict, pings: list[Ping], idx: GeofenceIndex) -> tuple[dict, list[tuple]]:
    """Return (stamp fields, ledger rows) for one trip."""
    trip_no = trip["i_trip_no"]
    origin_fence = idx.by_key(trip["s_org_node_name"], role="origin") \
        or idx.by_key(trip["s_org_node_name"])

    if not pings:
        return {"ts": None, "gap": None, "status": "no_gps"}, []

    # -- single pass: which fences does this trail touch at all? -------------
    touched: dict[int, object] = {}
    for p in pings:
        for f in idx.locate(p.lat, p.lon):
            touched.setdefault(f.fence_id, f)

    # -- jitter-aware detection, only for the fences that matter -------------
    ledger: list[tuple] = []
    for fence in touched.values():
        for c in crossings(pings, fence):
            ledger.append(
                (trip_no, fence.fence_id, fence.key, fence.role,
                 c.event, c.ts,
                 None if c.gap_min is None else int(round(c.gap_min)),
                 c.lat, c.lon)
            )

    if origin_fence is None:
        return {"ts": None, "gap": None, "status": "no_fence"}, ledger

    res = first_sustained_exit(pings, origin_fence)
    status = res.status

    if status == "never_exited":
        # Two very different things arrive here and must not share a code.
        #
        #  * An intra-belt trip -- JAMSHEDPUR -> GAMHARIA, -> CRM BARA -- whose
        #    destination is itself inside the origin fence. The truck really
        #    never left; there is no exit to find and nothing is wrong.
        #  * A Jamshedpur -> Chennai run whose trail also never leaves the
        #    fence. That truck certainly went to Chennai, so the tracker died
        #    inside the works. It is a GPS failure and belongs in the coverage
        #    report, not averaged into detention as a 'no exit'.
        dest = idx.by_key(trip["s_dest_node_name"])
        if dest is not None:
            inside_origin = haversine_m(
                dest.lat, dest.lon, origin_fence.lat, origin_fence.lon
            ) <= origin_fence.radius_m
            status = "intra_fence" if inside_origin else "gps_died_at_origin"
        # dest is None -> destination has no fence, so we genuinely cannot
        # tell which case this is. Leave it as never_exited.

    return (
        {
            "ts": res.ts,
            "gap": None if res.gap_min is None else int(round(res.gap_min)),
            "status": status,
        },
        ledger,
    )


def run(
    trip_class: str | None = "zonal",
    only_missing: bool = False,
    limit: int | None = None,
    conn=None,
) -> dict:
    """Backfill the geofence stamp and ledger.

    Args:
        trip_class: restrict to one lane ("zonal" / "local"), or None for both.
        only_missing: skip trips already carrying a status, for incremental runs.
        limit: cap the number of trips, for a quick smoke run.
    """
    own = conn is None
    conn = conn or get_connection()
    t0 = time.perf_counter()
    try:
        ensure_schema(conn)
        idx = build_index(conn)
        if len(idx) == 0:
            raise RuntimeError(
                "No active geofences. Run geofence.store.seed_from_anchors() first."
            )

        with conn.cursor() as cur:
            trips = _fetch_trips(cur, trip_class, only_missing, limit)

        stats = {
            "trips_scanned": 0, "pings_scanned": 0, "ledger_rows": 0,
            "status": {}, "index": idx.stats,
        }

        for start in range(0, len(trips), TRIP_BLOCK):
            block = trips[start:start + TRIP_BLOCK]
            with conn.cursor() as cur:
                pings_by_trip = _fetch_pings(cur, [t["i_trip_no"] for t in block])

                for trip in block:
                    pings = pings_by_trip.get(trip["i_trip_no"], [])
                    stamp, ledger = _analyse_trip(trip, pings, idx)

                    cur.execute(
                        "DELETE FROM tta_trip_geofence_events WHERE i_trip_no = %s",
                        (trip["i_trip_no"],),
                    )
                    if ledger:
                        cur.executemany(
                            """INSERT INTO tta_trip_geofence_events
                                 (i_trip_no, i_fence_id, s_fence_key, s_role,
                                  s_event, dt_event, i_gap_min, d_lat, d_long)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            ledger,
                        )
                    # NexGen: the trip row belongs to the fleet service, so the
                    # finding is kept in geofence's own trip_geofence_out (the
                    # legacy tta_trips view joins it back in).
                    from nexgen.core.tenancy import current_tenant_id
                    cur.execute(
                        """INSERT INTO trip_geofence_out
                              (i_tenant_id, i_trip_no, dt_geofence_out, i_geofence_out_gap_min, s_geofence_out_status)
                           VALUES (%s, %s, %s, %s, %s)
                           ON DUPLICATE KEY UPDATE dt_geofence_out = VALUES(dt_geofence_out),
                              i_geofence_out_gap_min = VALUES(i_geofence_out_gap_min),
                              s_geofence_out_status = VALUES(s_geofence_out_status)""",
                        (current_tenant_id(), trip["i_trip_no"], stamp["ts"], stamp["gap"], stamp["status"]),
                    )

                    stats["trips_scanned"] += 1
                    stats["pings_scanned"] += len(pings)
                    stats["ledger_rows"] += len(ledger)
                    stats["status"][stamp["status"]] = stats["status"].get(stamp["status"], 0) + 1
            conn.commit()
            logger.info("geofence backfill: %d/%d trips", stats["trips_scanned"], len(trips))

        elapsed = time.perf_counter() - t0
        stats["elapsed_sec"] = round(elapsed, 2)
        stats["pings_per_sec"] = int(stats["pings_scanned"] / elapsed) if elapsed else 0
        stats["finished_at"] = datetime.now().isoformat(timespec="seconds")
        logger.info("geofence backfill done: %s", stats)
        return stats
    finally:
        if own:
            conn.close()
