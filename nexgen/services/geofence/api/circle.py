"""Geofence API.

    GET  /api/v1/geofence/fences                -> the fence definitions in use
    GET  /api/v1/geofence/status                -> backfill coverage by status
    GET  /api/v1/geofence/detention             -> the three detention windows
    GET  /api/v1/geofence/detention/by/{dim}    -> hidden-tail leaderboard
    GET  /api/v1/geofence/gps/summary           -> fleet GPS health
    GET  /api/v1/geofence/gps/transporters      -> GPS health per transporter
    GET  /api/v1/geofence/gps/regions           -> GPS health per region
    GET  /api/v1/geofence/gps/route-end-gap     -> where trails actually stop
    GET  /api/v1/geofence/gps/ungeofenced       -> delivery points needing a fence
    GET  /api/v1/geofence/gps/detail            -> the trips behind one scorecard row
    GET  /api/v1/geofence/trip/{no}/delivery-geofence -> the complaint, per trip
    GET  /api/v1/geofence/demo-trips            -> trips worth showing a client
    GET  /api/v1/geofence/trip/{no}/track       -> one trip's trail + fences, for the map
    POST /api/v1/geofence/import/preview        -> validate a client file, write nothing
    POST /api/v1/geofence/import                -> load the client's real geofences
    POST /api/v1/geofence/seed                  -> (re)seed fences from anchors
    POST /api/v1/geofence/backfill              -> recompute stamps + ledger

Reads are cheap and safe. The two POSTs are the only writes: `seed` rewrites
fence definitions that are not hand-flagged, and `backfill` rewrites
`dt_geofence_out` and the crossing ledger. Both are idempotent.

`backfill` walks every ping in scope and takes minutes on the full corpus, so
it is a deliberate operator action, not something a dashboard should call on
render. Pass `only_missing=true` for the incremental case -- after an ETL
cycle, that stamps just the new trips.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, Depends

from nexgen.shared.legacy_db import get_db
from nexgen.shared.circlefence import store
from nexgen.shared.circlefence import backfill as backfill_svc
from nexgen.shared.circlefence import importer as importer_svc
from nexgen.shared.circlefence import detention as detention_svc
from nexgen.shared.circlefence import gps_quality as gps_svc
from nexgen.shared.circlefence import track as track_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/geofence", tags=["Geofence"])

VALID_CLASSES = {"zonal", "local"}


def _trip_class(value: str | None) -> str | None:
    """`all` (or unset) means both lanes; anything else must name a real one."""
    if value in (None, "", "all"):
        return None
    if value not in VALID_CLASSES:
        raise HTTPException(400, f"trip_class must be one of {sorted(VALID_CLASSES)} or 'all'")
    return value


# ----------------------------------------------------------------------
# Definitions and coverage
# ----------------------------------------------------------------------

@router.get("/fences")
def list_fences(include_inactive: bool = False, conn=Depends(get_db)):
    """The fences the detector is running against, with index statistics."""
    idx = store.build_index(conn, active_only=not include_inactive)
    return {
        "index": idx.stats,
        "fences": [
            {
                "fence_id": f.fence_id, "key": f.key, "name": f.name,
                "role": f.role, "lat": f.lat, "lon": f.lon,
                "radius_m": f.radius_m,
            }
            for f in sorted(idx.fences, key=lambda x: (x.role, x.key))
        ],
    }


@router.get("/status")
def status(conn=Depends(get_db)):
    """How much of the corpus carries a geofence verdict, and of what kind."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s_trip_class, COALESCE(s_geofence_out_status, 'not_computed') AS st,
                      COUNT(*) AS n
                 FROM tta_trips GROUP BY 1, 2"""
        )
        rows = cur.fetchall()
    by_class: dict[str, dict] = {}
    for r in rows:
        by_class.setdefault(r["s_trip_class"], {})[r["st"]] = r["n"]
    return {
        "by_trip_class": by_class,
        "status_meaning": {
            "ok": "a confirmed exit from the origin fence; dt_geofence_out is set",
            "intra_fence": "destination is inside the origin fence, so there is no exit to find",
            "gps_died_at_origin": "trail stopped inside the origin fence on a long-haul trip",
            "never_inside": "no ping ever fell inside the origin fence",
            "never_exited": "no confirmed exit and the destination has no fence to judge by",
            "no_gps": "the trip has no pings at all",
            "no_fence": "the origin node has no geofence defined",
        },
    }


# ----------------------------------------------------------------------
# Detention
# ----------------------------------------------------------------------

@router.get("/detention")
def detention_summary(
    trip_class: str = Query("zonal", description="zonal | local | all"),
    consignor_id: int | None = None,
    conn=Depends(get_db),
):
    """Declared works detention vs the hidden tail vs the true total."""
    return detention_svc.summary(_trip_class(trip_class), consignor_id, conn)


@router.get("/detention/by/{dimension}")
def detention_by(
    dimension: str,
    trip_class: str = Query("zonal"),
    min_trips: int = Query(5, ge=1),
    conn=Depends(get_db),
):
    """Hidden-tail leaderboard by `transporter` or `destination`."""
    if dimension not in ("transporter", "destination"):
        raise HTTPException(400, "dimension must be 'transporter' or 'destination'")
    return detention_svc.by_dimension(dimension, _trip_class(trip_class), min_trips, conn)


# ----------------------------------------------------------------------
# GPS quality
# ----------------------------------------------------------------------

@router.get("/gps/summary")
def gps_summary(trip_class: str = Query("zonal"), conn=Depends(get_db)):
    return gps_svc.fleet_summary(_trip_class(trip_class), conn)


@router.get("/gps/transporters")
def gps_transporters(
    trip_class: str = Query("zonal"),
    min_trips: int = Query(5, ge=1),
    conn=Depends(get_db),
):
    return gps_svc.transporter_scorecard(_trip_class(trip_class), min_trips, conn)


@router.get("/gps/regions")
def gps_regions(
    trip_class: str = Query("zonal"),
    min_trips: int = Query(5, ge=1),
    conn=Depends(get_db),
):
    return gps_svc.region_scorecard(_trip_class(trip_class), min_trips, conn)


@router.get("/trip/{trip_no}/delivery-geofence")
def trip_delivery_geofence(trip_no: int, conn=Depends(get_db)):
    """Does this trip match the consignor's complaint, check by check?

    The complaint -- "GPS was being captured, customer was not geo fenced, met
    expected km ~90%, but the trip closed as no-geofence" -- is four separate
    claims. This evaluates each one against this trip and returns them
    individually, so a partial match is never reported as the whole thing.
    """
    try:
        return gps_svc.delivery_geofence_verdict(trip_no, conn)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/gps/detail")
def gps_dimension_detail(
    dimension: str = Query(..., description="transporter | region"),
    value: str = Query(..., description="the exact scorecard row value"),
    trip_class: str = Query("zonal"),
    conn=Depends(get_db),
):
    """Every trip behind one scorecard row, with the inputs that judged it.

    This is what makes the percentages checkable: ping count, the lag to the
    first ping, the provider's uptime and the geofence status for each trip, so
    a reader can recompute any cell or open the trip and look at the trail.
    """
    try:
        return gps_svc.dimension_detail(dimension, value, _trip_class(trip_class), conn)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/gps/route-end-gap")
def gps_route_end_gap(
    trip_class: str = Query("zonal"),
    min_trips: int = Query(5, ge=1),
    conn=Depends(get_db),
):
    """Per destination, how far short of it the GPS trail actually stopped."""
    return gps_svc.route_end_gap(_trip_class(trip_class), min_trips, conn)


@router.get("/gps/destination-detail")
def gps_destination_detail(
    destination: str = Query(..., description="exact destination node name"),
    trip_class: str = Query("zonal"),
    conn=Depends(get_db),
):
    """Every trip to one destination, with where its trail actually stopped.

    Makes the lane summary checkable: per trip, the distance from its last ping
    to the fence, what it closed as, how far it ran against the lane median, and
    whether eTrans had a geofence for it at all. `gap_is_conclusive` says
    whether the gap can be read as a shortfall -- against a town-centroid fence
    it cannot.
    """
    return gps_svc.destination_detail(destination, _trip_class(trip_class), conn)


@router.get("/gps/ungeofenced")
def gps_ungeofenced(trip_class: str = Query("zonal"), conn=Depends(get_db)):
    """Delivery locations that need a geofence, with suggested centres."""
    return gps_svc.ungeofenced_destinations(_trip_class(trip_class), conn)


# ----------------------------------------------------------------------
# Writes
# ----------------------------------------------------------------------

@router.post("/seed")
def seed(conn=Depends(get_db)):
    """(Re)build fence definitions from the GPS-derived facility anchors.

    Rows flagged `b_manual` are left alone, so hand corrections survive.
    """
    store.ensure_schema(conn)
    return store.seed_from_anchors(conn)


@router.post("/backfill")
def backfill(
    trip_class: str = Query("zonal", description="zonal | local | all"),
    only_missing: bool = Query(False, description="skip trips already carrying a status"),
    limit: int | None = Query(None, ge=1, description="cap trips, for a smoke run"),
    conn=Depends(get_db),
):
    """Recompute `dt_geofence_out` and the crossing ledger.

    Minutes on the full corpus. Use `only_missing=true` to stamp just the
    trips a recent ETL cycle added.
    """
    try:
        return backfill_svc.run(_trip_class(trip_class), only_missing, limit, conn)
    except RuntimeError as exc:
        # Raised when no fences exist yet -- an operator-fixable state, not a bug.
        raise HTTPException(409, str(exc)) from exc


@router.post("/import")
def import_client_fences(
    fences: list[dict],
    cutover: bool = Query(
        False,
        description="deactivate every derived fence -- only when the client's set is complete",
    ),
    conn=Depends(get_db),
):
    """Load client-supplied geofences.

    The derived fences are a stand-in until the client hands over the real
    geometry; this is where that geometry lands. Imported rows are marked
    `b_manual` and are never overwritten by `/seed`.

    Rows are validated before they are written -- a fence with swapped lat/lon
    is a valid coordinate pair somewhere in the world, and would silently
    report every truck as absent rather than raising anything. Bad rows come
    back in `rejected` with a reason; good rows are still imported.

    `cutover` is off by default so a partial delivery cannot blind the fleet
    for every node the client has not sent yet. Re-run the backfill afterwards
    so the stamps reflect the new geometry.
    """
    store.ensure_schema(conn)
    return importer_svc.import_fences(fences, conn, deactivate_derived=cutover)


# ----------------------------------------------------------------------
# Single-trip view -- what a client is shown before they believe an aggregate
# ----------------------------------------------------------------------

@router.get("/demo-trips")
def list_demo_trips(limit: int = Query(12, ge=1, le=50), conn=Depends(get_db)):
    """Trips that actually demonstrate something, rather than a random sample.

    Two thirds of the corpus is `intra_fence` -- a truck whose destination is
    inside the origin fence, where there is correctly nothing to see. Opening a
    demo on one of those shows a flat line and proves nothing, so this returns
    the long confirmed hidden tails and the died-at-origin trips instead.
    """
    return track_svc.demo_trips(limit, conn)


@router.get("/trip/{trip_no}/track")
def trip_track(trip_no: int, conn=Depends(get_db)):
    """One trip's GPS trail, fences and stamps -- everything needed to draw it.

    Each ping is tagged `in_origin`, so the map can colour the trail by whether
    the truck had actually cleared the works. That red segment after the
    gate-out stamp is the hidden detention, shown rather than asserted.
    """
    try:
        return track_svc.trip_track(trip_no, conn)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/import/preview")
def preview_client_fences(fences: list[dict], conn=Depends(get_db)):
    """Validate a client geofence file and report what importing it would do.

    Writes nothing. Returns the column mapping we detected in their file, the
    per-row verdicts, how far each fence would move from the derived stand-in
    it replaces, and which node names match no trip -- with a `did_you_mean`
    suggestion, since the client's "PITHAMPUR" and the feed's
    "PITHAMPUR (M.P.)" are the same place and the name is the only join.
    """
    store.ensure_schema(conn)
    return importer_svc.preview(fences, conn)
