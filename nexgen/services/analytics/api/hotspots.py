"""
Pilferage Hotspot Discovery API.

    GET  /api/v1/hotspots                 -> ranked investigation queue
    GET  /api/v1/hotspots/status          -> corpus coverage + extraction backlog
    GET  /api/v1/hotspots/stops           -> the individual stops behind the counts
    GET  /api/v1/hotspots/{cluster_id}    -> evidence pack for one place
    POST /api/v1/hotspots/extract         -> pull new stop events from GPS
    POST /api/v1/hotspots/refresh         -> rebuild anchors, masks, clusters
    POST /api/v1/hotspots/{id}/label      -> record an investigator verdict

WHAT THIS IS NOT
----------------
An investigation queue, not an accusation. It finds anomalous *stopping*, not
theft. Every response carries `disclaimer` so a consumer (dashboard, MCP
client, LLM) cannot present a ranked coordinate as a finding of wrongdoing.

SCOPING
-------
gps_stop_clusters carries a cnr_id dimension written at build time (0 = the
all-consignor rollup), so scoping is a plain WHERE with no re-aggregation —
the summary-table convention from backend/app/core/consignor.py.

The per-cluster read matches on `scope.summary_id` exactly rather than using
scope.allows(): an unscoped caller sees rollup clusters and a scoped caller
sees only its own. A rollup cluster aggregates across consignors, so it must
never be reachable from inside a single consignor's scope.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from nexgen.shared.legacy_db import get_db
from nexgen.shared.common.consignor import ConsignorScope, consignor_scope

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/hotspots", tags=["Pilferage Hotspots"])

DISCLAIMER = (
    "Anomalous stopping, not theft. These are locations that attract repeated "
    "stops by multiple vehicles across multiple carriers. Each is an "
    "investigation lead requiring physical verification, not evidence of "
    "wrongdoing."
)

VALID_LABEL_STATUSES = {
    "investigating", "known_facility", "rest_stop", "cleared", "confirmed",
}


class LabelIn(BaseModel):
    status: str = Field(..., description=f"one of {sorted(VALID_LABEL_STATUSES)}")
    note: str = ""
    labelled_by: str = ""


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------

@router.get("")
def list_hotspots_ep(
    limit: int = Query(50, ge=1, le=500),
    include_low_support: bool = False,
    include_amenities: bool = False,
    scope: ConsignorScope = Depends(consignor_scope),
    conn=Depends(get_db),
):
    """Ranked investigation queue.

    Low-support clusters (too few stops/vehicles/carriers to mean anything) and
    amenity-adjacent ones (next to a fuel stop, dhaba, toll or parking area)
    are hidden by default. Both are filtered, never deleted — pass the flags to
    audit what the ranking set aside.
    """
    from nexgen.services.analytics.lib.tta_hotspot_clusters import list_hotspots
    res = list_hotspots(conn, limit=limit, include_low_support=include_low_support,
                        include_amenities=include_amenities, cnr_id=scope.id)
    res["disclaimer"] = DISCLAIMER
    res["consignor"] = scope.name
    return res


@router.get("/status")
def hotspots_status_ep(scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Corpus coverage and extraction backlog — how much history the ranking
    is standing on. A queue built from two weeks of data deserves less
    confidence than one built from a year, and this is where that shows."""
    from nexgen.services.analytics.lib.tta_hotspots import stop_extraction_status
    st = stop_extraction_status(conn, cnr_id=scope.id)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) AS clusters, MAX(dt_refreshed) AS last_built
               FROM gps_stop_clusters WHERE cnr_id = %s""",
            (scope.summary_id,),
        )
        st["clusters"] = cur.fetchone()
    st["disclaimer"] = DISCLAIMER
    return st


@router.get("/stops")
def hotspot_stops_ep(
    clustered: bool = Query(True, description="true = stops inside a cluster; "
                                              "false = the whole extracted corpus"),
    limit: int = Query(500, ge=1, le=2000),
    scope: ConsignorScope = Depends(consignor_scope),
    conn=Depends(get_db),
):
    """The individual stops behind a headline count.

    Every KPI on this page is a count of these rows, so each one has to be able
    to produce them: which vehicle, which carrier, when it stopped and for how
    long. A number nobody can open is a number nobody can check.

    `clustered=false` returns the full corpus including stops masked as a
    plant, consignee or amenity — `s_place_class` says which, so what the
    pipeline set aside stays visible rather than silently vanishing.
    """
    from nexgen.services.analytics.lib.tta_hotspot_clusters import stop_records
    rows = stop_records(conn, cnr_id=scope.id, clustered=clustered, limit=limit)
    return {
        "stops": rows,
        "count": len(rows),
        "truncated": len(rows) >= limit,
        "clustered": clustered,
        "consignor": scope.name,
        "disclaimer": DISCLAIMER,
    }


@router.get("/{cluster_id}")
def hotspot_detail_ep(cluster_id: int,
                      scope: ConsignorScope = Depends(consignor_scope),
                      conn=Depends(get_db)):
    """Evidence pack: the cluster, its member stops, the trips and carriers
    behind them, its hour-of-day profile, and per-vehicle / per-carrier
    rollups derived from those same stop rows."""
    from nexgen.services.analytics.lib.tta_hotspot_clusters import hotspot_detail
    detail = hotspot_detail(conn, cluster_id)
    # 404 rather than 403 on a scope miss, so the response never reveals that a
    # cluster exists in another consignor's scope.
    if not detail or detail["cluster"]["cnr_id"] != scope.summary_id:
        raise HTTPException(404, f"Hotspot {cluster_id} not found")
    detail["disclaimer"] = DISCLAIMER
    return detail


# ----------------------------------------------------------------------
# Builds — heavy batch ops on a dedicated long-timeout connection
# ----------------------------------------------------------------------

@router.post("/extract")
def hotspots_extract_ep(limit: int = Query(500, ge=1, le=5000)):
    """Extract stop events for trips synced since the last run.

    Append-only and skip-if-running. Bounded by `limit`; the response reports
    `more_pending` when the queue was not drained.
    """
    from nexgen.shared.legacy_db import get_connection
    from nexgen.services.analytics.lib.tta_hotspots import run_hotspot_schema, run_stop_extraction
    conn = get_connection(read_timeout=3600, write_timeout=3600)
    try:
        run_hotspot_schema(conn)
        return run_stop_extraction(conn, limit=limit)
    finally:
        conn.close()


@router.post("/refresh")
def hotspots_refresh_ep(run_masking: bool = True):
    """Rebuild facility anchors, stop masking, clusters and scores.

    Minutes-long on a large corpus (the anchor pass walks every trip's first
    and last ping). Pass run_masking=false to re-cluster with the existing
    masks — the fast path when only scoring weights changed.
    """
    from nexgen.shared.legacy_db import get_connection
    from nexgen.services.analytics.lib.tta_hotspots import run_hotspot_schema
    from nexgen.services.analytics.lib.tta_hotspot_clusters import refresh_clusters
    conn = get_connection(read_timeout=3600, write_timeout=3600)
    try:
        run_hotspot_schema(conn)
        return refresh_clusters(conn, run_masking=run_masking)
    finally:
        conn.close()


# ----------------------------------------------------------------------
# Investigator verdicts
# ----------------------------------------------------------------------

@router.post("/{cluster_id}/label")
def hotspot_label_ep(cluster_id: int, body: LabelIn,
                     scope: ConsignorScope = Depends(consignor_scope),
                     conn=Depends(get_db)):
    """Record a verdict against this cluster's LOCATION.

    Stored against coordinates, not the cluster id, so it survives the nightly
    rebuild that renumbers clusters. Append-only: a changed verdict is a new
    row, preserving who decided what and when.
    """
    if body.status not in VALID_LABEL_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(VALID_LABEL_STATUSES)}")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT d_lat, d_long, cnr_id FROM gps_stop_clusters WHERE id = %s",
            (cluster_id,),
        )
        cluster = cur.fetchone()
    if not cluster or cluster["cnr_id"] != scope.summary_id:
        raise HTTPException(404, f"Hotspot {cluster_id} not found")

    from nexgen.services.analytics.lib.tta_hotspot_clusters import add_label
    return add_label(conn, cluster["d_lat"], cluster["d_long"],
                     body.status, body.note, body.labelled_by)
