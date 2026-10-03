"""Transporter (carrier) API.

    GET /api/v1/transporters                      -> scored league table
    GET /api/v1/transporters/reliability-matrix   -> volume vs reliability
    GET /api/v1/transporters/compare              -> head-to-head, 2-4 carriers
    GET /api/v1/transporters/{name}               -> one carrier, everything

Shares the TTA dashboard filter dependency (date window, dimension filters and
the active consignor scope), so the same URL params work here as on /analytics
and a figure on the carrier page matches the same figure on the dashboard.

Route order matters: `/{name:path}` is declared last because it would otherwise
swallow "compare" and "reliability-matrix" as carrier names. It is a `:path`
converter because carrier names contain slashes, dots and commas.
"""
from fastapi import APIRouter, Depends, Query

from nexgen.services.analytics.api.tta_dashboard import dashboard_filters
from nexgen.shared.legacy_db import get_db
from nexgen.services.analytics.lib import transporter_insights as svc

router = APIRouter(prefix="/transporters", tags=["Transporters"])


@router.get("")
def list_transporters(search: str = "", min_trips: int = svc.DEFAULT_MIN_TRIPS,
                      f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """Every carrier with KPIs, composite score, grade and rank, plus the
    fleet-wide benchmark medians used to judge them."""
    return svc.list_transporters(conn, f, search, min_trips)


@router.get("/reliability-matrix")
def reliability_matrix(min_trips: int = svc.DEFAULT_MIN_TRIPS,
                       otd_target: float = Query(svc.OTD_TARGET, ge=0, le=100),
                       f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """Volume against reliability, one point per carrier, split into quadrants.

    Answers "where is the freight sitting relative to the service" rather than
    "who is best" — a weak carrier with four trips and a weak carrier holding a
    third of the freight rank identically on a league table and could not be
    more different as decisions. Each carrier carries a Wilson interval on its
    on-time rate, so a position built on ten trips is visibly less certain than
    one built on five hundred.
    """
    return svc.reliability_matrix(conn, f, min_trips, otd_target)


@router.get("/best-by-lane")
def best_by_lane(min_trips: int = svc.LANE_MIN_TRIPS, limit: int = Query(60, ge=1, le=300),
                 f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """Which carrier to give each lane to, judged only on that lane.

    Overall carrier averages mostly measure who holds the longer routes; this
    compares carriers only against others who ran the SAME origin-destination
    pair. Lanes are ordered by the on-time gap between best and worst, so the
    biggest available service win is first. A carrier needs `min_trips` on the
    lane to be eligible, and ranking uses a Wilson lower bound so nobody wins a
    lane on three lucky trips.

    The response also carries a `commodity` block. A commodity-level version was
    asked for and cannot be built: no material dimension exists anywhere in the
    feed. The block says exactly what is missing and what would be needed, so
    the gap is visible in the product rather than only in a conversation.
    """
    return svc.best_by_lane(conn, f, min_trips, limit)


@router.get("/compare")
def compare_transporters(
    names: str = Query("", description="||-separated carrier names (2–4)"),
    granularity: str = Query("M", pattern="^[DWM]$"),
    min_trips: int = svc.DEFAULT_MIN_TRIPS,
    f: dict = Depends(dashboard_filters), conn=Depends(get_db),
):
    """Head-to-head benchmark of 2–4 hand-picked carriers: KPIs, insights,
    trends on a shared period grid, transit/detention spread, fleet & outcome
    mix, and the lanes they both actually run.

    Names are ||-separated (not comma) because carrier names contain commas.
    """
    return svc.compare_transporters(
        conn, [n for n in names.split("||") if n.strip()], f, min_trips, granularity)


@router.get("/{name:path}")
def transporter_detail(name: str, min_trips: int = svc.DEFAULT_MIN_TRIPS,
                       f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """One carrier's whole record, in a single response.

    Identity (bio), KPIs against the fleet median, plain-English insights, GPS
    tracking quality, monthly trend, lanes, states served, vehicle list, driver
    list, fleet and outcome mix, transit and detention spread, departure
    rhythm, worst delays and recent trips.

    One call rather than a dozen: every block is a slice of the same filtered
    trip frame, so splitting it would re-derive that frame per block and could
    render blocks from two different filter states side by side.
    """
    return svc.transporter_detail(conn, name, f, min_trips)
