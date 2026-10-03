"""Application API, version 1: what the pages read.

Everything here reads the *published* run unless a `run` parameter names
another, so every page on screen describes the same run and a run under
evaluation is never shown by accident.
"""

from fastapi import APIRouter

from nexgen.shared.geoengine.api.v1 import (alerts, days, drill, entities, geofences, quality, regions, routes,
                               stops, trips, uploads)

# NexGen: under /api/v1/geo (Geo-Fencing's paths collided with Smart-Truck's
# /trips, /vehicles, /drivers, /transporters, /routes). Routes and cost are
# served by the routing service, which mounts `routes_router`.
router = APIRouter(prefix="/api/v1/geo")
for module in (days, geofences, regions, trips, entities, alerts, stops, quality, uploads, drill):
    router.include_router(module.router)

routes_router = APIRouter(prefix="/api/v1/geo")
routes_router.include_router(routes.router)
