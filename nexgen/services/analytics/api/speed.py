"""Speed & safety API.

    GET  /api/v1/speed/overview         -> in-plant vs on-road speed, one limit each
    GET  /api/v1/speed/daily            -> safety violations per day
    GET  /api/v1/speed/events           -> the violation register (drill-down)
    GET  /api/v1/speed/carriers         -> safety scorecard per transporter
    GET  /api/v1/speed/running-pattern  -> what the fleet does across 24 hours
    GET  /api/v1/speed/status           -> when the rollups were last rebuilt
    POST /api/v1/speed/rebuild          -> rebuild them

**The speed limit is a parameter on every read.** `road_limit` judges pings
outside a plant fence and `plant_limit` judges pings inside one; both default to
the business figures (60 and 20 km/h) and both are echoed back in every
response, because a violation count without its limit is not a number anyone
can act on. Only the limits the event register was materialised for are valid
(see speed_profile.ROAD_LIMITS / PLANT_LIMITS) -- an unlisted value falls back
to the default rather than being answered approximately under the caller's
label. `/overview` returns the full option list so a UI never has to hardcode it.

Every read honours the same filter set as /analytics (date window, consignor
scope, transporter, destination, fleet), so a number here and a number there
describe the same trips.

`POST /rebuild` rescans the ping table and takes minutes -- an operator action,
not something a dashboard calls on render.
"""
import logging

from fastapi import APIRouter, Depends, Query

from nexgen.services.analytics.api.tta_dashboard import dashboard_filters
from nexgen.shared.legacy_db import get_db
from nexgen.services.analytics.lib import speed_profile
from nexgen.services.analytics.lib import speed_safety as svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/speed", tags=["Speed & Safety"])


@router.get("/overview")
def speed_overview(
    road_limit: int = Query(speed_profile.DEFAULT_ROAD_LIMIT,
                            description="km/h limit applied outside plant fences"),
    plant_limit: int = Query(speed_profile.DEFAULT_PLANT_LIMIT,
                             description="km/h limit applied inside plant fences"),
    f: dict = Depends(dashboard_filters), conn=Depends(get_db),
):
    """Speed inside plants vs on the road, judged against the chosen limits.

    Each zone returns its full 1 km/h speed histogram, time-weighted average and
    percentile speeds, and the time / distance / episodes spent over the limit.
    """
    return svc.speed_overview(conn, f, road_limit, plant_limit)


@router.get("/daily")
def safety_daily(
    road_limit: int = Query(speed_profile.DEFAULT_ROAD_LIMIT),
    plant_limit: int = Query(speed_profile.DEFAULT_PLANT_LIMIT),
    f: dict = Depends(dashboard_filters), conn=Depends(get_db),
):
    """Safety violations per day, each row carrying that day's exposure (trips,
    vehicles) and the normalised rate — so the trend reads as safety rather
    than as dispatch volume."""
    return svc.safety_daily(conn, f, road_limit, plant_limit)


@router.get("/events")
def speed_events(
    road_limit: int = Query(speed_profile.DEFAULT_ROAD_LIMIT),
    plant_limit: int = Query(speed_profile.DEFAULT_PLANT_LIMIT),
    zone: str = Query("", pattern="^(plant|road)?$"),
    transporter: str = "",
    rows: int = Query(300, ge=1, le=2000),
    f: dict = Depends(dashboard_filters), conn=Depends(get_db),
):
    """The violation register: one row per overspeed episode, worst peak first."""
    return svc.events(conn, f, road_limit, plant_limit,
                      zone or None, transporter or None, rows)


@router.get("/carriers")
def carrier_safety(
    road_limit: int = Query(speed_profile.DEFAULT_ROAD_LIMIT),
    plant_limit: int = Query(speed_profile.DEFAULT_PLANT_LIMIT),
    min_trips: int = Query(1, ge=1),
    f: dict = Depends(dashboard_filters), conn=Depends(get_db),
):
    """Safety scorecard per transporter, rated per 100 trips that produced GPS."""
    return svc.carrier_safety(conn, f, road_limit, plant_limit, min_trips)


@router.get("/running-pattern")
def running_pattern(
    transporter: str = "",
    f: dict = Depends(dashboard_filters), conn=Depends(get_db),
):
    """Moving / stopped / in-plant hours for each hour of the clock.

    Not the same question as the departure-rhythm heatmap: that counts trips
    *leaving* at each hour, this measures what the trucks already out there are
    doing. Pass `transporter` for one carrier's fleet.
    """
    return svc.running_pattern(conn, f, transporter or None)


@router.get("/status")
def speed_status(conn=Depends(get_db)):
    """Rollup freshness: when each was built and how far behind the ping table."""
    return speed_profile.build_status(conn)


@router.post("/rebuild")
def speed_rebuild(conn=Depends(get_db)):
    """Rescan tta_trip_gps and rebuild all three speed/safety rollups.

    Minutes on the full corpus, and a full replace rather than a merge — see
    speed_profile.refresh_all. Deliberately an operator action.
    """
    out = speed_profile.refresh_all(conn)
    # The read cache holds answers derived from the rollups that were just
    # replaced; without this the screens would keep serving the old build for
    # another five minutes after an operator explicitly rebuilt them.
    svc.invalidate_cache()
    return out
